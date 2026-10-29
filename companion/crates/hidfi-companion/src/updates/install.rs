//! "Install and restart": the Companion's new version, downloaded and checked
//! against the signed release (updates.rs `fetch_installer`), put where this
//! app is installed and started - the running copy then hands over to it
//! (handover.rs) and quits. Pairings, settings and keys live in the app's
//! folder, never in the program, so nothing of them changes.
//!
//! Nothing is left half-replaced: the new program is made in full beside the
//! old one first (`<name>.new`), checked, and only then swapped in with two
//! renames; a failed second rename puts the first back. The old program stays
//! as `<name>.old` until the new one starts and removes it (`clean_old`).
//!
//! macOS: the installer is a disk image; it is mounted read-only and out of
//! sight, its app must be HID-Fi Companion (bundle id) and newer, and it is
//! copied with `ditto` (keeps the signature and attributes). Windows and Linux:
//! the installer is the program itself. Windows will not overwrite a running
//! .exe but lets it be renamed - so: rename, then put the new one in its place.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

pub const BUNDLE_ID: &str = "in.hidfi.companion";

fn sibling(p: &Path, suffix: &str) -> PathBuf {
    let mut s = p.as_os_str().to_owned();
    s.push(suffix);
    PathBuf::from(s)
}

/// Removes a path, file or folder; missing is fine.
fn remove(p: &Path) -> std::io::Result<()> {
    match std::fs::symlink_metadata(p) {
        Ok(m) if m.is_dir() => std::fs::remove_dir_all(p),
        Ok(_) => std::fs::remove_file(p),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e),
    }
}

/// `staged` (complete, beside `current`) takes `current`'s place; the old one
/// is kept as `<current>.old`. On any failure `current` is what it was.
pub fn swap_in(current: &Path, staged: &Path) -> Result<(), String> {
    let old = sibling(current, ".old");
    remove(&old).map_err(|e| format!("could not clear {}: {e}", old.display()))?;
    std::fs::rename(current, &old)
        .map_err(|e| format!("could not move the running copy aside: {e}"))?;
    if let Err(e) = std::fs::rename(staged, current) {
        // Put it back: the app stays exactly as it was.
        let back = std::fs::rename(&old, current);
        return Err(format!(
            "could not put the new version in place: {e}{}",
            if back.is_err() {
                " (and the old one could not be put back)"
            } else {
                ""
            }
        ));
    }
    Ok(())
}

/// Removes what an update left: `<this program>.old` (the copy it replaced).
/// Called once the new version runs.
pub fn clean_old() {
    if let Some(p) = installed_path() {
        let old = sibling(&p, ".old");
        if old.exists() {
            match remove(&old) {
                Ok(()) => log::info!("removed the previous version ({})", old.display()),
                Err(e) => log::warn!("could not remove {}: {e}", old.display()),
            }
        }
    }
}

/// Where this app is installed: the .app bundle on macOS, the program elsewhere.
pub fn installed_path() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    if cfg!(target_os = "macos") {
        // .../HID-Fi Companion.app/Contents/MacOS/hidfi-companion
        let app = exe.parent()?.parent()?.parent()?;
        (app.extension()? == "app").then(|| app.to_path_buf())
    } else {
        Some(exe)
    }
}

/// "4.2.0" from the bundle's Info.plist.
#[cfg(target_os = "macos")]
fn plist(app: &Path, key: &str) -> Option<String> {
    let out = Command::new("/usr/libexec/PlistBuddy")
        .args(["-c", &format!("Print {key}")])
        .arg(app.join("Contents/Info.plist"))
        .output()
        .ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// The new app out of the disk image, beside `current` as `<current>.new`.
#[cfg(target_os = "macos")]
fn stage(dmg: &Path, current: &Path) -> Result<PathBuf, String> {
    let mnt = std::env::temp_dir().join(format!("hidfi-update-{}", std::process::id()));
    let _ = std::fs::create_dir_all(&mnt);
    let ok = Command::new("hdiutil")
        .args([
            "attach",
            "-nobrowse",
            "-readonly",
            "-noautoopen",
            "-quiet",
            "-mountpoint",
        ])
        .arg(&mnt)
        .arg(dmg)
        .status()
        .map_err(|e| e.to_string())?
        .success();
    if !ok {
        return Err("the downloaded disk image would not open".into());
    }
    let r = (|| {
        let apps: Vec<PathBuf> = std::fs::read_dir(&mnt)
            .map_err(|e| e.to_string())?
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|x| x == "app"))
            .collect();
        let [app] = apps.as_slice() else {
            return Err(format!("the disk image holds {} apps, not one", apps.len()));
        };
        if plist(app, "CFBundleIdentifier").as_deref() != Some(BUNDLE_ID) {
            return Err("the disk image's app is not HID-Fi Companion".into());
        }
        let v = plist(app, "CFBundleShortVersionString").unwrap_or_default();
        if !crate::handover::newer(&v, crate::handover::VERSION) {
            return Err(format!("the disk image holds {v}, not a newer version"));
        }
        let staged = sibling(current, ".new");
        remove(&staged).map_err(|e| e.to_string())?;
        let ok = Command::new("ditto")
            .arg(app)
            .arg(&staged)
            .status()
            .map_err(|e| e.to_string())?
            .success();
        if !ok {
            let _ = remove(&staged);
            return Err(format!(
                "could not copy it into {}",
                current
                    .parent()
                    .map_or(String::new(), |p| p.display().to_string())
            ));
        }
        let ok = Command::new("codesign")
            .args(["--verify", "--deep", "--strict"])
            .arg(&staged)
            .status()
            .is_ok_and(|s| s.success());
        if !ok {
            let _ = remove(&staged);
            return Err("the new app's signature does not hold".into());
        }
        Ok(staged)
    })();
    let _ = Command::new("hdiutil")
        .args(["detach", "-quiet", "-force"])
        .arg(&mnt)
        .status();
    let _ = std::fs::remove_dir(&mnt);
    r
}

/// The new program, beside `current` as `<current>.new`, ready to run.
#[cfg(not(target_os = "macos"))]
fn stage(installer: &Path, current: &Path) -> Result<PathBuf, String> {
    let staged = sibling(current, ".new");
    remove(&staged).map_err(|e| e.to_string())?;
    std::fs::copy(installer, &staged)
        .map_err(|e| format!("could not copy it next to this app: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&staged, std::fs::Permissions::from_mode(0o755))
            .map_err(|e| e.to_string())?;
    }
    // It must be a HID-Fi Companion that runs here, and the newer one.
    let out = Command::new(&staged)
        .arg("--version")
        .output()
        .map_err(|e| format!("the new version does not start here: {e}"))?;
    let said = String::from_utf8_lossy(&out.stdout);
    let v = said.trim().strip_prefix("HID-Fi Companion ").unwrap_or("");
    if !crate::handover::newer(v, crate::handover::VERSION) {
        let _ = remove(&staged);
        return Err(format!(
            "the download is not a newer HID-Fi Companion ({})",
            said.trim()
        ));
    }
    Ok(staged)
}

/// Install the downloaded, checked installer in place of this app and start
/// it. Err says why not; the caller then opens the installer for the person.
pub fn install_and_restart(installer: &Path) -> Result<(), String> {
    let current =
        installed_path().ok_or("this copy of the app is not installed in a known place")?;
    let staged = stage(installer, &current)?;
    swap_in(&current, &staged)?;
    log::info!(
        "installed the new version at {}; starting it",
        current.display()
    );
    let started = if cfg!(target_os = "macos") {
        relaunch_mac(&current)
    } else {
        Command::new(&current).spawn()
    };
    match started {
        // It asks this copy to quit (handover.rs) once it is up.
        Ok(_) => Ok(()),
        Err(e) => {
            // Could not start it: the old copy keeps running, from its .old name;
            // the new one is in place for the next start.
            Err(format!(
                "installed, but it did not start ({e}): quit and open HID-Fi Companion"
            ))
        }
    }
}

/// macOS: `open -n` (a new instance, not the running one brought forward).
/// LaunchServices starts it without this process's environment, so this app's
/// own settings from it (HIDFI_HOME, HIDFI_RELEASES, ...) are passed on: a copy
/// run with its own folder must never come back using another's.
fn relaunch_mac(app: &Path) -> std::io::Result<std::process::Child> {
    let mut c = Command::new("open");
    c.arg("-n");
    for (k, v) in own_env() {
        c.arg("--env").arg(format!("{k}={v}"));
    }
    c.arg(app).spawn()
}

/// The environment this app reads (HIDFI_*, RUST_LOG).
pub fn own_env() -> Vec<(String, String)> {
    let mut v: Vec<(String, String)> = std::env::vars()
        .filter(|(k, _)| k.starts_with("HIDFI_") || k == "RUST_LOG")
        .collect();
    v.sort();
    v
}

/// How long the new copy has to take over before this one says so.
pub const TAKEOVER: Duration = Duration::from_secs(20);

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "hidfi-install-{}-{:?}",
            std::process::id(),
            std::thread::current().id()
        ));
        let _ = remove(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn the_new_version_takes_the_old_ones_place_and_the_old_one_is_kept_aside() {
        let d = tmp();
        let (cur, new) = (
            d.join("HID-Fi Companion.app"),
            d.join("HID-Fi Companion.app.new"),
        );
        std::fs::create_dir_all(cur.join("Contents")).unwrap();
        std::fs::write(cur.join("Contents/v"), "4.1.0").unwrap();
        std::fs::create_dir_all(new.join("Contents")).unwrap();
        std::fs::write(new.join("Contents/v"), "4.2.0").unwrap();
        // A leftover .old from an earlier update does not stop this one.
        std::fs::create_dir_all(d.join("HID-Fi Companion.app.old")).unwrap();
        swap_in(&cur, &new).unwrap();
        assert_eq!(
            std::fs::read_to_string(cur.join("Contents/v")).unwrap(),
            "4.2.0"
        );
        assert_eq!(
            std::fs::read_to_string(d.join("HID-Fi Companion.app.old/Contents/v")).unwrap(),
            "4.1.0"
        );
        assert!(!new.exists());
    }

    #[test]
    fn a_failed_swap_leaves_the_app_as_it_was() {
        let d = tmp();
        let cur = d.join("hidfi-companion");
        std::fs::write(&cur, "old").unwrap();
        // Nothing staged: the second rename fails, the first is undone.
        let e = swap_in(&cur, &d.join("missing.new")).unwrap_err();
        assert!(e.contains("could not put the new version in place"), "{e}");
        assert_eq!(std::fs::read_to_string(&cur).unwrap(), "old");
        assert!(!d.join("hidfi-companion.old").exists());
    }

    #[test]
    fn the_running_program_can_be_moved_aside_on_this_os() {
        // What Windows allows for a running .exe and every OS for this test's own:
        // a rename. Copy this test binary, start nothing, prove rename-while-open works.
        let d = tmp();
        let cur = d.join("prog");
        std::fs::copy(std::env::current_exe().unwrap(), &cur).unwrap();
        let f = std::fs::File::open(&cur).unwrap(); // held open, as a running program is
        std::fs::write(d.join("prog.new"), "new").unwrap();
        swap_in(&cur, &d.join("prog.new")).unwrap();
        drop(f);
        assert_eq!(std::fs::read_to_string(&cur).unwrap(), "new");
    }
}
