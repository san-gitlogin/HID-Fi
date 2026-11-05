//! Factory reset from the Companion (docs/FACTORY_RESET.md): the board as new,
//! without a pin in a pinhole. Three ways, chosen by what this computer has:
//!
//! - **paired** - this computer is paired with the board and it is on this USB: a
//!   signed request makes the board restart with its USB link open and a one-use
//!   token; the erase goes down that cable, signed over the token.
//! - **request** - not paired: the board shows the request on its dashboard and the
//!   owner approves with the access PIN.
//! - **recovery** - the board is already in recovery mode (the pinhole): the erase
//!   goes down that link with `confirm`.
//!
//! All network and serial I/O is on this worker's thread, never the service loop
//! or the window. The window's confirmation (warning, checkbox, 10 s countdown with
//! Cancel) comes first; this starts only when that ran out.
use crate::recovery::{Link, Ports, RecError};
use hidfi_core::discovery::Mac;
use hidfi_core::pairing::PairKey;
use hidfi_flash::port::Port;
use serde_json::{json, Value};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// How long a computer that is not paired waits for the owner (the board forgets
/// the request 120 s after its computer stops asking; this one keeps asking).
pub const APPROVAL_WAIT: Duration = Duration::from_secs(120);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Path {
    Paired,
    Request,
    Recovery,
}

impl Path {
    pub fn as_str(self) -> &'static str {
        match self {
            Path::Paired => "paired",
            Path::Request => "request",
            Path::Recovery => "recovery",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StepState {
    Todo,
    Doing,
    Done,
    Failed,
}

impl StepState {
    pub fn as_str(self) -> &'static str {
        match self {
            StepState::Todo => "todo",
            StepState::Doing => "doing",
            StepState::Done => "done",
            StepState::Failed => "failed",
        }
    }
}

/// One board's reset, as the window shows it. Never a secret.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ResetView {
    pub mac: String,
    pub path: Path,
    pub steps: Vec<(&'static str, StepState)>,
    /// What to tell the person now, or at the end.
    pub note: String,
    /// None while it runs; Some(true) erased, Some(false) nothing was changed.
    pub result: Option<bool>,
    /// Request: seconds left for the owner to approve.
    pub left_s: Option<u64>,
}

fn steps_for(path: Path) -> Vec<(&'static str, StepState)> {
    let names: &[&'static str] = match path {
        Path::Paired => &[
            "Asking the board",
            "Restarting with its USB link",
            "Erasing over USB",
            "Restarting as new",
        ],
        Path::Request => &[
            "Asking the board",
            "Waiting for approval on your phone",
            "Erasing and restarting as new",
        ],
        Path::Recovery => &["Erasing over USB", "Restarting as new"],
    };
    names.iter().map(|n| (*n, StepState::Todo)).collect()
}

/// Which way, from what this computer has: recovery mode (the pinhole) first, then
/// this computer's pairing (needs the board's address), else the owner's approval.
pub fn choose(in_recovery: bool, reachable: bool, paired: bool) -> Path {
    if in_recovery {
        Path::Recovery
    } else if paired && reachable {
        Path::Paired
    } else {
        Path::Request
    }
}

/// What the worker needs for one reset.
#[derive(Clone)]
pub struct Job {
    pub mac: Mac,
    pub path: Path,
    /// The board's address on the network (paired and request).
    pub base: Option<String>,
    /// This computer's pairing id and key with the board (paired).
    pub id: String,
    pub key: Option<PairKey>,
    /// This computer's name, as the dashboard will show it (request).
    pub name: String,
    /// The board's own WiFi name, for "join it again".
    pub ssid: String,
}

/// The network side, behind a trait so tests drive a model board.
pub trait Net: Send {
    /// The signed request: the board's one-use token.
    fn reset(&self, base: &str, id: &str, key: &PairKey) -> Result<String, String>;
    /// A request from a computer that is not paired: its id.
    fn request(&self, base: &str, name: &str) -> Result<String, String>;
    /// pending, declined, approved or unknown.
    fn request_status(&self, base: &str, request: &str) -> Result<String, String>;
}

/// Which boards are in recovery mode or on a reset link on this USB right now.
pub type UsbLinks = Arc<dyn Fn() -> Vec<Mac> + Send + Sync>;

pub struct Worker {
    tx: Sender<Msg>,
    state: Arc<Mutex<Vec<ResetView>>>,
    claimed: Arc<Mutex<Vec<String>>>,
}

enum Msg {
    Run(Job),
    Clear(String),
}

/// How long the steps wait; shorter in tests.
#[derive(Clone, Copy)]
pub struct Timing {
    pub link_appears: Duration,
    pub link_goes: Duration,
    pub poll: Duration,
    pub approval: Duration,
}

impl Default for Timing {
    fn default() -> Timing {
        Timing {
            link_appears: Duration::from_secs(25),
            link_goes: Duration::from_secs(15),
            poll: Duration::from_secs(1),
            approval: APPROVAL_WAIT,
        }
    }
}

impl Worker {
    pub fn start(
        net: Box<dyn Net>,
        ports: Box<dyn Ports>,
        usb: UsbLinks,
        state: Arc<Mutex<Vec<ResetView>>>,
        changed: impl Fn() + Send + 'static,
        timing: Timing,
    ) -> Worker {
        let (tx, rx) = channel::<Msg>();
        let claimed: Arc<Mutex<Vec<String>>> = Arc::default();
        let (st, cl) = (state.clone(), claimed.clone());
        std::thread::Builder::new()
            .name("factory-reset".into())
            .spawn(move || {
                while let Ok(m) = rx.recv() {
                    match m {
                        Msg::Run(job) => {
                            let mac = job.mac.hex();
                            cl.lock()
                                .unwrap_or_else(|e| e.into_inner())
                                .push(mac.clone());
                            let mut ui = Ui {
                                state: &st,
                                changed: &changed,
                                mac: mac.clone(),
                            };
                            ui.begin(job.path);
                            run_job(&job, net.as_ref(), ports.as_ref(), &usb, &mut ui, timing);
                            cl.lock()
                                .unwrap_or_else(|e| e.into_inner())
                                .retain(|m| *m != mac);
                        }
                        Msg::Clear(mac) => {
                            st.lock()
                                .unwrap_or_else(|e| e.into_inner())
                                .retain(|v| v.mac != mac || v.result.is_none());
                            changed();
                        }
                    }
                }
            })
            .expect("a thread");
        Worker { tx, state, claimed }
    }

    /// Start a reset (the window's countdown ran out). One at a time per board.
    pub fn run(&self, job: Job) {
        let busy = self
            .state
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .any(|v| v.mac == job.mac.hex() && v.result.is_none());
        if !busy {
            let _ = self.tx.send(Msg::Run(job));
        }
    }

    /// The person closed a finished reset's result.
    pub fn clear(&self, mac: &str) {
        let _ = self.tx.send(Msg::Clear(mac.to_string()));
    }

    /// Boards this worker is talking to over USB: recovery mode must leave them be.
    pub fn claimed(&self) -> Vec<String> {
        self.claimed
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }
}

struct Ui<'a> {
    state: &'a Mutex<Vec<ResetView>>,
    changed: &'a dyn Fn(),
    mac: String,
}

impl Ui<'_> {
    fn edit(&mut self, f: impl FnOnce(&mut ResetView)) {
        {
            let mut v = self.state.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(r) = v.iter_mut().find(|r| r.mac == self.mac) {
                f(r);
            }
        }
        (self.changed)();
    }
    fn begin(&mut self, path: Path) {
        {
            let mut v = self.state.lock().unwrap_or_else(|e| e.into_inner());
            v.retain(|r| r.mac != self.mac);
            v.push(ResetView {
                mac: self.mac.clone(),
                path,
                steps: steps_for(path),
                note: String::new(),
                result: None,
                left_s: None,
            });
        }
        (self.changed)();
    }
    fn step(&mut self, i: usize, s: StepState) {
        self.edit(|r| {
            if let Some(st) = r.steps.get_mut(i) {
                st.1 = s;
            }
        });
    }
    fn note(&mut self, text: &str) {
        let t = text.to_string();
        self.edit(|r| r.note = t);
    }
    fn left(&mut self, s: Option<u64>) {
        self.edit(|r| r.left_s = s);
    }
    fn finish(&mut self, ok: bool, failed_step: Option<usize>, text: &str) {
        let t = text.to_string();
        self.edit(|r| {
            if let Some(i) = failed_step {
                if let Some(st) = r.steps.get_mut(i) {
                    st.1 = StepState::Failed;
                }
            }
            r.note = t;
            r.result = Some(ok);
            r.left_s = None;
        });
    }
}

fn erased_note(ssid: &str) -> String {
    if ssid.is_empty() {
        "Erased. The board restarted as new: join its own WiFi again to set it up.".into()
    } else {
        format!("Erased. The board restarted as new: join its WiFi {ssid} again to set it up.")
    }
}

/// The board's own refusals, in words a person can act on.
fn refused(e: &str) -> String {
    let says = |c: &str| e.contains(c);
    if says("verifying") || says("busy") {
        "An update is running on the board. Try again when it has finished. Nothing was changed."
            .into()
    } else if says("unknown_pc") {
        "This computer is no longer paired with the board. Pair it again, or approve from the dashboard. Nothing was changed.".into()
    } else if says("pin_required") {
        "This board has no access PIN, so it cannot approve from your phone. Join its own WiFi, open the dashboard and use Settings, Factory reset.".into()
    } else if says("rate_limited") {
        "The board is busy answering other requests. Try again in a moment. Nothing was changed."
            .into()
    } else {
        format!("The board did not answer ({e}). Nothing was changed.")
    }
}

fn wait_for(usb: &UsbLinks, mac: &Mac, present: bool, max: Duration) -> bool {
    let end = Instant::now() + max;
    loop {
        if usb().contains(mac) == present {
            return true;
        }
        if Instant::now() >= end {
            return false;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

/// Open the board's link, a few tries: recovery mode's own look at a new link may
/// hold the port for a moment.
fn open_link(ports: &dyn Ports, mac: &Mac) -> Result<Link<crate::recovery::DynPort>, RecError> {
    let mut last = RecError::Open("its USB link is not there".into());
    for _ in 0..15 {
        if let Some(path) = ports.find(mac) {
            match ports.open(&path) {
                Ok(l) => return Ok(l),
                Err(e) => last = e,
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    Err(last)
}

fn erase<P: Port>(link: &mut Link<P>, body: Value) -> Result<(), String> {
    let r = link
        .cmd(&body, Duration::from_secs(6))
        .map_err(|e| e.text())?;
    if r["reply"] == "factory_reset" && r["status"] == "ok" {
        Ok(())
    } else {
        Err(r["reply"].as_str().unwrap_or("refused").to_string())
    }
}

fn run_job(job: &Job, net: &dyn Net, ports: &dyn Ports, usb: &UsbLinks, ui: &mut Ui, t: Timing) {
    match job.path {
        Path::Paired => {
            let (Some(base), Some(key)) = (job.base.as_deref(), job.key.as_ref()) else {
                ui.finish(
                    false,
                    Some(0),
                    "This computer cannot reach the board on the network. Nothing was changed.",
                );
                return;
            };
            ui.step(0, StepState::Doing);
            let token = match net.reset(base, &job.id, key) {
                Ok(t) => t,
                Err(e) => return ui.finish(false, Some(0), &refused(&e)),
            };
            ui.step(0, StepState::Done);
            ui.step(1, StepState::Doing);
            if !wait_for(usb, &job.mac, true, t.link_appears) {
                return ui.finish(false, Some(1), "The board did not come back with its USB link. Nothing was changed: it restarts normally within a minute.");
            }
            let mut link = match open_link(ports, &job.mac) {
                Ok(l) => l,
                Err(e) => {
                    return ui.finish(
                        false,
                        Some(1),
                        &format!("{} Nothing was changed.", e.text()),
                    )
                }
            };
            ui.step(1, StepState::Done);
            ui.step(2, StepState::Doing);
            let proof = hex::encode(hidfi_core::pairing::factory_proof(key, &job.id, &token));
            if let Err(e) = erase(&mut link, json!({"cmd": "factory_reset", "proof": proof})) {
                return ui.finish(false, Some(2), &refused(&e));
            }
            drop(link);
            ui.step(2, StepState::Done);
            ui.step(3, StepState::Doing);
            wait_for(usb, &job.mac, false, t.link_goes);
            ui.step(3, StepState::Done);
            ui.finish(true, None, &erased_note(&job.ssid));
        }
        Path::Request => {
            let Some(base) = job.base.as_deref() else {
                ui.finish(false, Some(0), "This computer cannot reach the board on the network. Use Factory reset in the dashboard's Settings instead.");
                return;
            };
            ui.step(0, StepState::Doing);
            let req = match net.request(base, &job.name) {
                Ok(r) => r,
                Err(e) => return ui.finish(false, Some(0), &refused(&e)),
            };
            ui.step(0, StepState::Done);
            ui.step(1, StepState::Doing);
            ui.note(&format!(
                "Open the board's dashboard on your phone: it asks you to approve \"{}\". Nothing changes until you do.",
                job.name
            ));
            let end = Instant::now() + t.approval;
            let mut fails = 0;
            let mut seen = false;
            loop {
                let left = end.saturating_duration_since(Instant::now());
                ui.left(Some(left.as_secs()));
                match net.request_status(base, &req).as_deref() {
                    Ok("pending") => {
                        seen = true;
                        fails = 0;
                    }
                    Ok("approved") => {
                        ui.step(1, StepState::Done);
                        ui.step(2, StepState::Done);
                        return ui.finish(
                            true,
                            None,
                            &format!("Approved on your phone. {}", erased_note(&job.ssid)),
                        );
                    }
                    Ok("declined") => {
                        return ui.finish(
                            false,
                            Some(1),
                            "Declined on the dashboard. Nothing was changed.",
                        );
                    }
                    Ok(_) => {
                        return ui.finish(
                            false,
                            Some(1),
                            if seen {
                                "Nobody approved it in time. Nothing was changed."
                            } else {
                                "The board forgot the request. Nothing was changed."
                            },
                        );
                    }
                    Err(_) => {
                        fails += 1;
                        if fails >= 10 {
                            return ui.finish(false, Some(1), "The board stopped answering. If you approved it, it has restarted as new; otherwise nothing was changed.");
                        }
                    }
                }
                if left.is_zero() {
                    return ui.finish(
                        false,
                        Some(1),
                        "Nobody approved it in time. Nothing was changed.",
                    );
                }
                std::thread::sleep(t.poll);
            }
        }
        Path::Recovery => {
            ui.step(0, StepState::Doing);
            let mut link = match open_link(ports, &job.mac) {
                Ok(l) => l,
                Err(e) => {
                    return ui.finish(
                        false,
                        Some(0),
                        &format!("{} Nothing was changed.", e.text()),
                    )
                }
            };
            if let Err(e) = erase(&mut link, json!({"cmd": "factory_reset", "confirm": true})) {
                return ui.finish(false, Some(0), &refused(&e));
            }
            drop(link);
            ui.step(0, StepState::Done);
            ui.step(1, StepState::Doing);
            wait_for(usb, &job.mac, false, t.link_goes);
            ui.step(1, StepState::Done);
            ui.finish(true, None, &erased_note(&job.ssid));
        }
    }
}

// ---- the real network ------------------------------------------------------------------

pub struct RealNet {
    agent: ureq::Agent,
}

impl Default for RealNet {
    fn default() -> Self {
        RealNet {
            agent: ureq::Agent::config_builder()
                .timeout_global(Some(Duration::from_secs(8)))
                .http_status_as_error(false)
                .build()
                .into(),
        }
    }
}

impl RealNet {
    fn post(&self, base: &str, body: &hidfi_core::proto::Request) -> Result<Value, String> {
        let mut r = self
            .agent
            .post(&format!("{base}/api/companion"))
            .header("Content-Type", "application/json")
            .send(body.to_body())
            .map_err(|e| e.to_string())?;
        let text = r.body_mut().read_to_string().map_err(|e| e.to_string())?;
        let v: Value = serde_json::from_str(&text).map_err(|e| e.to_string())?;
        if v["status"] == "ok" {
            Ok(v)
        } else {
            Err(v["reply"].as_str().unwrap_or("refused").to_string())
        }
    }
}

impl Net for RealNet {
    fn reset(&self, base: &str, id: &str, key: &PairKey) -> Result<String, String> {
        use hidfi_core::proto::Request;
        let mut last = String::new();
        // Once more on a stale challenge: this computer's heartbeat may have asked at
        // the same moment (as for updates).
        for _ in 0..2 {
            let chal = self.post(base, &Request::Chal { id: id.into() })?["chal"]
                .as_str()
                .ok_or("no challenge")?
                .to_string();
            let mac = hex::encode(hidfi_core::pairing::reset_mac(key, id, &chal));
            match self.post(
                base,
                &Request::Reset {
                    id: id.into(),
                    chal,
                    mac,
                },
            ) {
                Ok(v) => {
                    return v["token"]
                        .as_str()
                        .map(str::to_string)
                        .ok_or("no token".into())
                }
                Err(e) if e == "stale_chal" => last = e,
                Err(e) => return Err(e),
            }
        }
        Err(last)
    }
    fn request(&self, base: &str, name: &str) -> Result<String, String> {
        let v = self.post(
            base,
            &hidfi_core::proto::Request::ResetRequest { name: name.into() },
        )?;
        v["request"]
            .as_str()
            .map(str::to_string)
            .ok_or("no request id".into())
    }
    fn request_status(&self, base: &str, request: &str) -> Result<String, String> {
        let v = self.post(
            base,
            &hidfi_core::proto::Request::ResetRequestStatus {
                request: request.into(),
            },
        )?;
        Ok(v["state"].as_str().unwrap_or("unknown").to_string())
    }
}

#[cfg(test)]
mod tests;
