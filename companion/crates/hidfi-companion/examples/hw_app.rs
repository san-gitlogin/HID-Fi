//! The real service against the real board: real USB listing, real network,
//! real mDNS - only the screen is a test pattern (so no Screen Recording
//! permission is needed). For companion/tests/hardware.py. Test tool only.
//!
//! args: STORE_DIR        stdout: one JSON line per change; stdin: "quit"

use hidfi_agent::real::{MdnsBrowse, NusbList, UreqHttp};
use hidfi_companion::service::{self, Cmd, Env};
use hidfi_companion::store::Store;
use hidfi_stream::pattern::Pattern;
use hidfi_stream::pipeline::Source;
use std::io::BufRead;
use std::sync::Arc;
use std::time::Duration;

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let store = Store::open(a[1].clone().into()).unwrap();
    let mut st = store.state();
    st.name = "HID-Fi hardware test".into();
    store.save_state(&st).unwrap();
    let env = Env {
        usb: Box::new(NusbList),
        http: Box::new(UreqHttp::new(Duration::from_secs(7))),
        browse: Box::new(MdnsBrowse::start()),
        source: Arc::new(|_| Ok(Box::new(Pattern::new(1280, 720)) as Box<dyn Source>)),
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
        if matches!(line.as_deref().map(str::trim), Ok("quit") | Ok("") | Err(_)) {
            let _ = h.tx.send(Cmd::Quit);
            std::thread::sleep(Duration::from_millis(300));
            return;
        }
    }
}
