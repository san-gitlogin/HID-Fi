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

use hidfi_agent::real::{MdnsBrowse, NusbList, UreqHttp};
use hidfi_companion::service::{self, Env};
use hidfi_companion::store::Store;
use std::sync::Arc;
use std::time::Duration;

fn main() {
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
        _ => {
            log::info!("already running");
            return;
        }
    };
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
        usb: Box::new(NusbList),
        http: Box::new(UreqHttp::new(Duration::from_secs(7))),
        browse: Box::new(MdnsBrowse::start()),
        source: Arc::new(service::real_source),
        displays: Arc::new(hidfi_stream::display::list),
        capture_allowed: Arc::new(service::capture_allowed),
    };
    let store_for_settings = Store::open(store.dir().to_path_buf()).expect("same folder");
    // Not paired yet: the window opens by itself to say what to do.
    let first_run = store.key().is_none();
    let handle = service::start(store, env);

    #[cfg(target_os = "macos")]
    replaced::watch();
    #[cfg(any(target_os = "macos", windows))]
    desktop::run(handle, state, store_for_settings, first_run);
    #[cfg(target_os = "linux")]
    linux::run(handle, state, store_for_settings, first_run);
}
