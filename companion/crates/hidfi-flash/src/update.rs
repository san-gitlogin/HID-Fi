//! Updating a board over the cable, step by step, with a journal (docs/OTA.md,
//! "Over the cable: the journal").
//!
//! Two paths:
//! - **two slots** (the board already has hid_fi/partitions.csv): the image goes
//!   into the slot that is NOT running, then one otadata sector chooses it.
//!   The running firmware is never touched - exactly like an update over the air.
//! - **the one-time switch** from the old single-slot layout: the image goes to
//!   0x650000 (outside every partition of the old table), then otadata, then -
//!   the one risky write - the 3 KB partition table. The old firmware stays in
//!   app0, so even the switch rolls back by itself if the new firmware fails.
//!
//! Every step is written to the journal before it starts and after it ends;
//! every step has a cancel and a resume. Settings (NVS, 0x9000-0xDFFF) are
//! never written, and a backup of 0x0-0xFFFF is read before anything else.

use std::path::Path;

use md5::{Digest, Md5};
use serde::{Deserialize, Serialize};

use crate::flasher::{FlashError, Flasher, SECTOR};
use crate::image::{self, Partition};
use crate::port::Port;
use crate::stub::Stub;

pub const FLASH_SIZE: u32 = 16 * 1024 * 1024;
pub const NVS: (u32, u32) = (0x9000, 0x5000);
const OTADATA_AT: u32 = 0xE000;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum UpdateError {
    #[error(transparent)]
    Flash(#[from] FlashError),
    #[error("not an ESP32-S3 (chip id {0}) - nothing was written")]
    WrongChip(u32),
    #[error("this is not the board you chose (MAC {0}) - nothing was written")]
    WrongBoard(String),
    #[error("this board runs firmware that is not HID-Fi - refusing to replace it")]
    ForeignFirmware,
    #[error("the board's flash layout is not one HID-Fi made - refusing to write")]
    UnknownLayout,
    #[error("the new firmware does not fit the slot")]
    TooBig,
    #[error("the release is not a HID-Fi image for this chip")]
    BadRelease,
    #[error("cancelled")]
    Cancelled,
    #[error("the settings area read back different - the backup was written back; nothing else was changed after it")]
    SettingsRestored,
}

/// How the flasher reaches the ROM loader.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Connect {
    /// The COM port (CH343): the auto-reset lines enter the loader, and the EN pulse leaves it.
    Reset,
    /// The native USB port, after the firmware restarted into the loader on the
    /// Companion's request (compOta "download_mode"): no reset to enter; leaving
    /// clears "force download" first.
    InLoader,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Path2 {
    TwoSlot,
    SwitchLayout,
}

/// The checkpoints. Each is recorded before and after it runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum Step {
    Planned,
    Preflighted,
    ImageWritten,
    Switched,
    TableWritten,
    Restarted,
    Cancelled,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Journal {
    pub mac: String,
    pub to: String,
    pub path: Path2,
    pub step: Step,
    pub target_slot: usize,
    pub target_offset: u32,
    pub image_md5: String,
    pub image_len: u32,
    /// The otadata as found, to put back exactly on cancel.
    pub original_otadata: String,
    /// The table sector as found (switch path), to put back exactly on cancel.
    pub original_table: String,
    pub history: Vec<String>,
}

impl Journal {
    /// Written to a new file, flushed, then renamed over the old one: a crash
    /// leaves either the old journal or the new one, never half of one.
    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        use std::io::Write;
        let tmp = path.with_extension("tmp");
        let mut f = std::fs::File::create(&tmp)?;
        f.write_all(serde_json::to_string_pretty(self).unwrap().as_bytes())?;
        f.sync_all()?;
        std::fs::rename(&tmp, path)
    }

    pub fn load(path: &Path) -> Option<Journal> {
        serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
    }
}

pub struct Release<'a> {
    pub image: &'a [u8],
    /// partitions.bin of the two-slot layout (for the one-time switch)
    pub table: &'a [u8],
}

#[derive(Debug, Clone)]
pub struct Found {
    pub backup: Vec<u8>,
    pub table: Option<Vec<Partition>>,
    pub running_slot: usize,
    pub running_version: Option<String>,
    pub layout: Option<Path2>,
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn unhex(s: &str) -> Vec<u8> {
    (0..s.len() / 2)
        .map(|i| u8::from_str_radix(&s[2 * i..2 * i + 2], 16).unwrap_or(0))
        .collect()
}

fn mac_str(m: &[u8; 6]) -> String {
    m.iter()
        .map(|b| format!("{b:02X}"))
        .collect::<Vec<_>>()
        .join(":")
}

fn layout_of(table: &[Partition]) -> Option<Path2> {
    let nvs = table.iter().find(|p| p.name == "nvs")?;
    if (nvs.offset, nvs.size) != NVS {
        return None; // settings elsewhere: not a layout HID-Fi made
    }
    match image::app_slots(table).as_slice() {
        [a] if a.offset == 0x10000 => Some(Path2::SwitchLayout),
        [a, b] if a.offset == 0x10000 && b.size == a.size => Some(Path2::TwoSlot),
        _ => None,
    }
}

/// Step C1: is this the chosen board, an ESP32-S3, running HID-Fi, on a layout
/// HID-Fi made? Reads a backup of 0x0-0xFFFF. Writes nothing.
pub fn preflight<P: Port>(
    f: &mut Flasher<P>,
    mac: &[u8; 6],
    connect: Connect,
) -> Result<Found, UpdateError> {
    match connect {
        Connect::Reset => f.enter_bootloader()?,
        Connect::InLoader => f.attach_to_loader()?,
    }
    f.sync()?;
    let chip = f.chip_id()?;
    if chip != image::ESP32S3_CHIP_ID as u32 {
        return Err(UpdateError::WrongChip(chip));
    }
    let got = f.mac()?;
    if &got != mac {
        return Err(UpdateError::WrongBoard(mac_str(&got)));
    }
    f.load_stub(&Stub::esp32s3().map_err(FlashError::Check)?)?;
    f.attach_flash(FLASH_SIZE)?;
    let backup = f.read_flash(0, 0x10000)?;
    let table = image::parse_table(&backup[0x8000..0x8000 + image::TABLE_LEN]);
    let layout = table.as_deref().and_then(layout_of);
    let (mut running_slot, mut running_version) = (0, None);
    if let Some(t) = &table {
        let slots = image::app_slots(t);
        running_slot = image::otadata_choice(&backup[OTADATA_AT as usize..], slots.len())
            .map_or(0, |c| c.slot);
        if let Some(s) = slots.get(running_slot) {
            let head = f.read_flash(s.offset, image::ID_WINDOW as u32)?;
            running_version = image::identify(&head).and_then(|i| i.hidfi_version);
        }
    }
    Ok(Found {
        backup,
        table,
        running_slot,
        running_version,
        layout,
    })
}

pub struct Progress<'a> {
    pub cancel: &'a dyn Fn() -> bool,
    pub on: &'a mut dyn FnMut(Step, usize, usize),
}

/// Runs (or resumes, when `journal_path` holds an unfinished journal for this
/// board) a cable update. Returns once the board has been restarted into the new
/// firmware; whether it proves itself is the firmware's self-test.
pub fn run<P: Port>(
    f: &mut Flasher<P>,
    mac: &[u8; 6],
    connect: Connect,
    rel: &Release,
    journal_path: &Path,
    p: Progress,
) -> Result<Journal, UpdateError> {
    let id = image::identify(rel.image).ok_or(UpdateError::BadRelease)?;
    let to = id.hidfi_version.clone().ok_or(UpdateError::BadRelease)?;
    if id.chip_id != image::ESP32S3_CHIP_ID || !image::image_is_valid(rel.image) {
        return Err(UpdateError::BadRelease);
    }
    let new_table = image::parse_table(rel.table).ok_or(UpdateError::BadRelease)?;
    if layout_of(&new_table) != Some(Path2::TwoSlot) {
        return Err(UpdateError::BadRelease);
    }
    let found = preflight(f, mac, connect)?;
    let leave = |f: &mut Flasher<P>| -> Result<(), FlashError> {
        match connect {
            Connect::Reset => f.hard_reset(),
            Connect::InLoader => f.leave_forced_download(),
        }
    };
    let resume = Journal::load(journal_path)
        .filter(|j| j.mac == mac_str(mac) && j.to == to && j.step < Step::Restarted);

    let mut j = match resume {
        Some(j) => j,
        None => {
            let path = found.layout.ok_or(UpdateError::UnknownLayout)?;
            if found.running_version.is_none() {
                return Err(UpdateError::ForeignFirmware);
            }
            let (slot, offset, room) = match path {
                Path2::TwoSlot => {
                    let t = found.table.as_ref().unwrap();
                    let s = image::app_slots(t)[1 - found.running_slot.min(1)].clone();
                    (1 - found.running_slot.min(1), s.offset, s.size)
                }
                Path2::SwitchLayout => {
                    let s = image::app_slots(&new_table)[1].clone();
                    (1, s.offset, s.size)
                }
            };
            if rel.image.len().div_ceil(0x4000) * 0x4000 > room as usize {
                return Err(UpdateError::TooBig);
            }
            Journal {
                mac: mac_str(mac),
                to: to.clone(),
                path,
                step: Step::Planned,
                target_slot: slot,
                target_offset: offset,
                image_md5: hex(&Md5::digest(rel.image)),
                image_len: rel.image.len() as u32,
                original_otadata: hex(&found.backup[OTADATA_AT as usize..]),
                original_table: hex(&found.backup[0x8000..0x9000]),
                history: Vec::new(),
            }
        }
    };
    let save = |j: &mut Journal, step: Step, note: &str| -> Result<(), UpdateError> {
        j.step = step;
        j.history.push(format!("{step:?}: {note}"));
        j.save(journal_path)
            .map_err(|e| UpdateError::Flash(FlashError::Port(e.to_string())))
    };
    if j.step == Step::Planned {
        save(
            &mut j,
            Step::Preflighted,
            "the chosen board, an ESP32-S3 running HID-Fi; backup read",
        )?;
    }
    let cancel = |f: &mut Flasher<P>, j: &mut Journal| -> UpdateError {
        // Put back exactly what was there, newest change first.
        if j.step >= Step::TableWritten {
            let _ = f.write(0x8000, &unhex(&j.original_table), &mut |_| true);
        }
        if j.step >= Step::Switched {
            let _ = f.write(OTADATA_AT, &unhex(&j.original_otadata), &mut |_| true);
        }
        let _ = leave(f);
        let _ = save(j, Step::Cancelled, "everything put back as found");
        UpdateError::Cancelled
    };

    // C2: the image, into a slot that is not running.
    if j.step < Step::ImageWritten {
        let already = f.md5(j.target_offset, j.image_len)?;
        if hex(&already) != j.image_md5 {
            if (p.cancel)() {
                return Err(cancel(f, &mut j));
            }
            let total = rel.image.len();
            let on = &mut *p.on;
            let r = f.write(j.target_offset, rel.image, &mut |n| {
                on(Step::ImageWritten, n, total);
                !(p.cancel)()
            });
            match r {
                Err(FlashError::Cancelled) => return Err(cancel(f, &mut j)),
                r => r?,
            }
        }
        save(&mut j, Step::ImageWritten, "image written and read back")?;
    }
    if (p.cancel)() {
        return Err(cancel(f, &mut j));
    }
    // C3: choose it - one otadata sector, the one not holding the current choice.
    if j.step < Step::Switched {
        let now = f.read_flash(OTADATA_AT, 0x2000)?;
        let want = image::otadata_switch(&unhex(&j.original_otadata), j.target_slot, image::NEW, 2);
        if image::otadata_choice(&now, 2).map(|c| c.slot) != Some(j.target_slot) || now != want {
            let sector = (0..2)
                .find(|s| now[s * 0x1000..(s + 1) * 0x1000] != want[s * 0x1000..(s + 1) * 0x1000]);
            if let Some(s) = sector {
                f.write(
                    OTADATA_AT + s as u32 * SECTOR,
                    &want[s * 0x1000..(s + 1) * 0x1000],
                    &mut |_| true,
                )?;
            }
        }
        save(&mut j, Step::Switched, "otadata chooses the new slot")?;
    }
    if (p.cancel)() {
        return Err(cancel(f, &mut j));
    }
    // S4 (the one-time switch only): the table - the last write.
    if j.path == Path2::SwitchLayout && j.step < Step::TableWritten {
        // If the board restarted since the switch, the OLD firmware ran - and a
        // release before the update work marks the running choice VALID at start.
        // The new firmware would then start trusted, without its self-test. So the
        // choice must still be NEW here; if not, choose it again, freshly.
        let od = f.read_flash(OTADATA_AT, 0x2000)?;
        if image::otadata_choice(&od, 2).map(|c| c.state) != Some(image::NEW) {
            let again = image::otadata_switch(&od, j.target_slot, image::NEW, 2);
            if let Some(s) = (0..2)
                .find(|s| od[s * 0x1000..(s + 1) * 0x1000] != again[s * 0x1000..(s + 1) * 0x1000])
            {
                f.write(
                    OTADATA_AT + s as u32 * SECTOR,
                    &again[s * 0x1000..(s + 1) * 0x1000],
                    &mut |_| true,
                )?;
            }
        }
        let now = f.read_flash(0x8000, image::TABLE_LEN as u32)?;
        if now != rel.table[..image::TABLE_LEN.min(rel.table.len())] {
            f.write(0x8000, rel.table, &mut |_| true)?;
        }
        save(&mut j, Step::TableWritten, "the two-slot table is in")?;
    }
    if (p.cancel)() {
        return Err(cancel(f, &mut j));
    }
    // The new slot must still be chosen, untried (NEW). If the board restarted since
    // the switch - an unplug, a power cut - the new firmware has had its probation
    // start, and the reset this resume made to reach the loader cut it short: the
    // bootloader would now count it as failed. Choose it again, freshly (found by
    // the unplug sweep).
    let od = f.read_flash(OTADATA_AT, 0x2000)?;
    let ch = image::otadata_choice(&od, 2);
    if ch.map(|c| (c.slot, c.state)) != Some((j.target_slot, image::NEW)) {
        let again = image::otadata_switch(&od, j.target_slot, image::NEW, 2);
        if let Some(s) =
            (0..2).find(|s| od[s * 0x1000..(s + 1) * 0x1000] != again[s * 0x1000..(s + 1) * 0x1000])
        {
            f.write(
                OTADATA_AT + s as u32 * SECTOR,
                &again[s * 0x1000..(s + 1) * 0x1000],
                &mut |_| true,
            )?;
        }
    }
    // The settings must be exactly as read in preflight. No path writes there (the
    // sweep proves it); if a byte differs all the same, the backup - held only in
    // memory, never on disk: it holds the vault - goes straight back, and we stop.
    let nvs_at = NVS.0 as usize;
    let nvs_now = f.read_flash(NVS.0, NVS.1)?;
    let nvs_was = &found.backup[nvs_at..nvs_at + NVS.1 as usize];
    if nvs_now != nvs_was {
        f.write(NVS.0, nvs_was, &mut |_| true)?;
        let _ = leave(f);
        return Err(UpdateError::SettingsRestored);
    }
    // C4: start it. The port stays open: the firmware proves itself undisturbed.
    leave(f)?;
    save(&mut j, Step::Restarted, "restarted into the new firmware")?;
    Ok(j)
}
