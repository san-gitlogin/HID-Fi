//! The app's work, on one background thread: find the board on this computer,
//! pair, keep it told where we listen, and serve viewers.
//!
//! The tray never touches the network. It reads a snapshot (`View`) and sends
//! commands (`Cmd`); the service wakes for a command at once, otherwise sleeps
//! for exactly as long as the session asked.

use crate::store::{State, Store};
use hidfi_agent::env::{Browse, Http, SystemClock, UsbList};
use hidfi_agent::locate;
use hidfi_agent::session::{Identity, Listener, Session, Status, TicketNonces};
use hidfi_core::discovery::{Mac, Selection};
use hidfi_core::pairing::PairKey;
use hidfi_stream::pipeline::{Pipeline, Source};
use hidfi_stream::server::{self, Server, TicketGate, Watchers};
use std::sync::mpsc::{channel, Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};

/// Where finding the board stands.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum BoardState {
    Looking,
    /// No HID-Fi on this computer's USB. `elsewhere` were seen on the network.
    NotPluggedIn {
        elsewhere: usize,
    },
    /// Plugged in, but not reachable: it has not joined this network.
    NotOnNetwork,
    /// Several boards on this computer: the user picks one.
    Choose(Vec<(String, String)>),
    Found {
        url: String,
        firmware: String,
    },
}

/// Everything the tray shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct View {
    pub board: BoardState,
    pub session: Status,
    pub watching: usize,
    pub capture_allowed: bool,
    pub port: Option<u16>,
    pub name: String,
    pub problem: Option<String>,
}

pub enum Cmd {
    PairAgain,
    /// Forget the pairing on this computer (the dashboard can remove it too).
    Forget,
    ChooseBoard(String),
    Quit,
}

pub struct Handle {
    pub view: Arc<Mutex<View>>,
    pub tx: Sender<Cmd>,
    pub watchers: Option<Watchers>,
    notify: Notify,
}

/// Called whenever the view changes, so a window can sleep until then instead
/// of waking on a timer to look.
type Notify = Arc<Mutex<Option<Box<dyn Fn() + Send>>>>;

impl Handle {
    /// `f` runs (on the service thread) after every change to `view`.
    pub fn on_change(&self, f: impl Fn() + Send + 'static) {
        *self.notify.lock().unwrap_or_else(|e| e.into_inner()) = Some(Box::new(f));
    }
}

/// What makes a screen source for a display. Real capture in the app; a
/// test pattern in tests, which also keeps tests from ever triggering the
/// macOS Screen Recording prompt.
pub type SourceFactory = Arc<dyn Fn(Option<u32>) -> Result<Box<dyn Source>, String> + Send + Sync>;

pub struct Env {
    pub usb: Box<dyn UsbList>,
    pub http: Box<dyn Http>,
    pub browse: Box<dyn Browse>,
    pub source: SourceFactory,
    /// The displays viewers may choose from.
    pub displays: Arc<dyn Fn() -> Vec<hidfi_stream::display::Display> + Send + Sync>,
    /// Whether capture is permitted (macOS Screen Recording); shown in the tray.
    pub capture_allowed: Arc<dyn Fn() -> bool + Send + Sync>,
}

const FIND_SOON: Duration = Duration::from_secs(3);
const FIND_LATER: Duration = Duration::from_secs(10);
/// How often the board is checked to still be on this computer's USB, even
/// while everything else is quiet. A USB listing costs well under a millisecond.
const USB_CHECK: Duration = Duration::from_secs(3);
/// How long a board on USB may stay unanswered before the tray says it is not
/// on this network.
const NOT_ON_NETWORK_AFTER: Duration = Duration::from_secs(20);

pub fn capture_allowed() -> bool {
    #[cfg(target_os = "macos")]
    {
        hidfi_stream::sck::allowed()
    }
    #[cfg(not(target_os = "macos"))]
    {
        true
    }
}

pub fn real_source(display: Option<u32>) -> Result<Box<dyn Source>, String> {
    hidfi_stream::screen(display, 30)
}

pub fn start(store: Store, env: Env) -> Handle {
    let state = store.state();
    let key = Arc::new(RwLock::new(store.key()));
    let nonces = TicketNonces::default();
    let view = Arc::new(Mutex::new(View {
        board: BoardState::Looking,
        session: Status::Connecting,
        watching: 0,
        capture_allowed: (env.capture_allowed)(),
        port: None,
        name: state.name.clone(),
        problem: None,
    }));
    let (tx, rx) = channel();

    // Listening for viewers starts at once, and stays: the port and the
    // certificate's fingerprint are what the board tells dashboards.
    let source = env.source.clone();
    let pipeline = Arc::new(Pipeline::start(
        30,
        4_000_000,
        move |display| source(display),
        hidfi_stream::encoder,
    ));
    let gate = Arc::new(TicketGate::new(key.clone(), nonces.clone()));
    let listener: Option<(Server, Listener)> = match store.cert() {
        Some(cert) => {
            let fp = hidfi_stream::rtc::cert_fp(&cert);
            let displays = env.displays.clone();
            match server::start(
                None,
                gate,
                pipeline.clone(),
                cert,
                Box::new(move || displays()),
            ) {
                Ok(s) => {
                    let l = Listener { port: s.port, fp };
                    Some((s, l))
                }
                Err(e) => {
                    view.lock().unwrap_or_else(|e| e.into_inner()).problem =
                        Some(format!("Cannot accept viewers: {e}"));
                    None
                }
            }
        }
        None => {
            view.lock().unwrap_or_else(|e| e.into_inner()).problem =
                Some("Cannot make a certificate".into());
            None
        }
    };
    let watchers = listener.as_ref().map(|(s, _)| s.watchers.clone());
    let v = view.clone();
    let notify: Notify = Arc::default();
    let n = notify.clone();
    std::thread::Builder::new()
        .name("service".into())
        .spawn(move || run(store, state, env, key, nonces, listener, pipeline, v, n, rx))
        .expect("spawning the service thread");
    Handle {
        view,
        tx,
        watchers,
        notify,
    }
}

#[allow(clippy::too_many_arguments)]
fn run(
    store: Store,
    mut state: State,
    env: Env,
    key: Arc<RwLock<Option<PairKey>>>,
    nonces: TicketNonces,
    listener: Option<(Server, Listener)>,
    _pipeline: Arc<Pipeline>,
    view: Arc<Mutex<View>>,
    notify: Notify,
    rx: Receiver<Cmd>,
) {
    let mut id = [0u8; 16];
    let _ = hex::decode_to_slice(&state.id, &mut id);
    let identity = Identity {
        id,
        name: state.name.clone(),
        key: key.read().unwrap_or_else(|e| e.into_inner()).clone(),
    };
    let mut session: Option<Session> = None;
    let mut pending_identity = Some(identity);
    let mut chosen: Option<Mac> = state.board_mac.as_deref().and_then(Mac::parse);
    let started = Instant::now();
    let mut unseen_since: Option<Instant> = None;
    let mut last_miss_log: Option<Instant> = None;
    let set = |f: &dyn Fn(&mut View)| {
        let mut v = view.lock().unwrap_or_else(|e| e.into_inner());
        let before = v.clone();
        let (b, st) = (v.board.clone(), v.session.clone());
        f(&mut v);
        let changed = *v != before;
        // Changes only, so a quiet day writes nothing. Never the pairing code.
        if v.board != b {
            log::info!("board: {:?}", v.board);
        }
        if v.session != st {
            match &v.session {
                Status::ShowCode(_) => log::info!("session: showing the pairing code"),
                other => log::info!("session: {other:?}"),
            }
        }
        drop(v);
        if changed {
            if let Some(f) = notify.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
                f();
            }
        }
    };

    loop {
        // Still plugged into this computer? If the board moved to another one,
        // stop at once: watching computer A while typing into computer B is the
        // one mistake this app must never make.
        if let (Some(s), Some(mac)) = (
            session.as_ref(),
            state.board_mac.as_deref().and_then(Mac::parse),
        ) {
            let here = hidfi_core::discovery::usb_macs(&env.usb.list()).contains(&mac);
            if !here {
                log::info!("the board left this computer's USB");
                pending_identity = Some(s.identity.clone());
                session = None;
                set(&|v| v.board = BoardState::NotPluggedIn { elsewhere: 0 });
            }
        }
        // Find (or re-find) the board when there is no session, or it was lost.
        let need_find = session.as_ref().is_none_or(|s| s.lost());
        if need_find {
            let remembered = chosen.zip(state.board_url.clone());
            let found = locate::find(
                env.usb.as_ref(),
                env.http.as_ref(),
                env.browse.as_ref(),
                remembered.as_ref().map(|(m, u)| (*m, u.as_str())),
            );
            let board = match found.selection {
                Selection::Board(b) => {
                    unseen_since = None;
                    if state.board_mac.as_deref() != Some(&b.mac.hex())
                        || state.board_url.as_deref() != Some(&b.url)
                    {
                        state.board_mac = Some(b.mac.hex());
                        state.board_url = Some(b.url.clone());
                        if let Err(e) = store.save_state(&state) {
                            log::warn!("could not save state: {e}");
                        }
                    }
                    match session.as_mut() {
                        Some(s) => s.set_base(b.url.clone()),
                        None => {
                            let ident = pending_identity.take().expect("identity used once");
                            let mut s = Session::new(ident, b.url.clone());
                            s.nonces = nonces.clone();
                            if let Some((_, l)) = &listener {
                                s.set_listener(l.clone());
                            }
                            session = Some(s);
                        }
                    }
                    BoardState::Found {
                        url: b.url,
                        firmware: b.firmware,
                    }
                }
                Selection::NotPluggedIn { elsewhere } => BoardState::NotPluggedIn { elsewhere },
                // On USB but not answering yet: a fresh install has no address
                // to try, mDNS answers within a few seconds and a lookup by name
                // can take five on macOS. Keep looking quietly before saying it
                // is not on this network, or every first start says so.
                Selection::NotOnNetwork { .. } => {
                    let since = *unseen_since.get_or_insert_with(Instant::now);
                    if since.elapsed() < NOT_ON_NETWORK_AFTER {
                        BoardState::Looking
                    } else {
                        // Say why, once a minute at most: the log is how a "not
                        // on this network" that is not true gets explained.
                        if last_miss_log
                            .is_none_or(|t: Instant| t.elapsed() > Duration::from_secs(60))
                        {
                            last_miss_log = Some(Instant::now());
                            for (url, why) in &found.misses {
                                log::warn!("board on USB, but {url} did not answer: {why}");
                            }
                        }
                        BoardState::NotOnNetwork
                    }
                }
                Selection::Choose(v) => {
                    BoardState::Choose(v.into_iter().map(|b| (b.mac.hex(), b.url)).collect())
                }
            };
            set(&|v| v.board = board.clone());
        }

        // Viewers are welcome only while the board is plugged in here. Decided
        // after finding, so a board that just came back is open at once.
        if let Some((srv, _)) = &listener {
            srv.set_open(session.is_some());
        }

        // One step of the conversation with the board.
        let mut wait = if started.elapsed() < Duration::from_secs(60) {
            FIND_SOON
        } else {
            FIND_LATER
        };
        if let Some(s) = session.as_mut() {
            if !matches!(
                view.lock().unwrap_or_else(|e| e.into_inner()).board,
                BoardState::Found { .. }
            ) && s.lost()
            {
                // Still lost after looking: try again soon.
            } else {
                wait = s.step(env.http.as_ref(), &SystemClock);
            }
            if s.key_changed {
                s.key_changed = false;
                *key.write().unwrap_or_else(|e| e.into_inner()) = s.identity.key.clone();
                if let Err(e) = store.save_key(s.identity.key.as_ref()) {
                    log::error!("could not save the pairing key: {e}");
                    set(&|v| v.problem = Some("Could not save the pairing".into()));
                }
            }
            let st = s.status().clone();
            set(&|v| v.session = st.clone());
        }
        let watching = listener.as_ref().map_or(0, |(s, _)| s.watchers.count());
        let allowed = (env.capture_allowed)();
        set(&|v| {
            v.watching = watching;
            v.capture_allowed = allowed;
            v.port = listener.as_ref().map(|(s, _)| s.port);
        });

        // While someone watches, keep the tray's count current.
        if watching > 0 {
            wait = wait.min(Duration::from_secs(2));
        }
        wait = wait.min(USB_CHECK);
        match rx.recv_timeout(wait) {
            Ok(Cmd::Quit) | Err(RecvTimeoutError::Disconnected) => break,
            Ok(Cmd::PairAgain) => {
                if let Some(s) = session.as_mut() {
                    s.request_pairing();
                }
            }
            Ok(Cmd::Forget) => {
                if let Some(s) = session.as_mut() {
                    s.reset_pairing();
                    s.key_changed = true;
                }
            }
            Ok(Cmd::ChooseBoard(mac)) => {
                chosen = Mac::parse(&mac);
                if let Some(s) = session.as_mut() {
                    // A different board: start over against it.
                    if state.board_mac.as_deref() != Some(&mac) {
                        let ident = Identity {
                            id: s.identity.id,
                            name: s.identity.name.clone(),
                            key: None,
                        };
                        pending_identity = Some(ident);
                        session = None;
                        *key.write().unwrap_or_else(|e| e.into_inner()) = None;
                        let _ = store.save_key(None);
                    }
                }
                state.board_mac = Some(mac);
                state.board_url = None;
                let _ = store.save_state(&state);
            }
            Err(RecvTimeoutError::Timeout) => {}
        }
    }
    drop(listener);
}
