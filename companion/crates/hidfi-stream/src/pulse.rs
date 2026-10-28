//! The computer's sound and its microphone on Linux, through PulseAudio - or
//! PipeWire's PulseAudio service, which every current desktop runs
//! (docs/AUDIO.md). libpulse-simple is loaded at run time, as OpenH264 is, so the
//! app builds without it and says plainly when a desktop has no sound server.
//!
//! System sound: the default output's monitor (`@DEFAULT_MONITOR@`) - what the
//! speakers or headphones play. Microphone: the default input. 48 kHz float
//! stereo is asked for; the server converts. Reads are 10 ms, so a chunk is
//! never older than that when it arrives.

use crate::audio::{Chunk, Ended, Input, Running, Sink};
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

#[repr(C)]
struct SampleSpec {
    format: c_int,
    rate: u32,
    channels: u8,
}

#[repr(C)]
struct BufferAttr {
    maxlength: u32,
    tlength: u32,
    prebuf: u32,
    minreq: u32,
    fragsize: u32,
}

const PA_STREAM_RECORD: c_int = 2;
const PA_SAMPLE_FLOAT32LE: c_int = 5;
const RATE: u32 = 48_000;
const CHANNELS: u8 = 2;
/// 10 ms of 48 kHz float stereo.
const READ_BYTES: usize = 480 * 2 * 4;

type New = unsafe extern "C" fn(
    *const c_char,
    *const c_char,
    c_int,
    *const c_char,
    *const c_char,
    *const SampleSpec,
    *const c_void,
    *const BufferAttr,
    *mut c_int,
) -> *mut c_void;
type Read = unsafe extern "C" fn(*mut c_void, *mut c_void, usize, *mut c_int) -> c_int;
type Free = unsafe extern "C" fn(*mut c_void);
type StrError = unsafe extern "C" fn(c_int) -> *const c_char;

struct Lib {
    _lib: libloading::Library,
    new: New,
    read: Read,
    free: Free,
    strerror: StrError,
}

fn load() -> Result<Lib, String> {
    let names = ["libpulse-simple.so.0", "libpulse-simple.so"];
    let lib = names
        .iter()
        // SAFETY: loading a system library by its well-known name.
        .find_map(|n| unsafe { libloading::Library::new(n) }.ok())
        .ok_or("no sound server on this desktop (PulseAudio or PipeWire's pulse service)")?;
    // SAFETY: the documented libpulse-simple symbols, with their C signatures.
    unsafe {
        let new = *lib
            .get::<New>(b"pa_simple_new\0")
            .map_err(|e| e.to_string())?;
        let read = *lib
            .get::<Read>(b"pa_simple_read\0")
            .map_err(|e| e.to_string())?;
        let free = *lib
            .get::<Free>(b"pa_simple_free\0")
            .map_err(|e| e.to_string())?;
        let strerror = match libloading::Library::new("libpulse.so.0") {
            Ok(p) => {
                let f = *p
                    .get::<StrError>(b"pa_strerror\0")
                    .map_err(|e| e.to_string())?;
                std::mem::forget(p); // kept for the life of the process, as libpulse-simple holds it anyway
                f
            }
            Err(e) => return Err(e.to_string()),
        };
        Ok(Lib {
            _lib: lib,
            new,
            read,
            free,
            strerror,
        })
    }
}

struct Capture(Arc<AtomicBool>);
impl Drop for Capture {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Relaxed);
    }
}

pub fn open(which: Input, sink: Sink, ended: Ended) -> Result<Box<dyn Running>, String> {
    let lib = load()?;
    let spec = SampleSpec {
        format: PA_SAMPLE_FLOAT32LE,
        rate: RATE,
        channels: CHANNELS,
    };
    // Small fragments: the server hands over 10 ms at a time, not its default ~2 s.
    let attr = BufferAttr {
        maxlength: u32::MAX,
        tlength: u32::MAX,
        prebuf: u32::MAX,
        minreq: u32::MAX,
        fragsize: READ_BYTES as u32,
    };
    let name = CString::new("HID-Fi Companion").expect("no NUL");
    let stream = CString::new(match which {
        Input::System => "the computer's sound, for the HID-Fi dashboard",
        Input::Microphone => "the microphone, for the HID-Fi dashboard",
    })
    .expect("no NUL");
    let dev = CString::new("@DEFAULT_MONITOR@").expect("no NUL");
    let mut err: c_int = 0;
    // SAFETY: valid C strings and structs for the duration of the call.
    let s = unsafe {
        (lib.new)(
            std::ptr::null(),
            name.as_ptr(),
            PA_STREAM_RECORD,
            if which == Input::System {
                dev.as_ptr()
            } else {
                std::ptr::null()
            },
            stream.as_ptr(),
            &spec,
            std::ptr::null(),
            &attr,
            &mut err,
        )
    };
    if s.is_null() {
        // SAFETY: pa_strerror returns a static string.
        let why = unsafe { CStr::from_ptr((lib.strerror)(err)) }
            .to_string_lossy()
            .into_owned();
        return Err(format!("the sound server refused: {why}"));
    }
    let stop = Arc::new(AtomicBool::new(false));
    let st = stop.clone();
    let handle = s as usize;
    std::thread::Builder::new()
        .name("pulse".into())
        .spawn(move || {
            let s = handle as *mut c_void;
            let mut buf = vec![0f32; READ_BYTES / 4];
            while !st.load(Ordering::Relaxed) {
                let mut err: c_int = 0;
                // SAFETY: `buf` holds READ_BYTES; the stream is ours until freed below.
                let r =
                    unsafe { (lib.read)(s, buf.as_mut_ptr() as *mut c_void, READ_BYTES, &mut err) };
                if r < 0 {
                    // SAFETY: as above.
                    let why = unsafe { CStr::from_ptr((lib.strerror)(err)) }
                        .to_string_lossy()
                        .into_owned();
                    ended(format!("the sound server stopped: {why}"));
                    break;
                }
                // The read returns once the 10 ms are complete: they began 10 ms ago.
                let at = Instant::now() - Duration::from_millis(10);
                sink(Chunk {
                    samples: buf.clone(),
                    rate: RATE,
                    channels: CHANNELS as usize,
                    at,
                });
            }
            // SAFETY: freed once, on the thread that used it.
            unsafe { (lib.free)(s) };
        })
        .map_err(|e| e.to_string())?;
    Ok(Box::new(Capture(stop)))
}
