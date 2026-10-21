//! H.264 on Linux with Cisco's OpenH264, loaded at run time.
//!
//! The library is the binary Cisco publishes (libopenh264-2.6.0-linux64.8.so,
//! or -linux-arm64.8.so), downloaded by the installer from Cisco - that is what
//! keeps H.264's patent licence paid by Cisco. It is only loaded if its SHA-256
//! is one of the known releases (openh264's own check): a different file is
//! refused, never run.
//!
//! Set for screen sharing: screen-content mode, bitrate rate control, no
//! skipped frames, a keyframe at least every 4 s and on request. OpenH264
//! produces constrained baseline, which every browser decodes.
//!
//! NOT YET RUN ON LINUX: compiled and checked from macOS only.

use crate::convert::bgra_to_i420;
use crate::frame::{EncodeError, Encoded, Encoder, EncoderConfig, Frame};
use openh264::encoder::{
    BitRate, Encoder as OhEncoder, EncoderConfig as OhConfig, FrameRate, FrameType,
    IntraFramePeriod, RateControlMode, UsageType,
};
use openh264::formats::YUVSource;
use openh264::OpenH264API;
use std::path::PathBuf;

pub const LIB: &str = if cfg!(target_arch = "aarch64") {
    "libopenh264-2.6.0-linux-arm64.8.so"
} else {
    "libopenh264-2.6.0-linux64.8.so"
};

/// Where the installer puts the library: the app's data folder, or next to
/// the executable, or $HIDFI_OPENH264.
pub fn library_path() -> Option<PathBuf> {
    if let Some(p) = std::env::var_os("HIDFI_OPENH264") {
        return Some(PathBuf::from(p));
    }
    let mut c = Vec::new();
    if let Some(d) = dirs_data() {
        c.push(d.join("hidfi-companion").join(LIB));
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(d) = exe.parent() {
            c.push(d.join(LIB));
        }
    }
    c.into_iter().find(|p| p.exists())
}

fn dirs_data() -> Option<PathBuf> {
    std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share")))
}

struct I420<'a> {
    buf: &'a [u8],
    w: usize,
    h: usize,
}

impl YUVSource for I420<'_> {
    fn dimensions(&self) -> (usize, usize) {
        (self.w, self.h)
    }
    fn strides(&self) -> (usize, usize, usize) {
        (self.w, self.w / 2, self.w / 2)
    }
    fn y(&self) -> &[u8] {
        &self.buf[..self.w * self.h]
    }
    fn u(&self) -> &[u8] {
        &self.buf[self.w * self.h..self.w * self.h * 5 / 4]
    }
    fn v(&self) -> &[u8] {
        &self.buf[self.w * self.h * 5 / 4..self.w * self.h * 3 / 2]
    }
}

pub struct OhH264 {
    enc: OhEncoder,
    path: PathBuf,
    w: u32,
    h: u32,
    fps: u32,
    bps: u32,
    want_bps: u32,
    rebuilt: std::time::Instant,
    nv12: Vec<u8>,
    i420: Vec<u8>,
}

/// A bitrate change this large rebuilds the encoder (openh264's wrapper sets the
/// rate only at creation), at most this often, starting on a keyframe.
const REBUILD_RATIO: f64 = 0.3;
const REBUILD_EVERY: std::time::Duration = std::time::Duration::from_secs(5);

fn make(path: &PathBuf, bps: u32, fps: u32) -> Result<OhEncoder, EncodeError> {
    let api = OpenH264API::from_blob_path(path)
        .map_err(|e| EncodeError::Unavailable(format!("{}: {e}", path.display())))?;
    OhEncoder::with_api_config(api, config(bps, fps))
        .map_err(|e| EncodeError::Unavailable(format!("OpenH264: {e}")))
}

// SAFETY: the encoder is used only through &mut self, on the pipeline thread.
unsafe impl Send for OhH264 {}

fn config(bps: u32, fps: u32) -> OhConfig {
    OhConfig::new()
        .bitrate(BitRate::from_bps(bps))
        .max_frame_rate(FrameRate::from_hz(fps as f32))
        .usage_type(UsageType::ScreenContentRealTime)
        .rate_control_mode(RateControlMode::Bitrate)
        .skip_frames(false)
        .intra_frame_period(IntraFramePeriod::from_num_frames(fps * 4))
}

impl OhH264 {
    pub fn new(cfg: &EncoderConfig) -> Result<Self, EncodeError> {
        let (w, h) = (cfg.width & !1, cfg.height & !1);
        if w < 16 || h < 16 {
            return Err(EncodeError::Unavailable(format!(
                "unsupported size {w}x{h}"
            )));
        }
        let path = library_path().ok_or_else(|| {
            EncodeError::Unavailable(format!(
                "{LIB} is not installed (the installer downloads it from Cisco)"
            ))
        })?;
        let fps = cfg.fps.clamp(1, 60);
        let enc = make(&path, cfg.bitrate_bps, fps)?;
        Ok(OhH264 {
            enc,
            path,
            w,
            h,
            fps,
            bps: cfg.bitrate_bps,
            want_bps: cfg.bitrate_bps,
            rebuilt: std::time::Instant::now(),
            nv12: Vec::new(),
            i420: vec![0; (w * h * 3 / 2) as usize],
        })
    }
}

impl Encoder for OhH264 {
    fn encode(
        &mut self,
        frame: &Frame,
        _pts_ms: u64,
        force_key: bool,
    ) -> Result<Option<Encoded>, EncodeError> {
        if !frame.is_valid() || frame.width < self.w || frame.height < self.h {
            return Err(EncodeError::BadFrame);
        }
        let (w, h) = (self.w as usize, self.h as usize);
        bgra_to_i420(
            frame.bgra,
            frame.stride,
            w,
            h,
            &mut self.nv12,
            &mut self.i420,
        );
        let mut force_key = force_key;
        let off = (self.want_bps as f64 - self.bps as f64).abs() / self.bps.max(1) as f64;
        if off > REBUILD_RATIO && self.rebuilt.elapsed() >= REBUILD_EVERY {
            match make(&self.path, self.want_bps, self.fps) {
                Ok(e) => {
                    self.enc = e;
                    self.bps = self.want_bps;
                    force_key = true;
                }
                Err(e) => log::warn!("bitrate change: {e}"),
            }
            self.rebuilt = std::time::Instant::now();
        }
        if force_key {
            self.enc.force_intra_frame();
        }
        let bs = self
            .enc
            .encode(&I420 {
                buf: &self.i420,
                w,
                h,
            })
            .map_err(|e| EncodeError::Failed(e.to_string()))?;
        let keyframe = matches!(bs.frame_type(), FrameType::IDR | FrameType::I);
        if matches!(bs.frame_type(), FrameType::Skip | FrameType::Invalid) {
            return Ok(None);
        }
        let data = bs.to_vec();
        if data.is_empty() {
            return Ok(None);
        }
        Ok(Some(Encoded { data, keyframe }))
    }

    fn set_bitrate(&mut self, bps: u32) {
        self.want_bps = bps.clamp(100_000, 50_000_000);
    }

    fn size(&self) -> (u32, u32) {
        (self.w, self.h)
    }
}
