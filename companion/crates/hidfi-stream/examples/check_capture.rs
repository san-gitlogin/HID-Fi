//! On-device check of this computer's capture and H.264 encoder. Run it at the
//! computer (it records the screen for two seconds):
//!
//!   cargo run --release -p hidfi-stream --example check_capture
//!
//! Prints what it captured and encoded, checks the stream's structure, and
//! writes check_capture.h264 (play it with ffplay or VLC to see the picture).

use hidfi_stream::frame::{nal_units, Frame};
use std::time::{Duration, Instant};

fn main() {
    let mut fails = 0;
    let mut check = |name: &str, ok: bool, detail: String| {
        println!("{} {name} {detail}", if ok { "ok  " } else { "FAIL" });
        if !ok {
            fails += 1;
        }
    };
    let mut src = match hidfi_stream::screen(None, 30) {
        Ok(s) => s,
        Err(e) => {
            println!("FAIL capture could not start: {e}");
            std::process::exit(1);
        }
    };
    // The first frame can take a moment while the system starts capturing.
    let t0 = Instant::now();
    let mut first: Option<(u32, u32)> = None;
    while first.is_none() && t0.elapsed() < Duration::from_secs(5) {
        let _ = src.grab(true, &mut |f: &Frame| first = Some((f.width, f.height)));
        std::thread::sleep(Duration::from_millis(30));
    }
    let Some((w, h)) = first else {
        println!("FAIL no frame within 5 s");
        std::process::exit(1);
    };
    check(
        "captured the screen",
        w >= 16 && h >= 16,
        format!("{w}x{h} after {:?}", t0.elapsed()),
    );
    let mut enc = hidfi_stream::encoder(w & !1, h & !1, 30, 4_000_000).unwrap_or_else(|e| {
        println!("FAIL no encoder: {e}");
        std::process::exit(1)
    });
    let mut out = Vec::new();
    let (mut frames, mut keys, mut bytes, mut enc_time) = (0u32, 0u32, 0usize, Duration::ZERO);
    let cpu0 = cpu_seconds();
    let start = Instant::now();
    for i in 0..60u64 {
        let force = i == 0 || i == 30;
        let _ = src.grab(true, &mut |f: &Frame| {
            let t = Instant::now();
            match enc.encode(f, i * 33, force) {
                Ok(Some(e)) => {
                    frames += 1;
                    keys += e.keyframe as u32;
                    bytes += e.data.len();
                    out.extend_from_slice(&e.data);
                }
                Ok(None) => {}
                Err(e) => println!("     encode error: {e}"),
            }
            enc_time += t.elapsed();
        });
        std::thread::sleep(Duration::from_millis(33));
    }
    let wall = start.elapsed();
    let cpu = cpu_seconds() - cpu0;
    check("encoded frames", frames >= 50, format!("{frames} of 60"));
    check("keyframes on request", keys >= 2, format!("{keys}"));
    let nals = nal_units(&out);
    let types: std::collections::BTreeSet<u8> = nals
        .iter()
        .filter(|n| !n.is_empty())
        .map(|n| n[0] & 0x1f)
        .collect();
    check(
        "stream has SPS, PPS and an IDR",
        types.contains(&7) && types.contains(&8) && types.contains(&5),
        format!("{types:?}"),
    );
    if let Some(sps) = nals.iter().find(|n| !n.is_empty() && n[0] & 0x1f == 7) {
        check(
            "baseline profile (browsers decode it)",
            sps.len() > 2 && sps[1] == 66,
            format!("profile_idc {}", sps.get(1).copied().unwrap_or(0)),
        );
    }
    println!(
        "     {:.1} ms encode per frame, {:.0} kbit/s, CPU {:.0}% of one core over {:.1} s",
        enc_time.as_secs_f64() * 1000.0 / frames.max(1) as f64,
        bytes as f64 * 8.0 / wall.as_secs_f64() / 1000.0,
        cpu / wall.as_secs_f64() * 100.0,
        wall.as_secs_f64()
    );
    match std::fs::write("check_capture.h264", &out) {
        Ok(()) => println!("     wrote check_capture.h264 ({} KB)", out.len() / 1024),
        Err(e) => println!("     could not write the sample: {e}"),
    }
    std::process::exit(if fails == 0 { 0 } else { 1 });
}

fn cpu_seconds() -> f64 {
    #[cfg(unix)]
    {
        // SAFETY: getrusage with a valid out pointer.
        unsafe {
            let mut u: libc_rusage = std::mem::zeroed();
            if getrusage(0, &mut u) == 0 {
                return u.ru_utime.tv_sec as f64
                    + u.ru_utime.tv_usec as f64 / 1e6
                    + u.ru_stime.tv_sec as f64
                    + u.ru_stime.tv_usec as f64 / 1e6;
            }
        }
        0.0
    }
    #[cfg(not(unix))]
    {
        0.0 // Task Manager shows it on Windows
    }
}

#[cfg(unix)]
#[repr(C)]
struct Timeval {
    tv_sec: i64,
    tv_usec: i64,
}
#[cfg(unix)]
#[repr(C)]
struct libc_rusage {
    ru_utime: Timeval,
    ru_stime: Timeval,
    _rest: [i64; 14],
}
#[cfg(unix)]
extern "C" {
    fn getrusage(who: i32, usage: *mut libc_rusage) -> i32;
}
