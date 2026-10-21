//! The app's conversation with its board: pairing, then heartbeats, forever.
//!
//! A state machine driven by `step()`. Each call does at most a couple of HTTP
//! requests and returns how long to wait before the next call, so the thread
//! that runs it never spins and a stopped network costs nothing but a timer.
//!
//! Failure handling, by kind:
//! - The board is unreachable: keep the pairing, back off 1 s → 30 s, and go
//!   back to finding the board (its address may have changed).
//! - The board says it no longer knows us (`unknown_pc`, `bad_mac`): the
//!   pairing is gone on its side (unpaired, or the access PIN was reset), so
//!   forget the key and pair again. Never retry a key the board rejects.
//! - The board asks us to slow down: wait what it says.
//! - Anything malformed: treat as unreachable, never panic.

use crate::env::{Clock, Http, NetError};
use hidfi_core::pairing::{self, Confirmed, Initiator, PairKey};
use hidfi_core::proto::{PairState, RawReply, Refusal, ReplyError, Request, PROTOCOL};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const BEAT_EVERY: Duration = Duration::from_secs(20);
pub const PAIR_POLL: Duration = Duration::from_secs(1);
pub const BACKOFF_MIN: Duration = Duration::from_secs(1);
pub const BACKOFF_MAX: Duration = Duration::from_secs(30);
/// How long a ticket nonce stays good. The board's tickets name one of these.
pub const TN_LIFETIME: Duration = Duration::from_secs(90);
/// Nothing to do while waiting for the user; the tray wakes the loop early.
pub const IDLE_WAIT: Duration = Duration::from_secs(60);

/// What the tray shows. Each one tells the user what, if anything, to do.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Status {
    Connecting,
    NeedsPin,
    /// Open the dashboard: it shows this computer and a Pair button.
    WaitingForApproval,
    /// Type this into the dashboard.
    ShowCode(String),
    /// Three wrong codes, or a message changed in transit. Pair again from the tray.
    PairingFailed,
    /// Nobody approved it in time, or it was declined. Pair again from the tray.
    PairingExpired,
    Online,
    /// Lost the board; still paired, trying again.
    Reconnecting,
    Problem(String),
}

/// Ticket nonces this app has announced, newest first. Shared with the server
/// that checks viewers' tickets.
#[derive(Clone, Default)]
pub struct TicketNonces(Arc<Mutex<VecDeque<(String, Instant)>>>);

impl TicketNonces {
    pub fn announce(&self, tn: String, now: Instant) {
        let mut q = self.0.lock().unwrap_or_else(|e| e.into_inner());
        q.push_front((tn, now));
        q.truncate(8);
    }
    /// True if `tn` is ours and younger than [`TN_LIFETIME`].
    pub fn is_live(&self, tn: &str, now: Instant) -> bool {
        let q = self.0.lock().unwrap_or_else(|e| e.into_inner());
        q.iter()
            .any(|(t, at)| t == tn && now.saturating_duration_since(*at) < TN_LIFETIME)
    }
}

/// What the app keeps across restarts. The key goes in the OS keystore.
#[derive(Clone, Debug)]
pub struct Identity {
    pub id: [u8; 16],
    pub name: String,
    pub key: Option<PairKey>,
}

/// Where this app accepts viewers. Set once the server is listening.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Listener {
    pub port: u16,
    pub fp: String,
}

enum Phase {
    /// Unpaired and not asking: waiting for the user to choose Pair.
    Idle,
    Unpaired,
    /// `announced`: the board has our request. From then on only `state` is
    /// polled, so a request that was declined or expired is not re-made.
    Pairing {
        init: Initiator,
        confirmed: Option<Confirmed>,
        announced: bool,
        /// When the board took our request: a board that has been up for less
        /// time than this restarted and forgot it (requests live in its RAM).
        asked_at: Option<Instant>,
    },
    Paired,
}

pub struct Session {
    pub identity: Identity,
    base: String,
    phase: Phase,
    status: Status,
    backoff: Duration,
    listener: Option<Listener>,
    pub nonces: TicketNonces,
    /// Set when the key changed and the store must be updated.
    pub key_changed: bool,
}

fn rand16() -> [u8; 16] {
    let mut b = [0u8; 16];
    getrandom::fill(&mut b).expect("the OS random source is available");
    b
}

impl Session {
    pub fn new(identity: Identity, base: String) -> Self {
        let phase = if identity.key.is_some() {
            Phase::Paired
        } else {
            Phase::Unpaired
        };
        Session {
            identity,
            base,
            phase,
            status: Status::Connecting,
            backoff: BACKOFF_MIN,
            listener: None,
            nonces: TicketNonces::default(),
            key_changed: false,
        }
    }

    pub fn status(&self) -> &Status {
        &self.status
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    /// The board moved (new address): keep everything else.
    pub fn set_base(&mut self, base: String) {
        self.base = base;
    }

    pub fn set_listener(&mut self, l: Listener) {
        self.listener = Some(l);
    }

    /// Forget the pairing and start again (the user asked, or the board did).
    pub fn reset_pairing(&mut self) {
        self.identity.key = None;
        self.key_changed = true;
        self.phase = Phase::Unpaired;
    }

    /// The user chose Pair after an attempt expired or failed.
    pub fn request_pairing(&mut self) {
        if matches!(self.phase, Phase::Idle) {
            self.phase = Phase::Unpaired;
            self.status = Status::Connecting;
        }
    }

    pub fn is_paired(&self) -> bool {
        matches!(self.phase, Phase::Paired)
    }

    fn call(&self, http: &dyn Http, req: &Request) -> Result<RawReply, CallError> {
        let (code, body) = http
            .post_json(&format!("{}/api/companion", self.base), &req.to_body())
            .map_err(CallError::Net)?;
        RawReply::parse_http(code, &body).map_err(CallError::Reply)
    }

    fn id_hex(&self) -> String {
        hex::encode(self.identity.id)
    }

    /// One step. Returns how long to wait before the next.
    pub fn step(&mut self, http: &dyn Http, clock: &dyn Clock) -> Duration {
        let r = match self.phase {
            Phase::Idle => return IDLE_WAIT,
            Phase::Unpaired => self.start_pairing(http, clock),
            Phase::Pairing { .. } => self.poll_pairing(http, clock),
            Phase::Paired => self.beat(http, clock),
        };
        match r {
            Ok(wait) => {
                self.backoff = BACKOFF_MIN;
                wait
            }
            Err(e) => self.on_error(e),
        }
    }

    /// True when the last failure means "go and find the board again".
    pub fn lost(&self) -> bool {
        matches!(self.status, Status::Reconnecting)
    }

    fn on_error(&mut self, e: CallError) -> Duration {
        match e {
            CallError::Net(_) | CallError::Reply(ReplyError::Malformed) => {
                log::debug!("board {}: {:?}", self.base, e);
                self.status = if matches!(self.phase, Phase::Paired) {
                    Status::Reconnecting
                } else {
                    Status::Connecting
                };
                let wait = self.backoff;
                self.backoff = (self.backoff * 2).min(BACKOFF_MAX);
                wait
            }
            CallError::Reply(ReplyError::Refused(r)) => match r {
                Refusal::PinRequired => {
                    self.status = Status::NeedsPin;
                    Duration::from_secs(10)
                }
                Refusal::RateLimited { retry_ms } => Duration::from_millis(retry_ms.max(1000)),
                Refusal::UnknownPc | Refusal::BadProof if matches!(self.phase, Phase::Paired) => {
                    // The board threw our pairing away. Pair again rather than
                    // hammering it with a key it will never accept.
                    self.reset_pairing();
                    self.status = Status::Connecting;
                    BACKOFF_MIN
                }
                Refusal::StaleChallenge => BACKOFF_MIN,
                Refusal::BadProof => {
                    // During pairing: the reveal broke the commitment, which only
                    // happens if something in the middle changed a message.
                    self.phase = Phase::Idle;
                    self.status = Status::PairingFailed;
                    IDLE_WAIT
                }
                Refusal::Full => {
                    self.status =
                        Status::Problem("The board is busy with other pairing requests".into());
                    BACKOFF_MAX
                }
                Refusal::Unsupported => {
                    self.status =
                        Status::Problem("Update the board's firmware to use screen sharing".into());
                    BACKOFF_MAX
                }
                Refusal::UnknownPc | Refusal::Other(_) => {
                    self.status = Status::Problem(r.to_string());
                    BACKOFF_MAX
                }
            },
        }
    }

    fn start_pairing(&mut self, http: &dyn Http, clock: &dyn Clock) -> Result<Duration, CallError> {
        let init = Initiator::new()
            .map_err(|_| CallError::Net(NetError::Unreachable("no randomness".into())))?;
        self.phase = Phase::Pairing {
            init,
            confirmed: None,
            announced: false,
            asked_at: None,
        };
        self.poll_pairing(http, clock)
    }

    fn poll_pairing(&mut self, http: &dyn Http, clock: &dyn Clock) -> Result<Duration, CallError> {
        let id = self.id_hex();
        let name = self.identity.name.clone();
        let base = self.base.clone();
        let Phase::Pairing {
            init,
            confirmed,
            announced,
            asked_at,
        } = &mut self.phase
        else {
            return Ok(Duration::ZERO);
        };
        // `hello` once, then `state`. A second `hello` would make a new request
        // whenever the board has dropped ours: after the code is typed (so we
        // would never see "paired"), and after "Not now" (so the dashboard
        // would be asked again straight away).
        let req = if *announced {
            Request::State { id: id.clone() }
        } else {
            Request::Hello {
                v: PROTOCOL,
                id: id.clone(),
                name,
                a_pub: hex::encode(init.a_pub),
                commit: hex::encode(init.commit()),
            }
        };
        let post = |r: &Request| -> Result<RawReply, CallError> {
            let (code, body) = http
                .post_json(&format!("{base}/api/companion"), &r.to_body())
                .map_err(CallError::Net)?;
            RawReply::parse_http(code, &body).map_err(CallError::Reply)
        };
        let r = post(&req)?;
        if !*announced {
            *asked_at = Some(clock.now());
        }
        *announced = true;
        let next = match r.pair_state().map_err(CallError::Reply)? {
            PairState::Pending => {
                self.status = Status::WaitingForApproval;
                return Ok(PAIR_POLL);
            }
            PairState::Confirm => {
                let (b_pub, n_b) = r.board_key().map_err(CallError::Reply)?;
                // The same board key every poll, so this is idempotent: a reveal
                // lost on the way is simply sent again.
                let c = match init.complete(&b_pub, &n_b) {
                    Ok(c) => c,
                    Err(_) => {
                        self.phase = Phase::Idle;
                        self.status = Status::PairingFailed;
                        return Ok(IDLE_WAIT);
                    }
                };
                let code = pairing::format_code(c.code);
                let reveal = Request::Reveal {
                    id,
                    n_a: hex::encode(c.n_a),
                };
                *confirmed = Some(c);
                post(&reveal)?;
                self.status = Status::ShowCode(code);
                return Ok(PAIR_POLL);
            }
            PairState::CodeShown => match confirmed {
                Some(c) => {
                    self.status = Status::ShowCode(pairing::format_code(c.code));
                    return Ok(PAIR_POLL);
                }
                // We restarted mid-attempt and lost its key: start a new one.
                None => Phase::Unpaired,
            },
            PairState::Paired => match confirmed.take() {
                Some(c) => {
                    self.identity.key = Some(c.key);
                    self.key_changed = true;
                    self.status = Status::Connecting;
                    self.phase = Phase::Paired;
                    return Ok(Duration::ZERO);
                }
                None => Phase::Unpaired,
            },
            // Gone: it expired unapproved, or someone tapped "Not now". Do not
            // ask again on our own - that would pester every dashboard forever.
            // ...unless the board restarted since: it forgot the request
            // (they live in its RAM), nobody turned it down. Ask again.
            PairState::None => {
                if let Some(at) = *asked_at {
                    // Uptime is in whole seconds: a second of slack.
                    let asked = clock.now().duration_since(at);
                    if board_uptime(http, &base).is_some_and(|up| up + Duration::from_secs(1) < asked) {
                        *announced = false;
                        *asked_at = None;
                        return Ok(Duration::ZERO);
                    }
                }
                self.status = Status::PairingExpired;
                Phase::Idle
            }
            PairState::Aborted => {
                self.status = Status::PairingFailed;
                Phase::Idle
            }
        };
        let wait = if matches!(next, Phase::Idle) {
            IDLE_WAIT
        } else {
            PAIR_POLL
        };
        self.phase = next;
        Ok(wait)
    }

    fn beat(&mut self, http: &dyn Http, clock: &dyn Clock) -> Result<Duration, CallError> {
        let Some(key) = self.identity.key.clone() else {
            self.phase = Phase::Unpaired;
            return Ok(Duration::ZERO);
        };
        // Not listening yet: nothing to announce, but stay paired.
        let Some(l) = self.listener.clone() else {
            self.status = Status::Connecting;
            return Ok(PAIR_POLL);
        };
        let id = self.id_hex();
        let chal = self
            .call(http, &Request::Chal { id: id.clone() })?
            .challenge()
            .map_err(CallError::Reply)?;
        let chal = hex::encode(chal);
        let tn = hex::encode(rand16());
        let mac = hex::encode(pairing::beat_mac(&key, &id, l.port, &l.fp, &tn, &chal));
        // Announce the nonce before the board can hand out a ticket naming it.
        self.nonces.announce(tn.clone(), clock.now());
        self.call(
            http,
            &Request::Beat {
                id,
                port: l.port,
                fp: l.fp,
                tn,
                chal,
                mac,
            },
        )?;
        self.status = Status::Online;
        Ok(BEAT_EVERY)
    }
}

// The payloads are read through Debug, in the log.
#[derive(Debug)]
#[allow(dead_code)]
enum CallError {
    Net(NetError),
    Reply(ReplyError),
}

/// How long the board has been up, from its status; `None` if it does not say.
fn board_uptime(http: &dyn Http, base: &str) -> Option<Duration> {
    let (code, body) = http.get(&format!("{base}/api/status")).ok()?;
    if code != 200 {
        return None;
    }
    let v: serde_json::Value = serde_json::from_str(&body).ok()?;
    v.get("uptime_sec")?.as_u64().map(Duration::from_secs)
}

