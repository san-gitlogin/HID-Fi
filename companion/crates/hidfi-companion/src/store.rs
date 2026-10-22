//! What the app keeps between runs, in the per-user app folder:
//!   macOS   ~/Library/Application Support/HID-Fi Companion/
//!   Windows %APPDATA%\HID-Fi Companion\
//!   Linux   $XDG_CONFIG_HOME/hidfi-companion/ (~/.config/...)
//!
//! `state.json`   this install's id and name, the board it uses, its display
//! `pairing.key`  the pairing key (hex), owner-only
//! `dtls.cert` / `dtls.key`  the WebRTC certificate, so its fingerprint is
//!                stable and the board can vouch for it; owner-only
//!
//! Every write is atomic (temporary file, then rename), so a crash or power
//! cut mid-write leaves the previous file, never half of one. A missing or
//! unreadable file is treated as "not set", never as a reason to crash.
//!
//! The key is in a file, not the OS keychain, until the app is code-signed:
//! an unsigned binary's keychain access is tied to its exact build, so every
//! update would ask the user to allow access again (DESIGN.md, known gaps).

use hidfi_core::discovery::Mac;
use hidfi_core::pairing::PairKey;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

#[derive(Serialize, Deserialize, Clone, Debug, Default, PartialEq, Eq)]
pub struct State {
    /// 32 hex digits, made once per install.
    pub id: String,
    /// What the dashboard calls this computer.
    pub name: String,
    /// Before 4.0.2 (one board per computer). Read once, into `boards`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub board_mac: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub board_url: Option<String>,
    /// Every board this computer has used, with the address that worked last:
    /// tried first next time (no name lookup). Its pairing key is a file of
    /// its own (`pairing-<MAC>.key`).
    #[serde(default)]
    pub boards: Vec<KnownBoard>,
    /// Which display to share (a CGDirectDisplayID on macOS); None = main.
    pub display: Option<u32>,
    /// Start when the user logs in. On by default (A16).
    #[serde(default = "yes")]
    pub start_at_login: bool,
}

#[derive(Serialize, Deserialize, Clone, Debug, PartialEq, Eq)]
pub struct KnownBoard {
    /// 12 hex digits, upper case.
    pub mac: String,
    pub url: String,
}

fn yes() -> bool {
    true
}

pub struct Store {
    dir: PathBuf,
}

impl Store {
    pub fn default_dir() -> PathBuf {
        let base = dirs::config_dir().unwrap_or_else(std::env::temp_dir);
        if cfg!(target_os = "linux") {
            base.join("hidfi-companion")
        } else {
            base.join("HID-Fi Companion")
        }
    }

    pub fn open(dir: PathBuf) -> std::io::Result<Store> {
        fs::create_dir_all(&dir)?;
        private_dir(&dir);
        Ok(Store { dir })
    }

    pub fn dir(&self) -> &Path {
        &self.dir
    }

    /// The saved state, or a new one (fresh id, this computer's name).
    pub fn state(&self) -> State {
        let s: Option<State> = fs::read(self.dir.join("state.json"))
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok());
        match s {
            Some(s) if s.id.len() == 32 && s.id.bytes().all(|c| c.is_ascii_hexdigit()) => s,
            _ => {
                let mut id = [0u8; 16];
                getrandom::fill(&mut id).expect("OS randomness");
                let s = State {
                    id: hex::encode(id),
                    name: computer_name(),
                    start_at_login: true,
                    ..State::default()
                };
                if let Err(e) = self.save_state(&s) {
                    log::warn!("could not save state: {e}");
                }
                s
            }
        }
    }

    pub fn save_state(&self, s: &State) -> std::io::Result<()> {
        let body = serde_json::to_vec_pretty(s).expect("plain struct");
        write_atomic(&self.dir.join("state.json"), &body, false)
    }

    fn key_path(&self, mac: &Mac) -> PathBuf {
        self.dir.join(format!("pairing-{}.key", mac.hex()))
    }

    /// The pairing key with this board, if paired.
    pub fn key_for(&self, mac: &Mac) -> Option<PairKey> {
        read_key(&self.key_path(mac))
    }

    /// Every board this computer is paired with.
    pub fn keys(&self) -> Vec<(Mac, PairKey)> {
        let mut v: Vec<(Mac, PairKey)> = fs::read_dir(&self.dir)
            .into_iter()
            .flatten()
            .flatten()
            .filter_map(|e| {
                let name = e.file_name().into_string().ok()?;
                let mac = Mac::parse(name.strip_prefix("pairing-")?.strip_suffix(".key")?)?;
                Some((mac, read_key(&e.path())?))
            })
            .collect();
        v.sort_by_key(|(m, _)| *m);
        v
    }

    pub fn save_key_for(&self, mac: &Mac, k: Option<&PairKey>) -> std::io::Result<()> {
        let p = self.key_path(mac);
        match k {
            Some(k) => write_atomic(&p, hex::encode(k.as_bytes()).as_bytes(), true),
            None => match fs::remove_file(&p) {
                Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e),
                _ => Ok(()),
            },
        }
    }

    /// The WebRTC certificate, made on first use and kept.
    pub fn cert(&self) -> Option<hidfi_stream::rtc::DtlsCert> {
        let (c, k) = (self.dir.join("dtls.cert"), self.dir.join("dtls.key"));
        if let (Ok(certificate), Ok(private_key)) = (fs::read(&c), fs::read(&k)) {
            if !certificate.is_empty() && !private_key.is_empty() {
                return Some(hidfi_stream::rtc::DtlsCert {
                    certificate,
                    private_key,
                });
            }
        }
        let made = hidfi_stream::rtc::new_cert()?;
        let saved = write_atomic(&k, &made.private_key, true)
            .and_then(|_| write_atomic(&c, &made.certificate, true));
        if let Err(e) = saved {
            log::warn!(
                "could not save the certificate (its fingerprint will change next run): {e}"
            );
        }
        Some(made)
    }

    /// Brings a store from before 4.0.2 (one board, one `pairing.key`) to one
    /// key per board, so an update never loses a pairing. Idempotent.
    pub fn migrate(&self, s: &mut State) -> std::io::Result<()> {
        let legacy = self.dir.join("pairing.key");
        if let Some(mac) = s.board_mac.as_deref().and_then(Mac::parse) {
            if let Some(url) = s.board_url.take() {
                if !s.boards.iter().any(|b| b.mac == mac.hex()) {
                    s.boards.push(KnownBoard {
                        mac: mac.hex(),
                        url,
                    });
                }
            }
            if let Some(k) = read_key(&legacy) {
                if self.key_for(&mac).is_none() {
                    self.save_key_for(&mac, Some(&k))?;
                }
            }
            s.board_mac = None;
            self.save_state(s)?;
        }
        match fs::remove_file(&legacy) {
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e),
            _ => Ok(()),
        }
    }

    /// Remembers the address that worked for this board.
    pub fn remember(&self, s: &mut State, mac: &Mac, url: &str) -> std::io::Result<()> {
        match s.boards.iter_mut().find(|b| b.mac == mac.hex()) {
            Some(b) if b.url == url => return Ok(()),
            Some(b) => b.url = url.to_string(),
            None => s.boards.push(KnownBoard {
                mac: mac.hex(),
                url: url.to_string(),
            }),
        }
        self.save_state(s)
    }

    /// The remembered addresses, for finding.
    pub fn remembered(s: &State) -> Vec<(Mac, String)> {
        s.boards
            .iter()
            .filter_map(|b| Some((Mac::parse(&b.mac)?, b.url.clone())))
            .collect()
    }
}

fn read_key(p: &Path) -> Option<PairKey> {
    let t = fs::read_to_string(p).ok()?;
    let mut b = [0u8; 32];
    hex::decode_to_slice(t.trim(), &mut b).ok()?;
    Some(PairKey::from_bytes(b))
}

/// The name a person would recognise: the computer's own name.
pub fn computer_name() -> String {
    let raw = std::env::var("COMPUTERNAME")
        .ok()
        .or_else(|| {
            std::process::Command::new(if cfg!(target_os = "macos") {
                "scutil"
            } else {
                "hostname"
            })
            .args(if cfg!(target_os = "macos") {
                &["--get", "ComputerName"][..]
            } else {
                &[][..]
            })
            .output()
            .ok()
            .and_then(|o| String::from_utf8(o.stdout).ok())
        })
        .unwrap_or_default();
    hidfi_core::proto::clean_name(raw.trim())
}

fn write_atomic(path: &Path, data: &[u8], private: bool) -> std::io::Result<()> {
    let tmp = path.with_extension("tmp");
    {
        let mut f = open_new(&tmp, private)?;
        f.write_all(data)?;
        f.sync_all()?;
    }
    fs::rename(&tmp, path)
}

#[cfg(unix)]
fn open_new(p: &Path, private: bool) -> std::io::Result<fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    let _ = fs::remove_file(p);
    fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(if private { 0o600 } else { 0o644 })
        .open(p)
}

#[cfg(not(unix))]
fn open_new(p: &Path, _private: bool) -> std::io::Result<fs::File> {
    // %APPDATA% is already readable only by this user.
    let _ = fs::remove_file(p);
    fs::OpenOptions::new().write(true).create_new(true).open(p)
}

fn private_dir(_dir: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(_dir, fs::Permissions::from_mode(0o700));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp() -> Store {
        let mut id = [0u8; 8];
        getrandom::fill(&mut id).unwrap();
        Store::open(std::env::temp_dir().join(format!("hidfi-store-{}", hex::encode(id)))).unwrap()
    }

    #[test]
    fn a_new_install_gets_an_id_that_sticks() {
        let s = tmp();
        let a = s.state();
        assert_eq!(a.id.len(), 32);
        assert!(a.start_at_login);
        assert_eq!(s.state().id, a.id);
        fs::remove_dir_all(s.dir()).unwrap();
    }

    #[test]
    fn a_corrupt_state_file_is_replaced_not_fatal() {
        let s = tmp();
        fs::write(s.dir().join("state.json"), b"{not json").unwrap();
        assert_eq!(s.state().id.len(), 32);
        fs::write(s.dir().join("state.json"), br#"{"id":"zz","name":"x"}"#).unwrap();
        assert_ne!(s.state().id, "zz");
        fs::remove_dir_all(s.dir()).unwrap();
    }

    #[test]
    fn each_board_has_its_own_key_private_and_forgettable() {
        let s = tmp();
        let (a, b) = (
            Mac::parse("DCB4D909F7C8").unwrap(),
            Mac::parse("D4F98DB4A1B0").unwrap(),
        );
        assert!(s.key_for(&a).is_none() && s.keys().is_empty());
        let (ka, kb) = (PairKey::from_bytes([9; 32]), PairKey::from_bytes([7; 32]));
        s.save_key_for(&a, Some(&ka)).unwrap();
        s.save_key_for(&b, Some(&kb)).unwrap();
        assert!(s.key_for(&a).unwrap() == ka && s.key_for(&b).unwrap() == kb);
        assert_eq!(
            s.keys().len(),
            2,
            "both pairings kept: moving a board never costs the other"
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(s.dir().join("pairing-DCB4D909F7C8.key"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600);
            let dmode = fs::metadata(s.dir()).unwrap().permissions().mode() & 0o777;
            assert_eq!(dmode, 0o700);
        }
        s.save_key_for(&a, None).unwrap();
        s.save_key_for(&a, None).unwrap(); // forgetting twice is fine
        assert!(s.key_for(&a).is_none());
        assert!(
            s.key_for(&b).is_some(),
            "forgetting one board keeps the other"
        );
        fs::write(s.dir().join("pairing-DCB4D909F7C8.key"), "short").unwrap();
        assert!(s.key_for(&a).is_none(), "a damaged key is no key");
        fs::remove_dir_all(s.dir()).unwrap();
    }

    #[test]
    fn an_install_from_before_keeps_its_pairing() {
        // 4.0.1 and earlier: one board in state.json, its key in pairing.key.
        let s = tmp();
        let k = PairKey::from_bytes([5; 32]);
        fs::write(s.dir().join("pairing.key"), hex::encode(k.as_bytes())).unwrap();
        let mut st = s.state();
        st.board_mac = Some("DCB4D909F7C8".into());
        st.board_url = Some("http://192.168.0.101".into());
        s.save_state(&st).unwrap();
        let mut st = s.state();
        s.migrate(&mut st).unwrap();
        let mac = Mac::parse("DCB4D909F7C8").unwrap();
        assert!(
            s.key_for(&mac).unwrap() == k,
            "the pairing moved to its board's file"
        );
        assert!(!s.dir().join("pairing.key").exists());
        assert_eq!(
            Store::remembered(&st),
            vec![(mac, "http://192.168.0.101".to_string())]
        );
        let again = s.state();
        assert_eq!(again.board_mac, None);
        assert_eq!(again.boards.len(), 1, "and it is saved");
        s.migrate(&mut s.state()).unwrap(); // twice is harmless
        assert!(s.key_for(&mac).unwrap() == k);
        fs::remove_dir_all(s.dir()).unwrap();
    }

    #[test]
    fn the_certificate_is_stable_across_runs() {
        let s = tmp();
        let a = s.cert().unwrap();
        let b = s.cert().unwrap();
        assert_eq!(
            hidfi_stream::rtc::cert_fp(&a),
            hidfi_stream::rtc::cert_fp(&b)
        );
        fs::remove_dir_all(s.dir()).unwrap();
    }

    #[test]
    fn the_computer_has_a_name() {
        let n = computer_name();
        assert!(!n.is_empty() && n.len() <= 32);
    }
}
