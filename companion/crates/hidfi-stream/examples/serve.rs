//! Streams a test pattern (or nothing yet: real capture comes with the app) to
//! one viewer, for end-to-end checks in a real browser. Prints one JSON line:
//! the port and a valid one-use ticket. Test tool only.
//!
//!   cargo run -p hidfi-stream --example serve -- [WIDTH HEIGHT FPS [still|busy]]
//!
//! The streamer's log (info and up) goes to stderr.

use hidfi_agent::session::TicketNonces;
use hidfi_core::pairing::{self, PairKey};
use hidfi_stream::pattern::Pattern;
use hidfi_stream::pipeline::{Pipeline, Source};
use hidfi_stream::server::{self, TicketGate};
use std::sync::{Arc, RwLock};
use std::time::Instant;

struct Stderr;
impl log::Log for Stderr {
    fn enabled(&self, m: &log::Metadata) -> bool {
        m.level() <= log::Level::Info
    }
    fn log(&self, r: &log::Record) {
        if self.enabled(r.metadata()) {
            eprintln!("{} {}", r.level(), r.args());
        }
    }
    fn flush(&self) {}
}

fn main() {
    let _ = log::set_logger(&Stderr).map(|()| log::set_max_level(log::LevelFilter::Info));
    let a: Vec<String> = std::env::args().collect();
    let w: u32 = a.get(1).and_then(|s| s.parse().ok()).unwrap_or(1280);
    let h: u32 = a.get(2).and_then(|s| s.parse().ok()).unwrap_or(720);
    let fps: u32 = a.get(3).and_then(|s| s.parse().ok()).unwrap_or(30);
    let still = a.get(4).is_some_and(|s| s == "still");
    let busy = a.get(4).is_some_and(|s| s == "busy");
    let key = PairKey::from_bytes([7; 32]);
    let nonces = TicketNonces::default();
    let tn = "1c".repeat(16);
    nonces.announce(tn.clone(), Instant::now());
    let gate = Arc::new(TicketGate::new(
        Arc::new(RwLock::new(Some(key.clone()))),
        nonces,
    ));
    let pipeline = Arc::new(Pipeline::start(
        fps,
        3_000_000,
        move || -> Result<Box<dyn Source>, String> {
            Ok(if still {
                Box::new(Pattern::new(w, h).still())
            } else if busy {
                Box::new(Pattern::new(w, h).busy())
            } else {
                Box::new(Pattern::new(w, h))
            })
        },
        hidfi_stream::encoder,
    ));
    let cert = hidfi_stream::rtc::new_cert().expect("certificate");
    let fp = hidfi_stream::rtc::cert_fp(&cert);
    let srv = server::start(
        None,
        gate,
        pipeline.clone(),
        cert,
        Box::new(move || (w & !1, h & !1)),
    )
    .expect("listening");
    let tickets: Vec<_> = (0..12)
        .map(|i| {
            let vn = format!("{:032x}", i + 1);
            serde_json::json!({"vn": vn, "ticket": hex::encode(pairing::ticket(&key, &tn, &vn))})
        })
        .collect();
    println!(
        "{}",
        serde_json::json!({"port": srv.port, "tn": tn, "fp": fp, "tickets": tickets})
    );
    loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        eprintln!(
            "viewers={} pipeline_viewers={}",
            srv.watchers.count(),
            pipeline.viewers()
        );
    }
}
