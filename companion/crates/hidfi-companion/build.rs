//! Windows only: the .exe's icon and its version information (Properties, Details;
//! Task Manager; the taskbar), as a compiled resource file the linker takes as it is.
//! Without them Windows showed a blank, generic icon for the running app, which
//! looked like something that should not be trusted.
//!
//! The .res format is written here directly (Microsoft's "Resource File Formats" and
//! "VS_VERSIONINFO" documentation), so the build needs no resource compiler: Rust's
//! lld-link on a Mac and MSVC's link.exe in CI both accept a .res file as input.
//! The icon is assets/app.ico (package/make-icons.py); it is icon 1, and the app
//! loads its window and taskbar icons from it (desktop.rs).
use std::{env, fs, path::PathBuf};

const PUBLISHER: &str = "HID-Fi";
const PRODUCT: &str = "HID-Fi Companion";
const DESCRIPTION: &str = "HID-Fi Companion";
const COPYRIGHT: &str = "Copyright (c) 2026 Santhosh \u{2014} HID-Fi"; // LICENSE.md, Required Notice
const EN_US: u16 = 0x0409;

fn main() {
    println!("cargo:rerun-if-changed=assets/app.ico");
    println!("cargo:rerun-if-changed=build.rs");
    if env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }
    let ico = fs::read("assets/app.ico").expect("assets/app.ico (package/make-icons.py)");
    let version = env::var("CARGO_PKG_VERSION").unwrap();
    let res = resource_file(&ico, &version);
    let out = PathBuf::from(env::var("OUT_DIR").unwrap()).join("hidfi-companion.res");
    fs::write(&out, res).unwrap();
    // Bins only: the app and its examples' own .exe files are what Windows shows.
    println!("cargo:rustc-link-arg-bins={}", out.display());
    println!("cargo:rustc-link-arg-examples={}", out.display());
}

/// The whole .res file: the empty entry every .res starts with, each image of the
/// icon (RT_ICON), the icon group that lists them (RT_GROUP_ICON, id 1), and the
/// version information (RT_VERSION, id 1).
pub fn resource_file(ico: &[u8], version: &str) -> Vec<u8> {
    let images = ico_images(ico);
    let mut res = Vec::new();
    entry(&mut res, 0, 0, 0, 0, &[]);
    let mut group = vec![0, 0, 1, 0];
    group.extend((images.len() as u16).to_le_bytes());
    for (i, img) in images.iter().enumerate() {
        let id = i as u16 + 1;
        entry(&mut res, 3, id, 0x1010, EN_US, img.data);
        group.extend_from_slice(&img.dir[..12]); // width .. bytes in resource, as in the .ico
        group.extend(id.to_le_bytes()); // in the .exe, an id replaces the file offset
    }
    entry(&mut res, 14, 1, 0x1030, EN_US, &group);
    entry(&mut res, 16, 1, 0x0030, EN_US, &version_info(version));
    res
}

struct IcoImage<'a> {
    dir: &'a [u8], // the 16-byte ICONDIRENTRY
    data: &'a [u8],
}

fn ico_images(ico: &[u8]) -> Vec<IcoImage<'_>> {
    let u16_at = |o: usize| u16::from_le_bytes([ico[o], ico[o + 1]]) as usize;
    let u32_at = |o: usize| u32::from_le_bytes(ico[o..o + 4].try_into().unwrap()) as usize;
    assert!(
        ico.len() > 6 && u16_at(0) == 0 && u16_at(2) == 1,
        "app.ico is not an icon file"
    );
    (0..u16_at(4))
        .map(|i| {
            let d = 6 + 16 * i;
            let (len, at) = (u32_at(d + 8), u32_at(d + 12));
            IcoImage {
                dir: &ico[d..d + 16],
                data: &ico[at..at + len],
            }
        })
        .collect()
}

/// One RESOURCEHEADER (numeric type and name) and its data, padded to 4 bytes.
fn entry(res: &mut Vec<u8>, kind: u16, name: u16, flags: u16, lang: u16, data: &[u8]) {
    res.extend((data.len() as u32).to_le_bytes());
    res.extend(32u32.to_le_bytes()); // header size
    res.extend([0xFF, 0xFF]);
    res.extend(kind.to_le_bytes());
    res.extend([0xFF, 0xFF]);
    res.extend(name.to_le_bytes());
    res.extend(0u32.to_le_bytes()); // data version
    res.extend(flags.to_le_bytes());
    res.extend(lang.to_le_bytes());
    res.extend(0u32.to_le_bytes()); // version
    res.extend(0u32.to_le_bytes()); // characteristics
    res.extend_from_slice(data);
    pad4(res);
}

fn pad4(b: &mut Vec<u8>) {
    while !b.len().is_multiple_of(4) {
        b.push(0);
    }
}

fn wide(s: &str) -> Vec<u8> {
    s.encode_utf16()
        .chain([0])
        .flat_map(u16::to_le_bytes)
        .collect()
}

/// A version block: wLength, wValueLength, wType, szKey, padding, Value, padding,
/// children (each on a 4-byte boundary). wValueLength counts WORDs for text values.
fn block(key: &str, value: &[u8], value_len: u16, text: bool, children: &[Vec<u8>]) -> Vec<u8> {
    let mut b = vec![0, 0];
    b.extend(value_len.to_le_bytes());
    b.extend((text as u16).to_le_bytes());
    b.extend(wide(key));
    pad4(&mut b);
    b.extend_from_slice(value);
    for c in children {
        pad4(&mut b);
        b.extend_from_slice(c);
    }
    let n = b.len() as u16;
    b[..2].copy_from_slice(&n.to_le_bytes());
    b
}

fn version_numbers(version: &str) -> [u16; 4] {
    let mut n = [0u16; 4];
    for (i, p) in version.split(['.', '-', '+']).take(3).enumerate() {
        n[i] = p.parse().unwrap_or(0);
    }
    n
}

fn version_info(version: &str) -> Vec<u8> {
    let [a, b, c, d] = version_numbers(version);
    let (ms, ls) = ((a as u32) << 16 | b as u32, (c as u32) << 16 | d as u32);
    let mut fixed = Vec::new();
    for v in [
        0xFEEF04BDu32,
        0x0001_0000,
        ms,
        ls,
        ms,
        ls,
        0x3F,
        0,
        0x0004_0004,
        1,
        0,
        0,
        0,
    ] {
        fixed.extend(v.to_le_bytes());
    }
    let strings: Vec<Vec<u8>> = [
        ("CompanyName", PUBLISHER),
        ("FileDescription", DESCRIPTION),
        ("FileVersion", version),
        ("InternalName", "hidfi-companion"),
        ("LegalCopyright", COPYRIGHT),
        ("OriginalFilename", "hidfi-companion.exe"),
        ("ProductName", PRODUCT),
        ("ProductVersion", version),
    ]
    .iter()
    .map(|(k, v)| {
        let w = wide(v);
        block(k, &w, (w.len() / 2) as u16, true, &[])
    })
    .collect();
    let table = block("040904B0", &[], 0, true, &strings); // en-US, Unicode
    let sfi = block("StringFileInfo", &[], 0, true, &[table]);
    let translation = [0x09, 0x04, 0xB0, 0x04]; // 0x0409, code page 1200
    let var = block("Translation", &translation, 4, false, &[]);
    let vfi = block("VarFileInfo", &[], 0, true, &[var]);
    block(
        "VS_VERSION_INFO",
        &fixed,
        fixed.len() as u16,
        false,
        &[sfi, vfi],
    )
}
