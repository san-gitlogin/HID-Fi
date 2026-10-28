//! A simulated ESP32-S3 on the other end of the cable, for testing the flasher and
//! the update journal against every fault at every step - without a board, and
//! without risking anyone's.
//!
//! - NOR flash: 16 MB of bytes; erasing sets a 4 KB sector to 0xFF, writing can
//!   only clear bits (a write without an erase corrupts, as on the real chip).
//! - The ROM loader and the stub, as far as HID-Fi uses them (proto::Op); any
//!   other opcode is refused, and recorded, so a test can prove it never came.
//! - eFuses: a MAC and a chip id, read-only - there is no way to write them here
//!   either.
//! - The bootloader's choice ([`Sim::boot`]): table, otadata, NEW -> PENDING_VERIFY,
//!   PENDING_VERIFY at the next start -> ABORTED, invalid images skipped.
//! - Faults: lose power after N bytes written, an unplug after N frames, one bit
//!   flipped in a write, a different chip or MAC.

use std::io;

use md5::{Digest, Md5};

use crate::image::{self, Partition};
use crate::port::Port;
use crate::proto::{self, Op};
use crate::slip::{self, Decoder};

pub const FLASH: usize = 16 * 1024 * 1024;

#[derive(Debug, Clone, Default)]
pub struct Faults {
    /// Power is lost once this many bytes have been written to flash in total
    /// (the write in progress lands only partly). The board is dead until reconnect().
    pub power_cut_after_bytes: Option<usize>,
    /// The cable is pulled after this many request frames.
    pub unplug_after_frames: Option<usize>,
    /// The Nth FLASH_DATA block (0-based, counted over the session) has one bit flipped.
    pub flip_bit_in_block: Option<usize>,
    pub chip_id: Option<u32>,
    pub mac: Option<[u8; 6]>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Boot {
    /// The firmware in this slot runs; `pending` while it must still prove itself.
    App { slot: usize, version: Option<String>, pending: bool },
    /// No valid bootloader, table or app - but the ROM loader is in mask ROM and
    /// still answers over the cable: recoverable.
    Brick(&'static str),
}

pub struct Sim {
    pub flash: Vec<u8>,
    pub faults: Faults,
    pub mac: [u8; 6],
    /// Every opcode that reached the chip, refused or not - a test proves the
    /// forbidden ones never came.
    pub seen: Vec<u8>,
    pub boots: Vec<Boot>,
    /// Firmware versions that mark themselves valid the moment they start - every
    /// release before the update work (the core does it unless verifyRollbackLater()).
    pub valid_at_boot: Vec<String>,
    /// Every register write that reached the chip: (address, value, mask).
    pub reg_writes: Vec<(u32, u32, u32)>,
    /// RTC_CNTL_OPTION1 FORCE_DOWNLOAD_BOOT: set, every reset lands in the ROM loader.
    pub force_download: bool,
    /// Test-only fault: something outside the flasher changes one settings byte
    /// during the update (it happens at the first image MD5 check).
    pub corrupt_nvs_on_next_md5: bool,
    dec: Decoder,
    out: Vec<u8>,
    stub: bool,
    in_download: bool,
    dead: bool,
    written_total: usize,
    frames: usize,
    flash_blocks: usize,
    write_at: u32,
    write_end: u32,
    mem: Vec<u8>,
    dtr: bool,
    rts: bool,
    pending_read: Option<(u32, u32)>,
    read_origin: usize,
}

impl Sim {
    pub fn new(flash: Vec<u8>, mac: [u8; 6]) -> Sim {
        assert_eq!(flash.len(), FLASH);
        Sim {
            flash, faults: Faults::default(), mac, seen: Vec::new(), boots: Vec::new(), valid_at_boot: Vec::new(),
            reg_writes: Vec::new(), force_download: false, corrupt_nvs_on_next_md5: false,
            dec: Decoder::default(),
            out: Vec::new(), stub: false, in_download: false, dead: false, written_total: 0, frames: 0,
            flash_blocks: 0, write_at: 0, write_end: 0, mem: Vec::new(), dtr: false, rts: false, pending_read: None,
            read_origin: 0,
        }
    }

    /// Bytes written to flash over the session (what a power-cut position counts).
    pub fn written_total(&self) -> usize {
        self.written_total
    }

    pub fn is_dead(&self) -> bool {
        self.dead
    }

    /// The firmware restarted itself into the ROM loader (compOta "download_mode").
    pub fn enter_forced_download(&mut self) {
        self.force_download = true;
        self.in_download = true;
        self.stub = false;
        self.out.clear();
    }

    /// Power back / cable back: the chip starts its firmware (as after a replug).
    pub fn reconnect(&mut self) {
        self.dead = false;
        self.in_download = false;
        self.stub = false;
        self.faults.power_cut_after_bytes = None;
        self.faults.unplug_after_frames = None;
        let b = self.boot();
        self.boots.push(b);
    }

    fn table(&self) -> Option<Vec<Partition>> {
        image::parse_table(&self.flash[image::TABLE_OFFSET as usize..image::TABLE_OFFSET as usize + image::TABLE_LEN])
    }

    fn image_len(&self, off: usize, max: usize) -> Option<usize> {
        let h = &self.flash[off..];
        if h[0] != 0xE9 {
            return None;
        }
        let mut pos = 24usize;
        for _ in 0..h[1] {
            let len = u32::from_le_bytes(h.get(pos + 4..pos + 8)?.try_into().ok()?) as usize;
            pos += 8 + len;
            if pos > max {
                return None;
            }
        }
        pos = (pos + 16) & !15;
        if h[23] == 1 {
            pos += 32;
        }
        (pos <= max).then_some(pos)
    }

    /// What the bootloader does at power-on, including what it writes to otadata.
    pub fn boot(&mut self) -> Boot {
        if self.flash[0] != 0xE9 {
            return Boot::Brick("no bootloader");
        }
        let Some(table) = self.table() else { return Boot::Brick("no partition table") };
        let slots: Vec<Partition> = image::app_slots(&table).into_iter().cloned().collect();
        if slots.is_empty() {
            return Boot::Brick("no app partition");
        }
        let ota = table.iter().find(|p| p.ptype == 1 && p.subtype == 0).cloned();
        let mut choice = None;
        if let Some(o) = &ota {
            let at = o.offset as usize;
            let mut od = self.flash[at..at + 2 * image::OTADATA_SECTOR].to_vec();
            if let Some(c) = image::otadata_choice(&od, slots.len()) {
                let mut state = c.state;
                if state == image::PENDING_VERIFY {
                    // It started before and never proved itself: give up on it.
                    write_state(&mut od, c.sector, image::ABORTED);
                    self.flash[at..at + od.len()].copy_from_slice(&od);
                    choice = image::otadata_choice(&od, slots.len()).map(|c| (c.slot, c.state));
                    if choice.is_none() {
                        choice = Some((0, image::UNDEFINED));
                    }
                    state = choice.unwrap().1;
                } else {
                    choice = Some((c.slot, state));
                }
                if state == image::NEW {
                    let c2 = image::otadata_choice(&od, slots.len()).unwrap();
                    write_state(&mut od, c2.sector, image::PENDING_VERIFY);
                    self.flash[at..at + od.len()].copy_from_slice(&od);
                    choice = Some((c2.slot, image::PENDING_VERIFY));
                }
            }
        }
        let (want, state) = choice.unwrap_or((0, image::UNDEFINED));
        let order: Vec<usize> = std::iter::once(want).chain((0..slots.len()).filter(|s| *s != want)).collect();
        for s in order {
            let p = &slots[s];
            let (off, max) = (p.offset as usize, p.size as usize);
            if let Some(len) = self.image_len(off, max) {
                if image::image_is_valid(&self.flash[off..off + len]) {
                    let version = image::identify(&self.flash[off..off + image::ID_WINDOW.min(len)])
                        .and_then(|i| i.hidfi_version);
                    let mut pending = s == want && state == image::PENDING_VERIFY;
                    if pending && version.as_ref().is_some_and(|v| self.valid_at_boot.contains(v)) {
                        self.mark_valid();
                        pending = false;
                    }
                    return Boot::App { slot: s, version, pending };
                }
            }
        }
        Boot::Brick("no valid app")
    }

    /// The new firmware passed its self-test (what esp_ota_mark_app_valid_cancel_rollback does).
    pub fn mark_valid(&mut self) {
        let Some(table) = self.table() else { return };
        let Some(o) = table.iter().find(|p| p.ptype == 1 && p.subtype == 0).cloned() else { return };
        let at = o.offset as usize;
        let mut od = self.flash[at..at + 2 * image::OTADATA_SECTOR].to_vec();
        if let Some(c) = image::otadata_choice(&od, image::app_slots(&table).len()) {
            write_state(&mut od, c.sector, image::VALID);
            self.flash[at..at + od.len()].copy_from_slice(&od);
        }
    }

    // ---- the wire ----------------------------------------------------------------------

    fn reply(&mut self, op: u8, value: u32, data: &[u8], ok: bool) {
        let mut d = data.to_vec();
        d.extend(if ok { [0u8, 0] } else { [1u8, 5] });
        if !self.stub {
            d.extend([0u8, 0]); // the ROM sends two more status bytes
        }
        let mut f = vec![0x01, op];
        f.extend((d.len() as u16).to_le_bytes());
        f.extend(value.to_le_bytes());
        f.extend(d);
        self.out.extend(slip::encode(&f));
    }

    fn write_flash(&mut self, at: u32, data: &[u8]) {
        let mut data = data.to_vec();
        if let Some(n) = self.faults.flip_bit_in_block {
            if n == self.flash_blocks {
                let mid = data.len() / 2;
                data[mid] ^= 0x10;
            }
        }
        self.flash_blocks += 1;
        let mut take = data.len();
        if let Some(cut) = self.faults.power_cut_after_bytes {
            if self.written_total + take > cut {
                take = cut.saturating_sub(self.written_total);
                self.dead = true;
            }
        }
        for (i, b) in data[..take].iter().enumerate() {
            let a = at as usize + i;
            if a < FLASH {
                self.flash[a] &= *b; // NOR: a write only clears bits
            }
        }
        self.written_total += take;
    }

    fn erase(&mut self, from: u32, len: u32) {
        let start = (from as usize) & !0xFFF;
        let end = ((from + len) as usize + 0xFFF) & !0xFFF;
        for a in (start..end.min(FLASH)).step_by(0x1000) {
            if self.dead {
                return;
            }
            self.flash[a..a + 0x1000].fill(0xFF);
            if let Some(cut) = self.faults.power_cut_after_bytes {
                if self.written_total >= cut {
                    self.dead = true;
                }
            }
        }
    }

    fn handle(&mut self, frame: &[u8]) {
        // Acks while streaming a flash read are bare u32 frames.
        if let Some((at, left)) = self.pending_read {
            if frame.len() == 4 {
                self.stream_read(at, left);
                return;
            }
        }
        let Some(req) = proto::parse_request(frame) else { return };
        self.frames += 1;
        self.seen.push(req.op);
        if let Some(n) = self.faults.unplug_after_frames {
            if self.frames > n {
                self.dead = true;
                return;
            }
        }
        if !self.in_download {
            return; // running its firmware: the loader is not listening
        }
        let w = |i: usize| u32::from_le_bytes(req.data.get(i * 4..i * 4 + 4).unwrap_or(&[0; 4]).try_into().unwrap());
        let Some(op) = Op::from_u8(req.op) else {
            self.reply(req.op, 0, &[], false); // e.g. WRITE_REG: refused, and recorded in `seen`
            return;
        };
        match op {
            Op::Sync => {
                for _ in 0..3 {
                    self.reply(op as u8, 0x2010_0707, &[], true);
                }
            }
            Op::GetSecurityInfo => {
                let mut d = vec![0u8; 20];
                d[12..16].copy_from_slice(&self.faults.chip_id.unwrap_or(9).to_le_bytes());
                self.reply(op as u8, 0, &d, true);
            }
            Op::ReadReg => {
                let m = self.faults.mac.unwrap_or(self.mac);
                let v = match w(0) {
                    0x6000_7044 => u32::from_be_bytes([m[2], m[3], m[4], m[5]]),
                    0x6000_7048 => u32::from_be_bytes([0, 0, m[0], m[1]]),
                    _ => 0,
                };
                self.reply(op as u8, v, &[], true);
            }
            Op::MemBegin => {
                self.mem.clear();
                self.reply(op as u8, 0, &[], true);
            }
            Op::MemData => {
                let ok = proto::checksum(&req.data[16..]) == req.chk;
                self.mem.extend(&req.data[16..]);
                self.reply(op as u8, 0, &[], ok);
            }
            Op::MemEnd => {
                self.reply(op as u8, 0, &[], true);
                self.stub = true;
                self.out.extend(slip::encode(b"OHAI"));
            }
            Op::SpiAttach | Op::SpiSetParams | Op::ChangeBaudrate | Op::FlashEnd => self.reply(op as u8, 0, &[], true),
            Op::WriteReg => {
                let (addr, value, mask) = (w(0), w(1), w(2));
                self.reg_writes.push((addr, value, mask));
                if addr == proto::RTC_CNTL_OPTION1_REG && mask & 1 != 0 {
                    self.force_download = value & 1 != 0;
                }
                self.reply(op as u8, 0, &[], true);
            }
            Op::FlashBegin => {
                let (size, at) = (w(0), w(3));
                self.erase(at, size);
                self.write_at = at;
                self.write_end = at + size;
                if !self.dead {
                    self.reply(op as u8, 0, &[], true);
                }
            }
            Op::FlashData => {
                let len = w(0) as usize;
                let data = req.data[16..16 + len].to_vec();
                let ok = proto::checksum(&data) == req.chk && self.write_at + len as u32 <= self.write_end;
                if ok {
                    let at = self.write_at;
                    self.write_flash(at, &data);
                    self.write_at += len as u32;
                }
                if !self.dead {
                    self.reply(op as u8, 0, &[], ok);
                }
            }
            Op::SpiFlashMd5 => {
                if self.corrupt_nvs_on_next_md5 {
                    self.corrupt_nvs_on_next_md5 = false;
                    self.flash[0x9100] ^= 0x01;
                }
                let (at, len) = (w(0) as usize, w(1) as usize);
                let d = Md5::digest(&self.flash[at..at + len]);
                if self.stub {
                    self.reply(op as u8, 0, &d, true);
                } else {
                    let hex: String = d.iter().map(|b| format!("{b:02x}")).collect();
                    self.reply(op as u8, 0, hex.as_bytes(), true);
                }
            }
            Op::ReadFlash => {
                if !self.stub {
                    self.reply(op as u8, 0, &[], false); // the S3 ROM cannot read flash
                    return;
                }
                self.reply(op as u8, 0, &[], true);
                self.read_origin = w(0) as usize;
                self.pending_read = Some((w(0), w(1)));
                self.stream_read(w(0), w(1));
            }
        }
    }

    fn stream_read(&mut self, at: u32, left: u32) {
        if left == 0 {
            return;
        }
        let n = left.min(0x1000);
        let chunk = self.flash[at as usize..(at + n) as usize].to_vec();
        self.out.extend(slip::encode(&chunk));
        let (nat, nleft) = (at + n, left - n);
        if nleft == 0 {
            // the stub ends with the MD5 of everything it sent
            let start = self.read_origin;
            self.out.extend(slip::encode(&Md5::digest(&self.flash[start..nat as usize])));
            self.pending_read = None;
        } else {
            self.pending_read = Some((nat, nleft));
        }
    }
}

fn write_state(od: &mut [u8], sector: usize, state: u32) {
    let at = sector * image::OTADATA_SECTOR + 24;
    od[at..at + 4].copy_from_slice(&state.to_le_bytes());
}

impl Port for Sim {
    fn write_all(&mut self, data: &[u8]) -> io::Result<()> {
        if self.dead {
            return Err(io::Error::new(io::ErrorKind::BrokenPipe, "the board is unplugged"));
        }
        let mut frames = Vec::new();
        for &b in data {
            self.dec.push(b, &mut frames);
        }
        for f in frames {
            self.handle(&f);
        }
        Ok(())
    }

    fn read(&mut self, buf: &mut [u8], _timeout_ms: u32) -> io::Result<usize> {
        if self.dead {
            return Err(io::Error::new(io::ErrorKind::BrokenPipe, "the board is unplugged"));
        }
        let n = buf.len().min(self.out.len());
        buf[..n].copy_from_slice(&self.out[..n]);
        self.out.drain(..n);
        Ok(n)
    }

    fn set_dtr(&mut self, on: bool) -> io::Result<()> {
        self.dtr = on;
        Ok(())
    }

    fn set_rts(&mut self, on: bool) -> io::Result<()> {
        // EN follows RTS: releasing it restarts the chip. GPIO0 (DTR) low at that
        // moment means the ROM loader; high means the firmware.
        if self.rts && !on {
            self.stub = false;
            self.out.clear();
            self.pending_read = None;
            if self.dtr || self.force_download {
                self.in_download = true;      // GPIO0 low, or forced: the ROM loader again
            } else {
                self.in_download = false;
                let b = self.boot();
                self.boots.push(b);
            }
        }
        self.rts = on;
        Ok(())
    }

    fn set_baud(&mut self, _baud: u32) -> io::Result<()> {
        Ok(())
    }

    fn sleep_ms(&mut self, _ms: u32) {}
}
