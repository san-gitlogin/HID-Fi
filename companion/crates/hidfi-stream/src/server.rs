//! Where viewers knock: a WebSocket that takes a board-signed ticket, then an
//! SDP offer, answers it, and keeps the session's control channel.
//!
//! Wire format (JSON text frames):
//!   viewer → {"t":"hello","tn","vn","ticket"}      within 5 s of connecting
//!   app    → {"t":"ready","w","h"} | {"t":"error","reason"}
//!   viewer → {"t":"offer","sdp"}                    within 10 s
//!   app    → {"t":"answer","sdp"}
//!   app    → {"t":"stats","fps"}                    every second while live
//!   viewer → {"t":"key"}                            ask for a keyframe
//!   viewer → {"t":"bye"}
//!   app    → {"t":"end","reason"}                   then closes
//!
//! Defences (A10): private-network peers only; at most 2 viewers and 6
//! connections; 5 s to present a ticket; 64 KB messages; an address that
//! presents 5 bad tickets in 10 minutes is refused for 10 minutes. Nothing but
//! a JSON parse and one HMAC happens before a ticket is accepted.

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
pub const MAX_VIEWERS: usize = 4;
pub const MAX_CONNS: usize = 8;
pub const HELLO_DEADLINE: Duration = Duration::from_secs(5);
pub const OFFER_DEADLINE: Duration = Duration::from_secs(10);
pub const MAX_MESSAGE: usize = 64 * 1024;
const BAD_LIMIT: usize = 5;
const BAD_WINDOW: Duration = Duration::from_secs(600);
/// The ports tried in order; the first free one is used and told to the board.
pub const PORTS: std::ops::RangeInclusive<u16> = 47800..=47819;

/// Decides whether a ticket lets a viewer in.
pub trait Gate: Send + Sync {
    fn admit(&self, tn: &str, vn: &str, ticket: &[u8]) -> bool;
}

/// The real gate: the pairing key, the nonces this app announced, and every
/// `vn` already used (a ticket works once).
pub struct TicketGate {
    pub key: Arc<RwLock<Option<PairKey>>>,
    pub nonces: TicketNonces,
    used: Mutex<VecDeque<(String, Instant)>>,
}

impl TicketGate {
    pub fn new(key: Arc<RwLock<Option<PairKey>>>, nonces: TicketNonces) -> Self {
        TicketGate {
            key,
            nonces,
            used: Mutex::default(),
        }
    }
}

impl Gate for TicketGate {
    fn admit(&self, tn: &str, vn: &str, ticket: &[u8]) -> bool {
        let now = Instant::now();
        let key = self.key.read().unwrap_or_else(|e| e.into_inner());
        let Some(key) = key.as_ref() else {
            return false;
        };
        if !self.nonces.is_live(tn, now) || !pairing::ticket_ok(key, tn, vn, ticket) {
            return false;
        }
        let mut used = self.used.lock().unwrap_or_else(|e| e.into_inner());
        used.retain(|(_, at)| now.duration_since(*at) < Duration::from_secs(300));
        if used.iter().any(|(v, _)| v == vn) {
            return false;
        }
        used.push_back((vn.to_string(), now));
        true
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
    Key,
    Bye,
}

/// What the server tells whoever runs it (the tray): who is watching.
#[derive(Clone, Default)]
pub struct Watchers(Arc<AtomicUsize>);
impl Watchers {
    pub fn count(&self) -> usize {
        self.0.load(Ordering::Relaxed)
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
    cert: DtlsCert,
    conns: AtomicUsize,
    viewers: Watchers,
    bad: Mutex<HashMap<IpAddr, VecDeque<Instant>>>,
    size: Box<dyn Fn() -> (u32, u32) + Send + Sync>,
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

/// Binds the first free port in [`PORTS`] (or exactly `port` if given).
pub fn start(
    port: Option<u16>,
    gate: Arc<dyn Gate>,
    pipeline: Arc<Pipeline>,
    cert: DtlsCert,
    size: Box<dyn Fn() -> (u32, u32) + Send + Sync>,
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
        cert,
        conns: AtomicUsize::new(0),
        viewers: watchers.clone(),
        bad: Mutex::default(),
        size,
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
    if !c.gate.admit(&tn, &vn, &ticket) {
        c.strike(peer.ip());
        let _ = send(&mut ws, serde_json::json!({"t":"error","reason":"ticket"}));
        let _ = ws.close(None);
        return Err("bad ticket".into());
    }
    // Claim a viewer slot; give it back on every way out.
    if c.viewers.0.fetch_add(1, Ordering::SeqCst) >= MAX_VIEWERS {
        c.viewers.0.fetch_sub(1, Ordering::SeqCst);
        let _ = send(&mut ws, serde_json::json!({"t":"error","reason":"busy"}));
        return Err("busy".into());
    }
    struct Slot<'a>(&'a Watchers);
    impl Drop for Slot<'_> {
        fn drop(&mut self) {
            self.0 .0.fetch_sub(1, Ordering::SeqCst);
        }
    }
    let _slot = Slot(&c.viewers);

    let (w, h) = (c.size)();
    send(&mut ws, serde_json::json!({"t":"ready","w":w,"h":h})).map_err(|e| e.to_string())?;
    let Some(FromViewer::Offer { sdp }) = expect(&mut ws, OFFER_DEADLINE) else {
        return Err("no offer".into());
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
    // Frames hop from the pipeline's channel into the session's.
    let fwd_tx = tx.clone();
    let fwd_stop = stop.clone();
    let forwarder = std::thread::Builder::new()
        .name("rtc-fwd".into())
        .spawn(move || {
            while !fwd_stop.load(Ordering::Relaxed) {
                match vrx.recv_timeout(Duration::from_millis(500)) {
                    Ok(m) => {
                        if fwd_tx.try_send(m.into()).is_err() {
                            // The session is behind: drop, the pipeline will key.
                        }
                    }
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                    Err(_) => break,
                }
            }
        });
    let id = c.pipeline.subscribe(vtx);
    let control = c.pipeline.control(id);
    let (s2, f2, e2, ctl2) = (stop.clone(), fps.clone(), ended.clone(), control.clone());
    let session = std::thread::Builder::new()
        .name("rtc".into())
        .spawn(move || {
            let end = rtc::run(prepared, rx, tx, ctl2, s2.clone(), |n| {
                f2.store(n, Ordering::Relaxed)
            });
            *e2.lock().unwrap_or_else(|e| e.into_inner()) = Some(end);
            s2.store(true, Ordering::Relaxed);
        });

    // The control channel, until either side ends it.
    let mut last_stats = Instant::now();
    while !stop.load(Ordering::Relaxed)
        && !c.stop.load(Ordering::Relaxed)
        && c.open.load(Ordering::Relaxed)
    {
        match read(&mut ws, Instant::now() + Duration::from_millis(1000)) {
            Ok(Got::Msg(Some(FromViewer::Key))) => control.request_keyframe(),
            Ok(Got::Msg(Some(FromViewer::Bye))) | Err(_) => break,
            Ok(_) => {}
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
    if let Ok(t) = session {
        let _ = t.join();
    }
    if let Ok(t) = forwarder {
        let _ = t.join();
    }
    let reason = format!(
        "{:?}",
        ended
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .take()
            .unwrap_or(End::Stopped)
    );
    let _ = send(&mut ws, serde_json::json!({"t":"end","reason":reason}));
    let _ = ws.close(None);
    Ok(())
}
