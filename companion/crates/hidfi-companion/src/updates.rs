//! Updates for every HID-Fi on this computer, and for the Companion itself
//! (docs/OTA.md, step 8). The Companion
//!
//!   - reads the newest release from a manifest it checks the signature of first,
//!   - tells, per board: which firmware runs, whether the two still speak the same
//!     protocol, what is newer and what changed, and whether it goes over the air
//!     or needs the cable once,
//!   - updates over the air through the board's own engine (signed requests: the
//!     board checks the signature and the image itself), relaying the release from
//!     this computer when the board cannot reach the internet,
//!   - updates by cable when the air is not possible (hidfi-flash: checkpoints in a
//!     journal, cancel puts everything back, resume carries on),
//!   - downloads a newer Companion installer, checked against the signed manifest,
//!     and opens it only when the person asks.
//!
//! Every decision is a pure function below, tested on its own; the I/O sits
//! behind traits so the jobs run against fakes in tests.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use hidfi_core::ota::{self, Release, ReleaseError, Verdict};
use serde_json::Value;

/// Where releases are published. A test or a person can point the Companion
/// elsewhere with HIDFI_RELEASES; the signature rules do not change.
pub const RELEASES: &str = "https://github.com/san-gitlogin/HID-Fi/releases/latest/download/";
pub const MANIFEST: &str = "hidfi-ota.json";
pub const SIGNATURE: &str = "hidfi-ota.sig";
const IMAGE_CAP: usize = 6 * 1024 * 1024 + 0x40_0000;
const INSTALLER_CAP: usize = 200 * 1024 * 1024;
/// An update that shows no progress for this long, with nothing to wait for, failed.
pub const STALL: Duration = Duration::from_secs(10 * 60);
/// How often the Companion looks for a release on its own.
pub const CHECK_EVERY: Duration = Duration::from_secs(24 * 3600);

/// The key ids a release is signed with (embedded at release time, step 9).
/// A build with the feature "test-ota-key" also trusts the public TEST key of
/// tests/vectors/ota_v1.json - never a release.
pub fn trusted_keys() -> Vec<[u8; 65]> {
    #[allow(unused_mut)]
    let mut k: Vec<[u8; 65]> = ota::RELEASE_KEYS.to_vec();
    #[cfg(feature = "test-ota-key")]
    k.push(test_key());
    k
}

/// tests/vectors/ota_v1.json "test_public_key" - public, deliberately; a release
/// gate refuses a Companion build that contains it.
#[cfg(any(test, feature = "test-ota-key"))]
pub const TEST_KEY_HEX: &str = "0446f73c2faa078cc0ed394029be39afcda7fd03afaa8506813679dc7a659057fba6a6832b1a0179f1c1bf76edc1cb91395367fee91bb2eb6a9f46224d362ce626";

#[cfg(any(test, feature = "test-ota-key"))]
pub fn test_key() -> [u8; 65] {
    let mut k = [0u8; 65];
    hex::decode_to_slice(TEST_KEY_HEX, &mut k).expect("the test key is 65 bytes of hex");
    k
}

pub fn os_name() -> &'static str {
    if cfg!(target_os = "macos") {
        "macos"
    } else if cfg!(windows) {
        "windows"
    } else {
        "linux"
    }
}

// ---- what the person is offered ---------------------------------------------------------

/// What one board can be offered, from its status and the newest release.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Offer {
    /// No release known yet, or the board did not answer.
    Unknown,
    UpToDate,
    /// The board is proving a new firmware; nothing is offered until it is done.
    Verifying,
    Board {
        to: String,
        size: u64,
        /// The CHANGELOG sections newer than what runs, newest first: (version, text).
        changes: Vec<(String, String)>,
        security: bool,
        /// Over the air is possible (two-slot layout, and the board is reachable
        /// and paired); else the cable.
        over_air: bool,
        /// The board still has the single-slot layout: one cable update switches it.
        cable_once: bool,
    },
    /// This Companion is too old for the board's protocol.
    UpdateCompanionFirst,
}

/// The board's own status (GET /api/status) as the update logic reads it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Seen {
    pub firmware: Option<String>,
    pub layout: Option<String>,
    pub state: Option<String>,
    pub result: Option<String>,
    pub step: String,
    pub bytes: u64,
    pub total: u64,
    pub paused: bool,
    pub available: String,
    pub error: String,
    pub comp_api: Option<u32>,
    pub uptime: u64,
}

impl Seen {
    pub fn from_status(v: &Value) -> Seen {
        let s = |k: &str| v.get(k).and_then(Value::as_str).map(str::to_string);
        let n = |k: &str| v.get(k).and_then(Value::as_u64).unwrap_or(0);
        Seen {
            firmware: s("firmware"),
            layout: s("ota_layout"),
            state: s("ota_state"),
            result: s("ota_result"),
            step: s("ota_step").unwrap_or_else(|| "idle".into()),
            bytes: n("ota_bytes"),
            total: n("ota_total"),
            paused: v
                .get("ota_paused")
                .and_then(Value::as_bool)
                .unwrap_or(false),
            available: s("ota_available").unwrap_or_default(),
            error: s("ota_error").unwrap_or_default(),
            comp_api: v.get("comp_api").and_then(Value::as_u64).map(|x| x as u32),
            uptime: n("uptime_sec"),
        }
    }

    /// Firmware with the update engine reports its layout; v4.0.4 and older do not.
    pub fn has_engine(&self) -> bool {
        self.layout.is_some()
    }

    fn core(&self) -> ota::BoardStatus {
        ota::BoardStatus {
            firmware: self.firmware.clone(),
            comp_api: self.comp_api,
            ota_layout: self.layout.clone(),
            ota_state: self.state.clone(),
            ota_result: self.result.clone(),
        }
    }
}

/// The offer for a board. `paired_and_reachable`: this Companion can send it
/// signed update requests right now.
pub fn offer(
    seen: Option<&Seen>,
    rel: Option<&Release>,
    this_companion: &str,
    paired_and_reachable: bool,
) -> Offer {
    let Some(seen) = seen else {
        return Offer::Unknown;
    };
    let latest = rel.map(Release::latest);
    let verdicts = ota::verdicts(&seen.core(), this_companion, latest.as_ref());
    if verdicts.contains(&Verdict::Verifying) {
        return Offer::Verifying;
    }
    if verdicts.contains(&Verdict::UpdateCompanionFirst) {
        return Offer::UpdateCompanionFirst;
    }
    if verdicts.contains(&Verdict::Unknown) {
        return Offer::Unknown;
    }
    let (Some(rel), Some(fw)) = (rel, seen.firmware.as_deref()) else {
        return Offer::Unknown;
    };
    match verdicts.iter().find_map(|v| match v {
        Verdict::BoardUpdate { cable_once, .. } => Some(*cable_once),
        _ => None,
    }) {
        Some(cable_once) => Offer::Board {
            to: rel.version.clone(),
            size: rel.image.size,
            changes: rel
                .changes_since(fw)
                .into_iter()
                .map(|s| (s.version.clone(), s.text.trim().to_string()))
                .collect(),
            security: rel.security,
            // The board's own engine takes it only from min_from up, and only with
            // two slots; the cable has no such limit.
            over_air: !cable_once
                && paired_and_reachable
                && seen.has_engine()
                && rel.reachable_from(fw),
            cable_once,
        },
        None => Offer::UpToDate,
    }
}

/// Whether a newer Companion is published for this system.
pub fn companion_offer<'a>(
    rel: Option<&'a Release>,
    this_companion: &str,
) -> Option<&'a ota::Asset> {
    let rel = rel?;
    let c = rel.companion.as_ref()?;
    let newer = matches!((ota::parse_version(&c.version), ota::parse_version(this_companion)), (Some(a), Some(b)) if a > b);
    if newer {
        rel.companion_asset(os_name())
    } else {
        None
    }
}

// ---- what is happening -------------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Job {
    Idle,
    Checking,
    /// `paused`: the board holds the download while someone is using it.
    Downloading {
        bytes: u64,
        total: u64,
        paused: bool,
    },
    /// Downloaded and checked; the board restarts into it when idle, or now on request.
    Ready,
    Restarting,
    /// The new firmware is proving itself (about a minute).
    Verifying,
    Cable {
        step: String,
        bytes: u64,
        total: u64,
    },
    Done {
        to: String,
    },
    /// The new firmware failed its self-test; the board went back by itself.
    RolledBack,
    Stopped,
    Failed {
        why: String,
    },
}

/// The board's own engine, read from its status, while an over-the-air update runs.
pub fn air_phase(seen: &Seen, started_uptime: u64, to: &str) -> Job {
    let restarted = seen.uptime < started_uptime;
    if restarted || seen.state.as_deref() == Some("pending_verify") {
        return match (seen.state.as_deref(), seen.result.as_deref()) {
            (Some("pending_verify"), _) => Job::Verifying,
            (_, Some("rolled_back")) => Job::RolledBack,
            (Some("valid"), _)
                if seen.firmware.as_deref().and_then(ota::parse_version)
                    == ota::parse_version(to) =>
            {
                Job::Done { to: to.to_string() }
            }
            (Some("valid"), _) => Job::RolledBack,
            _ => Job::Restarting,
        };
    }
    match seen.step.as_str() {
        "checking" => Job::Checking,
        "downloading" | "verifying" | "switching" => Job::Downloading {
            bytes: seen.bytes,
            total: seen.total,
            paused: seen.paused,
        },
        "ready" => Job::Ready,
        _ if seen.error == "cancelled" => Job::Stopped,
        _ if !seen.error.is_empty() => Job::Failed {
            why: board_error(&seen.error).into(),
        },
        _ => Job::Idle,
    }
}

/// The board's error codes, in words.
pub fn board_error(code: &str) -> &'static str {
    match code {
        "no_connection" => "the board could not reach the release",
        "bad_signature" => "the release is not signed by HID-Fi - nothing was installed",
        "bad_manifest" => "the release description could not be read",
        "hash" | "flash_verify" => "the downloaded firmware did not match - nothing was installed",
        "flash_write" | "flash_read" => {
            "the board could not write its flash - nothing was switched"
        }
        "too_big" => "the release does not fit this board",
        "memory" => "the board ran short of memory - try again",
        "single_slot" => "the board needs the cable once",
        "nothing_to_install" => "there is nothing to install",
        "cancelled" => "stopped",
        _ => "the board stopped the update",
    }
}

/// Where the board's engine failed only because it could not reach the release:
/// this computer fetches it and serves it to the board.
pub fn needs_relay(seen: &Seen) -> bool {
    seen.step == "idle" && seen.error == "no_connection"
}

/// How a cable update reaches the board.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CablePlan {
    /// Ask the firmware to restart into the ROM loader; the native USB port then
    /// shows up named by the board's MAC - no guessing which port is which.
    /// `rescue`: this board's COM port, if known - the way back out if the
    /// loader never appears on USB (seen behind a USB extender: the board then
    /// waits in the loader, and only an EN reset through COM, or a power cycle,
    /// ends it).
    Native { rescue: Option<String> },
    /// The board's COM port.
    Com(String),
}

/// A COM port (CH343) on this computer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ComPort {
    pub path: String,
    /// The CH343's own USB serial number - what the Companion remembers a board's
    /// COM port by, once a preflight there has read the board's MAC.
    pub serial: Option<String>,
}

/// `seen`: the board's status if it answers over the network. `paired`: this
/// Companion can send it signed requests. `com`: COM ports here. `known`: the
/// serial of the COM port a preflight once found this board on.
/// `prefer_native`: tests only, to exercise the native path and its rescue.
pub fn cable_plan(
    seen: Option<&Seen>,
    paired: bool,
    com: &[ComPort],
    known: Option<&str>,
    prefer_native: bool,
) -> Result<CablePlan, String> {
    if let Some(s) = seen {
        if s.state.as_deref() == Some("pending_verify") {
            // Opening the port resets the board - that would roll the update back.
            return Err("the board is proving a new firmware - wait a minute".into());
        }
        if s.step != "idle" && s.step != "ready" {
            return Err("an update over the air is running - let it finish or cancel it".into());
        }
    }
    let mine = known
        .and_then(|k| com.iter().find(|c| c.serial.as_deref() == Some(k)))
        .map(|c| c.path.clone());
    let only = match com {
        [one] => Some(one.path.clone()),
        _ => None,
    };
    let native_ok = seen.is_some_and(|s| paired && s.has_engine());
    // COM first when it is surely this board's: it works through any hub or
    // extender, the MAC is checked before a byte is written, and a reset there
    // always brings the board back.
    if let (Some(p), false) = (&mine, prefer_native) {
        return Ok(CablePlan::Com(p.clone()));
    }
    if native_ok {
        return Ok(CablePlan::Native {
            rescue: mine.or(only),
        });
    }
    match (mine.or(only), com.len()) {
        (Some(p), _) => Ok(CablePlan::Com(p)),
        (None, 0) => Err("connect the board's COM port (the second USB-C) to this computer".into()),
        _ => Err(
            "several boards' COM ports are connected - leave only this board's COM cable in".into(),
        ),
    }
}

/// Which board each COM port belongs to, learned by preflights over COM:
/// `<updates dir>/com_ports.json`, `{"<CH343 serial>": "<MAC, 12 hex>"}`.
pub fn com_known(dir: &Path, mac: &str) -> Option<String> {
    let m: std::collections::BTreeMap<String, String> =
        serde_json::from_slice(&std::fs::read(dir.join("com_ports.json")).ok()?).ok()?;
    let want = mac.replace(':', "").to_uppercase();
    m.into_iter().find(|(_, v)| *v == want).map(|(k, _)| k)
}

pub fn com_remember(dir: &Path, serial: &str, mac: &str) -> std::io::Result<()> {
    let p = dir.join("com_ports.json");
    let mut m: std::collections::BTreeMap<String, String> = std::fs::read(&p)
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default();
    let mac = mac.replace(':', "").to_uppercase();
    m.retain(|_, v| *v != mac); // a board has one COM port at a time
    m.insert(serial.to_string(), mac);
    std::fs::create_dir_all(dir)?;
    let tmp = p.with_extension("json.part");
    std::fs::write(&tmp, serde_json::to_vec_pretty(&m).unwrap_or_default())?;
    std::fs::rename(tmp, p)
}

// ---- I/O, behind traits ------------------------------------------------------------

pub trait Fetch: Send + Sync {
    /// GET a file, at most `cap` bytes.
    fn get(&self, url: &str, cap: usize) -> Result<Vec<u8>, String>;
}

pub trait BoardLink: Send + Sync {
    fn status(&self, base: &str) -> Result<Value, String>;
    /// A signed update request (hid_fi/ota.h compOta). Ok: the board's `done`, if any.
    fn ota(&self, base: &str, act: &str, url: &str) -> Result<Option<String>, String>;
}

/// The newest release: manifest and signature from `source`, signature first.
pub fn fetch_release(
    f: &dyn Fetch,
    source: &str,
    trusted: &[[u8; 65]],
) -> Result<(Release, Vec<u8>, Vec<u8>), String> {
    let m = f.get(&format!("{source}{MANIFEST}"), ota::MANIFEST_CAP)?;
    let s = f.get(&format!("{source}{SIGNATURE}"), 4096)?;
    match ota::read_release(&m, &s, trusted) {
        Ok(r) => Ok((r, m, s)),
        Err(ReleaseError::BadSignature) => Err("the release is not signed by HID-Fi".into()),
        Err(ReleaseError::NotForUs(w)) => Err(format!("the release could not be used ({w})")),
        Err(ReleaseError::ImageMismatch) => Err("the release files do not match".into()),
    }
}

/// A file the manifest pins, downloaded and checked.
pub fn fetch_asset(
    f: &dyn Fetch,
    source: &str,
    a: &ota::Asset,
    cap: usize,
) -> Result<Vec<u8>, String> {
    let b = f.get(&format!("{source}{}", a.name), cap)?;
    if ota::asset_matches(a, &b) {
        Ok(b)
    } else {
        Err(format!("{} is not the file the release names", a.name))
    }
}

// ---- the over-the-air job ------------------------------------------------------------

pub struct Shared {
    pub job: Mutex<Job>,
    pub cancel: AtomicBool,
    pub restart_now: AtomicBool,
}

impl Shared {
    pub fn new() -> Arc<Shared> {
        Arc::new(Shared {
            job: Mutex::new(Job::Idle),
            cancel: AtomicBool::new(false),
            restart_now: AtomicBool::new(false),
        })
    }
    pub fn set(&self, j: Job) {
        *self.job.lock().unwrap_or_else(|e| e.into_inner()) = j;
    }
    pub fn get(&self) -> Job {
        self.job.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
}

pub trait Clock: Send + Sync {
    fn now(&self) -> Instant;
    fn sleep(&self, d: Duration);
}

pub struct RealClock;
impl Clock for RealClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
    fn sleep(&self, d: Duration) {
        std::thread::sleep(d)
    }
}

/// Starts a relay for a release and returns its base URL as the board must
/// reach it (this computer's address on the board's network). Dropped: closed.
pub trait RelayStarter: Send + Sync {
    fn start(
        &self,
        files: Vec<(String, Vec<u8>)>,
        board_base: &str,
    ) -> Result<(String, Box<dyn Send>), String>;
}

/// Over the air, start to finish: check (relaying when the board cannot reach
/// the release), install, wait for the restart, follow the self-test.
#[allow(clippy::too_many_arguments)]
pub fn air_job(
    link: &dyn BoardLink,
    base: &str,
    rel: &Release,
    release_files: &dyn Fn() -> Result<Vec<(String, Vec<u8>)>, String>,
    relay: &dyn RelayStarter,
    clock: &dyn Clock,
    sh: &Shared,
) {
    let to = rel.version.clone();
    let poll = |what: &dyn Fn(&Seen) -> bool, secs: u64| -> Result<Seen, String> {
        let end = clock.now() + Duration::from_secs(secs);
        loop {
            if let Ok(v) = link.status(base) {
                let s = Seen::from_status(&v);
                if what(&s) {
                    return Ok(s);
                }
            }
            if clock.now() >= end {
                return Err("the board did not answer in time".into());
            }
            clock.sleep(Duration::from_millis(1000));
        }
    };
    let fail = |why: String| sh.set(Job::Failed { why });
    sh.set(Job::Checking);
    if let Err(e) = link.ota(base, "check", "") {
        return fail(format!("the board refused the check: {e}"));
    }
    clock.sleep(Duration::from_millis(500));
    let mut seen = match poll(&|s| s.step == "idle", 120) {
        Ok(s) => s,
        Err(e) => return fail(e),
    };
    let mut _relay_guard = None;
    if seen.available != to && needs_relay(&seen) {
        // The board cannot reach the release: serve it from here, for one check.
        let files = match release_files() {
            Ok(f) => f,
            Err(e) => return fail(e),
        };
        let (url, guard) = match relay.start(files, base) {
            Ok(x) => x,
            Err(e) => return fail(format!("could not serve the release to the board: {e}")),
        };
        _relay_guard = Some(guard);
        if let Err(e) = link.ota(base, "from", &url) {
            return fail(format!("the board refused the relay: {e}"));
        }
        clock.sleep(Duration::from_millis(500));
        seen = match poll(&|s| s.step == "idle", 120) {
            Ok(s) => s,
            Err(e) => return fail(e),
        };
    }
    if seen.available != to {
        return fail(if seen.error.is_empty() {
            "the board does not see this release".into()
        } else {
            board_error(&seen.error).into()
        });
    }
    if sh.cancel.load(Ordering::SeqCst) {
        return sh.set(Job::Stopped);
    }
    let started = seen.uptime;
    if let Err(e) = link.ota(base, "install", "") {
        return fail(format!("the board refused the install: {e}"));
    }
    // No overall limit: a ready update waits for the board to be idle, and a
    // download pauses while someone uses the board - both for as long as it
    // takes. Only a board that stops moving without a reason is a failure.
    let mut moved_at = clock.now();
    let mut cancel_sent = false;
    let mut last = Job::Idle;
    loop {
        if sh.cancel.load(Ordering::SeqCst) && !cancel_sent {
            cancel_sent = true;
            let _ = link.ota(base, "cancel", "");
        }
        if sh.restart_now.swap(false, Ordering::SeqCst) {
            let _ = link.ota(base, "apply", "");
        }
        let job = match link.status(base) {
            Ok(v) => air_phase(&Seen::from_status(&v), started, &to),
            // While it restarts the board does not answer: say so, keep waiting.
            Err(_) if matches!(last, Job::Ready | Job::Restarting | Job::Verifying) => {
                Job::Restarting
            }
            Err(_) => last.clone(),
        };
        match &job {
            Job::Done { .. } | Job::RolledBack | Job::Stopped | Job::Failed { .. } => {
                return sh.set(job)
            }
            Job::Idle if cancel_sent => return sh.set(Job::Stopped),
            _ => {}
        }
        let waiting = matches!(job, Job::Ready | Job::Downloading { paused: true, .. });
        if job != last || waiting {
            moved_at = clock.now();
        }
        if job != last {
            sh.set(job.clone());
            last = job;
        }
        if clock.now() - moved_at >= STALL {
            return fail("the board stopped making progress".into());
        }
        clock.sleep(Duration::from_millis(1000));
    }
}

// ---- the Companion's own update --------------------------------------------------------

/// Downloads this system's installer into `dir`, checked against the signed
/// manifest. The person opens it; nothing installs itself.
pub fn fetch_installer(
    f: &dyn Fetch,
    source: &str,
    a: &ota::Asset,
    dir: &Path,
) -> Result<PathBuf, String> {
    let bytes = fetch_asset(f, source, a, INSTALLER_CAP)?;
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let path = dir.join(&a.name);
    let tmp = dir.join(format!("{}.part", a.name));
    std::fs::write(&tmp, &bytes).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, &path).map_err(|e| e.to_string())?;
    Ok(path)
}

pub fn image_cap() -> usize {
    IMAGE_CAP
}

pub mod center;
pub mod install;
pub mod real;

#[cfg(test)]
mod tests;
