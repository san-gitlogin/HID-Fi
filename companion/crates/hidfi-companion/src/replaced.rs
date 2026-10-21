//! macOS: an app whose program file is replaced while it runs (dragging a newer
//! version into Applications does exactly that) loses its local network access:
//! macOS ties that permission to the signed program it started from. Seen
//! live: "not on this network" for good, two seconds after a rebuild. So the
//! app watches its own program file and, once a new one has settled, starts it
//! and quits.

use std::path::PathBuf;
use std::time::{Duration, SystemTime};

fn stamp(p: &PathBuf) -> Option<(SystemTime, u64)> {
    let m = std::fs::metadata(p).ok()?;
    Some((m.modified().ok()?, m.len()))
}

pub fn watch() {
    let Ok(exe) = std::env::current_exe() else { return };
    // .../HID-Fi Companion.app/Contents/MacOS/hidfi-companion -> the .app
    let Some(bundle) = exe.ancestors().nth(3).map(|p| p.to_path_buf()) else { return };
    if bundle.extension().and_then(|e| e.to_str()) != Some("app") {
        return; // not running from a bundle (a developer build): nothing to do
    }
    let Some(first) = stamp(&exe) else { return };
    let _ = std::thread::Builder::new()
        .name("replaced".into())
        .spawn(move || loop {
            std::thread::sleep(Duration::from_secs(3));
            let Some(now) = stamp(&exe) else { continue }; // mid-copy: not there yet
            if now == first {
                continue;
            }
            // Let the copy finish: the same size and time twice, 2 s apart.
            std::thread::sleep(Duration::from_secs(2));
            if stamp(&exe) != Some(now) {
                continue;
            }
            log::info!("this app was replaced by a new version: restarting into it");
            let b = bundle.display().to_string().replace('\'', "'\\''");
            let _ = std::process::Command::new("/bin/sh")
                .arg("-c")
                .arg(format!("sleep 1.5; /usr/bin/open -n '{b}'"))
                .spawn();
            std::process::exit(0);
        });
}
