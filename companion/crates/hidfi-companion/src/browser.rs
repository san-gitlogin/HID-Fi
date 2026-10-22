//! The name of this computer's default web browser - the one "Open the
//! dashboard" opens. The page can only see a user agent, and Arc, Vivaldi,
//! Chromium and other Chromium browsers all say "Chrome" there: telling an Arc
//! user to switch on Google Chrome would send them to the wrong switch.

use std::process::Command;

/// Best effort: `None` when the OS does not say.
pub fn default_browser() -> Option<String> {
    if cfg!(target_os = "macos") {
        mac()
    } else if cfg!(windows) {
        windows()
    } else {
        linux()
    }
}

fn run(cmd: &str, args: &[&str]) -> Option<String> {
    let o = Command::new(cmd).args(args).output().ok()?;
    o.status
        .success()
        .then(|| String::from_utf8_lossy(&o.stdout).trim().to_string())
}

fn mac() -> Option<String> {
    let home = dirs::home_dir()?;
    let plist = home
        .join("Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist");
    let json = run("plutil", &["-convert", "json", "-o", "-", plist.to_str()?]);
    // Never chosen: macOS opens web links in Safari.
    let id = json
        .and_then(|j| mac_handler(&j))
        .unwrap_or_else(|| "com.apple.safari".into());
    if let Some(n) = known(&id) {
        return Some(n.into());
    }
    // Not one we know: ask Spotlight where the app is, and read its name.
    let q = format!("kMDItemCFBundleIdentifier == \"{id}\"c");
    let app = run("mdfind", &[&q])?.lines().next()?.to_string();
    let info = format!("{app}/Contents/Info.plist");
    run("plutil", &["-extract", "CFBundleDisplayName", "raw", &info])
        .or_else(|| run("plutil", &["-extract", "CFBundleName", "raw", &info]))
        .filter(|n| !n.is_empty())
}

/// The bundle id LaunchServices opens http links with.
pub fn mac_handler(json: &str) -> Option<String> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    v["LSHandlers"].as_array()?.iter().find_map(|h| {
        (h["LSHandlerURLScheme"].as_str() == Some("http"))
            .then(|| h["LSHandlerRoleAll"].as_str().map(str::to_lowercase))
            .flatten()
    })
}

fn known(bundle_id: &str) -> Option<&'static str> {
    Some(match bundle_id {
        "com.apple.safari" => "Safari",
        "com.google.chrome" => "Google Chrome",
        "org.mozilla.firefox" => "Firefox",
        "com.microsoft.edgemac" => "Microsoft Edge",
        "com.brave.browser" => "Brave",
        "company.thebrowser.browser" => "Arc",
        "com.vivaldi.vivaldi" => "Vivaldi",
        "com.operasoftware.opera" => "Opera",
        "org.chromium.chromium" => "Chromium",
        _ => return None,
    })
}

fn windows() -> Option<String> {
    let out = run(
        "reg",
        &[
            "query",
            r"HKCU\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
            "/v",
            "ProgId",
        ],
    )?;
    let prog = out.split_whitespace().last()?;
    windows_name(prog).map(Into::into)
}

/// A Windows ProgId (`ChromeHTML`, `MSEdgeHTM`, `FirefoxURL-308046B0AF4A39CB`).
pub fn windows_name(prog_id: &str) -> Option<&'static str> {
    let p = prog_id.to_ascii_lowercase();
    [
        ("chromehtml", "Google Chrome"),
        ("msedgehtm", "Microsoft Edge"),
        ("firefoxurl", "Firefox"),
        ("bravehtml", "Brave"),
        ("operastable", "Opera"),
        ("vivaldihtm", "Vivaldi"),
        ("chromiumhtm", "Chromium"),
    ]
    .iter()
    .find(|(k, _)| p.starts_with(k))
    .map(|(_, n)| *n)
}

fn linux() -> Option<String> {
    linux_name(&run("xdg-settings", &["get", "default-web-browser"])?)
}

/// A desktop file name (`firefox.desktop`, `com.brave.Browser.desktop`).
pub fn linux_name(desktop: &str) -> Option<String> {
    let s = desktop
        .trim()
        .trim_end_matches(".desktop")
        .to_ascii_lowercase();
    let n = [
        ("firefox", "Firefox"),
        ("google-chrome", "Google Chrome"),
        ("chromium", "Chromium"),
        ("brave", "Brave"),
        ("microsoft-edge", "Microsoft Edge"),
        ("vivaldi", "Vivaldi"),
        ("opera", "Opera"),
    ]
    .iter()
    .find(|(k, _)| s.contains(k))
    .map(|(_, n)| n.to_string());
    n.or_else(|| (!s.is_empty()).then(|| s.rsplit('.').next().unwrap_or(&s).to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_default_browser_is_read_from_each_os_s_own_record() {
        let j = r#"{"LSHandlers":[{"LSHandlerURLScheme":"mailto","LSHandlerRoleAll":"com.apple.mail"},
                    {"LSHandlerURLScheme":"http","LSHandlerRoleAll":"company.thebrowser.Browser"}]}"#;
        let id = mac_handler(j).unwrap();
        assert_eq!(
            known(&id),
            Some("Arc"),
            "Arc, not the Chrome its user agent claims"
        );
        assert_eq!(mac_handler(r#"{"LSHandlers":[]}"#), None);
        assert_eq!(windows_name("FirefoxURL-308046B0AF4A39CB"), Some("Firefox"));
        assert_eq!(windows_name("MSEdgeHTM"), Some("Microsoft Edge"));
        assert_eq!(windows_name("SomethingElse"), None);
        assert_eq!(
            linux_name("com.brave.Browser.desktop").as_deref(),
            Some("Brave")
        );
        assert_eq!(
            linux_name("firefox_firefox.desktop").as_deref(),
            Some("Firefox")
        );
        assert_eq!(
            linux_name("org.qutebrowser.qutebrowser.desktop").as_deref(),
            Some("qutebrowser")
        );
    }
}
