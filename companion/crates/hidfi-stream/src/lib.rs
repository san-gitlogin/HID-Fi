//! Capture, encode and send the screen to a dashboard over WebRTC.
pub mod audio;

/// One clock for picture and sound: every frame's and every sound packet's time
/// counts from here, so the browser can keep them in step (RTCP sender reports
/// tie both to the same wall clock).
pub fn epoch() -> std::time::Instant {
    static E: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
    *E.get_or_init(std::time::Instant::now)
}

pub mod convert;
pub mod display;
#[cfg(windows)]
pub mod dxgi;
pub mod frame;
#[cfg(windows)]
pub mod mf;
#[cfg(target_os = "linux")]
pub mod oh264;
pub mod pattern;
pub mod pipeline;
#[cfg(target_os = "linux")]
pub mod pulse;
pub mod rtc;
#[cfg(target_os = "macos")]
pub mod sck;
#[cfg(target_os = "macos")]
pub mod sck_audio;
pub mod server;
#[cfg(target_os = "macos")]
pub mod tap_audio;
#[cfg(target_os = "macos")]
pub mod vt;
#[cfg(windows)]
pub mod wasapi;
#[cfg(target_os = "linux")]
pub mod x11;

/// Why the last macOS process tap was not used (diagnostics, `--check-sound`).
#[cfg(target_os = "macos")]
pub static LAST_TAP_REFUSAL: std::sync::Mutex<String> = std::sync::Mutex::new(String::new());

/// The computer's own sound or microphone, captured the way this OS allows
/// (docs/AUDIO.md). An OS without a capture yet says so; the picture carries on.
pub fn audio_capture(
    i: audio::Input,
    sink: audio::Sink,
    ended: audio::Ended,
) -> Result<Box<dyn audio::Running>, String> {
    #[cfg(target_os = "macos")]
    {
        // The system sound through a process tap when allowed (about 10 ms);
        // otherwise, and for the microphone, ScreenCaptureKit.
        if i == audio::Input::System {
            match tap_audio::open(sink.clone(), ended.clone()) {
                Ok(t) => {
                    log::info!("sound: capturing through a Core Audio process tap");
                    return Ok(t);
                }
                Err(why) => {
                    log::info!("sound: ScreenCaptureKit for the system sound ({why})");
                    *LAST_TAP_REFUSAL.lock().unwrap_or_else(|e| e.into_inner()) = why;
                }
            }
        }
        sck_audio::open(i, sink, ended)
    }
    #[cfg(windows)]
    {
        wasapi::open(i, sink, ended)
    }
    #[cfg(target_os = "linux")]
    {
        pulse::open(i, sink, ended)
    }
    #[cfg(not(any(target_os = "macos", windows, target_os = "linux")))]
    {
        let _ = (i, sink, ended);
        Err("sound capture is not available on this system yet".into())
    }
}

/// The best H.264 encoder this computer has, for `Pipeline::start`.
pub fn encoder(w: u32, h: u32, fps: u32, bps: u32) -> Result<Box<dyn frame::Encoder>, String> {
    let cfg = frame::EncoderConfig {
        width: w,
        height: h,
        fps,
        bitrate_bps: bps,
    };
    #[cfg(target_os = "macos")]
    {
        vt::VtEncoder::new(&cfg)
            .map(|e| Box::new(e) as Box<dyn frame::Encoder>)
            .map_err(|e| e.to_string())
    }
    #[cfg(windows)]
    {
        mf::MfEncoder::new(&cfg)
            .map(|e| Box::new(e) as Box<dyn frame::Encoder>)
            .map_err(|e| e.to_string())
    }
    #[cfg(target_os = "linux")]
    {
        oh264::OhH264::new(&cfg)
            .map(|e| Box::new(e) as Box<dyn frame::Encoder>)
            .map_err(|e| e.to_string())
    }
    #[cfg(not(any(target_os = "macos", windows, target_os = "linux")))]
    {
        let _ = cfg;
        Err("no H.264 encoder for this OS yet".into())
    }
}

/// This computer's screen, for `Pipeline::start`: ScreenCaptureKit on macOS,
/// Desktop Duplication on Windows. `display` picks one (None = the main one).
pub fn screen(display: Option<u32>, fps: u32) -> Result<Box<dyn pipeline::Source>, String> {
    #[cfg(target_os = "macos")]
    {
        // Never asks: a viewer connecting (a phone retrying the Screen tab)
        // must not put a system dialog in front of the person, again and again.
        // The app shows "Allow Screen Recording", and only that click asks.
        if !sck::allowed() {
            return Err("Screen Recording is not allowed for HID-Fi Companion".into());
        }
        sck::ScreenSource::start(display, fps).map(|s| Box::new(s) as Box<dyn pipeline::Source>)
    }
    #[cfg(windows)]
    {
        let _ = fps;
        dxgi::DxgiSource::start(display).map(|s| Box::new(s) as Box<dyn pipeline::Source>)
    }
    #[cfg(target_os = "linux")]
    {
        let _ = fps;
        x11::X11Source::start(display).map(|s| Box::new(s) as Box<dyn pipeline::Source>)
    }
    #[cfg(not(any(target_os = "macos", windows, target_os = "linux")))]
    {
        let _ = (display, fps);
        Err("screen capture is not available on this OS yet".into())
    }
}

/// Keeps a socket out of child processes. Windows hands out accepted sockets
/// (and duplicates of them) inheritable: any program this app starts - the
/// browser, `clip`, the new version during "Install and restart" - got a copy,
/// held the connection open after this app closed it, and reset it when it
/// was killed (found on Windows 11: a viewer's connection reset by a child's
/// exit). Call on every accepted socket and every `try_clone`. No-op elsewhere
/// (Rust opens sockets close-on-exec there).
#[cfg(windows)]
pub fn keep_to_this_process<S: std::os::windows::io::AsRawSocket>(s: &S) {
    use windows::Win32::Foundation::{
        SetHandleInformation, HANDLE, HANDLE_FLAGS, HANDLE_FLAG_INHERIT,
    };
    // SAFETY: a socket handle this process owns, for the call's duration.
    let _ = unsafe {
        SetHandleInformation(
            HANDLE(s.as_raw_socket() as *mut core::ffi::c_void),
            HANDLE_FLAG_INHERIT.0,
            HANDLE_FLAGS(0),
        )
    };
}

#[cfg(not(windows))]
pub fn keep_to_this_process<S>(_: &S) {}

#[cfg(all(test, windows))]
mod inherit_tests {
    use std::net::{TcpListener, TcpStream, UdpSocket};
    use std::os::windows::io::AsRawSocket;
    use windows::Win32::Foundation::{GetHandleInformation, HANDLE};

    fn inheritable<S: AsRawSocket>(s: &S) -> bool {
        let mut f = 0u32;
        // SAFETY: a socket handle this test owns.
        unsafe {
            GetHandleInformation(HANDLE(s.as_raw_socket() as *mut core::ffi::c_void), &mut f)
        }
        .unwrap();
        f & 1 != 0
    }

    #[test]
    fn no_socket_this_app_holds_goes_to_a_child() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let c = TcpStream::connect(l.local_addr().unwrap()).unwrap();
        let (a, _) = l.accept().unwrap();
        let ac = a.try_clone().unwrap();
        let u = UdpSocket::bind("127.0.0.1:0").unwrap();
        let uc = u.try_clone().unwrap();
        eprintln!(
            "as Windows hands them out - listener {}, connected {}, accepted {}, accepted clone {}, udp {}, udp clone {}",
            inheritable(&l), inheritable(&c), inheritable(&a), inheritable(&ac), inheritable(&u), inheritable(&uc)
        );
        for s in [&a, &ac, &c] {
            super::keep_to_this_process(s);
            assert!(!inheritable(s));
        }
        super::keep_to_this_process(&l);
        super::keep_to_this_process(&u);
        super::keep_to_this_process(&uc);
        assert!(!inheritable(&l) && !inheritable(&u) && !inheritable(&uc));
    }
}
