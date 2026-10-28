//! SLIP framing (RFC 1055), as the ESP serial loader speaks it: 0xC0 ends a
//! frame, 0xDB 0xDC stands for 0xC0 and 0xDB 0xDD for 0xDB.

const END: u8 = 0xC0;
const ESC: u8 = 0xDB;
const ESC_END: u8 = 0xDC;
const ESC_ESC: u8 = 0xDD;

pub fn encode(frame: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(frame.len() + 8);
    out.push(END);
    for &b in frame {
        match b {
            END => out.extend_from_slice(&[ESC, ESC_END]),
            ESC => out.extend_from_slice(&[ESC, ESC_ESC]),
            _ => out.push(b),
        }
    }
    out.push(END);
    out
}

/// Feeds bytes in, hands whole frames out. A malformed escape drops the frame
/// rather than guessing at its contents.
#[derive(Default)]
pub struct Decoder {
    buf: Vec<u8>,
    esc: bool,
    bad: bool,
    started: bool,
}

impl Decoder {
    pub fn push(&mut self, byte: u8, out: &mut Vec<Vec<u8>>) {
        if byte == END {
            if self.started && !self.buf.is_empty() && !self.bad {
                out.push(std::mem::take(&mut self.buf));
            }
            self.buf.clear();
            self.esc = false;
            self.bad = false;
            self.started = true;
            return;
        }
        if !self.started {
            return; // noise before the first frame (a boot banner): ignored
        }
        if self.esc {
            self.esc = false;
            match byte {
                ESC_END => self.buf.push(END),
                ESC_ESC => self.buf.push(ESC),
                _ => self.bad = true,
            }
        } else if byte == ESC {
            self.esc = true;
        } else {
            self.buf.push(byte);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn decode_all(bytes: &[u8]) -> Vec<Vec<u8>> {
        let mut d = Decoder::default();
        let mut out = Vec::new();
        for &b in bytes {
            d.push(b, &mut out);
        }
        out
    }

    #[test]
    fn round_trip_including_the_special_bytes() {
        let frame = vec![0x00, 0xC0, 0xDB, 0x01, 0xDB, 0xDC, 0xC0];
        assert_eq!(decode_all(&encode(&frame)), vec![frame]);
    }

    #[test]
    fn noise_before_the_first_frame_is_ignored() {
        let mut bytes = b"ESP-ROM:esp32s3-20210327\r\n".to_vec();
        bytes.extend(encode(&[1, 2, 3]));
        assert_eq!(decode_all(&bytes), vec![vec![1, 2, 3]]);
    }

    #[test]
    fn a_bad_escape_drops_the_frame_not_the_next() {
        let mut bytes = vec![END, 1, ESC, 0x42, 2, END];
        bytes.extend(encode(&[9]));
        assert_eq!(decode_all(&bytes), vec![vec![9]]);
    }

    #[test]
    fn back_to_back_frames() {
        let mut bytes = encode(&[1]);
        bytes.extend(encode(&[2, 2]));
        assert_eq!(decode_all(&bytes), vec![vec![1], vec![2, 2]]);
    }
}
