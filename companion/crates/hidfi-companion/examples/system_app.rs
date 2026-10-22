//! The real service with a fake USB bus and a test-pattern screen, for the
//! system test (companion/tests/system.py). Test tool only.
//!
//! args: STORE_DIR BOARD_URL BOARD_MAC [BOARD_URL BOARD_MAC ...] - one or more
//! boards plugged into this pretend computer (a computer can have two).
//! stdin: "unplug" / "plug" (every board), "unplug <MAC>" / "plug <MAC>"
//! (one), "pair", "quit"
//! stdout: one JSON line per change of what the tray would show.

use hidfi_agent::env::{Browse, UsbList};
use hidfi_agent::real::UreqHttp;
use hidfi_companion::service::{self, Cmd, Env};
use hidfi_companion::store::Store;
use hidfi_core::discovery::UsbDevice;
use hidfi_stream::pattern::FakeDisplays;
use std::io::BufRead;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

/// The boards on this pretend USB: (MAC, plugged in).
struct FakeUsb(Vec<(String, Arc<AtomicBool>)>);
impl UsbList for FakeUsb {
    fn list(&self) -> Vec<UsbDevice> {
        self.0
            .iter()
            .filter(|(_, p)| p.load(Ordering::Relaxed))
            .map(|(mac, _)| UsbDevice {
                vid: 0x303a,
                pid: 0x1001,
                serial: Some(mac.clone()),
            })
            .collect()
    }
}
struct NoBrowse;
impl Browse for NoBrowse {
    fn boards(&self) -> Vec<String> {
        vec![]
    }
}

struct Stderr;
impl log::Log for Stderr {
    fn enabled(&self, m: &log::Metadata) -> bool {
        m.level() <= log::Level::Debug
    }
    fn log(&self, r: &log::Record) {
        if self.enabled(r.metadata()) {
            eprintln!("{} {}: {}", r.level(), r.target(), r.args());
        }
    }
    fn flush(&self) {}
}

fn main() {
    // The service's log on stderr (a test may keep it).
    let _ = log::set_logger(&Stderr).map(|()| log::set_max_level(log::LevelFilter::Debug));
    let a: Vec<String> = std::env::args().collect();
    let store = Store::open(a[1].clone().into()).unwrap();
    let mut st = store.state();
    // HIDFI_TEST_NAME: what the dashboard calls this pretend computer.
    st.name = std::env::var("HIDFI_TEST_NAME").unwrap_or_else(|_| "System Test PC".into());
    let boards: Vec<(String, Arc<AtomicBool>)> = a[2..]
        .chunks(2)
        .map(|c| {
            (
                c[1].replace(':', "").to_uppercase(),
                Arc::new(AtomicBool::new(true)),
            )
        })
        .collect();
    for c in a[2..].chunks(2) {
        let mac = hidfi_core::discovery::Mac::parse(&c[1].replace(':', "")).expect("a MAC");
        store.remember(&mut st, &mac, &c[0]).unwrap();
    }
    store.save_state(&st).unwrap();
    // HIDFI_DISPLAYS=n pretends to have n displays; stdin "unplug-display <id>" pulls one.
    let fake = FakeDisplays::from_env(1280, 720);
    let env = Env {
        usb: Box::new(FakeUsb(boards.clone())),
        http: Box::new(UreqHttp::new(Duration::from_secs(2))),
        browse: Box::new(NoBrowse),
        source: {
            let f = fake.clone();
            Arc::new(move |d| f.source(d, false))
        },
        displays: {
            let f = fake.clone();
            Arc::new(move || f.list())
        },
        capture_allowed: Arc::new(|| true),
    };
    let h = service::start(store, env);
    let view = h.view.clone();
    std::thread::spawn(move || {
        let mut last = String::new();
        loop {
            let v = view.lock().unwrap().clone();
            let line = serde_json::json!({
                "board": format!("{:?}", v.board), "session": format!("{:?}", v.session),
                "boards": v.boards.iter().map(|b| serde_json::json!({
                    "mac": b.mac, "board": format!("{:?}", b.board), "session": format!("{:?}", b.session),
                })).collect::<Vec<_>>(),
                "watching": v.watching, "port": v.port, "problem": v.problem,
            })
            .to_string();
            if line != last {
                println!("{line}");
                last = line;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
    });
    for line in std::io::stdin().lock().lines() {
        let line = line.unwrap_or_default();
        if let Some(id) = line
            .trim()
            .strip_prefix("unplug-display ")
            .and_then(|s| s.parse().ok())
        {
            fake.unplug(id);
            continue;
        }
        let set = |mac: Option<&str>, on: bool| {
            for (m, p) in &boards {
                if mac.is_none_or(|x| x.replace(':', "").eq_ignore_ascii_case(m)) {
                    p.store(on, Ordering::Relaxed);
                }
            }
        };
        if let Some(m) = line.trim().strip_prefix("unplug ") {
            set(Some(m), false);
            continue;
        }
        if let Some(m) = line.trim().strip_prefix("plug ") {
            set(Some(m), true);
            continue;
        }
        match line.trim() {
            "unplug" => set(None, false),
            "plug" => set(None, true),
            "pair" => {
                let _ = h.tx.send(Cmd::PairAgain(None));
            }
            "quit" | "" => {
                let _ = h.tx.send(Cmd::Quit);
                std::thread::sleep(Duration::from_millis(200));
                return;
            }
            _ => {}
        }
    }
}
