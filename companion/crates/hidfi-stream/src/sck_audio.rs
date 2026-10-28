//! The computer's sound and its microphone on macOS, through ScreenCaptureKit
//! (docs/AUDIO.md).
//!
//! System sound: every app's audio, taken before the system routes it to a
//! device - speakers, headphones, AirPods or a virtual device all give the same
//! capture, and changing the output while watching changes nothing here. The
//! Companion's own sounds are left out. Needs the Screen Recording permission
//! the picture already needs.
//!
//! Microphone: ScreenCaptureKit's own microphone capture (macOS 15 and later),
//! the system's default input; needs the Microphone permission.
//!
//! ScreenCaptureKit always captures a display too: its picture is kept to 2x2
//! pixels at one frame a second and never read.

use crate::audio::{Chunk, Ended, Input, Running, Sink};
use block2::RcBlock;
use core_foundation_sys::base::{CFRelease, CFTypeRef};
use dispatch2::DispatchQueue;
use objc2::rc::Retained;
use objc2::runtime::{NSObject, NSObjectProtocol, ProtocolObject};
use objc2::{define_class, msg_send, AllocAnyThread, DefinedClass};
use objc2_core_media::{CMSampleBuffer, CMTime, CMTimeFlags};
use objc2_foundation::{NSArray, NSError};
use objc2_screen_capture_kit::{
    SCContentFilter, SCShareableContent, SCStream, SCStreamConfiguration, SCStreamDelegate, SCStreamOutput,
    SCStreamOutputType, SCWindow,
};
use std::ffi::c_void;
use std::sync::mpsc;
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct Asbd {
    sample_rate: f64,
    format_id: u32,
    format_flags: u32,
    bytes_per_packet: u32,
    frames_per_packet: u32,
    bytes_per_frame: u32,
    channels_per_frame: u32,
    bits_per_channel: u32,
    reserved: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct AudioBuffer {
    channels: u32,
    bytes: u32,
    data: *mut c_void,
}

/// An AudioBufferList with room for up to eight buffers (planar 7.1).
#[repr(C)]
struct BufferList {
    count: u32,
    buffers: [AudioBuffer; 8],
}

const FLAG_FLOAT: u32 = 1;
const FLAG_NON_INTERLEAVED: u32 = 1 << 5;
const ASSURE_16_BYTE_ALIGNMENT: u32 = 1;

#[link(name = "CoreMedia", kind = "framework")]
extern "C" {
    fn CMSampleBufferGetFormatDescription(sb: *mut c_void) -> *mut c_void;
    fn CMAudioFormatDescriptionGetStreamBasicDescription(desc: *mut c_void) -> *const Asbd;
    fn CMSampleBufferGetNumSamples(sb: *mut c_void) -> isize;
    fn CMSampleBufferGetPresentationTimeStamp(sb: *mut c_void) -> CMTime;
    fn CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sb: *mut c_void,
        size_needed: *mut usize,
        list: *mut BufferList,
        list_size: usize,
        a1: *const c_void,
        a2: *const c_void,
        flags: u32,
        block: *mut *mut c_void,
    ) -> i32;
    fn CMClockGetHostTimeClock() -> *mut c_void;
    fn CMClockGetTime(clock: *mut c_void) -> CMTime;
}

fn seconds(t: CMTime) -> Option<f64> {
    (t.flags.contains(CMTimeFlags::Valid) && t.timescale > 0).then(|| t.value as f64 / t.timescale as f64)
}

/// The sample's capture time on our clock: how long ago, by the host clock, it began.
unsafe fn captured_at(sb: *mut c_void) -> Instant {
    let now = Instant::now();
    let pts = seconds(CMSampleBufferGetPresentationTimeStamp(sb));
    let host = seconds(CMClockGetTime(CMClockGetHostTimeClock()));
    match (pts, host) {
        (Some(p), Some(h)) if h >= p && h - p < 1.0 => now - Duration::from_secs_f64(h - p),
        _ => now,
    }
}

/// Interleaved f32 from a sample of float PCM, planar or interleaved.
unsafe fn read_pcm(sb: *mut c_void) -> Option<(Vec<f32>, u32, usize)> {
    let desc = CMSampleBufferGetFormatDescription(sb);
    if desc.is_null() {
        return None;
    }
    let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc).as_ref()?;
    if asbd.format_flags & FLAG_FLOAT == 0 || asbd.bits_per_channel != 32 {
        return None; // ScreenCaptureKit gives 32-bit float; anything else is not expected
    }
    let channels = asbd.channels_per_frame.clamp(1, 8) as usize;
    let frames = CMSampleBufferGetNumSamples(sb).max(0) as usize;
    let mut block: *mut c_void = std::ptr::null_mut();
    let mut needed: usize = 0;
    // First the size the list needs, then a list of exactly that size: a larger
    // one is refused as "too small" (-12737), seen on macOS 15.
    let q = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sb,
        &mut needed,
        std::ptr::null_mut(),
        0,
        std::ptr::null(),
        std::ptr::null(),
        0,
        std::ptr::null_mut(),
    );
    if q != 0 || needed < 8 || needed > std::mem::size_of::<BufferList>() {
        LAST_STATUS.store(q, std::sync::atomic::Ordering::Relaxed);
        return None;
    }
    let mut storage = vec![0u128; needed.div_ceil(16)];
    let st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sb,
        std::ptr::null_mut(),
        storage.as_mut_ptr() as *mut BufferList,
        needed,
        std::ptr::null(),
        std::ptr::null(),
        ASSURE_16_BYTE_ALIGNMENT,
        &mut block,
    );
    LAST_STATUS.store(st, std::sync::atomic::Ordering::Relaxed);
    if st != 0 {
        return None;
    }
    let count = *(storage.as_ptr() as *const u32);
    let n = (count as usize).min((needed - 8) / std::mem::size_of::<AudioBuffer>()).min(8);
    let first = (storage.as_ptr() as *const u8).add(8) as *const AudioBuffer;
    let bufs: Vec<AudioBuffer> = (0..n).map(|i| *first.add(i)).collect();
    let mut out = vec![0f32; frames * channels];
    if asbd.format_flags & FLAG_NON_INTERLEAVED != 0 {
        for (c, b) in bufs.iter().take(channels).enumerate() {
            let len = (b.bytes as usize / 4).min(frames);
            if b.data.is_null() {
                continue;
            }
            let s = std::slice::from_raw_parts(b.data as *const f32, len);
            for (i, v) in s.iter().enumerate() {
                out[i * channels + c] = *v;
            }
        }
    } else if let Some(b) = bufs.first().copied().filter(|b| !b.data.is_null()) {
        let len = (b.bytes as usize / 4).min(frames * channels);
        out[..len].copy_from_slice(std::slice::from_raw_parts(b.data as *const f32, len));
    }
    if !block.is_null() {
        CFRelease(block as CFTypeRef);
    }
    Some((out, asbd.sample_rate.round() as u32, channels))
}

/// Diagnostics (`--check-sound`): sample buffers delivered, and ones that could
/// not be read, with the last unreadable format's flags and bits.
pub static DELIVERED: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
pub static UNREADABLE: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
pub static LAST_FORMAT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
/// The last status of reading a buffer's samples (0 = read).
pub static LAST_STATUS: std::sync::atomic::AtomicI32 = std::sync::atomic::AtomicI32::new(0);

struct Ivars {
    sink: Sink,
    ended: Ended,
    kind: SCStreamOutputType,
}

define_class!(
    #[unsafe(super(NSObject))]
    #[name = "HidFiAudioOutput"]
    #[ivars = Ivars]
    struct Output;

    unsafe impl NSObjectProtocol for Output {}

    unsafe impl SCStreamOutput for Output {
        #[unsafe(method(stream:didOutputSampleBuffer:ofType:))]
        fn did_output(&self, _stream: &SCStream, sample: &CMSampleBuffer, kind: SCStreamOutputType) {
            if kind != self.ivars().kind {
                return;
            }
            use std::sync::atomic::Ordering::Relaxed;
            DELIVERED.fetch_add(1, Relaxed);
            let sb = sample as *const CMSampleBuffer as *mut c_void;
            // SAFETY: a valid sample for the duration of this call.
            unsafe {
                let at = captured_at(sb);
                match read_pcm(sb) {
                    Some((samples, rate, channels)) if rate > 0 && !samples.is_empty() => {
                        (self.ivars().sink)(Chunk { samples, rate, channels, at });
                    }
                    _ => {
                        UNREADABLE.fetch_add(1, Relaxed);
                        let d = CMSampleBufferGetFormatDescription(sb);
                        if let Some(a) = (!d.is_null()).then(|| CMAudioFormatDescriptionGetStreamBasicDescription(d)).and_then(|p| p.as_ref()) {
                            LAST_FORMAT.store(((a.format_flags as u64) << 32) | ((a.bits_per_channel as u64) << 16) | a.channels_per_frame as u64, Relaxed);
                        }
                    }
                }
            }
        }
    }

    unsafe impl SCStreamDelegate for Output {
        #[unsafe(method(stream:didStopWithError:))]
        fn did_stop(&self, _stream: &SCStream, error: &NSError) {
            (self.ivars().ended)(error.localizedDescription().to_string());
        }
    }
);

impl Output {
    fn new(sink: Sink, ended: Ended, kind: SCStreamOutputType) -> Retained<Self> {
        let this = Self::alloc().set_ivars(Ivars { sink, ended, kind });
        // SAFETY: NSObject's designated initialiser.
        unsafe { msg_send![super(this), init] }
    }
}

struct Capture {
    stream: Retained<SCStream>,
    _output: Retained<Output>,
    _queue: dispatch2::DispatchRetained<DispatchQueue>,
}

// SAFETY: SCStream is thread safe for start/stop; the output only calls the sink.
unsafe impl Send for Capture {}

impl Drop for Capture {
    fn drop(&mut self) {
        // SAFETY: stopping a live stream; no completion handler needed.
        unsafe { self.stream.stopCaptureWithCompletionHandler(None) };
    }
}

fn shareable() -> Result<Retained<SCShareableContent>, String> {
    let (tx, rx) = mpsc::channel::<Result<Retained<SCShareableContent>, String>>();
    let tx = Mutex::new(Some(tx));
    let block = RcBlock::new(move |content: *mut SCShareableContent, err: *mut NSError| {
        let r = if !err.is_null() || content.is_null() {
            // SAFETY: a valid NSError when non-null.
            let msg = unsafe { err.as_ref() }.map(|e| e.localizedDescription().to_string()).unwrap_or_default();
            Err(if msg.is_empty() { "screen recording is not allowed".to_string() } else { msg })
        } else {
            // SAFETY: valid for the block's duration; retained to outlive it.
            unsafe { Retained::retain(content) }.ok_or_else(|| "no content".to_string())
        };
        if let Some(tx) = tx.lock().ok().and_then(|mut t| t.take()) {
            let _ = tx.send(r);
        }
    });
    // SAFETY: the block is retained by the call until it runs.
    unsafe { SCShareableContent::getShareableContentWithCompletionHandler(&block) };
    rx.recv_timeout(Duration::from_secs(10)).map_err(|_| "the system did not answer".to_string())?
}

/// Whether this macOS's ScreenCaptureKit answers `sel` (sound: macOS 13;
/// microphone: macOS 15). The app runs from 12.3, where calling either would crash.
fn has(sel: objc2::runtime::Sel) -> bool {
    let config = unsafe { SCStreamConfiguration::new() };
    // SAFETY: asking an object whether it answers a selector.
    unsafe { msg_send![&*config, respondsToSelector: sel] }
}

pub fn open(which: Input, sink: Sink, ended: Ended) -> Result<Box<dyn Running>, String> {
    if !has(objc2::sel!(setCapturesAudio:)) {
        return Err("sharing sound needs macOS 13 or later".into());
    }
    if which == Input::Microphone && !has(objc2::sel!(setCaptureMicrophone:)) {
        return Err("sharing the microphone needs macOS 15 or later".into());
    }
    let content = shareable().map_err(|e| {
        if e.to_lowercase().contains("not") && e.to_lowercase().contains("allow") || e.contains("declined") {
            "Screen Recording is not allowed for HID-Fi Companion".to_string()
        } else {
            e
        }
    })?;
    // SAFETY: property reads and documented initialisers on live objects.
    unsafe {
        let display = content.displays().iter().next().ok_or("no display")?;
        let none: Retained<NSArray<SCWindow>> = NSArray::new();
        let filter = SCContentFilter::initWithDisplay_excludingWindows(SCContentFilter::alloc(), &display, &none);
        let config = SCStreamConfiguration::new();
        config.setWidth(2);
        config.setHeight(2);
        config.setMinimumFrameInterval(CMTime { value: 1, timescale: 1, flags: CMTimeFlags::Valid, epoch: 0 });
        let kind = match which {
            Input::System => {
                config.setCapturesAudio(true);
                config.setSampleRate(48_000);
                config.setChannelCount(2);
                // Never the Companion's own sounds.
                config.setExcludesCurrentProcessAudio(true);
                SCStreamOutputType::Audio
            }
            Input::Microphone => {
                config.setCaptureMicrophone(true);
                SCStreamOutputType::Microphone
            }
        };
        let output = Output::new(sink, ended, kind);
        let stream = SCStream::initWithFilter_configuration_delegate(
            SCStream::alloc(),
            &filter,
            &config,
            Some(ProtocolObject::from_ref(&*output)),
        );
        let queue = DispatchQueue::new("hidfi.audio", None);
        stream
            .addStreamOutput_type_sampleHandlerQueue_error(ProtocolObject::from_ref(&*output), kind, Some(&queue))
            .map_err(|e| e.localizedDescription().to_string())?;
        let (tx, rx) = mpsc::channel::<Result<(), String>>();
        let tx = Mutex::new(Some(tx));
        let done = RcBlock::new(move |err: *mut NSError| {
            let r = match err.as_ref() {
                None => Ok(()),
                Some(e) => Err(e.localizedDescription().to_string()),
            };
            if let Some(tx) = tx.lock().ok().and_then(|mut t| t.take()) {
                let _ = tx.send(r);
            }
        });
        stream.startCaptureWithCompletionHandler(Some(&done));
        rx.recv_timeout(Duration::from_secs(10))
            .map_err(|_| "the sound capture did not start".to_string())?
            .map_err(|e| match which {
                Input::Microphone if e.to_lowercase().contains("microphone") || e.to_lowercase().contains("not authorized") => {
                    "Microphone access is not allowed for HID-Fi Companion".to_string()
                }
                _ => e,
            })?;
        Ok(Box::new(Capture { stream, _output: output, _queue: queue }))
    }
}
