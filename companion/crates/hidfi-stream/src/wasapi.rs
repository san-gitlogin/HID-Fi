//! The computer's sound and its microphone on Windows, through WASAPI
//! (docs/AUDIO.md).
//!
//! System sound: loopback of the default output device - what the speakers or
//! headphones play. When the default output changes while someone watches
//! (headphones plugged in, a Bluetooth headset connecting), the capture moves to
//! the new one within a second. Microphone: the default input. Windows gives
//! nothing in loopback while nothing plays; the hub sends nothing then, as it
//! should. Formats: float, 16- or 32-bit PCM, any rate and channel count - the
//! hub's conform makes 48 kHz stereo of it.

use crate::audio::{Chunk, Ended, Input, Running, Sink};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::Arc;
use std::time::{Duration, Instant};
use windows::Win32::Media::Audio::{
    eCapture, eConsole, eRender, IAudioCaptureClient, IAudioClient, IMMDevice, IMMDeviceEnumerator,
    MMDeviceEnumerator, AUDCLNT_BUFFERFLAGS_SILENT, AUDCLNT_SHAREMODE_SHARED,
    AUDCLNT_STREAMFLAGS_LOOPBACK, WAVEFORMATEX, WAVEFORMATEXTENSIBLE,
};
use windows::Win32::Media::Multimedia::KSDATAFORMAT_SUBTYPE_IEEE_FLOAT;
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CoTaskMemFree, CLSCTX_ALL, COINIT_MULTITHREADED,
};
use windows::Win32::System::Performance::{QueryPerformanceCounter, QueryPerformanceFrequency};

const WAVE_FORMAT_PCM: u16 = 1;
const WAVE_FORMAT_IEEE_FLOAT: u16 = 3;
const WAVE_FORMAT_EXTENSIBLE: u16 = 0xFFFE;

#[derive(Clone, Copy)]
enum Sample {
    F32,
    I16,
    I32,
}

struct Format {
    rate: u32,
    channels: usize,
    sample: Sample,
}

/// # Safety
/// `f` points to a WAVEFORMATEX (or WAVEFORMATEXTENSIBLE) from GetMixFormat.
unsafe fn read_format(f: *const WAVEFORMATEX) -> Result<Format, String> {
    let w = &*f;
    let float = match w.wFormatTag {
        WAVE_FORMAT_IEEE_FLOAT => true,
        WAVE_FORMAT_PCM => false,
        WAVE_FORMAT_EXTENSIBLE => {
            let x = &*(f as *const WAVEFORMATEXTENSIBLE);
            let sub = std::ptr::addr_of!(x.SubFormat).read_unaligned();
            sub == KSDATAFORMAT_SUBTYPE_IEEE_FLOAT
        }
        t => return Err(format!("an audio format this app does not read ({t:#x})")),
    };
    let sample = match (float, w.wBitsPerSample) {
        (true, 32) => Sample::F32,
        (false, 16) => Sample::I16,
        (false, 32) => Sample::I32,
        (_, b) => return Err(format!("{b}-bit audio is not read")),
    };
    Ok(Format {
        rate: w.nSamplesPerSec,
        channels: w.nChannels.max(1) as usize,
        sample,
    })
}

fn to_f32(data: *const u8, frames: usize, f: &Format) -> Vec<f32> {
    let n = frames * f.channels;
    // SAFETY: WASAPI gives `frames` frames of the mix format at `data`.
    unsafe {
        match f.sample {
            Sample::F32 => std::slice::from_raw_parts(data as *const f32, n).to_vec(),
            Sample::I16 => std::slice::from_raw_parts(data as *const i16, n)
                .iter()
                .map(|&s| s as f32 / 32768.0)
                .collect(),
            Sample::I32 => std::slice::from_raw_parts(data as *const i32, n)
                .iter()
                .map(|&s| s as f32 / 2_147_483_648.0)
                .collect(),
        }
    }
}

fn id_of(d: &IMMDevice) -> String {
    // SAFETY: GetId returns a CoTaskMem string we free here.
    unsafe {
        match d.GetId() {
            Ok(p) => {
                let s = p.to_string().unwrap_or_default();
                CoTaskMemFree(Some(p.0 as *const _));
                s
            }
            Err(_) => String::new(),
        }
    }
}

/// The time a QPC position (100 ns units) stands for, on our clock.
fn at_qpc(pos: u64) -> Instant {
    let now = Instant::now();
    let (mut c, mut f) = (0i64, 0i64);
    // SAFETY: plain out-parameters.
    unsafe {
        if QueryPerformanceCounter(&mut c).is_err()
            || QueryPerformanceFrequency(&mut f).is_err()
            || f == 0
        {
            return now;
        }
    }
    let now_100ns = (c as i128 * 10_000_000 / f as i128) as i64;
    let age = now_100ns - pos as i64;
    if (0..10_000_000).contains(&age) {
        now - Duration::from_nanos(age as u64 * 100)
    } else {
        now
    }
}

struct Capture(Arc<AtomicBool>);
impl Drop for Capture {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Relaxed);
    }
}

pub fn open(which: Input, sink: Sink, ended: Ended) -> Result<Box<dyn Running>, String> {
    let stop = Arc::new(AtomicBool::new(false));
    let st = stop.clone();
    // The first open's result is the answer; after that, the thread keeps going.
    let (tx, rx) = mpsc::channel::<Result<(), String>>();
    std::thread::Builder::new()
        .name("wasapi".into())
        .spawn(move || {
            // SAFETY: COM for this thread.
            let _ = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
            let mut first = Some(tx);
            while !st.load(Ordering::Relaxed) {
                match run(which, &sink, &st, &mut first) {
                    Ok(()) => {} // the default device changed: open the new one
                    Err(why) => {
                        if let Some(t) = first.take() {
                            let _ = t.send(Err(why));
                        } else {
                            ended(why);
                        }
                        return;
                    }
                }
            }
        })
        .map_err(|e| e.to_string())?;
    rx.recv_timeout(Duration::from_secs(5))
        .map_err(|_| "the sound device did not answer".to_string())??;
    Ok(Box::new(Capture(stop)))
}

/// One device until it stops being the default (Ok) or fails (Err).
fn run(
    which: Input,
    sink: &Sink,
    stop: &AtomicBool,
    first: &mut Option<mpsc::Sender<Result<(), String>>>,
) -> Result<(), String> {
    let flow = if which == Input::System {
        eRender
    } else {
        eCapture
    };
    // SAFETY: documented COM calls; every pointer comes from them.
    unsafe {
        let en: IMMDeviceEnumerator =
            CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_ALL).map_err(|e| e.message())?;
        let dev = en
            .GetDefaultAudioEndpoint(flow, eConsole)
            .map_err(|_| match which {
                Input::System => "no sound output on this computer".to_string(),
                Input::Microphone => "no microphone on this computer".to_string(),
            })?;
        let id = id_of(&dev);
        let client: IAudioClient = dev.Activate(CLSCTX_ALL, None).map_err(|e| e.message())?;
        let fmt = client.GetMixFormat().map_err(|e| e.message())?;
        let format = read_format(fmt);
        let flags = if which == Input::System {
            AUDCLNT_STREAMFLAGS_LOOPBACK
        } else {
            0
        };
        // 20 ms of buffer: room for a late read, little delay.
        let init = client.Initialize(AUDCLNT_SHAREMODE_SHARED, flags, 200_000, 0, fmt, None);
        CoTaskMemFree(Some(fmt as *const _));
        let format = format?;
        init.map_err(|e| match which {
            Input::Microphone if e.code().0 as u32 == 0x8007_0005 => {
                "Microphone access is not allowed for HID-Fi Companion".to_string()
            }
            _ => e.message(),
        })?;
        let cap: IAudioCaptureClient = client.GetService().map_err(|e| e.message())?;
        client.Start().map_err(|e| e.message())?;
        if let Some(t) = first.take() {
            let _ = t.send(Ok(()));
        }
        let mut checked = Instant::now();
        let result = loop {
            if stop.load(Ordering::Relaxed) {
                break Ok(());
            }
            std::thread::sleep(Duration::from_millis(5));
            loop {
                let n = match cap.GetNextPacketSize() {
                    Ok(n) => n,
                    Err(e) => return Err(format!("the sound device went away ({})", e.message())),
                };
                if n == 0 {
                    break;
                }
                let (mut data, mut frames, mut fl, mut qpc) =
                    (std::ptr::null_mut::<u8>(), 0u32, 0u32, 0u64);
                if let Err(e) = cap.GetBuffer(&mut data, &mut frames, &mut fl, None, Some(&mut qpc))
                {
                    return Err(format!("the sound device went away ({})", e.message()));
                }
                let samples = if fl & (AUDCLNT_BUFFERFLAGS_SILENT.0 as u32) != 0 || data.is_null() {
                    vec![0f32; frames as usize * format.channels]
                } else {
                    to_f32(data, frames as usize, &format)
                };
                let _ = cap.ReleaseBuffer(frames);
                if frames > 0 {
                    sink(Chunk {
                        samples,
                        rate: format.rate,
                        channels: format.channels,
                        at: at_qpc(qpc),
                    });
                }
            }
            // The default device changed (headphones plugged in): move to it.
            if checked.elapsed() >= Duration::from_secs(1) {
                checked = Instant::now();
                if let Ok(now) = en.GetDefaultAudioEndpoint(flow, eConsole) {
                    if id_of(&now) != id {
                        break Ok(());
                    }
                }
            }
        };
        let _ = client.Stop();
        result
    }
}
