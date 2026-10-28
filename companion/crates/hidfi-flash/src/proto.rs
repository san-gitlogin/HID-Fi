//! The ESP serial loader protocol - only the part HID-Fi needs, and nothing that
//! can do lasting harm. Every request goes through [`Op`]: no erase-all, no
//! erase-region, and register writes only through [`SafeWrite`] - one named,
//! fixed write (clearing "force download" so a board leaves the ROM loader),
//! never an address and value from outside. eFuses are programmed through
//! register writes; none of theirs can be named here, so burning one is not
//! something this crate can be asked to do (AGENTS.md, "Updates and flashing").

/// The only operations this crate can send.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(u8)]
pub enum Op {
    FlashBegin = 0x02,
    FlashData = 0x03,
    FlashEnd = 0x04,
    MemBegin = 0x05,
    MemEnd = 0x06,
    MemData = 0x07,
    Sync = 0x08,
    /// Only ever built by [`SafeWrite::frame`].
    WriteReg = 0x09,
    ReadReg = 0x0A,
    SpiSetParams = 0x0B,
    SpiAttach = 0x0D,
    ChangeBaudrate = 0x0F,
    SpiFlashMd5 = 0x13,
    GetSecurityInfo = 0x14,
    /// Stub only: stream a region of flash back.
    ReadFlash = 0xD2,
}

/// Opcodes this crate must never be able to send - checked by a test against [`Op`].
pub const FORBIDDEN: &[(u8, &str)] = &[
    (0xD0, "ERASE_FLASH - wipes every setting"),
    (0xD1, "ERASE_REGION - erases outside a checked write"),
    (0x10, "FLASH_DEFL_BEGIN - not needed"),
    (0x11, "FLASH_DEFL_DATA - not needed"),
    (0x12, "FLASH_DEFL_END - not needed"),
];

pub const ALL: &[Op] = &[
    Op::FlashBegin,
    Op::FlashData,
    Op::FlashEnd,
    Op::MemBegin,
    Op::MemEnd,
    Op::MemData,
    Op::Sync,
    Op::WriteReg,
    Op::ReadReg,
    Op::SpiSetParams,
    Op::SpiAttach,
    Op::ChangeBaudrate,
    Op::SpiFlashMd5,
    Op::GetSecurityInfo,
    Op::ReadFlash,
];

impl Op {
    pub fn from_u8(b: u8) -> Option<Op> {
        ALL.iter().copied().find(|o| *o as u8 == b)
    }
}

/// The register writes this crate can make - each a fixed address, value and mask.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SafeWrite {
    /// RTC_CNTL_OPTION1_REG bit 0 (FORCE_DOWNLOAD_BOOT) cleared, so the next reset
    /// starts the firmware instead of the ROM loader (esptool's hard_reset for the
    /// ESP32-S3, arduino-esp32 issue 6762).
    ClearForceDownload,
}

pub const RTC_CNTL_OPTION1_REG: u32 = 0x6000_812C;
pub const EFUSE_RANGE: std::ops::Range<u32> = 0x6000_7000..0x6000_8000;

impl SafeWrite {
    pub const ALL: &'static [SafeWrite] = &[SafeWrite::ClearForceDownload];

    /// (address, value, mask)
    pub fn target(self) -> (u32, u32, u32) {
        match self {
            SafeWrite::ClearForceDownload => (RTC_CNTL_OPTION1_REG, 0, 0x1),
        }
    }

    /// The WRITE_REG payload: address, value, mask, delay.
    pub fn payload(self) -> Vec<u8> {
        let (a, v, m) = self.target();
        [a, v, m, 0].iter().flat_map(|w| w.to_le_bytes()).collect()
    }
}

/// XOR checksum of a data payload, seeded 0xEF (FLASH_DATA, MEM_DATA).
pub fn checksum(data: &[u8]) -> u32 {
    data.iter().fold(0xEFu8, |a, b| a ^ b) as u32
}

/// A request frame (before SLIP): 0x00, op, length, checksum, data.
pub fn request(op: Op, data: &[u8], chk: u32) -> Vec<u8> {
    let mut f = Vec::with_capacity(8 + data.len());
    f.push(0x00);
    f.push(op as u8);
    f.extend((data.len() as u16).to_le_bytes());
    f.extend(chk.to_le_bytes());
    f.extend(data);
    f
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    pub op: u8,
    pub value: u32,
    pub data: Vec<u8>,
}

impl Response {
    /// The two status bytes after `expected` bytes of data: (0, _) is success.
    pub fn status(&self, expected: usize) -> Option<(u8, u8)> {
        let s = self.data.get(expected..expected + 2)?;
        Some((s[0], s[1]))
    }
}

pub fn parse_response(frame: &[u8]) -> Option<Response> {
    if frame.len() < 8 || frame[0] != 0x01 {
        return None;
    }
    let len = u16::from_le_bytes([frame[2], frame[3]]) as usize;
    let value = u32::from_le_bytes(frame[4..8].try_into().ok()?);
    let data = frame.get(8..8 + len)?.to_vec();
    Some(Response {
        op: frame[1],
        value,
        data,
    })
}

/// A request frame as the simulated chip sees it.
#[derive(Debug, Clone)]
pub struct Request {
    pub op: u8,
    pub chk: u32,
    pub data: Vec<u8>,
}

pub fn parse_request(frame: &[u8]) -> Option<Request> {
    if frame.len() < 8 || frame[0] != 0x00 {
        return None;
    }
    let len = u16::from_le_bytes([frame[2], frame[3]]) as usize;
    let chk = u32::from_le_bytes(frame[4..8].try_into().ok()?);
    Some(Request {
        op: frame[1],
        chk,
        data: frame.get(8..8 + len)?.to_vec(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nothing_that_burns_an_efuse_or_wipes_a_board_can_be_sent() {
        for (code, why) in FORBIDDEN {
            assert!(Op::from_u8(*code).is_none(), "{why} is reachable");
            assert!(ALL.iter().all(|o| *o as u8 != *code), "{why} is in Op");
        }
    }

    #[test]
    fn the_only_register_writes_touch_no_efuse() {
        for w in SafeWrite::ALL {
            let (addr, _, mask) = w.target();
            assert!(
                !EFUSE_RANGE.contains(&addr),
                "{w:?} writes into the eFuse block"
            );
            assert_eq!(
                (addr, mask),
                (RTC_CNTL_OPTION1_REG, 1),
                "{w:?}: only the force-download bit"
            );
        }
    }

    #[test]
    fn write_reg_is_built_in_one_place_only() {
        // Op::WriteReg must appear only in SafeWrite's sender (flasher.rs) - never a
        // general write_reg(addr, value) anywhere in the crate.
        let src = [include_str!("flasher.rs"), include_str!("update.rs")].concat();
        assert_eq!(src.matches("Op::WriteReg").count(), 1);
        assert!(!src.contains("fn write_reg"));
    }

    #[test]
    fn checksum_and_framing() {
        assert_eq!(checksum(&[]), 0xEF);
        assert_eq!(checksum(&[0xEF]), 0);
        let r = request(Op::ReadReg, &0x6000_7044u32.to_le_bytes(), 0);
        assert_eq!(&r[..4], &[0x00, 0x0A, 4, 0]);
        let resp = parse_response(&[0x01, 0x0A, 2, 0, 0x78, 0x56, 0x34, 0x12, 0, 0]).unwrap();
        assert_eq!((resp.value, resp.status(0)), (0x1234_5678, Some((0, 0))));
        assert!(
            parse_response(&[0x01, 0x0A, 9, 0, 0, 0, 0, 0, 0]).is_none(),
            "length past the frame"
        );
    }
}
