//! Starting at login (A16), the way each OS expects, with no admin rights:
//!   macOS   ~/Library/LaunchAgents/in.hidfi.companion.plist (launchd; restarts
//!           the app if it crashes, not if the user quits it)
//!   Windows HKCU\Software\Microsoft\Windows\CurrentVersion\Run
//!   Linux   ~/.config/autostart/hidfi-companion.desktop (XDG)
//! Turning it off removes exactly what turning it on wrote.

use std::path::{Path, PathBuf};

pub const LABEL: &str = "in.hidfi.companion";

fn exe() -> std::io::Result<PathBuf> {
    std::env::current_exe()
}

/// XML-escape for the plist: a path can contain & or <.
fn esc(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

pub fn plist(exe: &Path) -> String {
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
"#,
        esc(&exe.display().to_string())
    )
}

#[cfg_attr(not(all(unix, not(target_os = "macos"))), allow(dead_code))]
pub fn desktop_entry(exe: &Path) -> String {
    format!(
        "[Desktop Entry]\nType=Application\nName=HID-Fi Companion\nComment=Shows this computer's screen on the HID-Fi dashboard\nExec=\"{}\"\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
        exe.display().to_string().replace('"', "\\\"")
    )
}

pub fn set(on: bool) -> std::io::Result<()> {
    #[cfg(target_os = "macos")]
    {
        let p = dirs::home_dir()
            .unwrap_or_default()
            .join("Library/LaunchAgents")
            .join(format!("{LABEL}.plist"));
        if on {
            std::fs::create_dir_all(p.parent().unwrap())?;
            std::fs::write(&p, plist(&exe()?))
        } else {
            remove(&p)
        }
    }
    #[cfg(target_os = "windows")]
    {
        let mut c = std::process::Command::new("reg");
        let key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
        if on {
            c.args([
                "add",
                key,
                "/v",
                "HID-Fi Companion",
                "/t",
                "REG_SZ",
                "/f",
                "/d",
            ])
            .arg(format!("\"{}\"", exe()?.display()));
        } else {
            c.args(["delete", key, "/v", "HID-Fi Companion", "/f"]);
        }
        let st = c.status()?;
        if st.success() || !on {
            Ok(())
        } else {
            Err(std::io::Error::other("reg failed"))
        }
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let p = dirs::config_dir()
            .unwrap_or_default()
            .join("autostart/hidfi-companion.desktop");
        if on {
            std::fs::create_dir_all(p.parent().unwrap())?;
            std::fs::write(&p, desktop_entry(&exe()?))
        } else {
            remove(&p)
        }
    }
}

#[allow(dead_code)]
fn remove(p: &Path) -> std::io::Result<()> {
    match std::fs::remove_file(p) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_plist_is_well_formed_and_escapes_the_path() {
        let p = plist(Path::new(
            "/Applications/A & B <x>.app/Contents/MacOS/hidfi-companion",
        ));
        assert!(p.contains("A &amp; B &lt;x&gt;.app"));
        assert!(p.contains("<key>RunAtLoad</key><true/>"));
        assert!(
            p.contains("<key>SuccessfulExit</key><false/>"),
            "a quit stays quit"
        );
        assert_eq!(p.matches("<dict>").count(), p.matches("</dict>").count());
    }

    #[test]
    fn the_desktop_entry_quotes_the_path() {
        let d = desktop_entry(Path::new("/opt/hid fi/hidfi-companion"));
        assert!(d.contains("Exec=\"/opt/hid fi/hidfi-companion\""));
        assert!(d.starts_with("[Desktop Entry]\n"));
    }
}
