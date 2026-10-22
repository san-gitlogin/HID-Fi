//! How closely this computer's H.264 encoder keeps to the bitrate it is given:
//! busy content (scrolling text, every pixel moving) at full, half and quarter
//! size, at several rates, and with the rate changed while encoding (as the
//! app does when a link changes). Prints, for each case, the rate asked for,
//! what came out, frames out of frames in, and keyframes.
//!
//!   cargo run --release -p hidfi-stream --example rate_check [--check]
//!
//! --check fails (exit 1) if any case sends more than 5x its target: the
//! encoder is not following its bitrate at all.
//!
//! Seen on Windows (Media Foundation, v4.0): at 1920x1080 a constant 27 Mbit/s
//! whatever the target (12-54x), because the rate control mode was set after
//! the output type and the encoder ignored it.
use hidfi_stream::frame::{Encoder, Frame};
use hidfi_stream::pattern::Pattern;
use hidfi_stream::pipeline::Source;
use std::time::Instant;

const FPS: u32 = 30;
const SECS: u32 = 4;

fn run(enc: &mut dyn Encoder, src: &mut Pattern, rates: &[u32]) -> Vec<(u32, f64, u32, u32, u32)> {
    let mut out = Vec::new();
    let mut pts = 0u64;
    // RATE_JITTER=1: the target moves +-15% every 200 ms, as the app's does
    // when a link estimate moves (it re-sets the encoder on each >10% change).
    let jitter = std::env::var("RATE_JITTER").is_ok_and(|v| v == "1");
    for &bps in rates {
        enc.set_bitrate(bps);
        let (mut bytes, mut made, mut keys) = (0usize, 0u32, 0u32);
        let n = FPS * SECS;
        for i in 0..n {
            if jitter && i % 6 == 5 {
                enc.set_bitrate(if (i / 6) % 2 == 0 {
                    bps * 115 / 100
                } else {
                    bps * 85 / 100
                });
            }
            let mut got = None;
            src.grab(true, &mut |f: &Frame| {
                got = Some(enc.encode(f, pts, i == 0 && pts == 0));
            })
            .expect("pattern");
            pts += 1000 / FPS as u64;
            if let Some(Ok(Some(e))) = got {
                bytes += e.data.len();
                made += 1;
                keys += e.keyframe as u32;
            }
        }
        out.push((bps, bytes as f64 * 8.0 / SECS as f64, made, n, keys));
    }
    out
}

fn main() {
    let t0 = Instant::now();
    let check = std::env::args().any(|a| a == "--check");
    let mut worst = 0f64;
    println!(
        "{:>9} {:>9} {:>10} {:>10} {:>6} {:>5}",
        "size", "target", "out", "out/target", "frames", "keys"
    );
    for (w, h) in [(1920, 1080), (960, 540), (480, 270)] {
        let mut src = Pattern::new(w, h).busy();
        // RATE_FIRST: the rate the encoder is made with (the app makes it at
        // the link's rate, which can be high).
        let first: u32 = std::env::var("RATE_FIRST")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(3_000_000);
        let mut enc = match hidfi_stream::encoder(w, h, FPS, first) {
            Ok(e) => e,
            Err(e) => {
                println!("{w}x{h}: no encoder: {e}");
                continue;
            }
        };
        // Steady rates, then down and up again on the same encoder.
        let rates = [
            first, 3_000_000, 1_000_000, 500_000, 6_000_000, 900_000, 9_000_000, 12_000_000,
        ];
        for (bps, got, made, n, keys) in run(enc.as_mut(), &mut src, &rates) {
            worst = worst.max(got / bps as f64);
            println!(
                "{:>9} {:>8.2}M {:>9.2}M {:>9.1}x {:>3}/{:<3} {:>4}",
                format!("{w}x{h}"),
                bps as f64 / 1e6,
                got / 1e6,
                got / bps as f64,
                made,
                n,
                keys
            );
        }
    }
    println!(
        "{:.0} s; worst {worst:.1}x its target",
        t0.elapsed().as_secs_f64()
    );
    if check && worst > 5.0 {
        eprintln!("FAIL the encoder does not follow its bitrate ({worst:.1}x its target)");
        std::process::exit(1);
    }
}
