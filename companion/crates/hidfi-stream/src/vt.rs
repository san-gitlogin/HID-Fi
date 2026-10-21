//! H.264 on macOS with VideoToolbox: the Mac's own hardware encoder, licensed
//! by Apple, the same one FaceTime and screen recording use.
//!
//! Configured for screen sharing: real time, no B-frames (no reordering delay),
//! constrained baseline (what every browser decodes), and the low-latency rate
//! control where the OS has it. Each call encodes one frame and waits for it,
//! so latency is one frame and nothing is left queued.
//!
//! Every Apple call's status is checked. Objects are released exactly once, in
//! `Drop`, including when construction fails half way.

#![allow(non_upper_case_globals, non_snake_case, clippy::upper_case_acronyms)]

use crate::frame::{EncodeError, Encoded, Encoder, EncoderConfig, Frame};
use core_foundation::base::TCFType;
use core_foundation::boolean::CFBoolean;
use core_foundation::dictionary::CFDictionary;
use core_foundation::number::CFNumber;
use core_foundation::string::CFString;
use core_foundation_sys::array::{CFArrayGetCount, CFArrayGetValueAtIndex, CFArrayRef};
use core_foundation_sys::base::{CFAllocatorRef, CFRelease, CFTypeRef, OSStatus};
use core_foundation_sys::dictionary::{CFDictionaryContainsKey, CFDictionaryRef};
use core_foundation_sys::string::CFStringRef;
use std::ffi::c_void;
use std::ptr;
use std::sync::Mutex;

type VTSessionRef = *mut c_void;
type CMSampleBufferRef = *mut c_void;
type CVPixelBufferRef = *mut c_void;
type CMFormatDescriptionRef = *mut c_void;
type CMBlockBufferRef = *mut c_void;

#[repr(C)]
#[derive(Copy, Clone)]
struct CMTime {
    value: i64,
    timescale: i32,
    flags: u32,
    epoch: i64,
}
const CMTIME_VALID: u32 = 1;

type OutputCallback = extern "C" fn(*mut c_void, *mut c_void, OSStatus, u32, CMSampleBufferRef);

#[link(name = "VideoToolbox", kind = "framework")]
extern "C" {
    fn VTCompressionSessionCreate(
        allocator: CFAllocatorRef,
        width: i32,
        height: i32,
        codec: u32,
        encoder_spec: CFDictionaryRef,
        source_attrs: CFDictionaryRef,
        compressed_allocator: CFAllocatorRef,
        callback: OutputCallback,
        refcon: *mut c_void,
        out: *mut VTSessionRef,
    ) -> OSStatus;
    fn VTSessionSetProperty(session: VTSessionRef, key: CFStringRef, value: CFTypeRef) -> OSStatus;
    fn VTCompressionSessionPrepareToEncodeFrames(session: VTSessionRef) -> OSStatus;
    fn VTCompressionSessionEncodeFrame(
        session: VTSessionRef,
        image: CVPixelBufferRef,
        pts: CMTime,
        duration: CMTime,
        frame_props: CFDictionaryRef,
        source_ref: *mut c_void,
        info_out: *mut u32,
    ) -> OSStatus;
    fn VTCompressionSessionCompleteFrames(session: VTSessionRef, until: CMTime) -> OSStatus;
    fn VTCompressionSessionInvalidate(session: VTSessionRef);

    static kVTCompressionPropertyKey_RealTime: CFStringRef;
    static kVTCompressionPropertyKey_ProfileLevel: CFStringRef;
    static kVTCompressionPropertyKey_AllowFrameReordering: CFStringRef;
    static kVTCompressionPropertyKey_MaxKeyFrameInterval: CFStringRef;
    static kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration: CFStringRef;
    static kVTCompressionPropertyKey_AverageBitRate: CFStringRef;
    static kVTCompressionPropertyKey_DataRateLimits: CFStringRef;
    static kVTCompressionPropertyKey_ExpectedFrameRate: CFStringRef;
    static kVTProfileLevel_H264_ConstrainedBaseline_AutoLevel: CFStringRef;
    static kVTEncodeFrameOptionKey_ForceKeyFrame: CFStringRef;
    static kVTVideoEncoderSpecification_EnableLowLatencyRateControl: CFStringRef;
}

#[link(name = "CoreMedia", kind = "framework")]
extern "C" {
    fn CMSampleBufferGetDataBuffer(sb: CMSampleBufferRef) -> CMBlockBufferRef;
    fn CMSampleBufferGetFormatDescription(sb: CMSampleBufferRef) -> CMFormatDescriptionRef;
    fn CMSampleBufferGetSampleAttachmentsArray(sb: CMSampleBufferRef, create: u8) -> CFArrayRef;
    fn CMBlockBufferGetDataLength(bb: CMBlockBufferRef) -> usize;
    fn CMBlockBufferCopyDataBytes(
        bb: CMBlockBufferRef,
        offset: usize,
        len: usize,
        dest: *mut c_void,
    ) -> OSStatus;
    fn CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
        fd: CMFormatDescriptionRef,
        index: usize,
        ptr_out: *mut *const u8,
        size_out: *mut usize,
        count_out: *mut usize,
        nal_header_len_out: *mut i32,
    ) -> OSStatus;
    static kCMSampleAttachmentKey_NotSync: CFStringRef;
}

#[link(name = "CoreVideo", kind = "framework")]
extern "C" {
    fn CVPixelBufferCreate(
        allocator: CFAllocatorRef,
        width: usize,
        height: usize,
        format: u32,
        attrs: CFDictionaryRef,
        out: *mut CVPixelBufferRef,
    ) -> i32;
    fn CVPixelBufferLockBaseAddress(pb: CVPixelBufferRef, flags: u64) -> i32;
    fn CVPixelBufferUnlockBaseAddress(pb: CVPixelBufferRef, flags: u64) -> i32;
    fn CVPixelBufferGetBaseAddress(pb: CVPixelBufferRef) -> *mut c_void;
    fn CVPixelBufferGetBytesPerRow(pb: CVPixelBufferRef) -> usize;
    static kCVPixelBufferIOSurfacePropertiesKey: CFStringRef;
}

const CODEC_H264: u32 = u32::from_be_bytes(*b"avc1");
const PIXEL_BGRA: u32 = u32::from_be_bytes(*b"BGRA");

/// What the output callback writes into. Owned by the encoder, boxed so its
/// address is stable for the session's lifetime.
#[derive(Default)]
struct Sink {
    out: Option<Result<Encoded, String>>,
}

extern "C" fn on_output(
    refcon: *mut c_void,
    _src: *mut c_void,
    status: OSStatus,
    _flags: u32,
    sb: CMSampleBufferRef,
) {
    // SAFETY: refcon is the `Box<Mutex<Sink>>` the encoder created and keeps
    // alive until after the session is invalidated, which stops callbacks.
    let sink = unsafe { &*(refcon as *const Mutex<Sink>) };
    let result = if status != 0 {
        Err(format!("encode status {status}"))
    } else if sb.is_null() {
        // The encoder dropped the frame (allowed in real-time mode).
        return;
    } else {
        // SAFETY: sb is a valid CMSampleBuffer for the duration of this callback.
        unsafe { annexb_from_sample(sb) }
    };
    if let Ok(mut s) = sink.lock() {
        s.out = Some(result);
    }
}

/// AVCC (length-prefixed NAL units) to Annex B, with SPS and PPS in front of
/// a keyframe.
unsafe fn annexb_from_sample(sb: CMSampleBufferRef) -> Result<Encoded, String> {
    let mut keyframe = true;
    let atts = CMSampleBufferGetSampleAttachmentsArray(sb, 0);
    if !atts.is_null() && CFArrayGetCount(atts) > 0 {
        let d = CFArrayGetValueAtIndex(atts, 0) as CFDictionaryRef;
        if !d.is_null()
            && CFDictionaryContainsKey(d, kCMSampleAttachmentKey_NotSync as *const c_void) != 0
        {
            keyframe = false;
        }
    }
    let mut data = Vec::new();
    let fd = CMSampleBufferGetFormatDescription(sb);
    let mut nal_len_size: i32 = 4;
    if keyframe {
        if fd.is_null() {
            return Err("keyframe without a format description".into());
        }
        let mut count = 0usize;
        let st = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
            fd,
            0,
            ptr::null_mut(),
            ptr::null_mut(),
            &mut count,
            &mut nal_len_size,
        );
        if st != 0 {
            return Err(format!("parameter sets {st}"));
        }
        for i in 0..count {
            let (mut p, mut n) = (ptr::null(), 0usize);
            let st = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                fd,
                i,
                &mut p,
                &mut n,
                ptr::null_mut(),
                ptr::null_mut(),
            );
            if st != 0 || p.is_null() {
                return Err(format!("parameter set {i}: {st}"));
            }
            data.extend_from_slice(&[0, 0, 0, 1]);
            data.extend_from_slice(std::slice::from_raw_parts(p, n));
        }
    } else if !fd.is_null() {
        let _ = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
            fd,
            0,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut nal_len_size,
        );
    }
    if !(1..=4).contains(&nal_len_size) {
        return Err(format!("NAL length size {nal_len_size}"));
    }
    let bb = CMSampleBufferGetDataBuffer(sb);
    if bb.is_null() {
        return Err("no data buffer".into());
    }
    let len = CMBlockBufferGetDataLength(bb);
    let mut avcc = vec![0u8; len];
    let st = CMBlockBufferCopyDataBytes(bb, 0, len, avcc.as_mut_ptr() as *mut c_void);
    if st != 0 {
        return Err(format!("copy {st}"));
    }
    let n = nal_len_size as usize;
    let mut i = 0usize;
    while i + n <= avcc.len() {
        let mut l = 0usize;
        for b in &avcc[i..i + n] {
            l = (l << 8) | *b as usize;
        }
        i += n;
        if l == 0 || i + l > avcc.len() {
            return Err("truncated NAL unit".into());
        }
        data.extend_from_slice(&[0, 0, 0, 1]);
        data.extend_from_slice(&avcc[i..i + l]);
        i += l;
    }
    Ok(Encoded { data, keyframe })
}

pub struct VtEncoder {
    session: VTSessionRef,
    sink: Box<Mutex<Sink>>,
    width: u32,
    height: u32,
    pixbuf: CVPixelBufferRef,
}

// SAFETY: the session and pixel buffer are only used through &mut self, so
// never from two threads at once. VideoToolbox sessions may be used from any
// thread.
unsafe impl Send for VtEncoder {}

/// A hard ceiling next to the average: no half second may carry more than
/// half a second's worth at the target rate. Without it a keyframe of a busy
/// screen ran to half a second of a poor link on its own - more than a WiFi
/// router queues - so it was lost, asked for again, lost again.
fn set_limits(session: VTSessionRef, bps: i64) {
    let bytes = CFNumber::from((bps / 8 / 2).max(4_000));
    let window = CFNumber::from(0.5f64);
    let limits = core_foundation::array::CFArray::from_CFTypes(&[bytes.as_CFType(), window.as_CFType()]);
    // SAFETY: framework constant; a live session and value.
    if let Err(e) = set(session, unsafe { kVTCompressionPropertyKey_DataRateLimits }, limits.as_CFTypeRef()) {
        log::warn!("data rate limit refused: {e}");
    }
}

fn set(session: VTSessionRef, key: CFStringRef, value: CFTypeRef) -> Result<(), EncodeError> {
    // SAFETY: a live session, a framework-provided key, a live CF value.
    let st = unsafe { VTSessionSetProperty(session, key, value) };
    if st == 0 {
        Ok(())
    } else {
        Err(EncodeError::Failed(format!("property {st}")))
    }
}

impl VtEncoder {
    pub fn new(cfg: &EncoderConfig) -> Result<Self, EncodeError> {
        // H.264 needs even dimensions; the last odd row/column is dropped.
        let (w, h) = (cfg.width & !1, cfg.height & !1);
        if w < 16 || h < 16 || w > 8192 || h > 8192 {
            return Err(EncodeError::Unavailable(format!(
                "unsupported size {w}x{h}"
            )));
        }
        let sink: Box<Mutex<Sink>> = Box::default();
        let mut session: VTSessionRef = ptr::null_mut();
        // SAFETY: the key is a framework constant.
        let low_latency = unsafe {
            CFString::wrap_under_get_rule(kVTVideoEncoderSpecification_EnableLowLatencyRateControl)
        };
        let spec = CFDictionary::from_CFType_pairs(&[(
            low_latency.as_CFType(),
            CFBoolean::true_value().as_CFType(),
        )]);
        // SAFETY: all pointers are valid or null where the API allows null;
        // `sink` outlives the session (released after invalidate in Drop).
        let st = unsafe {
            VTCompressionSessionCreate(
                ptr::null(),
                w as i32,
                h as i32,
                CODEC_H264,
                spec.as_concrete_TypeRef(),
                ptr::null(),
                ptr::null(),
                on_output,
                &*sink as *const Mutex<Sink> as *mut c_void,
                &mut session,
            )
        };
        if st != 0 || session.is_null() {
            return Err(EncodeError::Unavailable(format!(
                "VTCompressionSessionCreate {st}"
            )));
        }
        let mut me = VtEncoder {
            session,
            sink,
            width: w,
            height: h,
            pixbuf: ptr::null_mut(),
        };
        let t = CFBoolean::true_value();
        let f = CFBoolean::false_value();
        let fps = cfg.fps.clamp(1, 120) as i32;
        // SAFETY: framework constants.
        unsafe {
            set(
                session,
                kVTCompressionPropertyKey_RealTime,
                t.as_CFTypeRef(),
            )?;
            set(
                session,
                kVTCompressionPropertyKey_ProfileLevel,
                kVTProfileLevel_H264_ConstrainedBaseline_AutoLevel as CFTypeRef,
            )?;
            set(
                session,
                kVTCompressionPropertyKey_AllowFrameReordering,
                f.as_CFTypeRef(),
            )?;
            // A keyframe at least every 4 s, so a viewer that lost one recovers
            // even if its request for a new one is lost too.
            set(
                session,
                kVTCompressionPropertyKey_MaxKeyFrameInterval,
                CFNumber::from(fps * 4).as_CFTypeRef(),
            )?;
            set(
                session,
                kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration,
                CFNumber::from(4.0f64).as_CFTypeRef(),
            )?;
            set(
                session,
                kVTCompressionPropertyKey_ExpectedFrameRate,
                CFNumber::from(fps).as_CFTypeRef(),
            )?;
            set(
                session,
                kVTCompressionPropertyKey_AverageBitRate,
                CFNumber::from(cfg.bitrate_bps as i64).as_CFTypeRef(),
            )?;
        }
        set_limits(session, cfg.bitrate_bps as i64);
        // SAFETY: a live session.
        let st = unsafe { VTCompressionSessionPrepareToEncodeFrames(session) };
        if st != 0 {
            return Err(EncodeError::Unavailable(format!("prepare {st}")));
        }
        // One IOSurface-backed buffer, reused for every frame.
        // SAFETY: framework constant key; empty dictionary value.
        let ios_key =
            unsafe { CFString::wrap_under_get_rule(kCVPixelBufferIOSurfacePropertiesKey) };
        let empty: CFDictionary<CFString, CFBoolean> = CFDictionary::from_CFType_pairs(&[]);
        let attrs = CFDictionary::from_CFType_pairs(&[(ios_key.as_CFType(), empty.as_CFType())]);
        // SAFETY: valid out pointer; attrs alive for the call.
        let st = unsafe {
            CVPixelBufferCreate(
                ptr::null(),
                w as usize,
                h as usize,
                PIXEL_BGRA,
                attrs.as_concrete_TypeRef(),
                &mut me.pixbuf,
            )
        };
        if st != 0 || me.pixbuf.is_null() {
            me.pixbuf = ptr::null_mut();
            return Err(EncodeError::Unavailable(format!(
                "CVPixelBufferCreate {st}"
            )));
        }
        Ok(me)
    }

    fn fill(&mut self, frame: &Frame) -> Result<(), EncodeError> {
        // SAFETY: a live pixel buffer, locked while written.
        unsafe {
            if CVPixelBufferLockBaseAddress(self.pixbuf, 0) != 0 {
                return Err(EncodeError::Failed("lock".into()));
            }
            let base = CVPixelBufferGetBaseAddress(self.pixbuf) as *mut u8;
            let dst_stride = CVPixelBufferGetBytesPerRow(self.pixbuf);
            if base.is_null() || dst_stride < self.width as usize * 4 {
                CVPixelBufferUnlockBaseAddress(self.pixbuf, 0);
                return Err(EncodeError::Failed("pixel buffer".into()));
            }
            let row = (self.width.min(frame.width) as usize) * 4;
            let rows = self.height.min(frame.height) as usize;
            for y in 0..rows {
                ptr::copy_nonoverlapping(
                    frame.bgra.as_ptr().add(y * frame.stride),
                    base.add(y * dst_stride),
                    row,
                );
            }
            CVPixelBufferUnlockBaseAddress(self.pixbuf, 0);
        }
        Ok(())
    }
}

impl Encoder for VtEncoder {
    fn encode(
        &mut self,
        frame: &Frame,
        pts_ms: u64,
        force_key: bool,
    ) -> Result<Option<Encoded>, EncodeError> {
        if !frame.is_valid() || frame.width < self.width || frame.height < self.height {
            return Err(EncodeError::BadFrame);
        }
        self.fill(frame)?;
        let pts = CMTime {
            value: pts_ms as i64,
            timescale: 1000,
            flags: CMTIME_VALID,
            epoch: 0,
        };
        let invalid = CMTime {
            value: 0,
            timescale: 0,
            flags: 0,
            epoch: 0,
        };
        let props = if force_key {
            // SAFETY: framework constant key.
            let k = unsafe { CFString::wrap_under_get_rule(kVTEncodeFrameOptionKey_ForceKeyFrame) };
            Some(CFDictionary::from_CFType_pairs(&[(
                k.as_CFType(),
                CFBoolean::true_value().as_CFType(),
            )]))
        } else {
            None
        };
        if let Ok(mut s) = self.sink.lock() {
            s.out = None;
        }
        // SAFETY: live session and buffer; props alive for the call.
        let st = unsafe {
            VTCompressionSessionEncodeFrame(
                self.session,
                self.pixbuf,
                pts,
                invalid,
                props
                    .as_ref()
                    .map_or(ptr::null(), |d| d.as_concrete_TypeRef()),
                ptr::null_mut(),
                ptr::null_mut(),
            )
        };
        if st != 0 {
            return Err(EncodeError::Failed(format!("encode {st}")));
        }
        // Wait for this frame: one frame of latency, nothing left queued.
        // SAFETY: live session.
        let st = unsafe { VTCompressionSessionCompleteFrames(self.session, pts) };
        if st != 0 {
            return Err(EncodeError::Failed(format!("complete {st}")));
        }
        let out = self.sink.lock().ok().and_then(|mut s| s.out.take());
        match out {
            None => Ok(None),
            Some(Ok(e)) => Ok(Some(e)),
            Some(Err(e)) => Err(EncodeError::Failed(e)),
        }
    }

    fn set_bitrate(&mut self, bps: u32) {
        let bps = bps.clamp(100_000, 50_000_000) as i64;
        // SAFETY: framework constant; a live session.
        if let Err(e) = unsafe {
            set(
                self.session,
                kVTCompressionPropertyKey_AverageBitRate,
                CFNumber::from(bps).as_CFTypeRef(),
            )
        } {
            log::warn!("bitrate change refused: {e}");
        }
        set_limits(self.session, bps);
    }

    fn size(&self) -> (u32, u32) {
        (self.width, self.height)
    }
}

impl Drop for VtEncoder {
    fn drop(&mut self) {
        // SAFETY: each object released once; invalidate stops callbacks before
        // the sink they write into is freed (it is dropped after this body).
        unsafe {
            if !self.session.is_null() {
                VTCompressionSessionInvalidate(self.session);
                CFRelease(self.session as CFTypeRef);
                self.session = ptr::null_mut();
            }
            if !self.pixbuf.is_null() {
                CFRelease(self.pixbuf as CFTypeRef);
                self.pixbuf = ptr::null_mut();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frame::nal_units;

    fn pattern(w: u32, h: u32, t: u32) -> Vec<u8> {
        let mut v = vec![0u8; (w * h * 4) as usize];
        for y in 0..h {
            for x in 0..w {
                let i = ((y * w + x) * 4) as usize;
                v[i] = (x + t) as u8;
                v[i + 1] = y as u8;
                v[i + 2] = ((x ^ y) + t * 3) as u8;
                v[i + 3] = 255;
            }
        }
        v
    }

    #[test]
    fn encodes_a_keyframe_then_deltas_then_a_forced_key() {
        let (w, h) = (640, 360);
        let mut e = VtEncoder::new(&EncoderConfig {
            width: w,
            height: h,
            fps: 30,
            bitrate_bps: 2_000_000,
        })
        .unwrap();
        let mut kinds = Vec::new();
        for t in 0..6u32 {
            let buf = pattern(w, h, t * 7);
            let f = Frame {
                width: w,
                height: h,
                stride: (w * 4) as usize,
                bgra: &buf,
            };
            let out = e
                .encode(&f, t as u64 * 33, t == 4)
                .unwrap()
                .expect("one frame out per frame in");
            let nals = nal_units(&out.data);
            let types: Vec<u8> = nals.iter().map(|n| n[0] & 0x1f).collect();
            if out.keyframe {
                assert!(
                    types.contains(&7) && types.contains(&8) && types.contains(&5),
                    "{types:?}"
                );
            } else {
                assert!(types.iter().all(|t| *t == 1 || *t == 6), "{types:?}");
            }
            kinds.push(out.keyframe);
        }
        assert!(kinds[0], "the first frame is a keyframe");
        assert!(!kinds[1], "then deltas");
        assert!(kinds[4], "a forced keyframe is honoured");
    }

    #[test]
    fn constrained_baseline_profile() {
        let (w, h) = (320, 240);
        let mut e = VtEncoder::new(&EncoderConfig {
            width: w,
            height: h,
            fps: 30,
            bitrate_bps: 500_000,
        })
        .unwrap();
        let buf = pattern(w, h, 0);
        let out = e
            .encode(
                &Frame {
                    width: w,
                    height: h,
                    stride: (w * 4) as usize,
                    bgra: &buf,
                },
                0,
                true,
            )
            .unwrap()
            .unwrap();
        let sps = nal_units(&out.data)
            .into_iter()
            .find(|n| n[0] & 0x1f == 7)
            .unwrap();
        assert_eq!(sps[1], 66, "profile_idc baseline");
        assert!(sps[2] & 0x40 != 0, "constraint_set1: constrained baseline");
    }

    #[test]
    fn odd_sizes_are_made_even_and_bad_frames_refused() {
        let mut e = VtEncoder::new(&EncoderConfig {
            width: 641,
            height: 361,
            fps: 30,
            bitrate_bps: 1_000_000,
        })
        .unwrap();
        assert_eq!(e.size(), (640, 360));
        let buf = vec![0u8; 10];
        assert!(matches!(
            e.encode(
                &Frame {
                    width: 641,
                    height: 361,
                    stride: 641 * 4,
                    bgra: &buf
                },
                0,
                false
            ),
            Err(EncodeError::BadFrame)
        ));
        assert!(VtEncoder::new(&EncoderConfig {
            width: 8,
            height: 8,
            fps: 30,
            bitrate_bps: 1
        })
        .is_err());
    }
}
