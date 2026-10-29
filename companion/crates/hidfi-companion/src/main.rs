//! HID-Fi Companion: a tray / menu-bar app that shows this computer's screen
//! on the HID-Fi dashboard. All the work is in `service`; this file is the
//! single-instance guard and the first-run setup, then the tray for the OS:
//! `desktop` (macOS, Windows) or `linux`.

#![cfg_attr(windows, windows_subsystem = "windows")]

mod act;
mod autostart;
#[cfg(any(target_os = "macos", windows))]
mod desktop;
mod icons;
#[cfg(target_os = "linux")]
mod linux;
mod logfile;
#[cfg(target_os = "macos")]
mod replaced;
#[cfg(any(target_os = "macos", windows))]
mod windowcheck;

use hidfi_agent::real::{MdnsBrowse, NusbList, UreqHttp};
use hidfi_companion::service::{self, Env};
use hidfi_companion::store::Store;
use std::sync::Arc;
use std::time::Duration;

/// This computer's USB. Test builds only (feature "test-ota-key"):
/// HIDFI_TEST_NO_USB sees no board, so a test copy of the app never asks a real
/// board on this desk to pair (companion/tests/self_update_mac.py).
fn usb() -> Box<dyn hidfi_agent::env::UsbList> {
    #[cfg(feature = "test-ota-key")]
    if std::env::var_os("HIDFI_TEST_NO_USB").is_some() {
        struct NoUsb;
        impl hidfi_agent::env::UsbList for NoUsb {
            fn list(&self) -> Vec<hidfi_core::discovery::UsbDevice> {
                Vec::new()
            }
        }
        return Box::new(NoUsb);
    }
    Box::new(NusbList)
}

fn main() {
    // Which version is installed - for a person, a script or a support request.
    // A diagnostic, run as the app so it has the app's permissions:
    //   open -n -W "HID-Fi Companion.app" --args --check-sound system|mic SECS OUT.jsonl
    // Before everything else, so it never disturbs a Companion already running.
    let args: Vec<String> = std::env::args().collect();
    if let Some(i) = args.iter().position(|a| a == "--check-sound") {
        hidfi_companion::soundcheck::run(&args[i + 1..]);
        return;
    }
    // The window's page in this OS's own web view, every state, then exit
    // (companion/tests/window_real.py). Touches nothing of the running app.
    #[cfg(any(target_os = "macos", windows))]
    if let Some(i) = args.iter().position(|a| a == "--window-check") {
        windowcheck::run(args.get(i + 1).map_or("window-check.json", String::as_str));
        return;
    }
    if std::env::args().any(|a| a == "--version" || a == "-V") {
        println!("HID-Fi Companion {}", hidfi_companion::handover::VERSION);
        return;
    }
    // HIDFI_HOME and HIDFI_NO_AUTOSTART exist for development and tests: a
    // test run must not install a login item or touch the real app folder.
    let dir = std::env::var_os("HIDFI_HOME")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(Store::default_dir);
    let no_autostart = std::env::var_os("HIDFI_NO_AUTOSTART").is_some();
    let store = match Store::open(dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("HID-Fi Companion cannot use its folder: {e}");
            std::process::exit(1);
        }
    };
    logfile::FileLog::install(store.dir(), log::LevelFilter::Info);

    // One instance only: a second launch (a double-click, login + manual start)
    // leaves quietly instead of fighting the first for the port.
    let lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(store.dir().join("instance.lock"));
    let _lock = match lock {
        Ok(f) if f.try_lock().is_ok() => f,
        Ok(f) => {
            // Another copy is running. A newer one (or the same version from a
            // fresh download) asks it to quit and takes its place; the same app
            // launched again leaves it be. See handover.rs.
            if !hidfi_companion::handover::ask_to_quit(store.dir()) {
                log::info!("already running");
                return;
            }
            let end = std::time::Instant::now() + Duration::from_secs(10);
            loop {
                if f.try_lock().is_ok() {
                    break f;
                }
                if std::time::Instant::now() > end {
                    log::warn!("the running copy did not quit within 10 s - leaving it running");
                    return;
                }
                std::thread::sleep(Duration::from_millis(200));
            }
        }
        Err(_) => {
            log::info!("already running");
            return;
        }
    };
    hidfi_companion::handover::announce(store.dir());
    // This copy runs: what an update replaced can go.
    hidfi_companion::updates::install::clean_old();
    log::info!("starting, version {}", env!("CARGO_PKG_VERSION"));
    std::panic::set_hook(Box::new(|p| log::error!("panic: {p}")));

    let state = store.state();
    // First run: start at login unless the user turned it off.
    if state.start_at_login && !no_autostart {
        if let Err(e) = autostart::set(true) {
            log::warn!("start at login: {e}");
        }
    }

    let env = Env {
        usb: usb(),
        http: Box::new(UreqHttp::new(Duration::from_secs(7))),
        browse: Box::new(MdnsBrowse::start()),
        source: Arc::new(service::real_source),
        displays: Arc::new(hidfi_stream::display::list),
        capture_allowed: Arc::new(service::capture_allowed),
        updates: Some(hidfi_companion::updates::center::UpdatesSetup::real()),
        sound: Some(Arc::new(hidfi_stream::audio_capture)),
        // A board on the same WiFi answers a ping in milliseconds; 2 s is "not answering".
        probe: Some(hidfi_companion::health::tcp_probe(Duration::from_secs(2))),
    };
    let store_for_settings = Store::open(store.dir().to_path_buf()).expect("same folder");
    // Not paired yet: the window opens by itself to say what to do.
    // Paired with no board yet (a pairing from before 4.0.2, not yet moved to
    // its board's file, counts).
    let first_run = store.keys().is_empty() && !store.dir().join("pairing.key").exists();
    let handle = service::start(store, env);

    #[cfg(target_os = "macos")]
    replaced::watch();
    #[cfg(any(target_os = "macos", windows))]
    desktop::run(handle, state, store_for_settings, first_run);
    #[cfg(target_os = "linux")]
    linux::run(handle, state, store_for_settings, first_run);
}
