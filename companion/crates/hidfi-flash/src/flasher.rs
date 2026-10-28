//! The client: talks to the ESP32-S3's ROM loader and, once loaded, Espressif's
//! stub - only through [`crate::proto::Op`]. Every write is checked by reading
//! its MD5 back; nothing is trusted because a command said "ok".

use std::collections::VecDeque;

use md5::{Digest, Md5};

use crate::port::Port;
use crate::proto::{self, Op, Response, SafeWrite};
use crate::slip::{self, Decoder};
use crate::stub::Stub;

pub const SECTOR: u32 = 0x1000;
const STUB_BLOCK: usize = 0x4000;
const RAM_BLOCK: usize = 0x1800;
const MAC_EFUSE_REG: u32 = 0x6000_7044;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum FlashError {
    #[error("the port went away: {0}")]
    Port(String),
    #[error("no answer from the chip ({0})")]
    Timeout(&'static str),
    #[error("the chip refused {0}: status {1:#04x} {2:#04x}")]
    Refused(&'static str, u8, u8),
    #[error("not an ESP32-S3 (chip id {0})")]
    WrongChip(u32),
    #[error("{0}")]
    Check(String),
    #[error("cancelled")]
    Cancelled,
}

impl From<std::io::Error> for FlashError {
    fn from(e: std::io::Error) -> Self {
        FlashError::Port(e.to_string())
    }
}

pub struct Flasher<P: Port> {
    pub port: P,
    dec: Decoder,
    frames: VecDeque<Vec<u8>>,
    stub: bool,
}

impl<P: Port> Flasher<P> {
    pub fn new(port: P) -> Self {
        Flasher { port, dec: Decoder::default(), frames: VecDeque::new(), stub: false }
    }

    pub fn has_stub(&self) -> bool {
        self.stub
    }

    // ---- framing --------------------------------------------------------------------

    fn send(&mut self, op: Op, data: &[u8], chk: u32) -> Result<(), FlashError> {
        let frame = slip::encode(&proto::request(op, data, chk));
        Ok(self.port.write_all(&frame)?)
    }

    fn send_raw(&mut self, frame: &[u8]) -> Result<(), FlashError> {
        Ok(self.port.write_all(&slip::encode(frame))?)
    }

    fn next_frame(&mut self, timeout_ms: u32) -> Result<Vec<u8>, FlashError> {
        let mut waited = 0;
        let mut buf = [0u8; 1024];
        loop {
            if let Some(f) = self.frames.pop_front() {
                return Ok(f);
            }
            if waited >= timeout_ms {
                return Err(FlashError::Timeout("frame"));
            }
            let n = self.port.read(&mut buf, 50)?;
            if n == 0 {
                waited += 50;
                continue;
            }
            let mut out = Vec::new();
            for &b in &buf[..n] {
                self.dec.push(b, &mut out);
            }
            self.frames.extend(out);
        }
    }

    /// Sends one command and returns its response; `expect` is the response data
    /// length before the two status bytes.
    fn command(&mut self, op: Op, data: &[u8], chk: u32, expect: usize, what: &'static str, timeout_ms: u32)
        -> Result<Response, FlashError> {
        self.send(op, data, chk)?;
        let mut waited = 0;
        loop {
            let f = self.next_frame(timeout_ms.saturating_sub(waited).max(50))?;
            waited += 50;
            if let Some(r) = proto::parse_response(&f) {
                if r.op != op as u8 {
                    continue;
                }
                return match r.status(expect) {
                    Some((0, _)) => Ok(r),
                    Some((a, b)) => Err(FlashError::Refused(what, a, b)),
                    None => Err(FlashError::Refused(what, 0xFF, 0xFF)),
                };
            }
            if waited > timeout_ms {
                return Err(FlashError::Timeout(what));
            }
        }
    }

    // ---- getting the chip's attention ---------------------------------------------------

    /// The CH343's auto-reset: EN low, then GPIO0 low while EN rises - the ROM
    /// loader starts (esptool's classic reset).
    pub fn enter_bootloader(&mut self) -> Result<(), FlashError> {
        self.port.set_dtr(false)?;
        self.port.set_rts(true)?;
        self.port.sleep_ms(100);
        self.port.set_dtr(true)?;
        self.port.set_rts(false)?;
        self.port.sleep_ms(50);
        self.port.set_dtr(false)?;
        self.stub = false;
        self.frames.clear();
        self.dec = Decoder::default();
        Ok(())
    }

    /// EN pulse with GPIO0 high: the board starts its firmware. The port stays
    /// open - on macOS a new open would reset it again (AGENTS.md).
    pub fn hard_reset(&mut self) -> Result<(), FlashError> {
        self.port.set_dtr(false)?;
        self.port.set_rts(true)?;
        self.port.sleep_ms(100);
        self.port.set_rts(false)?;
        self.stub = false;
        Ok(())
    }

    /// The board was already restarted into the ROM loader (by the firmware, over
    /// native USB): talk to it without a reset - a reset would leave the loader.
    pub fn attach_to_loader(&mut self) -> Result<(), FlashError> {
        self.stub = false;
        self.frames.clear();
        self.dec = Decoder::default();
        Ok(())
    }

    /// One of the few named register writes ([`SafeWrite`]).
    pub fn safe_write(&mut self, w: SafeWrite) -> Result<(), FlashError> {
        self.command(Op::WriteReg, &w.payload(), 0, 0, "clear a boot flag", 1000)?;
        Ok(())
    }

    /// Leave a ROM loader entered through "force download" (native USB): clear the
    /// flag, then reset - or the board would come straight back to the loader.
    pub fn leave_forced_download(&mut self) -> Result<(), FlashError> {
        self.safe_write(SafeWrite::ClearForceDownload)?;
        self.hard_reset()
    }

    pub fn sync(&mut self) -> Result<(), FlashError> {
        let mut data = vec![0x07, 0x07, 0x12, 0x20];
        data.extend([0x55u8; 32]);
        for _ in 0..7 {
            if self.command(Op::Sync, &data, 0, 0, "sync", 200).is_ok() {
                // the ROM answers a sync several times; drain them
                while self.next_frame(100).is_ok() {}
                return Ok(());
            }
        }
        Err(FlashError::Timeout("sync"))
    }

    pub fn chip_id(&mut self) -> Result<u32, FlashError> {
        let r = self.command(Op::GetSecurityInfo, &[], 0, 20, "read security info", 1000)?;
        Ok(u32::from_le_bytes(r.data[12..16].try_into().unwrap()))
    }

    pub fn read_reg(&mut self, addr: u32) -> Result<u32, FlashError> {
        Ok(self.command(Op::ReadReg, &addr.to_le_bytes(), 0, 0, "read a register", 1000)?.value)
    }

    /// The factory MAC from eFuse BLOCK1 - read, never written.
    pub fn mac(&mut self) -> Result<[u8; 6], FlashError> {
        let m0 = self.read_reg(MAC_EFUSE_REG)?;
        let m1 = self.read_reg(MAC_EFUSE_REG + 4)?;
        Ok([(m1 >> 8) as u8, m1 as u8, (m0 >> 24) as u8, (m0 >> 16) as u8, (m0 >> 8) as u8, m0 as u8])
    }

    pub fn load_stub(&mut self, stub: &Stub) -> Result<(), FlashError> {
        for (addr, bytes) in [(stub.text_start, &stub.text), (stub.data_start, &stub.data)] {
            let blocks = bytes.len().div_ceil(RAM_BLOCK);
            let begin = [bytes.len() as u32, blocks as u32, RAM_BLOCK as u32, addr];
            self.command(Op::MemBegin, &le(&begin), 0, 0, "load the stub", 1000)?;
            for (seq, chunk) in bytes.chunks(RAM_BLOCK).enumerate() {
                let mut d = le(&[chunk.len() as u32, seq as u32, 0, 0]);
                d.extend(chunk);
                self.command(Op::MemData, &d, proto::checksum(chunk), 0, "load the stub", 1000)?;
            }
        }
        self.send(Op::MemEnd, &le(&[0, stub.entry]), 0)?;
        for _ in 0..20 {
            if let Ok(f) = self.next_frame(200) {
                if f == b"OHAI" {
                    self.stub = true;
                    return Ok(());
                }
            }
        }
        Err(FlashError::Timeout("the stub to start"))
    }

    pub fn attach_flash(&mut self, size: u32) -> Result<(), FlashError> {
        self.command(Op::SpiAttach, &le(&[0]), 0, 0, "attach the flash", 1000)?;
        let p = [0, size, 0x10000, SECTOR, 0x100, 0xFFFF];
        self.command(Op::SpiSetParams, &le(&p), 0, 0, "set the flash size", 1000)?;
        Ok(())
    }

    // ---- reading and checking -----------------------------------------------------------

    pub fn md5(&mut self, offset: u32, len: u32) -> Result<[u8; 16], FlashError> {
        let expect = if self.stub { 16 } else { 32 };
        let r = self.command(Op::SpiFlashMd5, &le(&[offset, len, 0, 0]), 0, expect, "hash flash", 30_000)?;
        let mut out = [0u8; 16];
        if self.stub {
            out.copy_from_slice(&r.data[..16]);
        } else {
            let hex = std::str::from_utf8(&r.data[..32]).map_err(|_| FlashError::Check("md5 not hex".into()))?;
            for i in 0..16 {
                out[i] = u8::from_str_radix(&hex[2 * i..2 * i + 2], 16).map_err(|_| FlashError::Check("md5".into()))?;
            }
        }
        Ok(out)
    }

    /// Reads flash through the stub (the S3's ROM cannot), checking the stub's own MD5.
    pub fn read_flash(&mut self, offset: u32, len: u32) -> Result<Vec<u8>, FlashError> {
        if !self.stub {
            return Err(FlashError::Check("reading flash needs the stub".into()));
        }
        self.command(Op::ReadFlash, &le(&[offset, len, SECTOR, 64]), 0, 0, "read flash", 2000)?;
        let mut data = Vec::with_capacity(len as usize);
        while data.len() < len as usize {
            let f = self.next_frame(3000)?;
            data.extend(&f);
            if data.len() < len as usize && f.len() < SECTOR as usize {
                return Err(FlashError::Check("short block while reading flash".into()));
            }
            self.send_raw(&(data.len() as u32).to_le_bytes())?;
        }
        let digest = self.next_frame(3000)?;
        if digest.len() != 16 || digest.as_slice() != Md5::digest(&data).as_slice() {
            return Err(FlashError::Check("the data read back does not match its MD5".into()));
        }
        data.truncate(len as usize);
        Ok(data)
    }

    // ---- writing --------------------------------------------------------------------------

    /// Writes `data` at a sector-aligned `offset` (padded with 0xFF to whole
    /// sectors, which is what erased flash holds), then reads the MD5 back.
    /// `progress` gets the bytes written so far and answers whether to go on.
    pub fn write(&mut self, offset: u32, data: &[u8], progress: &mut dyn FnMut(usize) -> bool) -> Result<(), FlashError> {
        if !offset.is_multiple_of(SECTOR) {
            return Err(FlashError::Check(format!("unaligned write at {offset:#x}")));
        }
        // Whole sectors, then whole blocks: every block written lies inside the
        // region erased, so no write touches flash outside it.
        let sectors = data.len().div_ceil(SECTOR as usize) * SECTOR as usize;
        let block = if self.stub { STUB_BLOCK.min(sectors) } else { 0x400 };
        let mut padded = data.to_vec();
        padded.resize(sectors.div_ceil(block) * block, 0xFF);
        let blocks = padded.len() / block;
        let mut begin = le(&[padded.len() as u32, blocks as u32, block as u32, offset]);
        if !self.stub {
            begin.extend(0u32.to_le_bytes()); // the S3 ROM wants "not encrypted"
        }
        self.command(Op::FlashBegin, &begin, 0, 0, "start writing", 30_000)?;
        for (seq, chunk) in padded.chunks(block).enumerate() {
            let mut d = le(&[chunk.len() as u32, seq as u32, 0, 0]);
            d.extend(chunk);
            self.command(Op::FlashData, &d, proto::checksum(chunk), 0, "write", 10_000)?;
            if !progress(((seq + 1) * block).min(padded.len())) {
                return Err(FlashError::Cancelled);
            }
        }
        let got = self.md5(offset, padded.len() as u32)?;
        if got.as_slice() != Md5::digest(&padded).as_slice() {
            return Err(FlashError::Check(format!("what was written at {offset:#x} does not read back")));
        }
        Ok(())
    }
}

fn le(words: &[u32]) -> Vec<u8> {
    words.iter().flat_map(|w| w.to_le_bytes()).collect()
}
