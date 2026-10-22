//! What a tray or window action does - the same on every OS.

use hidfi_companion::menu::Action;
use hidfi_companion::opener::{self, Opener};
use hidfi_companion::service::{BoardState, Cmd, Handle};
use std::sync::OnceLock;

pub fn open(target: &str) {
    let r = if cfg!(target_os = "macos") {
        std::process::Command::new("open").arg(target).spawn()
    } else if cfg!(windows) {
        std::process::Command::new("cmd")
            .args(["/C", "start", "", target])
            .spawn()
    } else {
        std::process::Command::new("xdg-open").arg(target).spawn()
    };
    if let Err(e) = r {
        log::warn!("could not open {target}: {e}");
    }
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const SCREEN_RECORDING_SETTINGS: &str =
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";

fn network_settings() {
    if cfg!(target_os = "macos") {
        open("x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork");
    } else if cfg!(windows) {
        open("ms-settings:network-status");
    }
}

/// The dashboard, through this app's own page on 127.0.0.1 (see opener.rs):
/// it tries the board's name and address from the browser, and says what to
/// switch on when the browser is kept off the network.
fn open_dashboard(handle: &Handle, url: &str) {
    static OPENER: OnceLock<Option<Opener>> = OnceLock::new();
    let o = OPENER.get_or_init(|| match Opener::start(Box::new(network_settings)) {
        Ok(o) => Some(o),
        Err(e) => {
            log::warn!("could not start the dashboard opener: {e}");
            None
        }
    });
    let mac = {
        let v = handle.view.lock().unwrap_or_else(|e| e.into_inner());
        v.boards
            .iter()
            .find(|b| matches!(&b.board, BoardState::Found { url: f, .. } if f == url))
            .map(|b| b.mac.clone())
    };
    match o {
        Some(o) => open(&o.page_for(
            opener::candidates(url, mac.as_deref()),
            hidfi_companion::browser::default_browser(),
        )),
        None => open(&format!("{url}/#screen")),
    }
}

pub fn run(handle: &Handle, a: Action) {
    match a {
        Action::OpenDashboard(u) => open_dashboard(handle, &u),
        Action::AllowScreenRecording => {
            #[cfg(target_os = "macos")]
            {
                hidfi_stream::sck::ask();
                open(SCREEN_RECORDING_SETTINGS);
            }
        }
        Action::OpenNetworkSettings => network_settings(),
        Action::PairAgain(mac) => {
            let _ = handle.tx.send(Cmd::PairAgain(mac));
        }
        Action::Forget(mac) => {
            let _ = handle.tx.send(Cmd::Forget(mac));
        }
    }
}
