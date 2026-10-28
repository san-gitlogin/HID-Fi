//! The fault sweep: every cable update, run against the simulated ESP32-S3 with a
//! fault at every point - power lost after every 64th byte written, the cable
//! pulled after every frame, one bit flipped in every block, a cancel at every
//! step. After each one the board must:
//!
//! - start a working HID-Fi (the old one, or the new one on probation), or be in
//!   the one state the journal knows how to finish (the table half-written during
//!   the one-time switch - the ROM loader still answers, nothing is lost);
//! - have its settings (NVS) byte-identical;
//! - finish when the update is resumed from the journal;
//! - never have been sent an opcode that could burn an eFuse or wipe it.
//!
//! Enumerated, not imagined (docs/OTA.md, "Every level").

use std::cell::Cell;
use std::path::PathBuf;

use md5::{Digest, Md5};
use sha2::{Digest as _, Sha256};

use crate::flasher::Flasher;
use crate::image;
use crate::proto;
use crate::sim::{Boot, Faults, Sim, FLASH};
use crate::update::{self, Journal, Path2, Progress, Release, Step, UpdateError};

const MAC: [u8; 6] = [0xB8, 0x1F, 0x3F, 0xDA, 0x1A, 0x2C];
const OLD: &str = "hid_fi_v4.0.4";
const NEW: &str = "hid_fi_v4.1.0";

fn table(rows: &[(&str, u8, u8, u32, u32)]) -> Vec<u8> {
    let mut t = vec![0xFFu8; image::TABLE_LEN];
    for (i, (name, ty, sub, off, size)) in rows.iter().enumerate() {
        let e = &mut t[i * 32..i * 32 + 32];
        e[0] = 0xAA;
        e[1] = 0x50;
        e[2] = *ty;
        e[3] = *sub;
        e[4..8].copy_from_slice(&off.to_le_bytes());
        e[8..12].copy_from_slice(&size.to_le_bytes());
        e[12..28].fill(0);
        e[12..12 + name.len()].copy_from_slice(name.as_bytes());
        e[28..32].fill(0);
    }
    let n = rows.len() * 32;
    let digest = Md5::digest(&t[..n]);
    t[n..n + 32].fill(0xFF);
    t[n] = 0xEB;
    t[n + 1] = 0xEB;
    t[n + 16..n + 32].copy_from_slice(&digest);
    t
}

fn single_slot() -> Vec<u8> {
    table(&[("nvs", 1, 2, 0x9000, 0x5000), ("otadata", 1, 0, 0xE000, 0x2000), ("app0", 0, 0x10, 0x10000, 0x30_0000),
            ("spiffs", 1, 0x82, 0x31_0000, 0xE_0000), ("coredump", 1, 3, 0x3F_0000, 0x1_0000)])
}

fn two_slot() -> Vec<u8> {
    table(&[("nvs", 1, 2, 0x9000, 0x5000), ("otadata", 1, 0, 0xE000, 0x2000), ("app0", 0, 0x10, 0x10000, 0x64_0000),
            ("app1", 0, 0x11, 0x65_0000, 0x64_0000), ("nvs_ext", 1, 2, 0xC9_0000, 0x4_0000),
            ("spare", 1, 6, 0xCD_0000, 0x32_0000), ("coredump", 1, 3, 0xFF_0000, 0x1_0000)])
}

/// A valid app image carrying "hid_fi_v<x>", `size` bytes or so.
fn app(version: &str, size: usize) -> Vec<u8> {
    let mut img = vec![0u8; 24];
    img[0] = 0xE9;
    img[1] = 1;
    img[12] = 9;
    img[23] = 1;
    let mut seg = format!("{version}\0").into_bytes();
    let mut x = 7u32;
    while seg.len() < size {
        x = x.wrapping_mul(1_103_515_245).wrapping_add(12345);
        seg.push((x >> 16) as u8);
    }
    seg.resize(seg.len().div_ceil(4) * 4, 0);
    img.extend(0x3C00_0020u32.to_le_bytes());
    img.extend((seg.len() as u32).to_le_bytes());
    img.extend(&seg);
    let pos = (img.len() + 16) & !15;
    img.resize(pos, 0);
    let h = Sha256::digest(&img);
    img.extend_from_slice(&h);
    img
}

fn board(two: bool) -> Vec<u8> {
    let mut f = vec![0xFFu8; FLASH];
    f[0] = 0xE9; // a bootloader
    for (i, b) in f[1..0x4000].iter_mut().enumerate() {
        *b = (i * 31 % 251) as u8;
    }
    let t = if two { two_slot() } else { single_slot() };
    f[0x8000..0x8000 + t.len()].copy_from_slice(&t);
    for (i, b) in f[0x9000..0xE000].iter_mut().enumerate() {
        *b = (i * 7 % 253) as u8 ^ 0x5A; // somebody's settings
    }
    f[0xE000..0xE020].copy_from_slice(&image::otadata_entry(1, image::UNDEFINED)); // boot_app0
    let a = app(OLD, 20_000);
    f[0x10000..0x10000 + a.len()].copy_from_slice(&a);
    f
}

/// A two-slot board that already updated once: it runs app1 (otadata seq 2, valid).
fn board_on_app1() -> Vec<u8> {
    let mut f = board(true);
    let a = app(OLD, 20_000);
    f[0x65_0000..0x65_0000 + a.len()].copy_from_slice(&a);
    f[0x10000..0x10000 + 0x6000].fill(0xFF); // app0 holds nothing usable any more
    f[0xF000..0xF020].copy_from_slice(&image::otadata_entry(2, image::VALID));
    f
}

fn sim_on_app1() -> Sim {
    let mut s = Sim::new(board_on_app1(), MAC);
    s.valid_at_boot.push(OLD.to_string());
    s
}

fn sim(two: bool) -> Sim {
    let mut s = Sim::new(board(two), MAC);
    s.valid_at_boot.push(OLD.to_string()); // v4.0.4 marks itself valid at start
    s
}

struct Run {
    rel_img: Vec<u8>,
    rel_table: Vec<u8>,
    journal: PathBuf,
}

impl Run {
    fn new(tag: &str) -> Run {
        // unique per run: the tests run in parallel threads
        static N: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let n = N.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("hidfi-sweep-{}-{tag}-{n}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let journal = dir.join("journal.json");
        let _ = std::fs::remove_file(&journal);
        Run { rel_img: app(NEW, 40_000), rel_table: two_slot(), journal }
    }

    fn go(&self, s: Sim, cancel_at: Option<Step>) -> (Sim, Result<Journal, UpdateError>) {
        let mut f = Flasher::new(s);
        let hit = Cell::new(false);
        let cancel = || hit.get();
        let mut on = |step: Step, _n: usize, _t: usize| {
            if Some(step) == cancel_at {
                hit.set(true);
            }
        };
        let rel = Release { image: &self.rel_img, table: &self.rel_table };
        // Cancels "between steps" too: the journal records each, so we cancel once it moved past `cancel_at`.
        let jp = self.journal.clone();
        let cancel2 = || cancel() || (cancel_at.is_some() && Journal::load(&jp).is_some_and(|j| Some(j.step) >= cancel_at));
        let r = update::run(&mut f, &MAC, &rel, &self.journal, Progress { cancel: &cancel2, on: &mut on });
        (f.port, r)
    }
}

fn nvs(s: &Sim) -> &[u8] {
    &s.flash[0x9000..0xE000]
}

fn no_forbidden(s: &Sim) {
    for (code, why) in proto::FORBIDDEN {
        assert!(!s.seen.contains(code), "{why} was sent");
    }
}

/// The start the update's own restart caused (the sim boots on the reset).
fn starts_new_on_probation(s: &mut Sim) {
    match s.boots.last().cloned().expect("the update restarts the board") {
        Boot::App { version, pending, .. } => {
            assert_eq!(version.as_deref(), Some(NEW));
            assert!(pending, "the new firmware must start on probation - its self-test decides");
        }
        b => panic!("did not start: {b:?}"),
    }
}

#[test]
fn two_slots_end_to_end() {
    let r = Run::new("two");
    let s = sim(true);
    let before = s.flash.clone();
    let (mut s, res) = r.go(s, None);
    let j = res.unwrap();
    assert_eq!((j.path, j.step, j.target_slot), (Path2::TwoSlot, Step::Restarted, 1));
    assert_eq!(nvs(&s), &before[0x9000..0xE000]);
    assert_eq!(&s.flash[0x10000..0x10000 + 0x4000], &before[0x10000..0x10000 + 0x4000], "the running slot is never written");
    assert_eq!(&s.flash[0x8000..0x9000], &before[0x8000..0x9000], "the table is not touched on two slots");
    starts_new_on_probation(&mut s);
    no_forbidden(&s);
}

#[test]
fn the_one_time_switch_end_to_end() {
    let r = Run::new("switch");
    let s = sim(false);
    let before = s.flash.clone();
    let (mut s, res) = r.go(s, None);
    let j = res.unwrap();
    assert_eq!((j.path, j.target_offset), (Path2::SwitchLayout, 0x65_0000));
    assert_eq!(nvs(&s), &before[0x9000..0xE000]);
    assert_eq!(&s.flash[0x8000..0x8000 + image::TABLE_LEN], &r.rel_table[..]);
    assert_eq!(&s.flash[0x10000..0x10000 + 0x4000], &before[0x10000..0x10000 + 0x4000], "the old firmware stays in app0");
    starts_new_on_probation(&mut s);
    // and if it is reset before it proved itself, the old one is there to go back to
    assert!(matches!(s.boot(), Boot::App { slot: 0, ref version, .. } if version.as_deref() == Some(OLD)));
    no_forbidden(&s);
}

#[test]
fn nothing_is_written_to_a_board_that_is_not_the_one_chosen() {
    for (tag, faults, expect) in [
        ("chip", Faults { chip_id: Some(5), ..Default::default() }, "chip"),
        ("mac", Faults { mac: Some([1, 2, 3, 4, 5, 6]), ..Default::default() }, "board"),
    ] {
        let r = Run::new(tag);
        let mut s = sim(true);
        s.faults = faults;
        let before = s.flash.clone();
        let (s, res) = r.go(s, None);
        let e = res.unwrap_err();
        assert!(format!("{e:?}").to_lowercase().contains(expect), "{e:?}");
        assert!(s.flash == before, "{tag}: flash changed");
    }
}

#[test]
fn someone_elses_firmware_or_layout_is_refused() {
    let r = Run::new("foreign");
    let mut s = sim(true);
    let other = app("other_project_v1", 20_000);
    s.flash[0x10000..0x10000 + 0x6000].fill(0xFF);
    s.flash[0x10000..0x10000 + other.len()].copy_from_slice(&other);
    let before = s.flash.clone();
    let (s, res) = r.go(s, None);
    assert_eq!(res.unwrap_err(), UpdateError::ForeignFirmware);
    assert!(s.flash == before);

    let r = Run::new("layout");
    let mut s = sim(true);
    let t = table(&[("nvs", 1, 2, 0xA000, 0x4000), ("otadata", 1, 0, 0xE000, 0x2000), ("app0", 0, 0x10, 0x10000, 0x30_0000)]);
    s.flash[0x8000..0x9000].fill(0xFF);
    s.flash[0x8000..0x8000 + t.len()].copy_from_slice(&t);
    let before = s.flash.clone();
    let (s, res) = r.go(s, None);
    assert_eq!(res.unwrap_err(), UpdateError::UnknownLayout, "settings elsewhere: not a layout HID-Fi made");
    assert!(s.flash == before);
}

fn bytes_written(two: bool) -> usize {
    let r = Run::new(&format!("count{two}"));
    let (s, res) = r.go(sim(two), None);
    res.unwrap();
    s.written_total()
}

/// The only time a fault may leave a board that does not start: the one-time
/// switch, while its 3 KB partition table is being written (the bootloader reads
/// one table, at one place - this window cannot be designed away). The ROM loader
/// is in mask ROM and still answers; the journal knows, and resuming finishes it.
fn in_the_table_window(two: bool, j: &Option<Journal>, what: &str) {
    assert!(!two, "{what}: a two-slot update must never leave a board that does not start");
    let step = j.as_ref().map(|j| j.step);
    assert!(step == Some(Step::Switched), "{what}: outside the table write (journal at {step:?})");
}

fn sweep_power(two: bool) {
    let total = bytes_written(two);
    let mut cut = 0;
    let mut cases = 0;
    while cut < total {
        let tag = format!("cut{two}{cut}");
        let r = Run::new(&tag);
        let mut s = sim(two);
        let before = s.flash.clone();
        s.faults.power_cut_after_bytes = Some(cut);
        let (mut s, res) = r.go(s, None);
        assert!(res.is_err(), "cut at {cut}: the update cannot have finished");
        s.reconnect();
        assert_eq!(nvs(&s), &before[0x9000..0xE000], "cut at {cut}: settings changed");
        let j = Journal::load(&r.journal);
        match s.boots.last().unwrap() {
            Boot::App { version, .. } => {
                assert!(version.as_deref() == Some(OLD) || version.as_deref() == Some(NEW), "cut at {cut}: {version:?}")
            }
            Boot::Brick(why) => in_the_table_window(two, &j, &format!("cut at {cut}: {why}")),
        }
        // Resume: it must finish, settings still intact.
        let (mut s, res) = r.go(s, None);
        res.unwrap_or_else(|e| panic!("cut at {cut}: resume failed: {e}"));
        assert_eq!(nvs(&s), &before[0x9000..0xE000]);
        starts_new_on_probation(&mut s);
        no_forbidden(&s);
        cases += 1;
        cut += 64;
    }
    assert!(cases > 20, "the sweep must cover the whole update ({cases} cases)");
}

#[test]
fn a_board_running_its_second_slot_updates_into_the_first_and_survives_every_cut() {
    let total = {
        let r = Run::new("app1count");
        let (mut s, res) = r.go(sim_on_app1(), None);
        assert_eq!(res.unwrap().target_slot, 0, "the slot not running");
        starts_new_on_probation(&mut s);
        s.written_total()
    };
    let mut cut = 0;
    while cut < total {
        let r = Run::new(&format!("app1cut{cut}"));
        let mut s = sim_on_app1();
        let before = s.flash.clone();
        s.faults.power_cut_after_bytes = Some(cut);
        let (mut s, res) = r.go(s, None);
        assert!(res.is_err());
        s.reconnect();
        assert_eq!(nvs(&s), &before[0x9000..0xE000]);
        match s.boots.last().unwrap() {
            Boot::App { version, .. } => assert!(matches!(version.as_deref(), Some(OLD) | Some(NEW)), "cut {cut}"),
            b => panic!("cut at {cut}: {b:?} - the running choice must survive a torn otadata write"),
        }
        let (mut s, res) = r.go(s, None);
        res.unwrap();
        starts_new_on_probation(&mut s);
        cut += 64;
    }
}

#[test]
fn a_power_cut_anywhere_on_two_slots_loses_nothing() {
    sweep_power(true);
}

#[test]
fn a_power_cut_anywhere_in_the_one_time_switch_loses_nothing() {
    sweep_power(false);
}

fn frames(two: bool) -> usize {
    let r = Run::new(&format!("frames{two}"));
    let (s, res) = r.go(sim(two), None);
    res.unwrap();
    s.seen.len()
}

fn sweep_unplug(two: bool) {
    let n = frames(two);
    for k in 0..n {
        let r = Run::new(&format!("unplug{two}{k}"));
        let mut s = sim(two);
        let before = s.flash.clone();
        s.faults.unplug_after_frames = Some(k);
        let (mut s, res) = r.go(s, None);
        assert!(res.is_err(), "unplug after {k}");
        s.reconnect();
        assert_eq!(nvs(&s), &before[0x9000..0xE000], "unplug after frame {k}");
        if let Some(Boot::Brick(why)) = s.boots.last() {
            in_the_table_window(two, &Journal::load(&r.journal), &format!("unplug after frame {k}: {why}"));
        }
        let (mut s, res) = r.go(s, None);
        res.unwrap_or_else(|e| panic!("unplug after frame {k}: resume failed: {e}"));
        starts_new_on_probation(&mut s);
    }
}

#[test]
fn the_cable_pulled_after_any_frame_on_two_slots() {
    sweep_unplug(true);
}

#[test]
fn the_cable_pulled_after_any_frame_in_the_one_time_switch() {
    sweep_unplug(false);
}

#[test]
fn a_flipped_bit_is_caught_by_the_read_back_and_nothing_switches() {
    // Every block a clean update writes, flipped in turn: each must stop it.
    let blocks = {
        let r = Run::new("flipcount");
        let (s, res) = r.go(sim(true), None);
        res.unwrap();
        s.seen.iter().filter(|o| **o == proto::Op::FlashData as u8).count()
    };
    assert!(blocks >= 3);
    for k in 0..blocks {
        let r = Run::new(&format!("flip{k}"));
        let mut s = sim(true);
        s.faults.flip_bit_in_block = Some(k);
        let before = s.flash.clone();
        let (mut s, res) = r.go(s, None);
        assert!(res.is_err(), "flip in block {k} went unnoticed - every write must be read back");
        assert_eq!(nvs(&s), &before[0x9000..0xE000]);
        // What starts is the old firmware, or the new one exactly as released -
        // never a corrupted image (a flip in otadata's padding can leave a valid
        // choice of the already-verified new image; the read-back still stops it).
        match s.boot() {
            Boot::App { version, slot, .. } if version.as_deref() == Some(NEW) => {
                let at = if slot == 1 { 0x65_0000 } else { 0x10000 };
                assert_eq!(&s.flash[at..at + r.rel_img.len()], &r.rel_img[..], "flip {k}: a damaged image started");
            }
            Boot::App { version, .. } => assert_eq!(version.as_deref(), Some(OLD), "flip {k}"),
            b => panic!("flip {k}: {b:?}"),
        }
        let (mut s, res) = r.go(s, None);
        res.unwrap();
        starts_new_on_probation(&mut s);
    }
}

#[test]
fn cancel_at_every_step_puts_everything_back() {
    for two in [true, false] {
        for step in [Step::Preflighted, Step::ImageWritten, Step::Switched, Step::TableWritten] {
            if two && step == Step::TableWritten {
                continue;
            }
            let r = Run::new(&format!("cancel{two}{step:?}"));
            let s = sim(two);
            let before = s.flash.clone();
            let (mut s, res) = r.go(s, Some(step));
            assert!(matches!(res, Err(UpdateError::Cancelled)), "{two} {step:?}: {:?}", res.map(|j| j.step));
            assert_eq!(&s.flash[0x8000..0x10000], &before[0x8000..0x10000],
                       "{two} {step:?}: table, settings and otadata exactly as found");
            assert!(matches!(s.boot(), Boot::App { slot: 0, ref version, pending: false } if version.as_deref() == Some(OLD)),
                    "{two} {step:?}: the old firmware starts, trusted");
            assert_eq!(Journal::load(&r.journal).unwrap().step, Step::Cancelled);
        }
    }
}

#[test]
fn a_restart_between_the_switch_and_the_table_does_not_skip_the_self_test() {
    // The one-time switch: the cable is pulled after otadata was written but before
    // the table; the board restarts on the OLD firmware, which (being v4.0.4) marks
    // the new choice valid. Resuming must still make the new firmware prove itself.
    let table_before = sim(false).flash[0x8000..0x9000].to_vec();
    let n = frames(false);
    let found = (0..n).find_map(|k| {
        let r = Run::new(&format!("gap{k}"));
        let mut s = sim(false);
        s.faults.unplug_after_frames = Some(k);
        let (s, _) = r.go(s, None);
        let j = Journal::load(&r.journal)?;
        (j.step == Step::Switched && s.flash[0x8000..0x9000] == table_before[..]).then_some((r, s))
    });
    let (r, mut s) = found.expect("an unplug lands between the switch and the table");
    s.reconnect();
    assert!(matches!(s.boots.last(), Some(Boot::App { slot: 0, pending: false, .. })), "{:?}", s.boots.last());
    let (mut s, res) = r.go(s, None);
    res.unwrap();
    starts_new_on_probation(&mut s);
}
