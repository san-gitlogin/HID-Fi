//! Streams a test pattern (or nothing yet: real capture comes with the app) to
//! one viewer, for end-to-end checks in a real browser. Prints one JSON line:
//! the port and a valid one-use ticket. Test tool only.
//!
//!   cargo run -p hidfi-stream --example serve -- [WIDTH HEIGHT FPS [still|busy]]
//!
//! The streamer's log (info and up) goes to stderr.
//!
//! HIDFI_SOUND=test: a test sound (a tone with a click at every whole second of
//! the shared clock) as "the computer's sound"; HIDFI_SOUND=real: the real
//! capture; unset: no sound offered. HIDFI_SOUND_MIC=1 also shares a test (or
//! real) microphone.

use hidfi_agent::session::TicketNonces;
use hidfi_core::pairing::{self, PairKey};
use hidfi_stream::pattern::{FakeDisplays, Pattern};
use hidfi_stream::pipeline::{Pipeline, Source};
use hidfi_stream::server::{self, TicketGate};
use std::sync::{Arc, RwLock};
use std::time::Instant;

/// Info and up; HIDFI_DEBUG=1 adds hidfi_stream's debug lines, each with the
/// milliseconds since start (to trace congestion control in tests).
struct Stderr;
static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
fn debug() -> bool {
    std::env::var("HIDFI_DEBUG").is_ok_and(|v| v == "1")
}
impl log::Log for Stderr {
    fn enabled(&self, m: &log::Metadata) -> bool {
        m.level() <= log::Level::Info
            || (debug() && m.level() <= log::Level::Debug && m.target().starts_with("hidfi_stream"))
    }
    fn log(&self, r: &log::Record) {
        if self.enabled(r.metadata()) {
            let ms = START.get_or_init(Instant::now).elapsed().as_millis();
            eprintln!("{} {ms} {}", r.level(), r.args());
        }
    }
    fn flush(&self) {}
}

fn main() {
    START.get_or_init(Instant::now);
    let level = if debug() {
        log::LevelFilter::Debug
    } else {
        log::LevelFilter::Info
    };
    let _ = log::set_logger(&Stderr).map(|()| log::set_max_level(level));
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
        Arc::new(RwLock::new(vec![hidfi_stream::server::GateKey {
            tag: 0,
            key: key.clone(),
            here: true,
        }])),
        nonces,
    ));
    let fake = FakeDisplays::from_env(w, h);
    let f2 = fake.clone();
    // HIDFI_REAL_SCREEN=1: this computer's own screen and displays, as the app
    // captures them (the Linux test runs it on a virtual X desktop).
    let real = std::env::var_os("HIDFI_REAL_SCREEN").is_some();
    let pipeline = Arc::new(Pipeline::start(
        fps,
        3_000_000,
        move |d: Option<u32>| -> Result<Box<dyn Source>, String> {
            // HIDFI_FAIL_CAPTURE="...": capture fails with that message, as on
            // a Mac where Screen Recording is not allowed.
            if let Ok(why) = std::env::var("HIDFI_FAIL_CAPTURE") {
                return Err(why);
            }
            if real {
                hidfi_stream::screen(d, fps)
            } else if still {
                Ok(Box::new(Pattern::new(w, h).still()))
            } else {
                f2.source(d, busy)
            }
        },
        hidfi_stream::encoder,
    ));
    let cert = hidfi_stream::rtc::new_cert().expect("certificate");
    let fp = hidfi_stream::rtc::cert_fp(&cert);
    let sound = std::env::var("HIDFI_SOUND").ok();
    let audio = sound.as_deref().map(|kind| {
        let mic = std::env::var_os("HIDFI_SOUND_MIC").is_some();
        let opener: hidfi_stream::audio::Opener = if kind == "real" {
            Arc::new(hidfi_stream::audio_capture)
        } else {
            Arc::new(|i, sink, _ended| {
                let (rate, ch) = match i {
                    hidfi_stream::audio::Input::System => (48_000, 2),
                    hidfi_stream::audio::Input::Microphone => (44_100, 1),
                };
                Ok(hidfi_stream::audio::test_sound(
                    hidfi_stream::epoch(),
                    rate,
                    ch,
                    480,
                    sink,
                ))
            })
        };
        Arc::new(hidfi_stream::audio::AudioHub::start(
            opener,
            hidfi_stream::audio::Settings::new(true, mic),
        ))
    });
    let srv = server::start(None, gate, pipeline.clone(), audio, cert, {
        let f = fake.clone();
        Box::new(move || {
            if real {
                hidfi_stream::display::list()
            } else {
                f.list()
            }
        })
    })
    .expect("listening");
    let tickets: Vec<_> = (0..40)
        .map(|i| {
            let vn = format!("{:032x}", i + 1);
            serde_json::json!({"vn": vn, "ticket": hex::encode(pairing::ticket(&key, &tn, &vn))})
        })
        .collect();
    println!(
        "{}",
        serde_json::json!({"port": srv.port, "tn": tn, "fp": fp, "tickets": tickets,
            // The shared clock's start as a wall-clock time: the test sound clicks at
            // epoch_ms + k*1000, so a browser on this computer can time its arrival.
            "epoch_ms": (std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default()
                - hidfi_stream::epoch().elapsed()).as_secs_f64() * 1000.0})
    );
    // stdin (tests give it a pipe, or nothing - never their own: on Windows an
    // inherited SSH console is asynchronous, and Rust's standard library aborts
    // the process on such a read, "operation failed to complete synchronously").
    // "unplug <id>" takes a pretend display away; "still" and "move"
    // stop and restart the patterns (a desktop at rest, then a window dragged).
    std::thread::spawn(move || {
        for line in std::io::stdin().lines().map_while(Result::ok) {
            match line.trim() {
                "still" => {
                    hidfi_stream::pattern::HELD.store(true, std::sync::atomic::Ordering::Relaxed)
                }
                "move" => {
                    hidfi_stream::pattern::HELD.store(false, std::sync::atomic::Ordering::Relaxed)
                }
                _ => {}
            }
            if let Some(id) = line
                .trim()
                .strip_prefix("unplug ")
                .and_then(|s| s.parse().ok())
            {
                fake.unplug(id);
            }
        }
    });
    loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        eprintln!(
            "viewers={} pipeline_viewers={}",
            srv.watchers.count(),
            pipeline.viewers()
        );
    }
}
