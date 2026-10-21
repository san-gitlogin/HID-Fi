//! Capture, encode and send the screen to a dashboard over WebRTC.

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
pub mod rtc;
#[cfg(target_os = "macos")]
pub mod sck;
pub mod server;
#[cfg(target_os = "macos")]
pub mod vt;
#[cfg(target_os = "linux")]
pub mod x11;

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
        if !sck::allowed() {
            sck::ask();
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
