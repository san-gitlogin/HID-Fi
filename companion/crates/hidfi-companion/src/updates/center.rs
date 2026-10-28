//! The update centre: one thread that knows the newest release, what each board
//! here runs and is offered, and every update in flight. It never blocks the
//! service (pairing, heartbeats, screen sharing): the service hands it the
//! boards it sees and reads back a view.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use hidfi_core::ota::Release;
use hidfi_flash::update::{Journal, Step};

use super::*;

/// One board's update state, as the tray and window show it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BoardUpd {
    pub mac: String,
    pub firmware: Option<String>,
    pub offer: Offer,
    pub job: Job,
    /// A cable update stopped part way (unplugged, closed): where. Resume carries on.
    pub resumable: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum CompUpd {
    None,
    Available { to: String, size: u64 },
    Downloading { to: String },
    /// Checked against the signed manifest; opens when the person asks.
    Ready { to: String, path: PathBuf },
    Failed { why: String },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdView {
    pub boards: Vec<BoardUpd>,
    pub companion: CompUpd,
    /// The newest release read, if any.
    pub latest: Option<String>,
    /// Why no release could be read (shown quietly; boards are still served).
    pub problem: Option<String>,
    pub checking: bool,
}

impl Default for UpdView {
    fn default() -> Self {
        UpdView { boards: Vec::new(), companion: CompUpd::None, latest: None, problem: None, checking: false }
    }
}

impl UpdView {
    pub fn board(&self, mac: &str) -> Option<&BoardUpd> {
        self.boards.iter().find(|b| b.mac == mac)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum UpdCmd {
    CheckNow,
    /// Consent to update this board: over the air, or by cable (`cable`).
    Update { mac: String, cable: bool },
    Cancel(String),
    RestartNow(String),
    GetCompanion,
    OpenInstaller,
}

/// A board on this computer's USB, and its address when it answers.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BoardHere {
    pub mac: String,
    pub url: Option<String>,
}

/// For a board's MAC: its link, and whether this computer is paired with it.
pub type LinkFor = dyn Fn(&str) -> (Arc<dyn BoardLink>, bool) + Send + Sync;

/// (plan, MAC, the board over the network, image, table, journal, the release's version, job)
pub type CableFn = dyn Fn(CablePlan, &str, Option<(&dyn BoardLink, &str)>, &[u8], &[u8], &Path, &str, &Shared) + Send + Sync;

pub struct CenterEnv {
    pub fetch: Arc<dyn Fetch>,
    /// Status for any board; signed requests only for a paired one (`paired`).
    pub link_for: Arc<LinkFor>,
    pub relay: Arc<dyn RelayStarter>,
    pub cable: Arc<CableFn>,
    pub com_ports: Arc<dyn Fn() -> Vec<ComPort> + Send + Sync>,
    pub open: Arc<dyn Fn(&Path) + Send + Sync>,
    pub source: String,
    pub dir: PathBuf,
    pub this_version: String,
    pub trusted: Vec<[u8; 65]>,
    pub clock: Arc<dyn Clock>,
}

struct Running {
    sh: Arc<Shared>,
    done_at: Option<Instant>,
}

/// After `n` failed release checks in a row: 1, 2, 4, 8 ... minutes, never more
/// than the daily check. A network that drops once must not hide a release for a day.
pub fn retry_delay(n: u32) -> Duration {
    let mins = 1u64 << n.saturating_sub(1).min(16);
    Duration::from_secs(mins * 60).min(CHECK_EVERY)
}

pub fn journal_path(dir: &Path, mac: &str) -> PathBuf {
    dir.join("updates").join(format!("{}.json", mac.replace(':', "").to_uppercase()))
}

/// A journal left by a cable update that did not reach its end.
pub fn resumable(dir: &Path, mac: &str) -> Option<String> {
    let j = Journal::load(&journal_path(dir, mac))?;
    (j.step < Step::Restarted).then(|| format!("{:?}", j.step))
}

pub fn start(env: CenterEnv, boards: Arc<Mutex<Vec<BoardHere>>>, publish: Box<dyn Fn(UpdView) + Send>) -> Sender<UpdCmd> {
    let (tx, rx) = channel();
    std::thread::Builder::new()
        .name("updates".into())
        .spawn(move || run(env, boards, publish, rx))
        .expect("spawning the update thread");
    tx
}

type Rel = Option<(Release, Vec<u8>, Vec<u8>)>;

fn run(env: CenterEnv, boards: Arc<Mutex<Vec<BoardHere>>>, publish: Box<dyn Fn(UpdView) + Send>, rx: Receiver<UpdCmd>) {
    let env = Arc::new(env);
    let rel: Arc<Mutex<Rel>> = Arc::default();
    let mut view = UpdView::default();
    let mut next_release = env.clock.now();
    // Failed checks in a row: the next one comes sooner than a day, backing off.
    let failures = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let retry_due: Arc<Mutex<Option<Duration>>> = Arc::default();
    let mut seen: HashMap<String, (Seen, Instant)> = HashMap::new();
    let mut jobs: HashMap<String, Running> = HashMap::new();
    let comp: Arc<Mutex<CompUpd>> = Arc::new(Mutex::new(CompUpd::None));
    let checking = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let problem: Arc<Mutex<Option<String>>> = Arc::default();
    loop {
        let now = env.clock.now();
        if now >= next_release && !checking.load(Ordering::SeqCst) {
            next_release = now + CHECK_EVERY;
            checking.store(true, Ordering::SeqCst);
            let (e, r, c, p) = (env.clone(), rel.clone(), checking.clone(), problem.clone());
            let (fails, due) = (failures.clone(), retry_due.clone());
            std::thread::spawn(move || {
                match fetch_release(e.fetch.as_ref(), &e.source, &e.trusted) {
                    Ok(x) => {
                        *r.lock().unwrap_or_else(|e| e.into_inner()) = Some(x);
                        *p.lock().unwrap_or_else(|e| e.into_inner()) = None;
                        fails.store(0, Ordering::SeqCst);
                    }
                    Err(why) => {
                        *p.lock().unwrap_or_else(|e| e.into_inner()) = Some(why);
                        let n = fails.fetch_add(1, Ordering::SeqCst) + 1;
                        *due.lock().unwrap_or_else(|e| e.into_inner()) = Some(retry_delay(n));
                    }
                }
                c.store(false, Ordering::SeqCst);
            });
        }
        if let Some(d) = retry_due.lock().unwrap_or_else(|e| e.into_inner()).take() {
            next_release = now + d;
        }
        let here = boards.lock().unwrap_or_else(|e| e.into_inner()).clone();
        let busy = jobs.values().any(|j| j.done_at.is_none());
        let every = if busy { Duration::from_secs(2) } else { Duration::from_secs(30) };
        let release = rel.lock().unwrap_or_else(|e| e.into_inner()).clone();
        let mut out = Vec::new();
        for b in &here {
            let (link, paired) = (env.link_for)(&b.mac);
            if let Some(url) = &b.url {
                let stale = seen.get(&b.mac).is_none_or(|(_, at)| now.duration_since(*at) >= every);
                if stale {
                    if let Ok(v) = link.status(url) {
                        seen.insert(b.mac.clone(), (Seen::from_status(&v), now));
                    }
                }
            }
            let s = b.url.as_ref().and(seen.get(&b.mac)).map(|(s, _)| s);
            let job = jobs.get(&b.mac).map(|j| j.sh.get()).unwrap_or(Job::Idle);
            out.push(BoardUpd {
                mac: b.mac.clone(),
                firmware: s.and_then(|s| s.firmware.clone()),
                offer: offer(s, release.as_ref().map(|r| &r.0), &env.this_version, paired && b.url.is_some()),
                job,
                resumable: resumable(&env.dir, &b.mac),
            });
        }
        // A finished job's result stays on screen for a while, then clears.
        for j in jobs.values_mut() {
            let end = matches!(j.sh.get(), Job::Done { .. } | Job::RolledBack | Job::Stopped | Job::Failed { .. });
            if end && j.done_at.is_none() {
                j.done_at = Some(now);
            }
        }
        jobs.retain(|_, j| j.done_at.is_none_or(|t| now.duration_since(t) < Duration::from_secs(600)));
        let mut c = comp.lock().unwrap_or_else(|e| e.into_inner()).clone();
        if c == CompUpd::None {
            if let Some(a) = companion_offer(release.as_ref().map(|r| &r.0), &env.this_version) {
                let to = release.as_ref().and_then(|r| r.0.companion.as_ref()).map(|c| c.version.clone()).unwrap_or_default();
                c = CompUpd::Available { to, size: a.size };
            }
        }
        let next = UpdView {
            boards: out,
            companion: c,
            latest: release.as_ref().map(|r| r.0.version.clone()),
            problem: problem.lock().unwrap_or_else(|e| e.into_inner()).clone(),
            checking: checking.load(Ordering::SeqCst),
        };
        if next != view {
            view = next;
            publish(view.clone());
        }

        let wait = if busy { Duration::from_millis(500) } else { Duration::from_secs(2) };
        match rx.recv_timeout(wait) {
            Err(RecvTimeoutError::Disconnected) => break,
            Err(RecvTimeoutError::Timeout) => {}
            Ok(UpdCmd::CheckNow) => next_release = env.clock.now(),
            Ok(UpdCmd::Cancel(mac)) => {
                if let Some(j) = jobs.get(&mac) {
                    j.sh.cancel.store(true, Ordering::SeqCst);
                }
            }
            Ok(UpdCmd::RestartNow(mac)) => {
                if let Some(j) = jobs.get(&mac) {
                    j.sh.restart_now.store(true, Ordering::SeqCst);
                }
            }
            Ok(UpdCmd::Update { mac, cable }) => {
                if jobs.get(&mac).is_some_and(|j| j.done_at.is_none()) {
                    continue; // one update per board at a time
                }
                let Some(r) = release.clone() else { continue };
                let Some(b) = here.iter().find(|b| b.mac == mac).cloned() else { continue };
                let sh = Shared::new();
                jobs.insert(mac.clone(), Running { sh: sh.clone(), done_at: None });
                let s = b.url.as_ref().and(seen.get(&mac)).map(|(s, _)| s.clone());
                let e = env.clone();
                std::thread::spawn(move || {
                    let (link, paired) = (e.link_for)(&mac);
                    if !cable {
                        let Some(url) = b.url.clone() else {
                            return sh.set(Job::Failed { why: "the board is not reachable over the network".into() });
                        };
                        let (m, sig) = (r.1.clone(), r.2.clone());
                        let e2 = e.clone();
                        let rel2 = r.0.clone();
                        let files = move || -> Result<Vec<(String, Vec<u8>)>, String> {
                            let img = fetch_asset(e2.fetch.as_ref(), &e2.source, &rel2.image, image_cap())?;
                            Ok(vec![(MANIFEST.into(), m.clone()), (SIGNATURE.into(), sig.clone()), (rel2.image.name.clone(), img)])
                        };
                        return air_job(link.as_ref(), &url, &r.0, &files, e.relay.as_ref(), e.clock.as_ref(), &sh);
                    }
                    // HIDFI_CABLE_PREFER=native: tests only, to exercise the native path
                    // and its rescue on a board whose COM port is known.
                    let prefer_native = std::env::var("HIDFI_CABLE_PREFER").as_deref() == Ok("native");
                    let known = com_known(&e.dir.join("updates"), &mac);
                    let plan = match cable_plan(s.as_ref(), paired, &(e.com_ports)(), known.as_deref(), prefer_native) {
                        Ok(p) => p,
                        Err(why) => return sh.set(Job::Failed { why }),
                    };
                    sh.set(Job::Cable { step: "downloading the release".into(), bytes: 0, total: r.0.image.size });
                    let Some(ta) = r.0.table.clone() else {
                        return sh.set(Job::Failed { why: "this release has no partition table for the cable".into() });
                    };
                    let got = fetch_asset(e.fetch.as_ref(), &e.source, &r.0.image, image_cap())
                        .and_then(|img| Ok((img, fetch_asset(e.fetch.as_ref(), &e.source, &ta, 0x1000)?)));
                    let (img, table) = match got {
                        Ok(x) => x,
                        Err(why) => return sh.set(Job::Failed { why }),
                    };
                    if sh.cancel.load(Ordering::SeqCst) {
                        return sh.set(Job::Stopped);
                    }
                    let jp = journal_path(&e.dir, &mac);
                    if let Some(d) = jp.parent() {
                        let _ = std::fs::create_dir_all(d);
                    }
                    let l = b.url.as_deref().map(|u| (link.as_ref(), u));
                    (e.cable)(plan, &mac, l, &img, &table, &jp, &r.0.version, &sh);
                });
            }
            Ok(UpdCmd::GetCompanion) => {
                let Some(r) = release.clone() else { continue };
                let Some(a) = companion_offer(Some(&r.0), &env.this_version).cloned() else { continue };
                let to = r.0.companion.as_ref().map(|c| c.version.clone()).unwrap_or_default();
                *comp.lock().unwrap_or_else(|e| e.into_inner()) = CompUpd::Downloading { to: to.clone() };
                let (e, c) = (env.clone(), comp.clone());
                std::thread::spawn(move || {
                    let r = fetch_installer(e.fetch.as_ref(), &e.source, &a, &e.dir.join("downloads"));
                    *c.lock().unwrap_or_else(|e| e.into_inner()) = match r {
                        Ok(path) => CompUpd::Ready { to, path },
                        Err(why) => CompUpd::Failed { why },
                    };
                });
            }
            Ok(UpdCmd::OpenInstaller) => {
                if let CompUpd::Ready { path, .. } = &*comp.lock().unwrap_or_else(|e| e.into_inner()) {
                    (env.open)(path);
                }
            }
        }
    }
}

/// Everything the centre needs except what only the service knows (which boards
/// are paired, and this computer's id): main builds it, tests build their own.
pub struct UpdatesSetup {
    pub fetch: Arc<dyn Fetch>,
    pub relay: Arc<dyn RelayStarter>,
    pub cable: Arc<CableFn>,
    pub com_ports: Arc<dyn Fn() -> Vec<ComPort> + Send + Sync>,
    pub open: Arc<dyn Fn(&Path) + Send + Sync>,
    pub source: String,
    pub this_version: String,
    pub trusted: Vec<[u8; 65]>,
    pub clock: Arc<dyn Clock>,
}

impl UpdatesSetup {
    /// The real thing: HTTPS from the release source (HIDFI_RELEASES overrides
    /// it), the serial ports, the system's opener for the installer.
    pub fn real() -> UpdatesSetup {
        UpdatesSetup {
            fetch: Arc::new(real::UreqFetch::new()),
            relay: Arc::new(real::Relay),
            cable: Arc::new(|plan, mac, link, img, table, journal, to, sh| {
                real::cable_job(plan, mac, link, img, table, journal, to, sh)
            }),
            com_ports: Arc::new(real::com_ports),
            open: Arc::new(open_path),
            source: std::env::var("HIDFI_RELEASES").ok().filter(|s| s.ends_with('/')).unwrap_or_else(|| RELEASES.into()),
            this_version: env!("CARGO_PKG_VERSION").into(),
            trusted: trusted_keys(),
            clock: Arc::new(RealClock),
        }
    }

    pub fn into_env(self, dir: PathBuf, link_for: Arc<LinkFor>) -> CenterEnv {
        CenterEnv {
            fetch: self.fetch,
            link_for,
            relay: self.relay,
            cable: self.cable,
            com_ports: self.com_ports,
            open: self.open,
            source: self.source,
            dir,
            this_version: self.this_version,
            trusted: self.trusted,
            clock: self.clock,
        }
    }
}

/// Opens the downloaded installer with the system (Finder / Explorer / xdg-open):
/// the person installs it; nothing runs by itself.
fn open_path(p: &Path) {
    let p = p.as_os_str();
    #[cfg(target_os = "macos")]
    let _ = std::process::Command::new("open").arg(p).spawn();
    #[cfg(windows)]
    let _ = std::process::Command::new("explorer").arg("/select,").arg(p).spawn();
    #[cfg(target_os = "linux")]
    let _ = std::process::Command::new("xdg-open").arg(std::path::Path::new(p).parent().unwrap_or(std::path::Path::new("."))).spawn();
}
