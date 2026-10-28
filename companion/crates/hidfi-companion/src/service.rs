//! The app's work, on one background thread: find every HID-Fi plugged into
//! this computer, pair with each, keep each told where we listen, and serve
//! viewers from any of their dashboards.
//!
//! A computer can have two boards plugged in, and a board moves between
//! computers: each board has its own session and its own pairing (kept when it
//! leaves, so plugging it back needs no new pairing). A board that leaves this
//! computer's USB stops its session at once - watching computer A while typing
//! into computer B is the one mistake this app must never make.
//!
//! The tray never touches the network. It reads a snapshot (`View`) and sends
//! commands (`Cmd`); the service wakes for a command at once, otherwise sleeps
//! for exactly as long as the session asked.

use crate::store::{State, Store};
use hidfi_agent::env::{Browse, Http, SystemClock, UsbList};
use hidfi_agent::locate;
use hidfi_agent::session::{Identity, Listener, Session, Status, TicketNonces};
use hidfi_core::discovery::Mac;
use hidfi_core::pairing::PairKey;
use hidfi_stream::pipeline::{Pipeline, Source};
use hidfi_stream::server::{self, GateKey, Server, TicketGate, Watchers};
use std::collections::{BTreeMap, HashMap};
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
    Found {
        url: String,
        firmware: String,
    },
}

/// One board plugged into this computer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BoardView {
    /// 12 hex digits; the last 6 are what its WiFi and dashboard are named by.
    pub mac: String,
    pub board: BoardState,
    pub session: Status,
}

/// Everything the tray shows.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct View {
    /// With one board (the usual case): that board. With several: the first;
    /// `boards` has them all.
    pub board: BoardState,
    pub session: Status,
    /// Every HID-Fi plugged into this computer, by MAC.
    pub boards: Vec<BoardView>,
    pub watching: usize,
    pub capture_allowed: bool,
    pub port: Option<u16>,
    pub name: String,
    pub problem: Option<String>,
    /// Updates: each board's version, what it is offered, what is running.
    pub updates: crate::updates::center::UpdView,
}

pub enum Cmd {
    /// For this board (MAC), or every board plugged in.
    PairAgain(Option<String>),
    /// Forget the pairing on this computer (the dashboard can remove it too).
    Forget(Option<String>),
    /// An update action (the person's consent is the click that sends it).
    Update(crate::updates::center::UpdCmd),
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
    /// Updates for the boards and this app; None leaves them out (tests of other things).
    pub updates: Option<crate::updates::center::UpdatesSetup>,
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
    let mut state = store.state();
    if let Err(e) = store.migrate(&mut state) {
        log::warn!("could not move the pairing to its board's file: {e}");
    }
    // Until the service has looked at the USB, no board counts as plugged in.
    let keys: Arc<RwLock<Vec<GateKey>>> = Arc::new(RwLock::new(Vec::new()));
    let nonces = TicketNonces::default();
    let view = Arc::new(Mutex::new(View {
        board: BoardState::Looking,
        session: Status::Connecting,
        boards: Vec::new(),
        watching: 0,
        capture_allowed: (env.capture_allowed)(),
        port: None,
        name: state.name.clone(),
        problem: None,
        updates: Default::default(),
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
    let gate = Arc::new(TicketGate::new(keys.clone(), nonces.clone()));
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
    let notify: Notify = Arc::default();
    // Updates run on their own thread; the service tells it which boards are here.
    let upd_boards: Arc<Mutex<Vec<crate::updates::center::BoardHere>>> = Arc::default();
    let mut env = env;
    let upd_tx = env.updates.take().map(|setup| {
        let dir = store.dir().to_path_buf();
        let id = state.id.clone();
        let keys = Store::open(dir.clone()).expect("the folder the store already opened");
        let link_for: Arc<crate::updates::center::LinkFor> =
            Arc::new(move |mac: &str| {
                let key = Mac::parse(mac).and_then(|m| keys.key_for(&m));
                let paired = key.is_some();
                let key = key.unwrap_or_else(|| hidfi_core::pairing::PairKey::from_bytes([0; 32]));
                (Arc::new(crate::updates::real::HttpBoardLink::new(id.clone(), key)) as Arc<dyn crate::updates::BoardLink>, paired)
            });
        let (v2, n2) = (view.clone(), notify.clone());
        crate::updates::center::start(
            setup.into_env(dir, link_for),
            upd_boards.clone(),
            Box::new(move |u| {
                v2.lock().unwrap_or_else(|e| e.into_inner()).updates = u;
                if let Some(f) = n2.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
                    f();
                }
            }),
        )
    });
    let v = view.clone();
    let n = notify.clone();
    std::thread::Builder::new()
        .name("service".into())
        .spawn(move || {
            run(
                store, state, env, keys, nonces, listener, pipeline, v, n, rx, upd_boards, upd_tx,
            )
        })
        .expect("spawning the service thread");
    Handle {
        view,
        tx,
        watchers,
        notify,
    }
}

/// A board plugged into this computer and answering, and our conversation
/// with it.
struct Here {
    url: String,
    firmware: String,
    session: Session,
}

#[allow(clippy::too_many_arguments)]
fn run(
    store: Store,
    mut state: State,
    env: Env,
    keys: Arc<RwLock<Vec<GateKey>>>,
    nonces: TicketNonces,
    listener: Option<(Server, Listener)>,
    _pipeline: Arc<Pipeline>,
    view: Arc<Mutex<View>>,
    notify: Notify,
    rx: Receiver<Cmd>,
    upd_boards: Arc<Mutex<Vec<crate::updates::center::BoardHere>>>,
    upd_tx: Option<Sender<crate::updates::center::UpdCmd>>,
) {
    let mut id = [0u8; 16];
    let _ = hex::decode_to_slice(&state.id, &mut id);
    let name = state.name.clone();
    let identity = |mac: &Mac| Identity {
        id,
        name: name.clone(),
        key: store.key_for(mac),
    };
    let mut here: BTreeMap<Mac, Here> = BTreeMap::new();
    let mut offline_since: HashMap<Mac, Instant> = HashMap::new();
    let mut elsewhere = 0usize;
    let mut next_find = Instant::now();
    let started = Instant::now();
    let mut last_miss_log: Option<Instant> = None;
    let set = |f: &dyn Fn(&mut View)| {
        let mut v = view.lock().unwrap_or_else(|e| e.into_inner());
        let before = v.clone();
        f(&mut v);
        let changed = *v != before;
        // Changes only, so a quiet day writes nothing. Never the pairing code.
        if v.boards != before.boards || v.board != before.board {
            for b in &v.boards {
                let was = before.boards.iter().find(|o| o.mac == b.mac);
                if was.map(|o| &o.board) != Some(&b.board) {
                    log::info!("board {}: {:?}", short_mac(&b.mac), b.board);
                }
                if was.map(|o| &o.session) != Some(&b.session) {
                    match &b.session {
                        Status::ShowCode(_) => {
                            log::info!(
                                "board {}: session: showing the pairing code",
                                short_mac(&b.mac)
                            )
                        }
                        other => log::info!("board {}: session: {other:?}", short_mac(&b.mac)),
                    }
                }
            }
            if v.boards.is_empty() && v.board != before.board {
                log::info!("board: {:?}", v.board);
            }
        }
        drop(v);
        if changed {
            if let Some(f) = notify.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
                f();
            }
        }
    };
    // Every board this computer is paired with, kept in memory; the gate gets
    // them with "plugged in here now" on each turn.
    let mut paired: Vec<(Mac, PairKey)> = store.keys();

    loop {
        // What is on this computer's USB now. A board that left stops at once;
        // its pairing stays on disk for when it comes back.
        let on_usb = hidfi_core::discovery::usb_macs(&env.usb.list());
        let gone: Vec<Mac> = here
            .keys()
            .filter(|m| !on_usb.contains(m))
            .copied()
            .collect();
        for m in gone {
            log::info!("board {} left this computer's USB", short_mac(&m.hex()));
            here.remove(&m);
        }
        offline_since.retain(|m, _| on_usb.contains(m));
        // Tickets only from boards plugged in now; a board that left loses its
        // viewers (the server checks each viewer's board on every turn).
        *keys.write().unwrap_or_else(|e| e.into_inner()) = paired
            .iter()
            .map(|(m, k)| GateKey {
                tag: mac_tag(m),
                key: k.clone(),
                here: on_usb.contains(m),
            })
            .collect();

        // Find boards: when one on USB has no session, a session lost its
        // board, or now and then for boards that are not answering yet.
        let missing = on_usb.iter().any(|m| !here.contains_key(m));
        let lost = here.values().any(|h| h.session.lost());
        if (missing || lost) && Instant::now() >= next_find || on_usb.is_empty() && !here.is_empty()
        {
            let found = locate::find_all(
                env.usb.as_ref(),
                env.http.as_ref(),
                env.browse.as_ref(),
                &Store::remembered(&state),
            );
            elsewhere = found.elsewhere;
            for b in found.here {
                offline_since.remove(&b.mac);
                if let Err(e) = store.remember(&mut state, &b.mac, &b.url) {
                    log::warn!("could not save state: {e}");
                }
                match here.get_mut(&b.mac) {
                    Some(h) => {
                        h.session.set_base(b.url.clone());
                        h.session.found_again();
                        (h.url, h.firmware) = (b.url, b.firmware);
                    }
                    None => {
                        let mut s = Session::new(identity(&b.mac), b.url.clone());
                        s.nonces = nonces.clone();
                        if let Some((_, l)) = &listener {
                            s.set_listener(l.clone());
                        }
                        here.insert(
                            b.mac,
                            Here {
                                url: b.url,
                                firmware: b.firmware,
                                session: s,
                            },
                        );
                    }
                }
            }
            for m in found.offline {
                offline_since.entry(m).or_insert_with(Instant::now);
            }
            if !found.misses.is_empty()
                && offline_since
                    .values()
                    .any(|s| s.elapsed() >= NOT_ON_NETWORK_AFTER)
                && last_miss_log.is_none_or(|t: Instant| t.elapsed() > Duration::from_secs(60))
            {
                // Why, once a minute at most: the log is how a "not on this
                // network" that is not true gets explained.
                last_miss_log = Some(Instant::now());
                for (url, why) in &found.misses {
                    log::warn!("board on USB, but {url} did not answer: {why}");
                }
            }
            next_find = Instant::now()
                + if started.elapsed() < Duration::from_secs(60) {
                    FIND_SOON
                } else {
                    FIND_LATER
                };
        }

        // Viewers are welcome only while a board we serve is plugged in here.
        if let Some((srv, _)) = &listener {
            srv.set_open(!here.is_empty());
        }

        // One step of each conversation.
        let mut wait = if started.elapsed() < Duration::from_secs(60) {
            FIND_SOON
        } else {
            FIND_LATER
        };
        let mut keys_changed = false;
        for (mac, h) in here.iter_mut() {
            if !h.session.lost() {
                wait = wait.min(h.session.step(env.http.as_ref(), &SystemClock));
            }
            if h.session.key_changed {
                h.session.key_changed = false;
                keys_changed = true;
                if let Err(e) = store.save_key_for(mac, h.session.identity.key.as_ref()) {
                    log::error!("could not save the pairing key: {e}");
                    set(&|v| v.problem = Some("Could not save the pairing".into()));
                }
            }
        }
        if keys_changed {
            paired = store.keys();
            *keys.write().unwrap_or_else(|e| e.into_inner()) = paired
                .iter()
                .map(|(m, k)| GateKey {
                    tag: mac_tag(m),
                    key: k.clone(),
                    here: on_usb.contains(m),
                })
                .collect();
        }

        // The view: every board on USB, answering or not.
        let boards: Vec<BoardView> = on_usb
            .iter()
            .map(|m| match here.get(m) {
                Some(h) => BoardView {
                    mac: m.hex(),
                    board: BoardState::Found {
                        url: h.url.clone(),
                        firmware: h.firmware.clone(),
                    },
                    session: h.session.status().clone(),
                },
                None => BoardView {
                    mac: m.hex(),
                    // A fresh install has no address to try, mDNS answers within
                    // a few seconds and a lookup by name can take five on macOS:
                    // look quietly first, then say it is not on this network.
                    board: if offline_since
                        .get(m)
                        .is_some_and(|s| s.elapsed() >= NOT_ON_NETWORK_AFTER)
                    {
                        BoardState::NotOnNetwork
                    } else {
                        BoardState::Looking
                    },
                    session: Status::Connecting,
                },
            })
            .collect();
        let watching = listener.as_ref().map_or(0, |(s, _)| s.watchers.count());
        let allowed = (env.capture_allowed)();
        set(&|v| {
            match boards.first() {
                Some(b) => {
                    v.board = b.board.clone();
                    v.session = b.session.clone();
                }
                None => {
                    v.board = BoardState::NotPluggedIn { elsewhere };
                    v.session = Status::Connecting;
                }
            }
            v.boards = boards.clone();
            v.watching = watching;
            v.capture_allowed = allowed;
            v.port = listener.as_ref().map(|(s, _)| s.port);
        });

        // The update thread's list of boards here: every board on USB, with its
        // address once it answers.
        *upd_boards.lock().unwrap_or_else(|e| e.into_inner()) = on_usb
            .iter()
            .map(|m| crate::updates::center::BoardHere { mac: m.hex(), url: here.get(m).map(|h| h.url.clone()) })
            .collect();

        // While someone watches, keep the tray's count current.
        if watching > 0 {
            wait = wait.min(Duration::from_secs(2));
        }
        wait = wait.min(USB_CHECK);
        let pick = |mac: &Option<String>, m: &Mac| mac.as_deref().is_none_or(|x| x == m.hex());
        match rx.recv_timeout(wait) {
            Ok(Cmd::Quit) | Err(RecvTimeoutError::Disconnected) => break,
            Ok(Cmd::PairAgain(mac)) => {
                for (m, h) in here.iter_mut() {
                    if pick(&mac, m) {
                        h.session.request_pairing();
                    }
                }
            }
            Ok(Cmd::Update(c)) => {
                if let Some(tx) = &upd_tx {
                    let _ = tx.send(c);
                }
            }
            Ok(Cmd::Forget(mac)) => {
                for (m, h) in here.iter_mut() {
                    if pick(&mac, m) {
                        h.session.reset_pairing();
                        h.session.key_changed = true;
                    }
                }
            }
            Err(RecvTimeoutError::Timeout) => {}
        }
    }
    drop(listener);
}

/// A board's MAC as the number its viewers are tagged with.
fn mac_tag(m: &Mac) -> u64 {
    u64::from_str_radix(&m.hex(), 16).unwrap_or(0)
}

/// The last six hex digits: what the board's WiFi and dashboard are named by
/// (ESP32-HID-09F7C8, esp-hid-09f7c8.local).
pub fn short_mac(mac: &str) -> String {
    mac.get(mac.len().saturating_sub(6)..)
        .unwrap_or(mac)
        .to_uppercase()
}
