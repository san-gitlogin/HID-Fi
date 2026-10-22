//! H.264 on Windows with Media Foundation's own encoder (CLSID_MSH264EncoderMFT),
//! which ships with Windows 8 and later and is licensed by Microsoft.
//!
//! Configured for screen sharing, like the macOS encoder: low-latency mode, no
//! B-frames, constrained baseline (what every browser decodes), constant bitrate,
//! a keyframe at least every 4 s and on request. Frames go in as NV12
//! (convert::bgra_to_nv12, tested); out comes Annex B, with SPS and PPS put in
//! front of any keyframe that arrives without them.
//!
//! This is the synchronous software encoder, present on every Windows. The
//! GPU encoders are asynchronous MFTs; using them is a later step (lower CPU).
//!
//! NOT YET RUN ON WINDOWS: compiled and checked from macOS only. Run
//! `cargo run --release --example check_capture` on a Windows PC to verify.

use crate::convert::bgra_to_nv12;
use crate::frame::{nal_units, EncodeError, Encoded, Encoder, EncoderConfig, Frame};
use std::mem::ManuallyDrop;
use windows::core::{Interface, GUID};
use windows::Win32::Media::MediaFoundation::*;
use windows::Win32::System::Com::{
    CoCreateInstance, CoInitializeEx, CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED,
};
use windows::Win32::System::Variant::VARIANT;

fn fail(what: &str, e: windows::core::Error) -> EncodeError {
    EncodeError::Failed(format!("{what}: {e}"))
}
fn unavailable(what: &str, e: windows::core::Error) -> EncodeError {
    EncodeError::Unavailable(format!("{what}: {e}"))
}

pub struct MfEncoder {
    mft: IMFTransform,
    codec: Option<ICodecAPI>,
    w: u32,
    h: u32,
    fps: u32,
    nv12: Vec<u8>,
    seq_header: Vec<u8>,
}

// SAFETY: the transform is used only through &mut self, from the pipeline
// thread; COM is initialised multithreaded there.
unsafe impl Send for MfEncoder {}

fn pack(hi: u32, lo: u32) -> u64 {
    ((hi as u64) << 32) | lo as u64
}

fn video_type(
    subtype: &GUID,
    cfg: (u32, u32, u32),
    bitrate: Option<u32>,
) -> windows::core::Result<IMFMediaType> {
    let (w, h, fps) = cfg;
    // SAFETY: attribute setters on a fresh media type.
    unsafe {
        let t = MFCreateMediaType()?;
        t.SetGUID(&MF_MT_MAJOR_TYPE, &MFMediaType_Video)?;
        t.SetGUID(&MF_MT_SUBTYPE, subtype)?;
        t.SetUINT64(&MF_MT_FRAME_SIZE, pack(w, h))?;
        t.SetUINT64(&MF_MT_FRAME_RATE, pack(fps, 1))?;
        t.SetUINT64(&MF_MT_PIXEL_ASPECT_RATIO, pack(1, 1))?;
        t.SetUINT32(&MF_MT_INTERLACE_MODE, MFVideoInterlace_Progressive.0 as u32)?;
        if let Some(b) = bitrate {
            t.SetUINT32(&MF_MT_AVG_BITRATE, b)?;
            t.SetUINT32(
                &MF_MT_MPEG2_PROFILE,
                eAVEncH264VProfile_ConstrainedBase.0 as u32,
            )?;
        }
        Ok(t)
    }
}

impl MfEncoder {
    pub fn new(cfg: &EncoderConfig) -> Result<Self, EncodeError> {
        let (w, h) = (cfg.width & !1, cfg.height & !1);
        if !(16..=4096).contains(&w) || !(16..=2304).contains(&h) {
            return Err(EncodeError::Unavailable(format!(
                "unsupported size {w}x{h}"
            )));
        }
        let fps = cfg.fps.clamp(1, 60);
        // SAFETY: documented initialisation; repeated calls on a thread are fine.
        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
            MFStartup(MF_VERSION, MFSTARTUP_NOSOCKET)
                .map_err(|e| unavailable("Media Foundation", e))?;
        }
        // SAFETY: creating the system's H.264 encoder.
        let mft: IMFTransform =
            unsafe { CoCreateInstance(&CLSID_MSH264EncoderMFT, None, CLSCTX_INPROC_SERVER) }
                .map_err(|e| unavailable("H.264 encoder", e))?;
        // Codec settings before the media types: Microsoft's H.264 encoder reads
        // the rate control mode (and some others) only at SetOutputType. Set
        // after, it ignored them at 1080p and ran at a fixed quality - 27
        // Mbit/s for busy content whatever the target (rate_check, Windows VM).
        let codec: Option<ICodecAPI> = mft.cast().ok();
        if let Some(c) = &codec {
            // Best effort: an encoder that ignores one of these still works.
            // SAFETY: documented codec properties with matching VARIANT types.
            unsafe {
                let _ = c.SetValue(
                    &CODECAPI_AVEncCommonRateControlMode,
                    &VARIANT::from(eAVEncCommonRateControlMode_CBR.0 as u32),
                );
                let _ = c.SetValue(&CODECAPI_AVLowLatencyMode, &VARIANT::from(true));
                let _ = c.SetValue(&CODECAPI_AVEncMPVDefaultBPictureCount, &VARIANT::from(0u32));
                let _ = c.SetValue(&CODECAPI_AVEncMPVGOPSize, &VARIANT::from(fps * 4));
            }
            set_rate(c, cfg.bitrate_bps);
        }
        // Output first: the encoder decides what input it accepts from it.
        let out = video_type(&MFVideoFormat_H264, (w, h, fps), Some(cfg.bitrate_bps))
            .map_err(|e| unavailable("output type", e))?;
        let inp = video_type(&MFVideoFormat_NV12, (w, h, fps), None)
            .map_err(|e| unavailable("input type", e))?;
        // SAFETY: setting types on a fresh transform.
        unsafe {
            mft.SetOutputType(0, &out, 0)
                .map_err(|e| unavailable("H.264 output", e))?;
            mft.SetInputType(0, &inp, 0)
                .map_err(|e| unavailable("NV12 input", e))?;
        }
        // And again after: some settings only stick on a configured encoder.
        if let Some(c) = &codec {
            // SAFETY: as above.
            unsafe {
                let _ = c.SetValue(&CODECAPI_AVLowLatencyMode, &VARIANT::from(true));
                let _ = c.SetValue(&CODECAPI_AVEncMPVGOPSize, &VARIANT::from(fps * 4));
            }
            set_rate(c, cfg.bitrate_bps);
        }
        // SAFETY: start streaming on a configured transform.
        unsafe {
            mft.ProcessMessage(MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0)
                .map_err(|e| fail("begin", e))?;
            mft.ProcessMessage(MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0)
                .map_err(|e| fail("start", e))?;
        }
        let mut me = MfEncoder {
            mft,
            codec,
            w,
            h,
            fps,
            nv12: vec![0; (w * h * 3 / 2) as usize],
            seq_header: Vec::new(),
        };
        me.read_seq_header();
        Ok(me)
    }

    /// SPS+PPS from the negotiated output type, in Annex B.
    fn read_seq_header(&mut self) {
        // SAFETY: reading a blob attribute of the current type.
        unsafe {
            if let Ok(t) = self.mft.GetOutputCurrentType(0) {
                if let Ok(n) = t.GetBlobSize(&MF_MT_MPEG_SEQUENCE_HEADER) {
                    let mut b = vec![0u8; n as usize];
                    if t.GetBlob(&MF_MT_MPEG_SEQUENCE_HEADER, &mut b, None).is_ok() {
                        self.seq_header = b;
                    }
                }
            }
        }
    }

    fn input(&mut self, pts_ms: u64) -> Result<IMFSample, EncodeError> {
        let len = self.nv12.len() as u32;
        // SAFETY: a fresh buffer of the right size, written while locked.
        unsafe {
            let buf = MFCreateMemoryBuffer(len).map_err(|e| fail("buffer", e))?;
            let mut p: *mut u8 = std::ptr::null_mut();
            buf.Lock(&mut p, None, None).map_err(|e| fail("lock", e))?;
            std::ptr::copy_nonoverlapping(self.nv12.as_ptr(), p, self.nv12.len());
            buf.Unlock().map_err(|e| fail("unlock", e))?;
            buf.SetCurrentLength(len).map_err(|e| fail("length", e))?;
            let s = MFCreateSample().map_err(|e| fail("sample", e))?;
            s.AddBuffer(&buf).map_err(|e| fail("add buffer", e))?;
            s.SetSampleTime(pts_ms as i64 * 10_000)
                .map_err(|e| fail("time", e))?;
            s.SetSampleDuration(10_000_000 / self.fps as i64)
                .map_err(|e| fail("duration", e))?;
            Ok(s)
        }
    }

    /// Everything the encoder has ready, as one Annex B access unit.
    fn drain(&mut self) -> Result<Option<Encoded>, EncodeError> {
        let mut data = Vec::new();
        let mut key = false;
        for _ in 0..8 {
            // SAFETY: stream info query; output sample allocated when the
            // encoder does not provide its own; every COM pointer taken out of
            // the struct is dropped exactly once (ManuallyDrop::into_inner).
            unsafe {
                let info = self
                    .mft
                    .GetOutputStreamInfo(0)
                    .map_err(|e| fail("stream info", e))?;
                let provides = info.dwFlags & (MFT_OUTPUT_STREAM_PROVIDES_SAMPLES.0 as u32) != 0;
                let sample = if provides {
                    None
                } else {
                    let s = MFCreateSample().map_err(|e| fail("out sample", e))?;
                    let b = MFCreateMemoryBuffer(info.cbSize.max(self.w * self.h))
                        .map_err(|e| fail("out buffer", e))?;
                    s.AddBuffer(&b).map_err(|e| fail("out add", e))?;
                    Some(s)
                };
                let mut out = [MFT_OUTPUT_DATA_BUFFER {
                    dwStreamID: 0,
                    pSample: ManuallyDrop::new(sample),
                    dwStatus: 0,
                    pEvents: ManuallyDrop::new(None),
                }];
                let mut status = 0u32;
                let r = self.mft.ProcessOutput(0, &mut out, &mut status);
                let sample = ManuallyDrop::into_inner(std::ptr::read(&out[0].pSample));
                drop(ManuallyDrop::into_inner(std::ptr::read(&out[0].pEvents)));
                match r {
                    Ok(()) => {}
                    Err(e) if e.code() == MF_E_TRANSFORM_NEED_MORE_INPUT => break,
                    Err(e) if e.code() == MF_E_TRANSFORM_STREAM_CHANGE => {
                        // The encoder changed its output format: take the new one.
                        let t = self
                            .mft
                            .GetOutputAvailableType(0, 0)
                            .map_err(|e| fail("new type", e))?;
                        self.mft
                            .SetOutputType(0, &t, 0)
                            .map_err(|e| fail("set new type", e))?;
                        self.read_seq_header();
                        continue;
                    }
                    Err(e) => return Err(fail("ProcessOutput", e)),
                }
                let Some(s) = sample else { continue };
                if s.GetUINT32(&MFSampleExtension_CleanPoint).unwrap_or(0) == 1 {
                    key = true;
                }
                let buf = s
                    .ConvertToContiguousBuffer()
                    .map_err(|e| fail("contiguous", e))?;
                let mut p: *mut u8 = std::ptr::null_mut();
                let mut n = 0u32;
                buf.Lock(&mut p, None, Some(&mut n))
                    .map_err(|e| fail("lock out", e))?;
                data.extend_from_slice(std::slice::from_raw_parts(p, n as usize));
                buf.Unlock().map_err(|e| fail("unlock out", e))?;
            }
        }
        if data.is_empty() {
            return Ok(None);
        }
        // A viewer joining mid-stream needs the parameter sets with the keyframe.
        let has_sps = nal_units(&data)
            .iter()
            .any(|n| !n.is_empty() && n[0] & 0x1f == 7);
        if !has_sps
            && nal_units(&data)
                .iter()
                .any(|n| !n.is_empty() && n[0] & 0x1f == 5)
        {
            key = true;
            if !self.seq_header.is_empty() {
                let mut d = self.seq_header.clone();
                d.extend_from_slice(&data);
                data = d;
            }
        }
        Ok(Some(Encoded {
            data,
            keyframe: key,
        }))
    }
}

impl Encoder for MfEncoder {
    fn encode(
        &mut self,
        frame: &Frame,
        pts_ms: u64,
        force_key: bool,
    ) -> Result<Option<Encoded>, EncodeError> {
        if !frame.is_valid() || frame.width < self.w || frame.height < self.h {
            return Err(EncodeError::BadFrame);
        }
        bgra_to_nv12(
            frame.bgra,
            frame.stride,
            self.w as usize,
            self.h as usize,
            &mut self.nv12,
        );
        if force_key {
            if let Some(c) = &self.codec {
                // SAFETY: documented per-frame property.
                unsafe {
                    let _ = c.SetValue(&CODECAPI_AVEncVideoForceKeyFrame, &VARIANT::from(1u32));
                }
            }
        }
        let s = self.input(pts_ms)?;
        // SAFETY: a configured, streaming transform and a valid sample.
        unsafe { self.mft.ProcessInput(0, &s, 0) }.map_err(|e| fail("ProcessInput", e))?;
        self.drain()
    }

    fn set_bitrate(&mut self, bps: u32) {
        if let Some(c) = &self.codec {
            set_rate(c, bps);
        }
    }

    fn size(&self) -> (u32, u32) {
        (self.w, self.h)
    }
}

/// The rate, as a ceiling and not only an average: with the mean alone the
/// encoder was seen sending 1.5 - 2.5 times its target, so frames were held back
/// and each gap cost a keyframe. A half-second buffer bounds every burst, as
/// DataRateLimits does on macOS.
fn set_rate(c: &ICodecAPI, bps: u32) {
    let bps = bps.clamp(100_000, 50_000_000);
    // SAFETY: documented dynamic codec properties with VT_UI4 values; an encoder
    // that ignores one still encodes.
    unsafe {
        let _ = c.SetValue(&CODECAPI_AVEncCommonMeanBitRate, &VARIANT::from(bps));
        let _ = c.SetValue(&CODECAPI_AVEncCommonMaxBitRate, &VARIANT::from(bps));
        let _ = c.SetValue(&CODECAPI_AVEncCommonBufferSize, &VARIANT::from(bps / 2));
    }
}

impl Drop for MfEncoder {
    fn drop(&mut self) {
        // SAFETY: ending the stream on a live transform.
        unsafe {
            let _ = self.mft.ProcessMessage(MFT_MESSAGE_NOTIFY_END_OF_STREAM, 0);
        }
    }
}
