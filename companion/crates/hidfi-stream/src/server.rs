//! Where viewers knock: a WebSocket that takes a board-signed ticket, then an
//! SDP offer, answers it, and keeps the session's control channel.
//!
//! Wire format (JSON text frames):
//!   viewer → {"t":"hello","tn","vn","ticket"}      within 5 s of connecting
//!   app    → {"t":"ready","w","h"} | {"t":"error","reason"}
//!   viewer → {"t":"offer","sdp"}                    within 10 s
//!   app    → {"t":"answer","sdp"}
//!   app    → {"t":"stats","fps"}                    every second while live
//!   app    → {"t":"audio","system","mic","why"}     sound shared or not (docs/AUDIO.md):
//!            "on" | "off" | "unavailable"; with "ready" and whenever it changes
//!   viewer → {"t":"key"}                            ask for a keyframe
//!   viewer → {"t":"bye"}
//!   app    → {"t":"end","reason"}                   then closes
//!
//! Defences (A10): private-network peers only; at most 2 viewers and 6
//! connections; 5 s to present a ticket; 64 KB messages; an address that
//! presents 5 bad tickets in 10 minutes is refused for 10 minutes. Nothing but
//! a JSON parse and one HMAC happens before a ticket is accepted.

use crate::display::{self, Display};
use crate::pipeline::MAIN;
use crate::pipeline::{Pipeline, ToViewer};
use crate::rtc::{self, DtlsCert, End, ToSession};
use hidfi_agent::session::TicketNonces;
use hidfi_core::pairing::{self, PairKey};
use serde::Deserialize;
use std::collections::{HashMap, VecDeque};
use std::io::ErrorKind;
use std::net::{IpAddr, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicUsize, Ordering};
use std::sync::mpsc::sync_channel;
use std::sync::{Arc, Mutex, RwLock};
use std::time::{Duration, Instant};
use tungstenite::protocol::WebSocketConfig;
use tungstenite::{Message, WebSocket};

/// One encoder feeds every viewer, so more viewers cost the computer upload,
/// not CPU; the stream follows the slowest of them.
/// Six: a wall of four monitors plus a phone or two that control. Capture and
/// encoding are shared per monitor, so a viewer more costs only its sending.
pub const MAX_VIEWERS: usize = 6;
pub const MAX_CONNS: usize = 10;
pub const HELLO_DEADLINE: Duration = Duration::from_secs(5);
pub const OFFER_DEADLINE: Duration = Duration::from_secs(10);
pub const MAX_MESSAGE: usize = 64 * 1024;
const BAD_LIMIT: usize = 5;
const BAD_WINDOW: Duration = Duration::from_secs(600);
/// The ports tried in order; the first free one is used and told to the board.
pub const PORTS: std::ops::RangeInclusive<u16> = 47800..=47819;

/// Decides whether a ticket lets a viewer in.
/// What the gate says to a viewer's ticket.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Admit {
    /// In, on behalf of the board with this tag.
    Yes(u64),
    /// A good ticket, from a board that is not plugged into this computer now.
    Closed,
    No,
}

pub trait Gate: Send + Sync {
    fn admit(&self, tn: &str, vn: &str, ticket: &[u8]) -> Admit;
    /// Whether viewers admitted for this board may go on watching: false once
    /// it has left this computer's USB.
    fn still_open(&self, _tag: u64) -> bool {
        true
    }
}

/// The real gate: the pairing key, the nonces this app announced, and every
/// `vn` already used (a ticket works once).
/// A board this computer is paired with, as the gate sees it.
#[derive(Clone)]
pub struct GateKey {
    /// Which board (its MAC as a number): viewers are tied to it.
    pub tag: u64,
    pub key: PairKey,
    /// Plugged into this computer now. A board that leaves loses its viewers
    /// at once: watching computer A while typing into computer B is the one
    /// mistake this app must never make.
    pub here: bool,
}

pub struct TicketGate {
    /// One per board this computer is paired with: a computer can have two
    /// boards plugged in, and either's dashboard may watch.
    pub keys: Arc<RwLock<Vec<GateKey>>>,
    pub nonces: TicketNonces,
    used: Mutex<VecDeque<(String, Instant)>>,
}

impl TicketGate {
    pub fn new(keys: Arc<RwLock<Vec<GateKey>>>, nonces: TicketNonces) -> Self {
        TicketGate {
            keys,
            nonces,
            used: Mutex::default(),
        }
    }
}

impl Gate for TicketGate {
    fn admit(&self, tn: &str, vn: &str, ticket: &[u8]) -> Admit {
        let now = Instant::now();
        if !self.nonces.is_live(tn, now) {
            return Admit::No;
        }
        let (tag, here) = {
            let keys = self.keys.read().unwrap_or_else(|e| e.into_inner());
            match keys
                .iter()
                .find(|k| pairing::ticket_ok(&k.key, tn, vn, ticket))
            {
                Some(k) => (k.tag, k.here),
                None => return Admit::No,
            }
        };
        if !here {
            return Admit::Closed;
        }
        let mut used = self.used.lock().unwrap_or_else(|e| e.into_inner());
        used.retain(|(_, at)| now.duration_since(*at) < Duration::from_secs(300));
        if used.iter().any(|(v, _)| v == vn) {
            return Admit::No;
        }
        used.push_back((vn.to_string(), now));
        Admit::Yes(tag)
    }

    fn still_open(&self, tag: u64) -> bool {
        self.keys
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .any(|k| k.tag == tag && k.here)
    }
}

/// Addresses a viewer may come from: the private ranges a home network uses,
/// link-local, and loopback (tests). Never the public internet.
pub fn private_peer(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v) => v.is_private() || v.is_loopback() || v.is_link_local(),
        IpAddr::V6(v) => {
            if let Some(m) = v.to_ipv4_mapped() {
                return private_peer(IpAddr::V4(m));
            }
            v.is_loopback()
                || (v.segments()[0] & 0xfe00) == 0xfc00
                || (v.segments()[0] & 0xffc0) == 0xfe80
        }
    }
}

#[derive(Deserialize)]
#[serde(tag = "t", rename_all = "snake_case")]
enum FromViewer {
    Hello {
        tn: String,
        vn: String,
        ticket: String,
    },
    Offer {
        sdp: String,
    },
    /// Watch another display (an id from the list in `ready`).
    Display {
        id: u32,
    },
    Key,
    Bye,
}

/// What the server tells whoever runs it (the tray, the window): who is
/// watching, in all and through which board (its tag: the MAC as a number).
#[derive(Clone, Default)]
pub struct Watchers {
    total: Arc<AtomicUsize>,
    by_board: Arc<Mutex<HashMap<u64, usize>>>,
}
impl Watchers {
    pub fn count(&self) -> usize {
        self.total.load(Ordering::Relaxed)
    }
    /// Viewers admitted through this board's tickets.
    pub fn of_board(&self, tag: u64) -> usize {
        self.by_board
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&tag)
            .copied()
            .unwrap_or(0)
    }
    /// Claims a viewer slot for `tag`; None when every slot is taken. The slot
    /// is given back when the returned guard drops, on every way out.
    fn claim(&self, tag: u64, max: usize) -> Option<ViewerSlot<'_>> {
        if self.total.fetch_add(1, Ordering::SeqCst) >= max {
            self.total.fetch_sub(1, Ordering::SeqCst);
            return None;
        }
        *self
            .by_board
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .entry(tag)
            .or_default() += 1;
        Some(ViewerSlot { w: self, tag })
    }
}

struct ViewerSlot<'a> {
    w: &'a Watchers,
    tag: u64,
}
impl Drop for ViewerSlot<'_> {
    fn drop(&mut self) {
        let mut by = self.w.by_board.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(n) = by.get_mut(&self.tag) {
            *n -= 1;
            if *n == 0 {
                by.remove(&self.tag);
            }
        }
        drop(by);
        self.w.total.fetch_sub(1, Ordering::SeqCst);
    }
}

pub struct Server {
    pub port: u16,
    pub watchers: Watchers,
    stop: Arc<AtomicBool>,
    open: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Server {
    /// Closed: new viewers are refused (`"closed"`) and current ones are ended.
    /// Used when the board is no longer plugged into this computer, so nobody
    /// watches one computer while typing into another.
    pub fn set_open(&self, open: bool) {
        self.open.store(open, Ordering::Relaxed);
    }
    pub fn is_open(&self) -> bool {
        self.open.load(Ordering::Relaxed)
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        // Wake the accept loop.
        let _ = TcpStream::connect_timeout(
            &SocketAddr::from(([127, 0, 0, 1], self.port)),
            Duration::from_millis(200),
        );
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

struct Ctx {
    gate: Arc<dyn Gate>,
    pipeline: Arc<Pipeline>,
    /// The computer's sound, when this build has a way to capture it.
    audio: Option<Arc<crate::audio::AudioHub>>,
    cert: DtlsCert,
    conns: AtomicUsize,
    viewers: Watchers,
    bad: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
    displays: Displays,
    stop: Arc<AtomicBool>,
    open: Arc<AtomicBool>,
}

impl Ctx {
    fn blocked(&self, ip: IpAddr) -> bool {
        let mut b = self.bad.lock().unwrap_or_else(|e| e.into_inner());
        let now = Instant::now();
        let q = b.entry(ip).or_default();
        q.retain(|t| now.duration_since(*t) < BAD_WINDOW);
        let blocked = q.len() >= BAD_LIMIT;
        if q.is_empty() {
            b.remove(&ip);
        }
        // A flood of addresses cannot grow this without bound.
        if b.len() > 256 {
            b.retain(|_, q| q.len() >= BAD_LIMIT);
        }
        blocked
    }
    fn strike(&self, ip: IpAddr) {
        self.bad
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .entry(ip)
            .or_default()
            .push_back(Instant::now());
    }
}

/// The sound state as the viewer reads it.
fn audio_json(s: &crate::audio::State) -> serde_json::Value {
    use crate::audio::Share;
    let word = |x: &Share| match x {
        Share::On => "on",
        Share::Off => "off",
        Share::Unavailable(_) => "unavailable",
    };
    let why = |x: &Share| match x {
        Share::Unavailable(w) => serde_json::Value::String(w.chars().take(120).collect()),
        _ => serde_json::Value::Null,
    };
    serde_json::json!({"t":"audio","system":word(&s.system),"mic":word(&s.mic),
                       "why":{"system":why(&s.system),"mic":why(&s.mic)}})
}

/// The displays viewers may choose from, asked when one connects and every
/// few seconds while it watches (monitors come and go).
pub type Displays = Box<dyn Fn() -> Vec<Display> + Send + Sync>;

/// How often a watching viewer's display list is checked for changes.
const DISPLAYS_EVERY: Duration = Duration::from_secs(3);

/// Binds the first free port in [`PORTS`] (or exactly `port` if given).
pub fn start(
    port: Option<u16>,
    gate: Arc<dyn Gate>,
    pipeline: Arc<Pipeline>,
    audio: Option<Arc<crate::audio::AudioHub>>,
    cert: DtlsCert,
    displays: Displays,
) -> std::io::Result<Server> {
    let listener = match port {
        Some(p) => TcpListener::bind(("0.0.0.0", p))?,
        None => PORTS
            .clone()
            .find_map(|p| TcpListener::bind(("0.0.0.0", p)).ok())
            .ok_or_else(|| {
                std::io::Error::new(ErrorKind::AddrInUse, "no free port in 47800-47819")
            })?,
    };
    let port = listener.local_addr()?.port();
    let stop = Arc::new(AtomicBool::new(false));
    let open = Arc::new(AtomicBool::new(true));
    let watchers = Watchers::default();
    let ctx = Arc::new(Ctx {
        gate,
        pipeline,
        audio,
        cert,
        conns: AtomicUsize::new(0),
        viewers: watchers.clone(),
        bad: Mutex::default(),
        displays,
        stop: stop.clone(),
        open: open.clone(),
    });
    let thread = std::thread::Builder::new()
        .name("signal".into())
        .spawn(move || {
            for conn in listener.incoming() {
                if ctx.stop.load(Ordering::Relaxed) {
                    break;
                }
                let Ok(stream) = conn else { continue };
                let Ok(peer) = stream.peer_addr() else {
                    continue;
                };
                if !private_peer(peer.ip()) || ctx.blocked(peer.ip()) {
                    continue; // dropped: closes the connection
                }
                if ctx.conns.fetch_add(1, Ordering::SeqCst) >= MAX_CONNS {
                    ctx.conns.fetch_sub(1, Ordering::SeqCst);
                    continue;
                }
                let c = ctx.clone();
                let spawned = std::thread::Builder::new()
                    .name("viewer".into())
                    .spawn(move || {
                        if let Err(e) = serve(&c, stream, peer) {
                            log::debug!("viewer {peer}: {e}");
                        }
                        c.conns.fetch_sub(1, Ordering::SeqCst);
                    });
                if spawned.is_err() {
                    ctx.conns.fetch_sub(1, Ordering::SeqCst);
                }
            }
        })?;
    Ok(Server {
        port,
        watchers,
        stop,
        open,
        thread: Some(thread),
    })
}

fn send(ws: &mut WebSocket<TcpStream>, v: serde_json::Value) -> tungstenite::Result<()> {
    ws.send(Message::text(v.to_string()))
}

enum Got {
    /// A message; `None` if it was not one we understand (ignored).
    Msg(Option<FromViewer>),
    /// Nothing arrived before the deadline.
    Quiet,
}

/// Reads one viewer message, waiting at most until `deadline`. `Err` means the
/// socket is gone (closed, reset, protocol error).
fn read(ws: &mut WebSocket<TcpStream>, deadline: Instant) -> Result<Got, String> {
    loop {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return Ok(Got::Quiet);
        }
        let _ = ws
            .get_ref()
            .set_read_timeout(Some(left.min(Duration::from_millis(500))));
        match ws.read() {
            Ok(Message::Text(t)) => return Ok(Got::Msg(serde_json::from_str(&t).ok())),
            Ok(Message::Close(_)) => return Err("closed".into()),
            Ok(_) => continue,
            Err(tungstenite::Error::Io(e))
                if matches!(e.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) =>
            {
                continue
            }
            Err(e) => return Err(e.to_string()),
        }
    }
}

/// The first message, which must arrive in time and be of the right kind.
fn expect(ws: &mut WebSocket<TcpStream>, within: Duration) -> Option<FromViewer> {
    match read(ws, Instant::now() + within) {
        Ok(Got::Msg(m)) => m,
        _ => None,
    }
}

fn serve(c: &Ctx, stream: TcpStream, peer: SocketAddr) -> Result<(), String> {
    let local_ip = stream.local_addr().map_err(|e| e.to_string())?.ip();
    stream
        .set_read_timeout(Some(HELLO_DEADLINE))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(5)))
        .map_err(|e| e.to_string())?;
    let _ = stream.set_nodelay(true);
    let cfg = WebSocketConfig::default()
        .max_message_size(Some(MAX_MESSAGE))
        .max_frame_size(Some(MAX_MESSAGE));
    let mut ws = tungstenite::accept_with_config(stream, Some(cfg)).map_err(|e| e.to_string())?;

    let Some(FromViewer::Hello { tn, vn, ticket }) = expect(&mut ws, HELLO_DEADLINE) else {
        c.strike(peer.ip());
        let _ = send(&mut ws, serde_json::json!({"t":"error","reason":"hello"}));
        return Err("no hello".into());
    };
    if !c.open.load(Ordering::Relaxed) {
        let _ = send(&mut ws, serde_json::json!({"t":"error","reason":"closed"}));
        return Err("closed".into());
    }
    let ticket = hex::decode(&ticket).unwrap_or_default();
    let tag = match c.gate.admit(&tn, &vn, &ticket) {
        Admit::Yes(tag) => tag,
        Admit::Closed => {
            // A good ticket from a board that has left this computer: the
            // dashboard says the computer is not reachable, not "bad ticket".
            let _ = send(&mut ws, serde_json::json!({"t":"error","reason":"closed"}));
            let _ = ws.close(None);
            return Err("its board is not plugged in here".into());
        }
        Admit::No => {
            c.strike(peer.ip());
            let _ = send(&mut ws, serde_json::json!({"t":"error","reason":"ticket"}));
            let _ = ws.close(None);
            return Err("bad ticket".into());
        }
    };
    // Claim a viewer slot; give it back on every way out.
    let Some(_slot) = c.viewers.claim(tag, MAX_VIEWERS) else {
        let _ = send(&mut ws, serde_json::json!({"t":"error","reason":"busy"}));
        return Err("busy".into());
    };

    let mut shown = (c.displays)();
    send(&mut ws, {
        let mut r =
            serde_json::json!({"t":"ready","displays":shown,"display":display::main_id(&shown)});
        if let Some(h) = &c.audio {
            r["audio"] = audio_json(&h.state());
        }
        r
    })
    .map_err(|e| e.to_string())?;
    // The offer, within the deadline. A viewer may ask for its monitor first
    // (it knows the list from "ready"): keep that for when it is set up - seen
    // live, insisting on the offer first failed every remembered monitor and
    // every wall tile ("no offer").
    let until = Instant::now() + OFFER_DEADLINE;
    let mut early_display: Option<u32> = None;
    let sdp = loop {
        match read(&mut ws, until) {
            Ok(Got::Msg(Some(FromViewer::Offer { sdp }))) => break sdp,
            Ok(Got::Msg(Some(FromViewer::Display { id }))) => early_display = Some(id),
            Ok(Got::Msg(_)) => {}
            Ok(Got::Quiet) | Err(_) => return Err("no offer".into()),
        }
    };
    let prepared = match rtc::prepare(&sdp, local_ip, c.cert.clone()) {
        Ok(p) => p,
        Err(e) => {
            let _ = send(
                &mut ws,
                serde_json::json!({"t":"error","reason":"offer","detail":e.to_string()}),
            );
            return Err(e.to_string());
        }
    };
    send(
        &mut ws,
        serde_json::json!({"t":"answer","sdp":prepared.answer}),
    )
    .map_err(|e| e.to_string())?;

    // The session: frames and datagrams on one bounded channel.
    let (tx, rx) = sync_channel::<ToSession>(256);
    let (vtx, vrx) = sync_channel::<ToViewer>(8);
    let stop = Arc::new(AtomicBool::new(false));
    let fps = Arc::new(AtomicU32::new(0));
    let ended: Arc<Mutex<Option<End>>> = Arc::default();
    let id = c.pipeline.subscribe(vtx);
    let control = c.pipeline.control(id);
    if let Some(d) = early_display {
        if display::main_id(&shown) == Some(d) {
            control.set_display(MAIN);
        } else if shown.iter().any(|x| x.id == d) {
            control.set_display(d);
        }
    }
    // Sound, when this viewer asked for it (an offer with an audio section) and
    // this computer can share it: its own forwarder, so it never waits behind
    // frames. A packet the session cannot take at once is dropped (Opus and RED
    // cover a gap); queued sound would be lag that never recovers.
    let audio_sub = match (&c.audio, sdp.contains("m=audio")) {
        (Some(h), true) => {
            let (atx, arx) = sync_channel::<Arc<crate::audio::Packet>>(50);
            let id = h.subscribe(atx);
            let (ftx, fstop) = (tx.clone(), stop.clone());
            let t = std::thread::Builder::new()
                .name("rtc-audio".into())
                .spawn(move || {
                    while !fstop.load(Ordering::Relaxed) {
                        match arx.recv_timeout(Duration::from_millis(500)) {
                            Ok(p) => {
                                let _ = ftx.try_send(ToSession::Audio(p));
                            }
                            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                            Err(_) => break,
                        }
                    }
                })
                .ok();
            Some((h.clone(), id, t))
        }
        _ => None,
    };
    let mut told_audio = c.audio.as_ref().map(|h| h.state());
    // Frames hop from the pipeline's channel into the session's.
    let fwd_tx = tx.clone();
    let fwd_stop = stop.clone();
    let fwd_ctl = control.clone();
    let fwd_ended = ended.clone();
    let forwarder = std::thread::Builder::new()
        .name("rtc-fwd".into())
        .spawn(move || {
            while !fwd_stop.load(Ordering::Relaxed) {
                match vrx.recv_timeout(Duration::from_millis(500)) {
                    Ok(m) => {
                        if fwd_tx.try_send(m.into()).is_err() {
                            // The session is behind: drop it, and this viewer
                            // skips to the next keyframe.
                            fwd_ctl.request_keyframe();
                        }
                    }
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                    Err(_) => {
                        // The pipeline let this viewer go (capture failed): end
                        // the session and say so, not stream nothing at 0 fps.
                        fwd_ended
                            .lock()
                            .unwrap_or_else(|e| e.into_inner())
                            .get_or_insert(End::PipelineGone);
                        fwd_stop.store(true, Ordering::Relaxed);
                        break;
                    }
                }
            }
        });
    let (s2, f2, e2, ctl2) = (stop.clone(), fps.clone(), ended.clone(), control.clone());
    let session = std::thread::Builder::new()
        .name("rtc".into())
        .spawn(move || {
            let end = rtc::run(prepared, rx, tx, ctl2, s2.clone(), |n| {
                f2.store(n, Ordering::Relaxed)
            });
            // The first reason stands (the forwarder may have set why already).
            e2.lock()
                .unwrap_or_else(|e| e.into_inner())
                .get_or_insert(end);
            s2.store(true, Ordering::Relaxed);
        });

    // The control channel, until either side ends it.
    let mut last_stats = Instant::now();
    let mut last_displays = Instant::now();
    // The display this viewer was last told it watches (MAIN resolved to an id).
    let current = |list: &[Display], d: u32| {
        if d == MAIN {
            display::main_id(list)
        } else {
            Some(d)
        }
    };
    let mut told = current(&shown, MAIN);
    // What the viewer last asked to watch: if it gets something else, it was moved.
    let mut asked: Option<u32> = None;
    while !stop.load(Ordering::Relaxed)
        && !c.stop.load(Ordering::Relaxed)
        && c.open.load(Ordering::Relaxed)
        && c.gate.still_open(tag)
    {
        match read(&mut ws, Instant::now() + Duration::from_millis(1000)) {
            Ok(Got::Msg(Some(FromViewer::Key))) => control.browser_wants_keyframe(),
            Ok(Got::Msg(Some(FromViewer::Display { id }))) => {
                asked = Some(id);
                // Only a display this computer has; the main one by its id
                // follows the main display (MAIN), so it is never a dead end.
                if display::main_id(&shown) == Some(id) {
                    control.set_display(MAIN);
                } else if shown.iter().any(|d| d.id == id) {
                    control.set_display(id);
                }
            }
            Ok(Got::Msg(Some(FromViewer::Bye))) | Err(_) => break,
            Ok(_) => {}
        }
        if last_displays.elapsed() >= DISPLAYS_EVERY {
            last_displays = Instant::now();
            let now = (c.displays)();
            if now != shown {
                // A display that went away hands this viewer the main one.
                if control.display() != MAIN && !now.iter().any(|d| d.id == control.display()) {
                    control.set_display(MAIN);
                }
                shown = now;
                let id = current(&shown, control.display());
                if send(
                    &mut ws,
                    serde_json::json!({"t":"displays","displays":shown,"display":id}),
                )
                .is_err()
                {
                    break;
                }
                told = id;
            }
        }
        // Say which display this viewer watches whenever it changes: its own
        // choice, or the main one after its display could not be captured.
        let id = current(&shown, control.display());
        if id != told {
            let moved = asked.is_some_and(|a| Some(a) != id);
            if moved {
                asked = None;
            }
            if send(
                &mut ws,
                serde_json::json!({"t":"display","id":id,"fallback":moved}),
            )
            .is_err()
            {
                break;
            }
            told = id;
        }
        // The Companion's sound switches changed, or a device came or went: say so.
        if let Some(h) = &c.audio {
            let now = h.state();
            if told_audio.as_ref() != Some(&now) {
                if send(&mut ws, audio_json(&now)).is_err() {
                    break;
                }
                told_audio = Some(now);
            }
        }
        if last_stats.elapsed() >= Duration::from_secs(1) {
            last_stats = Instant::now();
            if send(
                &mut ws,
                serde_json::json!({"t":"stats","fps":fps.load(Ordering::Relaxed)}),
            )
            .is_err()
            {
                break;
            }
        }
    }
    stop.store(true, Ordering::Relaxed);
    c.pipeline.unsubscribe(id);
    if let Some((h, aid, t)) = audio_sub {
        h.unsubscribe(aid);
        if let Some(t) = t {
            let _ = t.join();
        }
    }
    if let Ok(t) = session {
        let _ = t.join();
    }
    if let Ok(t) = forwarder {
        let _ = t.join();
    }
    // "closed": the board was unplugged from this computer - the viewer can say
    // exactly that at once, instead of "reconnecting" and finding out later.
    let end = ended
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .take()
        .unwrap_or(End::Stopped);
    let reason = if !c.open.load(Ordering::Relaxed) || !c.gate.still_open(tag) {
        "closed".to_string()
    } else if end == End::PipelineGone {
        // Capture failed: "permission" lets the dashboard say exactly what to
        // switch on, instead of reconnecting forever.
        match c.pipeline.last_error() {
            Some(e) if e.contains("Screen Recording") => "permission".to_string(),
            Some(_) => "capture".to_string(),
            None => format!("{end:?}"),
        }
    } else {
        format!("{end:?}")
    };
    let _ = send(&mut ws, serde_json::json!({"t":"end","reason":reason}));
    let _ = ws.close(None);
    Ok(())
}

#[cfg(test)]
mod gate_tests {
    use super::*;
    use hidfi_core::pairing::{ticket, PairKey};

    #[test]
    fn viewers_are_counted_by_the_board_that_let_them_in() {
        let w = Watchers::default();
        let a1 = w.claim(0xA, 3).unwrap();
        let a2 = w.claim(0xA, 3).unwrap();
        let b1 = w.claim(0xB, 3).unwrap();
        assert_eq!((w.count(), w.of_board(0xA), w.of_board(0xB)), (3, 2, 1));
        assert!(
            w.claim(0xB, 3).is_none(),
            "every slot taken: refused, nothing counted"
        );
        assert_eq!((w.count(), w.of_board(0xB)), (3, 1));
        drop(a1);
        drop(b1);
        assert_eq!((w.count(), w.of_board(0xA), w.of_board(0xB)), (1, 1, 0));
        drop(a2);
        assert_eq!((w.count(), w.of_board(0xA)), (0, 0));
        assert!(
            w.by_board.lock().unwrap().is_empty(),
            "no entry left behind"
        );
    }

    #[test]
    fn a_ticket_from_any_board_plugged_in_gets_in_and_no_other() {
        // Two boards plugged into this computer, paired with both: a viewer
        // from either board's dashboard is welcome; a board it is not paired
        // with is not; a paired board that left this computer is "closed".
        let (a, b, stranger) = (
            PairKey::from_bytes([1; 32]),
            PairKey::from_bytes([2; 32]),
            PairKey::from_bytes([3; 32]),
        );
        let nonces = TicketNonces::default();
        nonces.announce("tn-a".into(), Instant::now());
        nonces.announce("tn-b".into(), Instant::now());
        let gk = |tag, key: &PairKey, here| GateKey {
            tag,
            key: key.clone(),
            here,
        };
        let keys = Arc::new(RwLock::new(vec![gk(1, &a, true), gk(2, &b, true)]));
        let gate = TicketGate::new(keys.clone(), nonces);
        assert_eq!(
            gate.admit("tn-a", "v1", &ticket(&a, "tn-a", "v1")),
            Admit::Yes(1)
        );
        assert_eq!(
            gate.admit("tn-b", "v2", &ticket(&b, "tn-b", "v2")),
            Admit::Yes(2)
        );
        assert_eq!(
            gate.admit("tn-a", "v3", &ticket(&stranger, "tn-a", "v3")),
            Admit::No
        );
        assert_eq!(
            gate.admit("tn-a", "v1", &ticket(&a, "tn-a", "v1")),
            Admit::No,
            "each ticket once"
        );
        // Board 2 leaves this computer's USB.
        keys.write().unwrap()[1].here = false;
        assert!(
            gate.still_open(1) && !gate.still_open(2),
            "its viewers end; the other's go on"
        );
        assert_eq!(
            gate.admit("tn-b", "v5", &ticket(&b, "tn-b", "v5")),
            Admit::Closed
        );
        let none = TicketGate::new(Arc::new(RwLock::new(vec![])), TicketNonces::default());
        assert_eq!(
            none.admit("tn-a", "v4", &ticket(&a, "tn-a", "v4")),
            Admit::No,
            "unpaired: nobody"
        );
    }
}
