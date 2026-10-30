//! A newer HID-Fi Companion takes over from the one already running.
//!
//! Seen on Windows: updating meant finding the tray icon and quitting the app
//! first. Running the new .exe while the old one ran did nothing - one instance
//! only, and the new one left quietly - and the installer's copy could fail
//! while the old program file was still locked.
//!
//! The running app writes `instance.json` (its version, process id and program
//! path) and watches for `quit.request`. A second launch that is newer, or the
//! same version from a different program file (a fresh download), writes that
//! request; the running app quits the way its own Quit does, and the new one
//! takes its place. The same version launched again leaves too, but first
//! writes `show.request`: the running app shows its window, so opening the app
//! a second time (a double-click, the Start menu) answers instead of doing
//! nothing. (macOS opens a running app by a reopen event instead: desktop.rs.)

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
const INFO: &str = "instance.json";
const REQUEST: &str = "quit.request";
const SHOW: &str = "show.request";

static QUIT: AtomicBool = AtomicBool::new(false);
static SHOW_ASKED: AtomicBool = AtomicBool::new(false);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Instance {
    pub version: String,
    pub pid: u32,
    pub exe: String,
}

/// "4.0.3" < "4.0.10" < "4.1": numeric parts, missing parts are 0.
pub fn newer(a: &str, b: &str) -> bool {
    let p = |s: &str| -> Vec<u64> {
        s.split('.')
            .map(|x| x.trim().parse().unwrap_or(0))
            .collect()
    };
    let (x, y) = (p(a), p(b));
    for i in 0..x.len().max(y.len()) {
        let (u, v) = (
            x.get(i).copied().unwrap_or(0),
            y.get(i).copied().unwrap_or(0),
        );
        if u != v {
            return u > v;
        }
    }
    false
}

/// Should a launch of (`version`, `exe`) take over from `running`?
pub fn should_take_over(running: &Instance, version: &str, exe: &str) -> bool {
    newer(version, &running.version) || (version == running.version && exe != running.exe)
}

/// The running app says who it is.
pub fn announce(dir: &Path) {
    let me = Instance {
        version: VERSION.into(),
        pid: std::process::id(),
        exe: current_exe(),
    };
    // Stale requests are not for us.
    let _ = std::fs::remove_file(dir.join(REQUEST));
    let _ = std::fs::remove_file(dir.join(SHOW));
    if let Ok(s) = serde_json::to_string(&me) {
        let _ = std::fs::write(dir.join(INFO), s);
    }
}

pub fn running(dir: &Path) -> Option<Instance> {
    serde_json::from_str(&std::fs::read_to_string(dir.join(INFO)).ok()?).ok()
}

/// A second launch: ask the running one to quit if this one should take over.
pub fn ask_to_quit(dir: &Path) -> bool {
    match running(dir) {
        Some(r) if should_take_over(&r, VERSION, &current_exe()) => {
            log::info!("taking over from version {} (pid {})", r.version, r.pid);
            std::fs::write(dir.join(REQUEST), VERSION).is_ok()
        }
        _ => false,
    }
}

/// The same app launched again: ask the running one to show its window.
pub fn ask_to_show(dir: &Path) -> bool {
    std::fs::write(dir.join(SHOW), VERSION).is_ok()
}

/// The running app watches for a request; `wake` nudges its event loop.
pub fn watch(dir: PathBuf, wake: impl Fn() + Send + 'static) {
    let _ = std::thread::Builder::new()
        .name("handover".into())
        .spawn(move || loop {
            std::thread::sleep(Duration::from_millis(400));
            if std::fs::remove_file(dir.join(SHOW)).is_ok() {
                log::info!("HID-Fi Companion was opened again - showing the window");
                SHOW_ASKED.store(true, Ordering::SeqCst);
                wake();
            }
            if dir.join(REQUEST).exists() {
                let _ = std::fs::remove_file(dir.join(REQUEST));
                log::info!("a newer HID-Fi Companion asked to take over - quitting");
                QUIT.store(true, Ordering::SeqCst);
                wake();
                return;
            }
        });
}

pub fn quit_requested() -> bool {
    QUIT.load(Ordering::SeqCst)
}

/// Was the window asked for by a second launch? Answers yes once per request.
pub fn show_requested() -> bool {
    SHOW_ASKED.swap(false, Ordering::SeqCst)
}

fn current_exe() -> String {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.canonicalize().ok())
        .map(|p| p.display().to_string())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn versions_compare_by_number_not_by_text() {
        assert!(newer("4.0.10", "4.0.3"));
        assert!(newer("4.1", "4.0.9"));
        assert!(newer("5.0.0", "4.9.9"));
        assert!(!newer("4.0.3", "4.0.3"));
        assert!(!newer("4.0.2", "4.0.3"));
        assert!(!newer("4.0", "4.0.0"));
    }

    #[test]
    fn a_newer_version_or_a_fresh_copy_takes_over_the_same_one_does_not() {
        let r = Instance {
            version: "4.0.3".into(),
            pid: 1,
            exe: "/Apps/a".into(),
        };
        assert!(should_take_over(&r, "4.0.4", "/Apps/a"), "an update");
        assert!(
            should_take_over(&r, "4.0.3", "/Downloads/a"),
            "the same version, downloaded again"
        );
        assert!(
            !should_take_over(&r, "4.0.3", "/Apps/a"),
            "a second click on the same app"
        );
        assert!(
            !should_take_over(&r, "4.0.2", "/Downloads/a"),
            "an older download never replaces a newer app"
        );
    }

    #[test]
    fn a_request_reaches_the_running_app_once() {
        let dir = std::env::temp_dir().join(format!("hidfi-handover-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(
            dir.join(INFO),
            serde_json::to_string(&Instance {
                version: "0.0.1".into(),
                pid: 1,
                exe: "x".into(),
            })
            .unwrap(),
        )
        .unwrap();
        assert!(ask_to_quit(&dir), "this build is newer than 0.0.1");
        let woke = std::sync::Arc::new(AtomicBool::new(false));
        let w = woke.clone();
        watch(dir.clone(), move || w.store(true, Ordering::SeqCst));
        for _ in 0..30 {
            if woke.load(Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        assert!(woke.load(Ordering::SeqCst) && quit_requested());
        assert!(!dir.join(REQUEST).exists(), "the request is consumed");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn opening_the_app_again_shows_the_running_one_once() {
        let dir = std::env::temp_dir().join(format!("hidfi-show-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let woke = std::sync::Arc::new(AtomicBool::new(false));
        let w = woke.clone();
        watch(dir.clone(), move || w.store(true, Ordering::SeqCst));
        assert!(ask_to_show(&dir));
        for _ in 0..30 {
            if woke.load(Ordering::SeqCst) {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        assert!(woke.load(Ordering::SeqCst), "the running app was woken");
        assert!(show_requested(), "and told to show its window");
        assert!(!show_requested(), "once per request");
        assert!(!dir.join(SHOW).exists(), "the request is consumed");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
