//! What is on a board's flash: the partition table, `otadata` (which slot
//! starts), and app images (is it a HID-Fi image, which version, which chip).
//! Read-only parsing; the same rules as the bootloader and tests/support/otadata.py.

use md5::{Digest, Md5};

pub const TABLE_OFFSET: u32 = 0x8000;
pub const TABLE_LEN: usize = 0xC00;
pub const OTADATA_SECTOR: usize = 0x1000;
pub const ESP32S3_CHIP_ID: u16 = 9;
/// HID-Fi's version string must sit in an image's first 4 KB (a build test
/// guards it): one small read tells a HID-Fi image and its version.
pub const ID_WINDOW: usize = 0x1000;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Partition {
    pub name: String,
    pub ptype: u8,
    pub subtype: u8,
    pub offset: u32,
    pub size: u32,
}

/// Parses a partition table (0x8000). None when it is not a valid table - an
/// erased sector, garbage, or a bad MD5 entry.
pub fn parse_table(data: &[u8]) -> Option<Vec<Partition>> {
    let mut out = Vec::new();
    for i in 0..(data.len() / 32) {
        let e = &data[i * 32..i * 32 + 32];
        match (e[0], e[1]) {
            (0xAA, 0x50) => {
                let name = String::from_utf8_lossy(&e[12..28])
                    .trim_end_matches('\0')
                    .to_string();
                out.push(Partition {
                    name,
                    ptype: e[2],
                    subtype: e[3],
                    offset: u32::from_le_bytes(e[4..8].try_into().ok()?),
                    size: u32::from_le_bytes(e[8..12].try_into().ok()?),
                });
            }
            (0xEB, 0xEB) => {
                let want = &e[16..32];
                let got = Md5::digest(&data[..i * 32]);
                return (got.as_slice() == want && !out.is_empty()).then_some(out);
            }
            (0xFF, 0xFF) => return (!out.is_empty()).then_some(out),
            _ => return None,
        }
    }
    None
}

pub fn app_slots(table: &[Partition]) -> Vec<&Partition> {
    table
        .iter()
        .filter(|p| p.ptype == 0 && (0x10..0x20).contains(&p.subtype))
        .collect()
}

// ---- otadata ---------------------------------------------------------------------

pub const NEW: u32 = 0x0;
pub const PENDING_VERIFY: u32 = 0x1;
pub const VALID: u32 = 0x2;
pub const INVALID: u32 = 0x3;
pub const ABORTED: u32 = 0x4;
pub const UNDEFINED: u32 = 0xFFFF_FFFF;

/// CRC-32 (IEEE) of the sequence number, seeded 0xFFFFFFFF - the bootloader's
/// `bootloader_common_ota_select_crc` (checked against the core's boot_app0.bin).
pub fn otadata_crc(seq: u32) -> u32 {
    // zlib.crc32(seq, 0xFFFFFFFF): the seed is inverted on entry, so the register starts at 0.
    let mut crc = 0u32;
    for b in seq.to_le_bytes() {
        crc ^= b as u32;
        for _ in 0..8 {
            crc = if crc & 1 != 0 {
                (crc >> 1) ^ 0xEDB8_8320
            } else {
                crc >> 1
            };
        }
    }
    !crc
}

pub fn otadata_entry(seq: u32, state: u32) -> [u8; 32] {
    let mut e = [0xFFu8; 32];
    e[0..4].copy_from_slice(&seq.to_le_bytes());
    e[24..28].copy_from_slice(&state.to_le_bytes());
    e[28..32].copy_from_slice(&otadata_crc(seq).to_le_bytes());
    e
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Choice {
    pub sector: usize,
    pub seq: u32,
    pub slot: usize,
    pub state: u32,
}

/// The slot the bootloader would start from this otadata (2 sectors). None: no
/// valid entry - the bootloader starts the first app slot.
pub fn otadata_choice(data: &[u8], slots: usize) -> Option<Choice> {
    if slots == 0 || data.len() < 2 * OTADATA_SECTOR {
        return None; // never a panic on what a board holds (a half-written table has no slots)
    }
    let mut best: Option<Choice> = None;
    for sector in 0..2 {
        let e = &data[sector * OTADATA_SECTOR..sector * OTADATA_SECTOR + 32];
        let seq = u32::from_le_bytes(e[0..4].try_into().ok()?);
        let state = u32::from_le_bytes(e[24..28].try_into().ok()?);
        let crc = u32::from_le_bytes(e[28..32].try_into().ok()?);
        if seq == 0xFFFF_FFFF || crc != otadata_crc(seq) || state == INVALID || state == ABORTED {
            continue;
        }
        if best.is_none_or(|b| seq > b.seq) {
            best = Some(Choice {
                sector,
                seq,
                slot: (seq.wrapping_sub(1) as usize) % slots,
                state,
            });
        }
    }
    best
}

/// A new otadata that starts `slot`, written into the sector that does NOT hold
/// the current choice - the switch is atomic: until that sector is complete the
/// old choice still wins.
pub fn otadata_switch(data: &[u8], slot: usize, state: u32, slots: usize) -> Vec<u8> {
    let slots = slots.max(1);
    let cur = otadata_choice(data, slots);
    let mut seq = cur.map_or(0, |c| c.seq) + 1;
    while (seq.wrapping_sub(1) as usize) % slots != slot {
        seq += 1;
    }
    let other = cur.map_or(0, |c| 1 - c.sector);
    let mut out = data.to_vec();
    out[other * OTADATA_SECTOR..(other + 1) * OTADATA_SECTOR].fill(0xFF);
    out[other * OTADATA_SECTOR..other * OTADATA_SECTOR + 32]
        .copy_from_slice(&otadata_entry(seq, state));
    out
}

// ---- app images --------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImageId {
    pub chip_id: u16,
    /// "hid_fi_v4.0.4" - None when the image is not HID-Fi's
    pub hidfi_version: Option<String>,
}

/// Identifies an app image from its first bytes (at least the header; the
/// HID-Fi version string needs the first 4 KB).
pub fn identify(head: &[u8]) -> Option<ImageId> {
    if head.len() < 24 || head[0] != 0xE9 {
        return None;
    }
    let chip_id = u16::from_le_bytes([head[12], head[13]]);
    let window = &head[..head.len().min(ID_WINDOW)];
    let hidfi_version = window.windows(8).position(|w| w == b"hid_fi_v").map(|i| {
        let rest = &window[i..];
        let end = rest
            .iter()
            .position(|&b| b == 0 || !b.is_ascii_graphic())
            .unwrap_or(rest.len());
        String::from_utf8_lossy(&rest[..end]).to_string()
    });
    Some(ImageId {
        chip_id,
        hidfi_version,
    })
}

/// Light validity check of a whole image, as the bootloader does it: magic,
/// segment headers inside the image, and the appended SHA-256 when present.
pub fn image_is_valid(img: &[u8]) -> bool {
    use sha2::{Digest as _, Sha256};
    if img.len() < 24 || img[0] != 0xE9 {
        return false;
    }
    let segments = img[1] as usize;
    let hash_appended = img[23] == 1;
    let mut pos = 24usize;
    for _ in 0..segments {
        if pos + 8 > img.len() {
            return false;
        }
        let len = u32::from_le_bytes(img[pos + 4..pos + 8].try_into().unwrap()) as usize;
        pos += 8 + len;
        if pos > img.len() {
            return false;
        }
    }
    // checksum byte, padded to 16
    pos = (pos + 16) & !15;
    if !hash_appended {
        return pos <= img.len();
    }
    if pos + 32 > img.len() {
        return false;
    }
    Sha256::digest(&img[..pos]).as_slice() == &img[pos..pos + 32]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn boot_app0() -> Vec<u8> {
        // seq 1, state undefined, in sector 0 - what every cable flash writes at 0xe000
        let mut d = vec![0xFFu8; 2 * OTADATA_SECTOR];
        d[..32].copy_from_slice(&otadata_entry(1, UNDEFINED));
        d
    }

    #[test]
    fn crc_matches_the_core_file() {
        assert_eq!(otadata_crc(1), 0x4743_989A, "boot_app0.bin's entry");
        // zlib.crc32(struct.pack('<I', seq), 0xFFFFFFFF)
        assert_eq!(otadata_crc(0), 0xFFFF_FFFF);
        assert_eq!(otadata_crc(2), 0x55F6_3774);
        assert_eq!(otadata_crc(28), 0xE530_17D8);
        assert_eq!(otadata_crc(0x7FFF_FFFF), 0xCCFC_5C3C);
    }

    #[test]
    fn switching_keeps_the_old_choice_until_the_new_sector_is_whole() {
        let d = boot_app0();
        assert_eq!(otadata_choice(&d, 2).map(|c| (c.slot, c.seq)), Some((0, 1)));
        let n = otadata_switch(&d, 1, NEW, 2);
        assert_eq!(&n[..OTADATA_SECTOR], &d[..OTADATA_SECTOR]);
        assert_eq!(
            otadata_choice(&n, 2).map(|c| (c.slot, c.state)),
            Some((1, NEW))
        );
        let mut torn = n.clone();
        torn[OTADATA_SECTOR + 28] = 0xFF; // power cut before the CRC landed
        assert_eq!(otadata_choice(&torn, 2).map(|c| c.slot), Some(0));
        for (state, want) in [(ABORTED, 0), (INVALID, 0), (PENDING_VERIFY, 1), (VALID, 1)] {
            assert_eq!(
                otadata_choice(&otadata_switch(&d, 1, state, 2), 2).map(|c| c.slot),
                Some(want)
            );
        }
    }

    #[test]
    fn identify_reads_chip_and_hidfi_version() {
        let mut img = vec![0u8; 0x400];
        img[0] = 0xE9;
        img[12] = 9;
        img[0x2ED..0x2ED + 14].copy_from_slice(b"hid_fi_v4.0.4\0");
        assert_eq!(
            identify(&img),
            Some(ImageId {
                chip_id: 9,
                hidfi_version: Some("hid_fi_v4.0.4".into())
            })
        );
        img[0x2ED] = b'X';
        assert_eq!(
            identify(&img).unwrap().hidfi_version,
            None,
            "someone else's firmware"
        );
        assert_eq!(identify(&[0xFF; 64]), None, "erased");
    }

    pub(crate) fn tiny_image(tag: &[u8]) -> Vec<u8> {
        use sha2::{Digest as _, Sha256};
        let mut body = vec![0u8; 24];
        body[0] = 0xE9;
        body[1] = 1;
        body[12] = 9;
        body[23] = 1;
        let seg: Vec<u8> = [b"hid_fi_v".as_slice(), tag, b"\0"].concat();
        let mut seg = seg;
        seg.resize(64, 0);
        body.extend(0x3C00_0000u32.to_le_bytes());
        body.extend((seg.len() as u32).to_le_bytes());
        body.extend(&seg);
        let pos = (body.len() + 16) & !15;
        body.resize(pos, 0);
        let h = Sha256::digest(&body);
        body.extend_from_slice(&h);
        body
    }

    #[test]
    fn validity_follows_the_appended_hash() {
        let img = tiny_image(b"4.1.0");
        assert!(image_is_valid(&img));
        let mut bad = img.clone();
        bad[40] ^= 1;
        assert!(!image_is_valid(&bad));
        assert!(!image_is_valid(&img[..img.len() - 1]));
        assert!(!image_is_valid(&[0xFF; 128]));
    }

    #[test]
    fn a_table_needs_its_md5() {
        let mut t = vec![0xFFu8; TABLE_LEN];
        let mut e = [0u8; 32];
        e[0] = 0xAA;
        e[1] = 0x50;
        e[4..8].copy_from_slice(&0x9000u32.to_le_bytes());
        e[8..12].copy_from_slice(&0x5000u32.to_le_bytes());
        e[12..15].copy_from_slice(b"nvs");
        t[..32].copy_from_slice(&e);
        let mut m = [0xFFu8; 32];
        m[0] = 0xEB;
        m[1] = 0xEB;
        m[16..32].copy_from_slice(&Md5::digest(&t[..32]));
        t[32..64].copy_from_slice(&m);
        assert_eq!(parse_table(&t).unwrap()[0].name, "nvs");
        t[5] ^= 1;
        assert_eq!(parse_table(&t), None, "a changed entry fails the MD5");
    }
}
