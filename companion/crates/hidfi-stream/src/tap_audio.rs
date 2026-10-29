//! The computer's sound on macOS 14.2+, through a Core Audio process tap: every
//! app's sound except the Companion's own, delivered in device-sized buffers
//! (5 ms) instead of ScreenCaptureKit's ~60 ms (docs/AUDIO.md).
//!
//! It needs the "System Audio Recording" permission. Without it a tap still runs
//! but delivers silence, which looks exactly like a quiet computer - so the
//! permission is asked of the system first (TCC's own check; there is no public
//! one). Granted: the tap. Not asked yet: ScreenCaptureKit for now, and the
//! question goes to the person in the background, so a prompt on the Mac never
//! holds up the sound on a phone; the next time sound starts, the tap is used.
//! Denied, or the check itself unavailable: ScreenCaptureKit, as before.

use crate::audio::{Chunk, Ended, Running, Sink};
use block2::RcBlock;
use dispatch2::DispatchQueue;
use objc2::rc::Retained;
use objc2::AllocAnyThread;
use objc2_core_audio::{
    kAudioAggregateDeviceIsPrivateKey, kAudioAggregateDeviceIsStackedKey,
    kAudioAggregateDeviceMainSubDeviceKey, kAudioAggregateDeviceNameKey,
    kAudioAggregateDeviceSubDeviceListKey, kAudioAggregateDeviceTapAutoStartKey,
    kAudioAggregateDeviceTapListKey, kAudioAggregateDeviceUIDKey, kAudioDevicePropertyDeviceUID,
    kAudioHardwarePropertyDefaultSystemOutputDevice,
    kAudioHardwarePropertyTranslatePIDToProcessObject, kAudioObjectPropertyElementMain,
    kAudioObjectPropertyScopeGlobal, kAudioObjectSystemObject, kAudioSubDeviceUIDKey,
    kAudioSubTapDriftCompensationKey, kAudioSubTapUIDKey, kAudioTapPropertyFormat,
    AudioDeviceCreateIOProcIDWithBlock, AudioDeviceDestroyIOProcID, AudioDeviceIOProcID,
    AudioDeviceStart, AudioDeviceStop, AudioHardwareCreateAggregateDevice,
    AudioHardwareCreateProcessTap, AudioHardwareDestroyAggregateDevice,
    AudioHardwareDestroyProcessTap, AudioObjectGetPropertyData, AudioObjectID,
    AudioObjectPropertyAddress, CATapDescription, CATapMuteBehavior,
};
use objc2_core_audio_types::{AudioBufferList, AudioStreamBasicDescription, AudioTimeStamp};
use objc2_foundation::{NSArray, NSMutableDictionary, NSNumber, NSObject, NSString, NSUUID};
use std::ffi::{c_void, CStr};
use std::ptr::NonNull;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// What the system says about System Audio Recording for this app.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Permission {
    Granted,
    Denied,
    NotAsked,
    /// The system's check could not be reached (a future macOS).
    Unknown,
}

type Preflight = unsafe extern "C" fn(*const c_void, *const c_void) -> i32;
type Request = unsafe extern "C" fn(*const c_void, *const c_void, *mut c_void);

fn tcc() -> Option<(Preflight, Request)> {
    static LIB: std::sync::OnceLock<Option<(usize, usize)>> = std::sync::OnceLock::new();
    let p = LIB.get_or_init(|| {
        // SAFETY: dlopen/dlsym of a system framework; null-checked.
        unsafe {
            let h = libc::dlopen(
                c"/System/Library/PrivateFrameworks/TCC.framework/Versions/A/TCC".as_ptr(),
                libc::RTLD_NOW,
            );
            if h.is_null() {
                return None;
            }
            let pre = libc::dlsym(h, c"TCCAccessPreflight".as_ptr());
            let req = libc::dlsym(h, c"TCCAccessRequest".as_ptr());
            (!pre.is_null() && !req.is_null()).then_some((pre as usize, req as usize))
        }
    });
    // SAFETY: the two symbols, with the signatures every user of them relies on.
    p.map(|(a, b)| unsafe {
        (
            std::mem::transmute::<usize, Preflight>(a),
            std::mem::transmute::<usize, Request>(b),
        )
    })
}

fn service() -> Retained<NSString> {
    NSString::from_str("kTCCServiceAudioCapture")
}

pub fn permission() -> Permission {
    let Some((pre, _)) = tcc() else {
        return Permission::Unknown;
    };
    let s = service();
    // SAFETY: an NSString is a CFString (toll-free bridged); no options.
    match unsafe { pre(Retained::as_ptr(&s) as *const c_void, std::ptr::null()) } {
        0 => Permission::Granted,
        1 => Permission::Denied,
        _ => Permission::NotAsked,
    }
}

/// Asks the person, in the background; the answer applies from the next start.
pub fn ask() {
    static ASKED: AtomicBool = AtomicBool::new(false);
    let Some((_, req)) = tcc() else { return };
    if ASKED.swap(true, Ordering::SeqCst) {
        return;
    }
    let s = service();
    let done = RcBlock::new(|granted: u8| {
        log::info!(
            "sound: System Audio Recording {}",
            if granted != 0 {
                "allowed"
            } else {
                "not allowed"
            }
        )
    });
    // SAFETY: CFString service, no options, a completion block the call retains.
    unsafe {
        req(
            Retained::as_ptr(&s) as *const c_void,
            std::ptr::null(),
            RcBlock::as_ptr(&done) as *mut c_void,
        )
    };
}

fn addr(sel: u32) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress {
        mSelector: sel,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain,
    }
}

/// # Safety
/// `T` must be the property's type.
unsafe fn get<T: Copy>(obj: AudioObjectID, sel: u32, qualifier: Option<&u32>) -> Result<T, i32> {
    let mut a = addr(sel);
    // SAFETY (caller): T is plain data for which all-zero bytes are valid.
    let mut v: T = std::mem::zeroed();
    let mut size = std::mem::size_of::<T>() as u32;
    let (qs, qp) = match qualifier {
        Some(q) => (4, q as *const u32 as *const c_void),
        None => (0, std::ptr::null()),
    };
    let st = AudioObjectGetPropertyData(
        obj,
        NonNull::from(&mut a),
        qs,
        qp,
        NonNull::from(&mut size),
        NonNull::from(&mut v).cast(),
    );
    if st == 0 {
        Ok(v)
    } else {
        Err(st)
    }
}

fn cstr(k: &CStr) -> Retained<NSString> {
    NSString::from_str(k.to_str().unwrap_or_default())
}

struct Tap {
    tap: AudioObjectID,
    /// The output the device is clocked by.
    out: AudioObjectID,
    agg: AudioObjectID,
    proc_id: AudioDeviceIOProcID,
    _queue: dispatch2::DispatchRetained<DispatchQueue>,
}

// SAFETY: the ids are plain integers; Core Audio calls on them are thread safe.
unsafe impl Send for Tap {}

impl Drop for Tap {
    fn drop(&mut self) {
        // SAFETY: undoing exactly what open() made, in reverse.
        unsafe {
            AudioDeviceStop(self.agg, self.proc_id);
            AudioDeviceDestroyIOProcID(self.agg, self.proc_id);
            AudioHardwareDestroyAggregateDevice(self.agg);
            AudioHardwareDestroyProcessTap(self.tap);
        }
    }
}

/// The tap, if it can be used now (see the module's notes); Err says why not.
/// A tap started only so macOS asks the person (its sound is discarded).
fn probe() {
    static PROBING: AtomicBool = AtomicBool::new(false);
    if PROBING.swap(true, Ordering::SeqCst) {
        return;
    }
    let _ = std::thread::Builder::new()
        .name("tap-ask".into())
        .spawn(|| {
            let sink: Sink = std::sync::Arc::new(|_| {});
            // SAFETY: as open(); the tap lives 60 s, then goes.
            if let Ok(t) = unsafe { start(sink, Arc::new(|_| {})) } {
                std::thread::sleep(Duration::from_secs(60));
                drop(t);
            }
            PROBING.store(false, Ordering::SeqCst);
        });
}

pub fn open(sink: Sink, ended: Ended) -> Result<Box<dyn Running>, String> {
    match permission() {
        Permission::Granted => {}
        Permission::NotAsked => {
            ask();
            // macOS shows this question when a tap starts: start one in the
            // background, its sound thrown away, for long enough to be answered.
            probe();
            return Err("System Audio Recording not answered yet".into());
        }
        Permission::Denied => return Err("System Audio Recording is not allowed".into()),
        Permission::Unknown => return Err("the system's permission check is not available".into()),
    }
    // SAFETY: see start().
    unsafe { start(sink, ended) }
}

/// For `--check-sound`: what the last tap was built on.
pub static LAST_BUILD: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());

/// How often the output under the tap is checked.
const CHECK: Duration = Duration::from_millis(250);

struct Watch(Arc<AtomicBool>);
impl Drop for Watch {
    fn drop(&mut self) {
        self.0.store(true, Ordering::SeqCst);
    }
}

fn alive(dev: AudioObjectID) -> bool {
    // SAFETY: a u32 property of a device id (an unknown id is an error, not UB).
    unsafe {
        get::<u32>(
            dev,
            objc2_core_audio::kAudioDevicePropertyDeviceIsAlive,
            None,
        )
    }
    .is_ok_and(|v| v != 0)
}

/// The tap, watched: the tap itself takes every app's sound whichever output
/// plays it, but it is read through a device clocked by one output. When that
/// output goes away (headphones unplugged, a Bluetooth speaker gone) the device
/// stops, so the tap is built again on the output macOS uses now. A quiet
/// computer is not a fault: the device only runs while some app plays.
///
/// # Safety
/// See build().
unsafe fn start(sink: Sink, _ended: Ended) -> Result<Box<dyn Running>, String> {
    let first = build(sink.clone())?;
    let stop = Arc::new(AtomicBool::new(false));
    let st = stop.clone();
    std::thread::Builder::new()
        .name("tap-watch".into())
        .spawn(move || {
            let mut tap = Some(first);
            while !st.load(Ordering::SeqCst) {
                std::thread::sleep(CHECK);
                if tap.as_ref().is_some_and(|t| alive(t.out)) {
                    continue;
                }
                if tap.take().is_some() {
                    log::info!("sound: the output under the tap went away - building it again");
                }
                // SAFETY: as start(). No output at all: tried again shortly.
                match unsafe { build(sink.clone()) } {
                    Ok(t) => tap = Some(t),
                    Err(why) => {
                        log::debug!("sound: tap not rebuilt yet: {why}");
                        std::thread::sleep(Duration::from_secs(1));
                    }
                }
            }
            drop(tap);
        })
        .map_err(|e| e.to_string())?;
    Ok(Box::new(Watch(stop)))
}

/// # Safety
/// Documented Core Audio calls; every id and pointer comes from them.
unsafe fn build(sink: Sink) -> Result<Tap, String> {
    {
        // The Companion's own sounds are never captured.
        let me = std::process::id() as u32;
        let own: AudioObjectID = get(
            kAudioObjectSystemObject as u32,
            kAudioHardwarePropertyTranslatePIDToProcessObject,
            Some(&me),
        )
        .map_err(|e| format!("own process ({e})"))?;
        let exclude = NSArray::from_retained_slice(&[NSNumber::new_u32(own)]);
        let desc = CATapDescription::initStereoGlobalTapButExcludeProcesses(
            CATapDescription::alloc(),
            &exclude,
        );
        desc.setPrivate(true);
        desc.setMuteBehavior(CATapMuteBehavior::Unmuted);
        let tap_uid = desc.UUID().UUIDString();
        let mut tap: AudioObjectID = 0;
        let st = AudioHardwareCreateProcessTap(Some(&desc), &mut tap);
        if st != 0 {
            return Err(format!("could not create the tap ({st})"));
        }
        let fail = |why: String| {
            AudioHardwareDestroyProcessTap(tap);
            why
        };
        // An aggregate device clocked by the default output, carrying the tap.
        let out: AudioObjectID = get(
            kAudioObjectSystemObject as u32,
            kAudioHardwarePropertyDefaultSystemOutputDevice,
            None,
        )
        .map_err(|e| fail(format!("no output device ({e})")))?;
        let out_uid: *const c_void = get::<usize>(out, kAudioDevicePropertyDeviceUID, None)
            .map_err(|e| fail(format!("output device id ({e})")))?
            as *const c_void;
        let out_uid: Retained<NSString> = Retained::retain(out_uid as *mut NSString)
            .ok_or_else(|| fail("output device id".into()))?;
        let sub = NSMutableDictionary::<NSString, NSObject>::new();
        sub.insert(
            &*cstr(kAudioSubDeviceUIDKey),
            &*Retained::into_super(out_uid.clone()),
        );
        let tapd = NSMutableDictionary::<NSString, NSObject>::new();
        tapd.insert(
            &*cstr(kAudioSubTapUIDKey),
            &*Retained::into_super(tap_uid.clone()),
        );
        tapd.insert(
            &*cstr(kAudioSubTapDriftCompensationKey),
            &*Retained::into_super(Retained::into_super(NSNumber::new_bool(true))),
        );
        let d = NSMutableDictionary::<NSString, NSObject>::new();
        let agg_uid = NSUUID::new().UUIDString();
        d.insert(
            &*cstr(kAudioAggregateDeviceUIDKey),
            &*Retained::into_super(agg_uid),
        );
        d.insert(
            &*cstr(kAudioAggregateDeviceNameKey),
            &*Retained::into_super(NSString::from_str("HID-Fi sound")),
        );
        d.insert(
            &*cstr(kAudioAggregateDeviceMainSubDeviceKey),
            &*Retained::into_super(out_uid),
        );
        d.insert(
            &*cstr(kAudioAggregateDeviceIsPrivateKey),
            &*Retained::into_super(Retained::into_super(NSNumber::new_bool(true))),
        );
        d.insert(
            &*cstr(kAudioAggregateDeviceIsStackedKey),
            &*Retained::into_super(Retained::into_super(NSNumber::new_bool(false))),
        );
        d.insert(
            &*cstr(kAudioAggregateDeviceTapAutoStartKey),
            &*Retained::into_super(Retained::into_super(NSNumber::new_bool(true))),
        );
        d.insert(
            &*cstr(kAudioAggregateDeviceSubDeviceListKey),
            &*Retained::into_super(NSArray::from_retained_slice(&[sub])),
        );
        d.insert(
            &*cstr(kAudioAggregateDeviceTapListKey),
            &*Retained::into_super(NSArray::from_retained_slice(&[tapd])),
        );
        let mut agg: AudioObjectID = 0;
        // SAFETY: an NSDictionary is a CFDictionary (toll-free bridged).
        let cfd = &*(Retained::as_ptr(&d) as *const objc2_core_foundation::CFDictionary);
        let st = AudioHardwareCreateAggregateDevice(cfd, NonNull::from(&mut agg));
        if st != 0 {
            return Err(fail(format!(
                "could not create the aggregate device ({st})"
            )));
        }
        let fail2 = |why: String| {
            AudioHardwareDestroyAggregateDevice(agg);
            AudioHardwareDestroyProcessTap(tap);
            why
        };
        let fmt: AudioStreamBasicDescription = get(tap, kAudioTapPropertyFormat, None)
            .map_err(|e| fail2(format!("tap format ({e})")))?;
        let rate = fmt.mSampleRate.round() as u32;
        let channels = fmt.mChannelsPerFrame.clamp(1, 8) as usize;
        let planar = fmt.mFormatFlags & (1 << 5) != 0;
        if fmt.mFormatFlags & 1 == 0 || fmt.mBitsPerChannel != 32 || rate == 0 {
            return Err(fail2(format!(
                "tap format not float32 ({:#x}, {} bits)",
                fmt.mFormatFlags, fmt.mBitsPerChannel
            )));
        }
        // Small buffers: the tap hands over sound every 5 ms instead of every ~11.
        let mut a = addr(objc2_core_audio::kAudioDevicePropertyBufferFrameSize);
        let mut frames: u32 = 240;
        let _ = objc2_core_audio::AudioObjectSetPropertyData(
            agg,
            NonNull::from(&mut a),
            0,
            std::ptr::null(),
            4,
            NonNull::from(&mut frames).cast(),
        );
        let queue = DispatchQueue::new("hidfi.tap", None);
        let block = RcBlock::new(
            move |_now: NonNull<AudioTimeStamp>,
                  input: NonNull<AudioBufferList>,
                  in_time: NonNull<AudioTimeStamp>,
                  _out: NonNull<AudioBufferList>,
                  _out_time: NonNull<AudioTimeStamp>| {
                let list = input.as_ref();
                let n = (list.mNumberBuffers as usize).min(8);
                let bufs = std::slice::from_raw_parts(list.mBuffers.as_ptr(), n);
                let Some(first) = bufs.first() else { return };
                let frames = if planar {
                    first.mDataByteSize as usize / 4
                } else {
                    first.mDataByteSize as usize / 4 / channels
                };
                if frames == 0 {
                    return;
                }
                let mut out = vec![0f32; frames * channels];
                if planar {
                    for (c, b) in bufs.iter().take(channels).enumerate() {
                        if b.mData.is_null() {
                            continue;
                        }
                        let s = std::slice::from_raw_parts(
                            b.mData as *const f32,
                            (b.mDataByteSize as usize / 4).min(frames),
                        );
                        for (i, v) in s.iter().enumerate() {
                            out[i * channels + c] = *v;
                        }
                    }
                } else if !first.mData.is_null() {
                    out.copy_from_slice(std::slice::from_raw_parts(
                        first.mData as *const f32,
                        frames * channels,
                    ));
                }
                let at = host_instant(in_time.as_ref().mHostTime);
                sink(Chunk {
                    samples: out,
                    rate,
                    channels,
                    at,
                });
            },
        );
        let mut proc_id: AudioDeviceIOProcID = None;
        let st = AudioDeviceCreateIOProcIDWithBlock(
            NonNull::from(&mut proc_id),
            agg,
            Some(&queue),
            RcBlock::as_ptr(&block) as *mut _,
        );
        if st != 0 {
            return Err(fail2(format!("could not listen to the tap ({st})")));
        }
        std::mem::forget(block); // owned by Core Audio until the IOProc is destroyed
        let st = AudioDeviceStart(agg, proc_id);
        if st != 0 {
            AudioDeviceDestroyIOProcID(agg, proc_id);
            return Err(fail2(format!("could not start the tap ({st})")));
        }
        *LAST_BUILD.lock().unwrap_or_else(|e| e.into_inner()) =
            format!("output {out}, {rate} Hz, {channels} ch");
        Ok(Tap {
            tap,
            out,
            agg,
            proc_id,
            _queue: queue,
        })
    }
}

/// A host time (mach absolute time) on our clock.
fn host_instant(host: u64) -> Instant {
    let now = Instant::now();
    // SAFETY: plain Core Audio calls.
    let cur = unsafe { objc2_core_audio::AudioGetCurrentHostTime() };
    if host == 0 || host > cur {
        return now;
    }
    // SAFETY: as above.
    let ns = unsafe { objc2_core_audio::AudioConvertHostTimeToNanos(cur - host) };
    if ns < 1_000_000_000 {
        now - Duration::from_nanos(ns)
    } else {
        now
    }
}
