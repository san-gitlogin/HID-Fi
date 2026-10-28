//! A cable update on a real board, exactly as the Companion runs it:
//!   cargo run -p hidfi-flash --features serial --example cable_update -- \
//!       <port> <MAC> <image.bin> <partitions.bin> <journal.json>
//! Then it keeps the same port open (a new open would reset the board - and a
//! reset before the self-test rolls the update back) and reads the firmware's
//! own status on it until the new version is kept. Prints JSON lines.
use std::path::Path;
use std::time::{Duration, Instant};

use hidfi_flash::flasher::Flasher;
use hidfi_flash::port::Port;
use hidfi_flash::serial::Serial;
use hidfi_flash::update::{self, Connect, Progress, Release, Step};

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let mac: Vec<u8> = a[2]
        .split(':')
        .map(|h| u8::from_str_radix(h, 16).unwrap())
        .collect();
    let mac: [u8; 6] = mac.try_into().unwrap();
    let image = std::fs::read(&a[3]).expect("image");
    let table = std::fs::read(&a[4]).expect("table");
    let mut f = Flasher::new(Serial::open(&a[1], 115_200).expect("open"));
    let rel = Release {
        image: &image,
        table: &table,
    };
    let mut last = 0usize;
    let mut on = |step: Step, n: usize, total: usize| {
        if n * 10 / total.max(1) != last {
            last = n * 10 / total.max(1);
            println!("{{\"progress\":\"{step:?}\",\"bytes\":{n},\"total\":{total}}}");
        }
    };
    let t = Instant::now();
    match update::run(
        &mut f,
        &mac,
        Connect::Reset,
        &rel,
        Path::new(&a[5]),
        Progress {
            cancel: &|| false,
            on: &mut on,
        },
    ) {
        Ok(j) => println!(
            "{{\"written\":true,\"path\":\"{:?}\",\"slot\":{},\"secs\":{:.1}}}",
            j.path,
            j.target_slot,
            t.elapsed().as_secs_f32()
        ),
        Err(e) => {
            println!("{{\"error\":\"{e}\"}}");
            std::process::exit(2);
        }
    }
    // Watch the firmware prove itself, on the port we already hold.
    let mut port = f.port;
    let deadline = Instant::now() + Duration::from_secs(150);
    let mut line = Vec::new();
    let mut last_poll = Instant::now() - Duration::from_secs(5);
    while Instant::now() < deadline {
        if last_poll.elapsed() > Duration::from_secs(3) {
            let _ = port.write_all(b"{\"cmd\":\"ota_status\"}\n");
            last_poll = Instant::now();
        }
        let mut b = [0u8; 256];
        let n = port.read(&mut b, 100).unwrap_or(0);
        for &c in &b[..n] {
            if c == b'\n' {
                let s = String::from_utf8_lossy(&line).trim().to_string();
                line.clear();
                if s.contains("\"reply\":\"ota_status\"") {
                    let v: serde_json::Value = serde_json::from_str(&s).unwrap_or_default();
                    println!(
                        "{{\"state\":{},\"firmware\":{},\"result\":{}}}",
                        v["ota_state"], v["firmware"], v["ota_result"]
                    );
                    if v["ota_state"] == "valid" {
                        return;
                    }
                }
            } else {
                line.push(c);
            }
        }
    }
    println!("{{\"error\":\"the new firmware was not kept within 150 s\"}}");
    std::process::exit(3);
}
