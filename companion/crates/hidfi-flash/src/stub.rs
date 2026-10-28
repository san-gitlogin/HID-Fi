//! Espressif's flasher stub for the ESP32-S3 (esp-flasher-stub v1.4.1,
//! Apache-2.0 - stub/README.md). Loaded into RAM for the session so flash can be
//! read; refused unless its bytes are exactly the pinned ones.

use sha2::{Digest, Sha256};

const JSON: &str = include_str!("../stub/esp32s3.json");
pub const SHA256: &str = "767c0ef61d864bb9524c52a2fa7ff5b144598c8df729b2cd31e84d8785e2d65b";

pub struct Stub {
    pub text: Vec<u8>,
    pub text_start: u32,
    pub data: Vec<u8>,
    pub data_start: u32,
    pub entry: u32,
}

impl Stub {
    pub fn esp32s3() -> Result<Stub, String> {
        let got = hex(&Sha256::digest(JSON.as_bytes()));
        if got != SHA256 {
            return Err(format!("the vendored stub is not v1.4.1 (sha256 {got})"));
        }
        let v: serde_json::Value = serde_json::from_str(JSON).map_err(|e| e.to_string())?;
        let num = |k: &str| {
            v[k].as_u64()
                .map(|x| x as u32)
                .ok_or(format!("stub: no {k}"))
        };
        let b64 = |k: &str| v[k].as_str().map(base64).ok_or(format!("stub: no {k}"))?;
        Ok(Stub {
            text: b64("text")?,
            text_start: num("text_start")?,
            data: b64("data")?,
            data_start: num("data_start")?,
            entry: num("entry")?,
        })
    }
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn base64(s: &str) -> Result<Vec<u8>, String> {
    const T: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::with_capacity(s.len() * 3 / 4);
    let (mut buf, mut bits) = (0u32, 0);
    for c in s.bytes().filter(|c| *c != b'=' && !c.is_ascii_whitespace()) {
        let v = T.iter().position(|t| *t == c).ok_or("stub: not base64")? as u32;
        buf = (buf << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_pinned_stub_loads() {
        let s = Stub::esp32s3().unwrap();
        // Python's base64 on the same file: 8668 and 264 bytes (the text ends in "==").
        assert_eq!(s.text.len(), 8668);
        assert_eq!(s.data.len(), 264);
        assert_eq!(
            (s.text_start, s.data_start, s.entry),
            (1077379072, 1070280132, 1077384616)
        );
    }

    #[test]
    fn base64_round_trip() {
        assert_eq!(base64("aGlkLWZp").unwrap(), b"hid-fi");
        assert!(base64("a*b").is_err());
    }
}
