//! A captured picture, and the encoder interface every platform implements.

/// One frame of the screen in BGRA, 8 bits per channel, rows `stride` bytes
/// apart (a row can be padded past `width * 4`).
pub struct Frame<'a> {
    pub width: u32,
    pub height: u32,
    pub stride: usize,
    pub bgra: &'a [u8],
}

impl Frame<'_> {
    /// True if the buffer really holds `height` rows of `stride` bytes and a
    /// row really fits `width` pixels. Encoders check this before touching it.
    pub fn is_valid(&self) -> bool {
        self.width > 0
            && self.height > 0
            && self.stride >= self.width as usize * 4
            && self.bgra.len() >= self.stride * (self.height as usize - 1) + self.width as usize * 4
    }
}

/// One encoded picture: H.264 in Annex B (start codes), the form str0m and
/// browsers take. A keyframe carries its SPS and PPS in front.
#[derive(Debug, Clone)]
pub struct Encoded {
    pub data: Vec<u8>,
    pub keyframe: bool,
}

#[derive(Debug, thiserror::Error)]
pub enum EncodeError {
    #[error("this computer has no usable H.264 encoder ({0})")]
    Unavailable(String),
    #[error("the frame does not match its size")]
    BadFrame,
    #[error("the encoder failed ({0})")]
    Failed(String),
}

/// A keyframe at least this often, so a viewer whose request for one was lost
/// still recovers. Rare on purpose: keyframes come when a viewer asks (joins,
/// loss), and a forced one every 4 s - a 1080p keyframe takes a few hundred
/// milliseconds to arrive - showed as a short freeze on every screen, 1.5 s in
/// 28 (tests/overload.py).
pub const KEYFRAME_EVERY_SECS: u32 = 20;

pub struct EncoderConfig {
    pub width: u32,
    pub height: u32,
    pub fps: u32,
    pub bitrate_bps: u32,
}

pub trait Encoder: Send {
    /// Encode one frame. `force_key` asks for a keyframe (a new viewer, or a
    /// viewer that lost packets and asked for one).
    fn encode(
        &mut self,
        frame: &Frame,
        pts_ms: u64,
        force_key: bool,
    ) -> Result<Option<Encoded>, EncodeError>;
    /// Change the target bitrate while running (congestion control).
    fn set_bitrate(&mut self, bps: u32);
    fn size(&self) -> (u32, u32);
}

/// Splits Annex B into NAL units (without start codes). Used by tests and by
/// anything that needs to inspect what an encoder produced.
pub fn nal_units(annexb: &[u8]) -> Vec<&[u8]> {
    let mut out = Vec::new();
    let mut i = 0;
    let mut start: Option<usize> = None;
    while i + 3 <= annexb.len() {
        let sc3 = annexb[i] == 0 && annexb[i + 1] == 0 && annexb[i + 2] == 1;
        if sc3 {
            if let Some(s) = start {
                let mut end = i;
                if end > s && annexb[end - 1] == 0 {
                    end -= 1; // the leading zero of a 4-byte start code
                }
                out.push(&annexb[s..end]);
            }
            i += 3;
            start = Some(i);
        } else {
            i += 1;
        }
    }
    if let Some(s) = start {
        if s < annexb.len() {
            out.push(&annexb[s..]);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_both_start_code_lengths() {
        let b = [
            0, 0, 0, 1, 0x67, 1, 2, 0, 0, 1, 0x68, 3, 0, 0, 0, 1, 0x65, 9, 9,
        ];
        let n = nal_units(&b);
        assert_eq!(
            n,
            vec![&[0x67, 1, 2][..], &[0x68, 3][..], &[0x65, 9, 9][..]]
        );
    }

    #[test]
    fn frame_bounds_are_checked() {
        let buf = vec![0u8; 16 * 2];
        assert!(Frame {
            width: 4,
            height: 2,
            stride: 16,
            bgra: &buf
        }
        .is_valid());
        assert!(!Frame {
            width: 5,
            height: 2,
            stride: 16,
            bgra: &buf
        }
        .is_valid());
        assert!(!Frame {
            width: 4,
            height: 3,
            stride: 16,
            bgra: &buf
        }
        .is_valid());
        assert!(!Frame {
            width: 0,
            height: 2,
            stride: 16,
            bgra: &buf
        }
        .is_valid());
    }
}
