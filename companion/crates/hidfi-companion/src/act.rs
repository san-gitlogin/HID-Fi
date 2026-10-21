//! What a tray or window action does - the same on every OS.

use hidfi_companion::menu::Action;
use hidfi_companion::service::{Cmd, Handle};

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

pub fn run(handle: &Handle, a: Action) {
    match a {
        Action::OpenDashboard(u) => open(&format!("{u}/#screen")),
        Action::AllowScreenRecording => {
            #[cfg(target_os = "macos")]
            {
                hidfi_stream::sck::ask();
                open(SCREEN_RECORDING_SETTINGS);
            }
        }
        Action::PairAgain => {
            let _ = handle.tx.send(Cmd::PairAgain);
        }
        Action::ChooseBoard(mac, _) => {
            let _ = handle.tx.send(Cmd::ChooseBoard(mac));
        }
        Action::Forget => {
            let _ = handle.tx.send(Cmd::Forget);
        }
    }
}
