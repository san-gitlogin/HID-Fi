//! Capture → encode → every viewer, on one thread that exists only while
//! someone is watching.
//!
//! With no viewer the thread is parked on a condition variable: no capture, no
//! encoding, no wakeups - the app costs nothing in the tray (A3). A new viewer
//! wakes it and gets a keyframe first.
//!
//! Lanes. Each viewer watches one display, at one of two qualities, and each
//! (display, quality) someone watches is a lane with its own encoder; each
//! display someone watches has one capture. Nothing runs for a display or a
//! quality nobody watches.
//!
//! - Displays: a computer with three monitors shows any of them; two viewers
//!   may watch two different ones. Switching is a keyframe away.
//! - Two qualities, like a video call: full size for viewers whose link carries
//!   it, half size for viewers on a poor link (a phone far from the router).
//!   One encoder for everyone made every screen as slow as the worst link -
//!   seen live, three phones at 1 fps. Each viewer moves between the two by its
//!   own bandwidth estimate, with a gap between the thresholds so it does not
//!   flap. A new viewer starts at half size and moves up once its link shows it
//!   can (about 3 s on a good one): starting at full size flooded a poor link,
//!   and its estimate took many seconds to recover.
//!
//! A viewer that falls behind loses frames (never queues them: queued video is
//! lag that never recovers) and then waits for the next keyframe, skipping the
//! frames in between, which it could not decode anyway. Only that viewer: the
//! others keep every frame. Keyframes asked for after the start are at most one
//! per [`KEY_GAP`] per lane - a keyframe is many times the size of a frame, and
//! a stream of them starves everyone on it.

use crate::convert::halve_bgra;
use crate::frame::{Encoded, Encoder, Frame};
use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU8, Ordering};
use std::sync::mpsc::{SyncSender, TrySendError};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

/// What a screen grabber gives the pipeline.
pub trait Source: Send {
    fn size(&self) -> (u32, u32);
    /// Calls `f` with the current picture if it changed since the last call,
    /// or if `force` is set, and returns whether `f` was called. An unchanged
    /// screen costs no encode.
    fn grab(&mut self, force: bool, f: &mut dyn FnMut(&Frame)) -> Result<bool, String>;
}

/// Something a viewer's sender thread receives.
pub enum ToViewer {
    Frame(Arc<Encoded>, u64),
}

/// "The main display", whichever it is: what a viewer watches until it picks one.
pub const MAIN: u32 = u32::MAX;
/// The three sizes, as simulcast layers: full, half (a quarter of the pixels)
/// and quarter (a sixteenth). A viewer on a thin link gets its own small
/// stream instead of setting the pace for everyone at half size - seen: one
/// viewer on a swinging link held two good ones to 2-5 fps.
pub const FULL: usize = 0;
pub const HALF: usize = 1;
pub const QUARTER: usize = 2;
const TIERS: usize = 3;
/// Below this a viewer moves to quarter size; above `QUARTER_UP`, back to half.
pub const QUARTER_BELOW: u32 = 700_000;
pub const QUARTER_UP: u32 = 1_200_000;
/// A viewer whose link estimate falls below this moves to half size...
pub const DOWN_BPS: u32 = 2_500_000;
/// ...and back to full size once it rises above this.
pub const UP_BPS: u32 = 4_500_000;
pub const MIN_BITRATE: u32 = 300_000;
/// Full size needs at least this, whatever the content.
pub const FULL_MIN_BPS: u32 = 1_000_000;
/// Full size while it can keep at least this many frames a second.
pub const FULL_FPS_OK: u32 = 20;
/// Half size needs at least this; quarter size takes what is left.
pub const HALF_MIN_BPS: u32 = 400_000;

/// The largest size whose cost fits `bps` with `margin` to spare: `need` is
/// full size's cost for this content (at FULL_FPS_OK); half size costs about a
/// third of it (a quarter of the pixels; H.264 grows slower than pixels),
/// quarter size a ninth. Light content fits full size on a thin link; busy
/// content gets a smaller picture before a slideshow.
fn best_tier(bps: u32, need: u32, margin: f64) -> usize {
    let floor = [FULL_MIN_BPS, HALF_MIN_BPS, 0];
    let share = [1.0, 3.0, 9.0];
    let bps = bps as f64;
    (0..TIERS)
        .find(|&t| bps >= floor[t] as f64 * margin && bps >= need as f64 / share[t] * margin)
        .unwrap_or(QUARTER)
}
pub const MAX_BITRATE: u32 = 12_000_000;
/// Keyframes asked for while streaming: at most one per this, per lane.
pub const KEY_GAP: Duration = Duration::from_millis(1000);
/// How long the link estimate must stay across a quality threshold before the
/// viewer changes quality (each change costs a keyframe): up only once a link
/// has proved itself, down sooner - a viewer that cannot keep up also lowers
/// the rate of everyone sharing its stream.
pub const TIER_HOLD: Duration = Duration::from_secs(3);
pub const TIER_HOLD_DOWN: Duration = Duration::from_secs(1);
/// How often the pipeline writes a line to the log while someone watches.
const STATS_EVERY: Duration = Duration::from_secs(10);

/// One viewer's state, shared between its session and the encoding loop.
#[derive(Default)]
struct Shared1 {
    /// Lost a frame (or changed lane): nothing until the next keyframe.
    need_key: AtomicBool,
    /// Its browser asked for a keyframe (it lost packets): one within KEY_GAP,
    /// and frames keep coming meanwhile - the browser drops what it cannot
    /// decode, and going quiet would starve the bandwidth estimate.
    want_key: AtomicBool,
    /// Just joined (or changed lane): a keyframe now, not within KEY_GAP.
    join: AtomicBool,
    tier: AtomicU8,
    /// Its latest bandwidth estimate, bit/s; 0 = none yet.
    est: AtomicU32,
    display: AtomicU32,
    /// Since when the estimate has been across the threshold to the other quality.
    crossed: Mutex<Option<Instant>>,
    /// Changed quality before: the next change waits TIER_HOLD. The first does
    /// not - the starting quality was a guess, not a measurement.
    switched: AtomicBool,
    /// Packets are being lost: a step down now, not after TIER_HOLD_DOWN.
    urgent: AtomicBool,
    /// What full size costs for what its display shows now, bit/s; 0 = not known
    /// yet. Set by the encoding loop (see `full_cost`).
    full_need: AtomicU32,
}

struct Viewer {
    id: u64,
    tx: SyncSender<ToViewer>,
    s: Arc<Shared1>,
}

#[derive(Default)]
struct Shared {
    viewers: Vec<Viewer>,
    next_id: u64,
    stop: bool,
    /// Why the main display could not be captured last time, until it can.
    last_error: Option<String>,
}

pub struct Pipeline {
    shared: Arc<(Mutex<Shared>, Condvar)>,
    thread: Option<std::thread::JoinHandle<()>>,
}

/// One viewer's handle: its display, its estimate, its keyframes.
#[derive(Clone)]
pub struct Control {
    s: Arc<Shared1>,
}

impl Control {
    /// This viewer's picture is broken (a lost frame, or the browser asked):
    /// it gets no more frames until a keyframe, which comes within [`KEY_GAP`].
    pub fn request_keyframe(&self) {
        self.s.need_key.store(true, Ordering::Relaxed);
    }
    /// The browser asked for a keyframe (a PLI or FIR: it lost packets and
    /// knows it). A keyframe comes within [`KEY_GAP`]; unlike
    /// [`Control::request_keyframe`], frames keep flowing until then. Seen: with
    /// the stream held back after each loss, the link estimate fell to a tenth
    /// of the link and took 35 s to climb back.
    pub fn browser_wants_keyframe(&self) {
        self.s.want_key.store(true, Ordering::Relaxed);
    }
    /// The viewer just connected: a keyframe now, not within [`KEY_GAP`].
    pub fn joined(&self) {
        self.restart();
    }
    fn restart(&self) {
        self.s.need_key.store(true, Ordering::Relaxed);
        self.s.join.store(true, Ordering::Relaxed);
    }
    /// Whether this viewer is waiting for a keyframe.
    pub fn needs_key(&self) -> bool {
        self.s.need_key.load(Ordering::Relaxed)
    }
    /// The quality this viewer gets: [`FULL`] or [`HALF`].
    pub fn tier(&self) -> usize {
        (self.s.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1)
    }
    /// The display this viewer watches ([`MAIN`] until it picks one).
    pub fn display(&self) -> u32 {
        self.s.display.load(Ordering::Relaxed)
    }
    /// Watch another display, from its next keyframe.
    pub fn set_display(&self, id: u32) {
        if self.s.display.swap(id, Ordering::Relaxed) != id {
            self.restart();
        }
    }
    /// The link is losing packets (a real drop, not WiFi jitter): as
    /// [`Control::offer_bitrate`], and a smaller picture at once if the rate
    /// calls for one - a full-size keyframe may not even fit the link any more.
    pub fn link_lost(&self, bps: u32) {
        self.s.urgent.store(true, Ordering::Relaxed);
        self.offer_bitrate(bps);
        self.s.urgent.store(false, Ordering::Relaxed);
    }
    /// This viewer's congestion-control estimate, in bits per second. It also
    /// decides the viewer's quality.
    pub fn offer_bitrate(&self, bps: u32) {
        self.s.est.store(bps, Ordering::Relaxed);
        let now = self.tier();
        // By the link, and by what full size costs for this content: busy
        // content at full size on a link that cannot carry it plays at a few
        // frames a second; at half size, smoothly. Smooth wins.
        // `full_need` is the cost at the full capture rate; full size is worth
        // it from FULL_FPS_OK on (sharp text at 20 fps beats a soft picture at
        // 30), so it needs only that share of it.
        let need =
            (self.s.full_need.load(Ordering::Relaxed) as u64 * FULL_FPS_OK as u64 / 30) as u32;
        let want = if need == 0 {
            // What this content costs is not known yet: by the link alone.
            match now {
                FULL if bps < QUARTER_BELOW => QUARTER,
                FULL if bps < DOWN_BPS => HALF,
                HALF if bps > UP_BPS => FULL,
                HALF if bps < QUARTER_BELOW => QUARTER,
                QUARTER if bps > UP_BPS => FULL,
                QUARTER if bps > QUARTER_UP => HALF,
                t => t,
            }
        } else {
            // Known: the largest size that fits, strictly to go up, loosely to
            // stay (hysteresis).
            let up = best_tier(bps, need, 1.2);
            let stay = best_tier(bps, need, 0.8);
            if up < now {
                up
            } else if stay > now {
                stay
            } else {
                now
            }
        };
        // Only once the estimate has stayed across for TIER_HOLD: a noisy
        // estimate (seen swinging 0.25 - 13 Mbit/s on one WiFi) would otherwise
        // switch quality, and send a keyframe, every few seconds.
        let next = {
            let mut c = self.s.crossed.lock().unwrap_or_else(|e| e.into_inner());
            if want == now {
                *c = None;
                now
            } else if !self.s.switched.swap(true, Ordering::Relaxed)
                || (want > now && self.s.urgent.swap(false, Ordering::Relaxed))
                || c.get_or_insert_with(Instant::now).elapsed()
                    >= if want > now {
                        TIER_HOLD_DOWN
                    } else {
                        TIER_HOLD
                    }
            {
                *c = None;
                want
            } else {
                now
            }
        };
        if next != now {
            // The other stream's frames only make sense from its keyframe.
            self.s.tier.store(next as u8, Ordering::Relaxed);
            self.restart();
        }
    }
}

/// The most frames per second a lane runs at, for its bitrate: only a truly
/// thin link is held to fewer frames up front. Above that the encoder decides
/// (see [`FpsCap`]): light motion fits 30 fps in 1 Mbit/s, busy content does
/// not, and a fixed rule halved the light case for nothing.
pub fn lane_fps(bps: u32, fps: u32) -> u32 {
    let cap = match bps {
        b if b >= 600_000 => 60,
        b if b >= 400_000 => 15,
        _ => 8,
    };
    fps.min(cap)
}

fn note_cap(d: u32, t: usize, changed: Option<(u32, f32)>) {
    if let Some((cap, ratio)) = changed {
        log::debug!(
            "{} {}: frame rate cap {cap} ({:.0}% of frames dropped by the encoder)",
            display_name(d),
            ["full", "half", "quarter"][t.min(TIERS - 1)],
            ratio * 100.0
        );
    }
}

/// A lane's frame rate, from what its encoder manages. An encoder over its
/// bitrate drops frames itself, at random - jerky motion. When it drops more
/// than one in five, the lane asks for a third fewer (evenly spaced, each with
/// more bits); when it drops almost none, half again more, up to the capture
/// rate. Content that moves less gets its frames back within seconds.
#[derive(Default)]
struct FpsCap {
    cap: Option<u32>,
    /// The lane's bitrate when the cap was lowered: once it has risen by half,
    /// the drops that lowered it say nothing any more (seen at start-up: the
    /// rate climbed within a second, the cap took four to follow).
    at_bps: u32,
    tried: u32,
    dropped: u32,
    since: Option<Instant>,
    /// When the lane started: no judging in its first FPS_GRACE, while the
    /// link estimate is still climbing (seen: 12 fps for four new viewers).
    born: Option<Instant>,
}

const FPS_GRACE: Duration = Duration::from_secs(3);

impl FpsCap {
    fn get(&self, fps: u32) -> u32 {
        self.cap.unwrap_or(fps).min(fps)
    }
    /// The lane's bitrate now: a much higher one lifts the cap.
    fn rate(&mut self, bps: u32) {
        if self.cap.is_some() && bps > self.at_bps.saturating_mul(3) / 2 {
            self.cap = None;
        }
    }
    /// Returns the new cap and the drop share when it changed (for the log).
    fn record(&mut self, dropped: bool, fps: u32, bps: u32, now: Instant) -> Option<(u32, f32)> {
        if now.duration_since(*self.born.get_or_insert(now)) < FPS_GRACE {
            return None;
        }
        self.tried += 1;
        self.dropped += dropped as u32;
        let since = *self.since.get_or_insert(now);
        if now.duration_since(since) < Duration::from_secs(1) || self.tried < 5 {
            return None;
        }
        let cap = self.get(fps);
        let ratio = self.dropped as f32 / self.tried as f32;
        if ratio > 0.2 {
            self.at_bps = bps;
        }
        self.cap = Some(if ratio > 0.2 {
            (cap * 2 / 3).max(8)
        } else if ratio < 0.05 {
            (cap * 3 / 2 + 1).min(fps)
        } else {
            cap
        });
        (self.tried, self.dropped, self.since) = (0, 0, Some(now));
        (self.cap != Some(cap)).then(|| (self.get(fps), ratio))
    }
}

/// Each lane's bitrate: the slowest estimate among its viewers (a viewer
/// without one yet counts as `initial`).
fn lane_rates(viewers: &[(u32, usize, u32)], initial: u32) -> BTreeMap<(u32, usize), u32> {
    let mut m = BTreeMap::new();
    for &(d, t, est) in viewers {
        let e = if est == 0 { initial } else { est };
        m.entry((d, t))
            .and_modify(|b: &mut u32| *b = (*b).min(e))
            .or_insert(e);
    }
    m
}

impl Pipeline {
    /// `source(display)` and the encoders are built on the pipeline thread when
    /// someone watches that display and dropped when nobody does, so an idle
    /// app holds no capture session and no encoder. `None` is the main display.
    /// An encoder is rebuilt whenever its picture changes size (resolution
    /// change, rotation), since an H.264 stream has one size.
    pub fn start<S, E>(fps: u32, initial_bps: u32, source: S, encoder: E) -> Pipeline
    where
        S: Fn(Option<u32>) -> Result<Box<dyn Source>, String> + Send + 'static,
        E: Fn(u32, u32, u32, u32) -> Result<Box<dyn Encoder>, String> + Send + 'static,
    {
        let shared: Arc<(Mutex<Shared>, Condvar)> = Arc::default();
        let s = shared.clone();
        let thread = std::thread::Builder::new()
            .name("pipeline".into())
            .spawn(move || run(s, fps.clamp(1, 60), initial_bps, source, encoder))
            .expect("spawning the pipeline thread");
        Pipeline {
            shared,
            thread: Some(thread),
        }
    }

    /// The handle for viewer `id` (from [`Pipeline::subscribe`]).
    pub fn control(&self, id: u64) -> Control {
        let s = self
            .shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .iter()
            .find(|v| v.id == id)
            .map(|v| v.s.clone())
            .unwrap_or_default();
        Control { s }
    }

    /// Add a viewer, on the main display, at half size until its link shows it
    /// can take full size. Frames arrive on `tx`; drop the receiver to leave.
    pub fn subscribe(&self, tx: SyncSender<ToViewer>) -> u64 {
        let (m, cv) = &*self.shared;
        let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
        s.next_id += 1;
        let id = s.next_id;
        let v = Arc::new(Shared1 {
            need_key: AtomicBool::new(true),
            want_key: AtomicBool::new(false),
            join: AtomicBool::new(true),
            tier: AtomicU8::new(HALF as u8),
            est: AtomicU32::new(0),
            display: AtomicU32::new(MAIN),
            crossed: Mutex::new(None),
            switched: AtomicBool::new(false),
            urgent: AtomicBool::new(false),
            full_need: AtomicU32::new(0),
        });
        s.viewers.push(Viewer { id, tx, s: v });
        cv.notify_all();
        id
    }

    pub fn unsubscribe(&self, id: u64) {
        let (m, _) = &*self.shared;
        m.lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .retain(|v| v.id != id);
    }

    /// Why capture last failed (viewers were let go for it), if it has since.
    pub fn last_error(&self) -> Option<String> {
        self.shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .last_error
            .clone()
    }

    pub fn viewers(&self) -> usize {
        self.shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .len()
    }
}

impl Drop for Pipeline {
    fn drop(&mut self) {
        {
            let (m, cv) = &*self.shared;
            m.lock().unwrap_or_else(|e| e.into_inner()).stop = true;
            cv.notify_all();
        }
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

/// One lane's encoder and what it has done since the last log line.
#[derive(Default)]
struct Lane {
    enc: Option<Box<dyn Encoder>>,
    applied_bps: u32,
    last_key: Option<Instant>,
    last_sent: Option<Instant>,
    /// When this lane is next due a frame (it may run below the capture rate).
    due: Option<Instant>,
    encoded: u32,
    keys: u32,
    bytes: u64,
    skipped: u32,
    fps_cap: FpsCap,
    /// Bytes out since `win_at`, for `out_bps` (the lane's real output rate).
    win_bytes: u64,
    win_at: Option<Instant>,
    out_bps: u32,
}

/// What full size costs for what a display shows, from its lanes' real output:
/// the full-size lane's rate scaled up to the capture rate if its encoder is
/// dropping frames, or else the half-size lane's at three times (four times the
/// pixels, but H.264 grows slower than that). Remembered, fading by a tenth a
/// second, so a desktop that moves, rests and moves again does not flip size
/// at every pause.
fn full_cost(lanes: [Option<(u32, u32)>; TIERS], fps: u32, before: u32, secs: f64) -> u32 {
    let scaled = |(out, rate): (u32, u32)| out as f64 * fps as f64 / rate.max(1) as f64;
    let now = match lanes {
        [Some(f), _, _] if f.0 > 0 => scaled(f),
        [_, Some(h), _] if h.0 > 0 => scaled(h) * 3.0,
        [_, _, Some(q)] if q.0 > 0 => scaled(q) * 9.0,
        _ => 0.0,
    };
    let faded = before as f64 * 0.9f64.powf(secs);
    now.max(faded) as u32
}

/// One display's capture.
struct Capture {
    src: Box<dyn Source>,
    half_px: Vec<u8>,
    quarter_px: Vec<u8>,
    grabbed: u32,
    failures: u32,
}

/// What the loop needs to know about one viewer this turn.
struct Seen {
    display: u32,
    tier: usize,
    est: u32,
    need_key: bool,
    join: bool,
}

fn display_name(d: u32) -> String {
    if d == MAIN {
        "main display".into()
    } else {
        format!("display {d}")
    }
}

fn run<S, E>(
    shared: Arc<(Mutex<Shared>, Condvar)>,
    fps: u32,
    initial_bps: u32,
    make_source: S,
    make_encoder: E,
) where
    S: Fn(Option<u32>) -> Result<Box<dyn Source>, String>,
    E: Fn(u32, u32, u32, u32) -> Result<Box<dyn Encoder>, String>,
{
    let period = Duration::from_micros(1_000_000 / fps as u64);
    let t0 = Instant::now();
    loop {
        // Park until someone watches.
        {
            let (m, cv) = &*shared;
            let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
            while s.viewers.is_empty() && !s.stop {
                s = cv.wait(s).unwrap_or_else(|e| e.into_inner());
            }
            if s.stop {
                return;
            }
        }
        let mut captures: HashMap<u32, Capture> = HashMap::new();
        let mut lanes: HashMap<(u32, usize), Lane> = HashMap::new();
        let mut next = Instant::now();
        let mut tally_at = Instant::now();
        let mut need_at = Instant::now();
        let mut need: HashMap<u32, u32> = HashMap::new();
        let mut longest = Duration::ZERO;
        loop {
            let turn = Instant::now();
            // Who watches what, this turn.
            let seen: Vec<Seen> = {
                let s = shared.0.lock().unwrap_or_else(|e| e.into_inner());
                s.viewers
                    .iter()
                    .map(|v| Seen {
                        display: v.s.display.load(Ordering::Relaxed),
                        tier: (v.s.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1),
                        est: v.s.est.load(Ordering::Relaxed),
                        need_key: v.s.need_key.load(Ordering::Relaxed)
                            || v.s.want_key.load(Ordering::Relaxed),
                        join: v.s.join.load(Ordering::Relaxed),
                    })
                    .collect()
            };
            if seen.is_empty() {
                break; // last viewer left: release captures and encoders
            }
            let rates = lane_rates(
                &seen
                    .iter()
                    .map(|v| (v.display, v.tier, v.est))
                    .collect::<Vec<_>>(),
                initial_bps,
            );
            // Nothing for a lane or a display nobody watches now.
            lanes.retain(|k, _| rates.contains_key(k));
            captures.retain(|d, _| rates.keys().any(|(x, _)| x == d));

            let now = Instant::now();
            let pts = t0.elapsed().as_millis() as u64;
            let mut produced: Vec<((u32, usize), Encoded)> = Vec::new();
            let displays: Vec<u32> = {
                let mut d: Vec<u32> = rates.keys().map(|(d, _)| *d).collect();
                d.dedup();
                d
            };
            for d in displays {
                if let std::collections::hash_map::Entry::Vacant(e) = captures.entry(d) {
                    match make_source(if d == MAIN { None } else { Some(d) }) {
                        Ok(src) => {
                            if d == MAIN {
                                shared
                                    .0
                                    .lock()
                                    .unwrap_or_else(|e| e.into_inner())
                                    .last_error = None;
                            }
                            e.insert(Capture {
                                src,
                                half_px: Vec::new(),
                                quarter_px: Vec::new(),
                                grabbed: 0,
                                failures: 0,
                            });
                        }
                        Err(err) => {
                            log::error!("cannot capture the {}: {err}", display_name(d));
                            move_off(&shared, d, &err);
                            continue;
                        }
                    }
                }
                let cap = captures.get_mut(&d).expect("just made");
                // Which of this display's lanes want a frame, and which a keyframe.
                let mut want = [false; TIERS];
                let mut force = [false; TIERS];
                let mut refresh = false;
                for t in 0..TIERS {
                    let Some(&bps) = rates.get(&(d, t)) else {
                        continue;
                    };
                    let lane = lanes.entry((d, t)).or_default();
                    let on = |v: &&Seen| v.display == d && v.tier == t;
                    let joined = seen.iter().filter(on).any(|v| v.join);
                    let asked = seen.iter().filter(on).any(|v| v.need_key)
                        && lane
                            .last_key
                            .is_none_or(|k| now.duration_since(k) >= KEY_GAP);
                    // A still screen sends nothing, but a frame every 2 s keeps
                    // the stream alive. Not a keyframe: joins and losses have
                    // their own, and on a thin link (where the encoder skips
                    // frames to keep its rate) "nothing for 2 s" happened all the
                    // time - 5 of every 6 frames were keyframes, 0.6 fps.
                    let stale = lane
                        .last_sent
                        .is_none_or(|s| now.duration_since(s) > Duration::from_secs(2));
                    force[t] = joined || asked || lane.enc.is_none();
                    let due = lane.due.is_none_or(|due| now >= due);
                    want[t] = force[t] || due || stale;
                    refresh |= stale;
                    let _ = bps;
                }
                if !want.iter().any(|w| *w) {
                    continue;
                }
                let mut err: Option<String> = None;
                let half_px = &mut cap.half_px;
                let quarter_px = &mut cap.quarter_px;
                let grabbed =
                    cap.src
                        .grab(refresh || force.iter().any(|f| *f), &mut |f: &Frame| {
                            // Each smaller size once per frame, the quarter from the half.
                            let mut half_wh: Option<(usize, usize)> = None;
                            let mut quarter_wh: Option<(usize, usize)> = None;
                            for t in 0..TIERS {
                                if !want[t] {
                                    continue;
                                }
                                let bps = rates[&(d, t)].clamp(MIN_BITRATE, MAX_BITRATE);
                                let lane = lanes.get_mut(&(d, t)).expect("made above");
                                // Due one step from now, less half a loop period, so a 15 fps
                                // lane on a 30 fps loop takes every other frame.
                                lane.fps_cap.rate(bps);
                                let lane_rate =
                                    lane_fps(bps, fps).min(lane.fps_cap.get(fps)).max(1);
                                let step = Duration::from_micros(1_000_000 / lane_rate as u64);
                                lane.due = Some(now + step.saturating_sub(period / 2));
                                let frame = if t == FULL {
                                    Frame { ..*f }
                                } else {
                                    let (hw, hh) = *half_wh.get_or_insert_with(|| {
                                        halve_bgra(
                                            f.bgra,
                                            f.stride,
                                            f.width as usize,
                                            f.height as usize,
                                            half_px,
                                        )
                                    });
                                    if t == HALF {
                                        Frame {
                                            width: hw as u32,
                                            height: hh as u32,
                                            stride: hw * 4,
                                            bgra: &half_px[..],
                                        }
                                    } else {
                                        let (qw, qh) = *quarter_wh.get_or_insert_with(|| {
                                            halve_bgra(&half_px[..], hw * 4, hw, hh, quarter_px)
                                        });
                                        Frame {
                                            width: qw as u32,
                                            height: qh as u32,
                                            stride: qw * 4,
                                            bgra: &quarter_px[..],
                                        }
                                    }
                                };
                                let (w, h) = (frame.width & !1, frame.height & !1);
                                let mut key = force[t];
                                if lane.enc.as_ref().is_none_or(|e| e.size() != (w, h)) {
                                    // New size (or first frame): a new encoder, starting on a keyframe.
                                    lane.enc = None;
                                    let made_at = Instant::now();
                                    let made = make_encoder(w, h, fps, bps);
                                    log::debug!(
                                        "{}: new {w}x{h} encoder in {} ms",
                                        display_name(d),
                                        made_at.elapsed().as_millis()
                                    );
                                    match made {
                                        Ok(e) => {
                                            lane.enc = Some(e);
                                            lane.applied_bps = bps;
                                            key = true;
                                        }
                                        Err(e) => {
                                            err = Some(e);
                                            continue;
                                        }
                                    }
                                }
                                let Some(e) = lane.enc.as_mut() else { continue };
                                if bps.abs_diff(lane.applied_bps) > lane.applied_bps / 10 {
                                    e.set_bitrate(bps);
                                    lane.applied_bps = bps;
                                }
                                match e.encode(&frame, pts, key) {
                                    Ok(Some(out)) => {
                                        note_cap(d, t, lane.fps_cap.record(false, fps, bps, now));
                                        produced.push(((d, t), out))
                                    }
                                    Ok(None) => {
                                        note_cap(d, t, lane.fps_cap.record(true, fps, bps, now))
                                    }
                                    Err(e) => err = Some(e.to_string()),
                                }
                            }
                        });
                match grabbed {
                    Ok(true) => cap.grabbed += 1,
                    Ok(false) => {}
                    Err(e) => err = Some(format!("capture: {e}")),
                }
                match err {
                    Some(e) => {
                        cap.failures += 1;
                        // Once, not once a frame (Windows repeated one 40 times a second).
                        if cap.failures == 1 {
                            log::warn!("{}: {e} (retrying)", display_name(d));
                        }
                        for t in 0..TIERS {
                            if let Some(l) = lanes.get_mut(&(d, t)) {
                                l.enc = None; // start clean next time
                            }
                        }
                        if cap.failures >= 50 {
                            log::warn!(
                                "{}: gave up after {} tries: {e}",
                                display_name(d),
                                cap.failures
                            );
                            captures.remove(&d);
                            move_off(&shared, d, "capture or encoding keeps failing");
                        }
                    }
                    None => cap.failures = 0,
                }
            }

            for (k, enc_frame) in produced {
                let lane = lanes.get_mut(&k).expect("lane of a produced frame");
                let sent_at = Instant::now();
                lane.last_sent = Some(sent_at);
                if enc_frame.keyframe {
                    lane.last_key = Some(sent_at);
                    lane.keys += 1;
                }
                lane.encoded += 1;
                lane.bytes += enc_frame.data.len() as u64;
                lane.win_bytes += enc_frame.data.len() as u64;
                let frame = Arc::new(enc_frame);
                let (m, _) = &*shared;
                let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
                s.viewers.retain(|v| {
                    let on = v.s.display.load(Ordering::Relaxed) == k.0
                        && (v.s.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1) == k.1;
                    if !on {
                        return true;
                    }
                    if frame.keyframe {
                        // The keyframe a join asked for is made: a viewer that could
                        // not take it waits for the next one, within KEY_GAP, like
                        // any other. Left set, it forced a keyframe on every frame.
                        v.s.join.store(false, Ordering::Relaxed);
                    }
                    if !frame.keyframe && v.s.need_key.load(Ordering::Relaxed) {
                        lane.skipped += 1;
                        return true; // undecodable without the keyframe it missed
                    }
                    match v.tx.try_send(ToViewer::Frame(frame.clone(), pts)) {
                        Ok(()) => {
                            if frame.keyframe {
                                v.s.need_key.store(false, Ordering::Relaxed);
                                v.s.want_key.store(false, Ordering::Relaxed);
                                v.s.join.store(false, Ordering::Relaxed);
                            }
                            true
                        }
                        Err(TrySendError::Full(_)) => {
                            v.s.need_key.store(true, Ordering::Relaxed);
                            lane.skipped += 1;
                            true
                        }
                        Err(TrySendError::Disconnected(_)) => false,
                    }
                });
            }

            if need_at.elapsed() >= Duration::from_secs(1) {
                let secs = need_at.elapsed().as_secs_f64();
                need_at = Instant::now();
                let mut by_display: HashMap<u32, [Option<(u32, u32)>; TIERS]> = HashMap::new();
                for ((d, t), lane) in lanes.iter_mut() {
                    let since = *lane.win_at.get_or_insert(need_at);
                    let span = need_at.duration_since(since).as_secs_f64();
                    if span >= 0.9 {
                        lane.out_bps = (lane.win_bytes as f64 * 8.0 / span) as u32;
                        (lane.win_bytes, lane.win_at) = (0, Some(need_at));
                    }
                    let bps = rates.get(&(*d, *t)).copied().unwrap_or(0);
                    let rate = lane_fps(bps, fps).min(lane.fps_cap.get(fps));
                    by_display.entry(*d).or_default()[*t] = Some((lane.out_bps, rate));
                }
                for (d, l) in by_display {
                    let before = need.get(&d).copied().unwrap_or(0);
                    let cost = full_cost(l, fps.min(30), before, secs);
                    need.insert(d, cost);
                }
                let (m, _) = &*shared;
                let s = m.lock().unwrap_or_else(|e| e.into_inner());
                for v in &s.viewers {
                    let d = v.s.display.load(Ordering::Relaxed);
                    let n = need.get(&d).copied().unwrap_or(0);
                    v.s.full_need.store(n, Ordering::Relaxed);
                }
            }

            if tally_at.elapsed() >= STATS_EVERY {
                let secs = tally_at.elapsed().as_secs_f64();
                // The slowest turn: every viewer waits for it (a new encoder,
                // a slow capture), so it shows as a stall on every screen.
                let mut line = format!(
                    "streaming: {} viewer(s), longest turn {} ms",
                    seen.len(),
                    longest.as_millis()
                );
                longest = Duration::ZERO;
                for (d, cap) in captures.iter_mut() {
                    line += &format!(
                        "; {} captured {:.1} fps",
                        display_name(*d),
                        cap.grabbed as f64 / secs
                    );
                    cap.grabbed = 0;
                }
                let mut keys: Vec<_> = lanes.keys().copied().collect();
                keys.sort();
                for k in keys {
                    let lane = lanes.get_mut(&k).expect("listed");
                    let (w, h) = lane.enc.as_ref().map(|e| e.size()).unwrap_or_default();
                    line += &format!(
                        "; {} {} {w}x{h}: {:.1} fps, {} keyframes, {} kbit/s (target {}), {} held back",
                        display_name(k.0),
                        ["full", "half", "quarter"][k.1.min(TIERS - 1)],
                        lane.encoded as f64 / secs,
                        lane.keys,
                        (lane.bytes as f64 * 8.0 / secs / 1000.0) as u64,
                        rates.get(&k).copied().unwrap_or(0) / 1000,
                        lane.skipped
                    );
                    (lane.encoded, lane.keys, lane.bytes, lane.skipped) = (0, 0, 0, 0);
                }
                log::info!("{line}");
                tally_at = Instant::now();
            }
            longest = longest.max(turn.elapsed());
            next += period;
            let now = Instant::now();
            if next > now {
                std::thread::sleep(next - now);
            } else {
                next = now; // fell behind: do not try to catch up with a burst
            }
        }
        drop(lanes);
        drop(captures);
    }
}

/// A display that cannot be captured (gone, or failing): its viewers move to
/// the main display; if it was the main display, they are let go - their
/// sessions end and the dashboard says so.
fn move_off(shared: &Arc<(Mutex<Shared>, Condvar)>, d: u32, why: &str) {
    let mut s = shared.0.lock().unwrap_or_else(|e| e.into_inner());
    if d == MAIN {
        log::error!("stopping: {why}");
        s.last_error = Some(why.to_string());
        s.viewers
            .retain(|v| v.s.display.load(Ordering::Relaxed) != MAIN);
        return;
    }
    for v in s
        .viewers
        .iter()
        .filter(|v| v.s.display.load(Ordering::Relaxed) == d)
    {
        v.s.display.store(MAIN, Ordering::Relaxed);
        v.s.need_key.store(true, Ordering::Relaxed);
        v.s.join.store(true, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::frame::EncodeError;
    use std::sync::atomic::AtomicUsize;
    use std::sync::mpsc::sync_channel;

    #[derive(Clone, Default)]
    struct Counts {
        grabs: Arc<AtomicUsize>,
        encoders: Arc<AtomicUsize>,
        keys: Arc<AtomicUsize>,
        frames: Arc<AtomicUsize>,
        /// The encoder drops this many of every 10 frames (over its bitrate).
        drops: Arc<AtomicUsize>,
        tried: Arc<AtomicUsize>,
    }

    struct FakeSource {
        c: Counts,
        size: Arc<Mutex<(u32, u32)>>,
        moving: bool,
        buf: Vec<u8>,
    }
    impl Source for FakeSource {
        fn size(&self) -> (u32, u32) {
            *self.size.lock().unwrap()
        }
        fn grab(&mut self, force: bool, f: &mut dyn FnMut(&Frame)) -> Result<bool, String> {
            self.c.grabs.fetch_add(1, Ordering::SeqCst);
            if !self.moving && !force {
                return Ok(false);
            }
            let (w, h) = self.size();
            self.buf.resize((w * h * 4) as usize, 0);
            f(&Frame {
                width: w,
                height: h,
                stride: (w * 4) as usize,
                bgra: &self.buf,
            });
            Ok(true)
        }
    }
    struct FakeEnc {
        c: Counts,
        size: (u32, u32),
        fail: bool,
    }
    impl Encoder for FakeEnc {
        fn encode(
            &mut self,
            _f: &Frame,
            _p: u64,
            key: bool,
        ) -> Result<Option<Encoded>, EncodeError> {
            if self.fail {
                return Err(EncodeError::Failed("planted".into()));
            }
            let n = self.c.tried.fetch_add(1, Ordering::SeqCst);
            if !key && n % 10 < self.c.drops.load(Ordering::SeqCst) {
                return Ok(None);
            }
            self.c.frames.fetch_add(1, Ordering::SeqCst);
            if key {
                self.c.keys.fetch_add(1, Ordering::SeqCst);
            }
            Ok(Some(Encoded {
                // The last byte says which picture this is: its width.
                data: vec![0, 0, 0, 1, if key { 0x65 } else { 0x41 }, self.size.0 as u8],
                keyframe: key,
            }))
        }
        fn set_bitrate(&mut self, _: u32) {}
        fn size(&self) -> (u32, u32) {
            self.size
        }
    }

    fn pipe(c: &Counts, size: Arc<Mutex<(u32, u32)>>, moving: bool, fail: bool) -> Pipeline {
        let (c1, c2) = (c.clone(), c.clone());
        Pipeline::start(
            50,
            3_000_000, // what the app starts at
            move |display: Option<u32>| {
                // Display 2 is a smaller screen; display 9 does not exist.
                let size = match display {
                    None => size.clone(),
                    Some(2) => Arc::new(Mutex::new((40, 30))),
                    Some(d) => return Err(format!("no display {d}")),
                };
                Ok(Box::new(FakeSource {
                    c: c1.clone(),
                    size,
                    moving,
                    buf: vec![],
                }) as Box<dyn Source>)
            },
            move |w, h, _, _| {
                c2.encoders.fetch_add(1, Ordering::SeqCst);
                Ok(Box::new(FakeEnc {
                    c: c2.clone(),
                    size: (w, h),
                    fail,
                }) as Box<dyn Encoder>)
            },
        )
    }

    #[test]
    fn nobody_watching_costs_nothing() {
        let c = Counts::default();
        let _p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(c.grabs.load(Ordering::SeqCst), 0);
        assert_eq!(c.encoders.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn a_viewer_starts_on_a_keyframe_and_leaving_stops_everything() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (tx, rx) = sync_channel(64);
        let id = p.subscribe(tx);
        let ToViewer::Frame(first, _) = rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(first.keyframe);
        std::thread::sleep(Duration::from_millis(150));
        p.unsubscribe(id);
        drop(rx);
        std::thread::sleep(Duration::from_millis(150));
        let grabs = c.grabs.load(Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(200));
        assert_eq!(
            c.grabs.load(Ordering::SeqCst),
            grabs,
            "no capture after the last viewer left"
        );
    }

    #[test]
    fn a_size_change_makes_a_new_encoder_that_starts_on_a_keyframe() {
        let c = Counts::default();
        let size = Arc::new(Mutex::new((64, 64)));
        let p = pipe(&c, size.clone(), true, false);
        let (tx, rx) = sync_channel(256);
        p.subscribe(tx);
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(c.encoders.load(Ordering::SeqCst), 1);
        *size.lock().unwrap() = (128, 72);
        std::thread::sleep(Duration::from_millis(100));
        assert_eq!(c.encoders.load(Ordering::SeqCst), 2);
        // Find the first frame after the switch: it is a keyframe.
        let mut keys = 0;
        while let Ok(ToViewer::Frame(f, _)) = rx.try_recv() {
            keys += f.keyframe as usize;
        }
        assert!(
            keys >= 2,
            "one key at the start, one at the new size: {keys}"
        );
    }

    #[test]
    fn a_static_screen_sends_only_a_frame_every_two_seconds_and_one_keyframe() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), false, false);
        let (tx, _rx) = sync_channel(256);
        p.subscribe(tx);
        std::thread::sleep(Duration::from_millis(4600));
        let frames = c.frames.load(Ordering::SeqCst);
        assert!(
            (2..=4).contains(&frames),
            "{frames} frames for a still screen in 4.6 s"
        );
        assert_eq!(
            c.keys.load(Ordering::SeqCst),
            1,
            "the joining viewer's keyframe; the refreshes are ordinary frames"
        );
    }

    #[test]
    fn a_slow_viewer_loses_frames_instead_of_queueing_them() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (tx, rx) = sync_channel(2);
        p.subscribe(tx);
        // Wait for frames rather than a fixed time (a slow CI machine makes fewer).
        let end = Instant::now() + Duration::from_secs(5);
        while c.frames.load(Ordering::SeqCst) <= 10 && Instant::now() < end {
            std::thread::sleep(Duration::from_millis(20));
        }
        // More than 10 made, only 2 waiting: the rest were dropped.
        assert!(c.frames.load(Ordering::SeqCst) > 10);
        let mut waiting = 0;
        while rx.try_recv().is_ok() {
            waiting += 1;
        }
        assert!(waiting <= 2);
        // Once it reads again, a keyframe repairs its picture within KEY_GAP.
        let end = Instant::now() + KEY_GAP * 2;
        let mut keyed = false;
        while Instant::now() < end && !keyed {
            if let Ok(ToViewer::Frame(f, _)) = rx.recv_timeout(Duration::from_millis(50)) {
                keyed = f.keyframe;
            }
        }
        assert!(keyed, "a keyframe came to repair it");
    }

    /// Seen live: one phone that could not keep up turned every frame into a
    /// keyframe for everyone, and all screens fell to about 1 fps.
    #[test]
    fn a_stalled_viewer_does_not_flood_the_others_with_keyframes() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (fast_tx, fast_rx) = sync_channel(256);
        let (slow_tx, slow_rx) = sync_channel(1); // never read: always full
        p.subscribe(fast_tx);
        let slow = p.subscribe(slow_tx);
        p.control(slow).request_keyframe(); // and it keeps asking, like a browser
        let start = Instant::now();
        let made0 = c.frames.load(Ordering::SeqCst);
        let (mut frames, mut keys) = (0, 0);
        while start.elapsed() < Duration::from_millis(2500) {
            p.control(slow).request_keyframe();
            if let Ok(ToViewer::Frame(f, _)) = fast_rx.recv_timeout(Duration::from_millis(20)) {
                frames += 1;
                keys += f.keyframe as usize;
            }
        }
        // Judged against what was made (a CI machine makes fewer frames), and
        // keyframes against the time that passed.
        let made = c.frames.load(Ordering::SeqCst) - made0;
        let secs = start.elapsed().as_secs_f64();
        assert!(
            frames * 10 >= made * 7 && frames > 20,
            "the healthy viewer kept its frames: {frames} of {made} made in {secs:.1} s"
        );
        assert!(
            keys as f64 <= 2.0 + secs, // one keyframe a second at most (KEY_GAP), not one per frame
            "{keys} keyframes of {frames}: at most one per KEY_GAP after the start"
        );
        drop(slow_rx);
    }

    /// Seen on Windows: up to 50 keyframes in 10 s. A viewer that joined (or
    /// changed quality) while its queue was full kept its "just joined" mark,
    /// and that mark forced a keyframe on every frame, past KEY_GAP, for every
    /// viewer of the lane.
    #[test]
    fn a_viewer_that_joins_with_a_full_queue_does_not_force_a_keyframe_every_frame() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (fast_tx, fast_rx) = sync_channel(256);
        p.subscribe(fast_tx);
        std::thread::sleep(Duration::from_millis(300));
        let (slow_tx, slow_rx) = sync_channel(1);
        let filler = Arc::new(Encoded {
            data: vec![],
            keyframe: false,
        });
        slow_tx.send(ToViewer::Frame(filler, 0)).unwrap(); // full before its first frame, never read
        p.subscribe(slow_tx);
        while fast_rx.try_recv().is_ok() {}
        let start = Instant::now();
        let (mut frames, mut keys) = (0, 0);
        while start.elapsed() < Duration::from_millis(2500) {
            if let Ok(ToViewer::Frame(f, _)) = fast_rx.recv_timeout(Duration::from_millis(20)) {
                frames += 1;
                keys += f.keyframe as usize;
            }
        }
        let secs = start.elapsed().as_secs_f64();
        assert!(frames > 20, "frames still flow: {frames}");
        assert!(
            keys as f64 <= 2.0 + secs,
            "{keys} keyframes of {frames} in {secs:.1} s: one per KEY_GAP at most"
        );
        drop(slow_rx);
    }

    /// Seen on Windows: the link estimate swung between 0.25 and 13 Mbit/s
    /// within seconds, and every swing across a threshold switched the viewer's
    /// quality - each switch a keyframe. A quality change now waits until the
    /// estimate has stayed on the other side for TIER_HOLD.
    #[test]
    fn a_swinging_link_estimate_does_not_flip_the_quality_back_and_forth() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (tx, rx) = sync_channel(256);
        let id = p.subscribe(tx);
        let ctl = p.control(id);
        let start = Instant::now();
        let mut flips = 0;
        let mut last = ctl.tier();
        let mut i = 0u32;
        while start.elapsed() < Duration::from_millis(2500) {
            i += 1;
            ctl.offer_bitrate(if i.is_multiple_of(2) {
                13_000_000
            } else {
                300_000
            });
            if ctl.tier() != last {
                flips += 1;
                last = ctl.tier();
            }
            std::thread::sleep(Duration::from_millis(100));
            while rx.try_recv().is_ok() {}
        }
        // The first estimate may move it once (the start was a guess); no more.
        assert!(
            flips <= 1,
            "a link swinging every 100 ms keeps its quality: {flips} switches"
        );
        // A real change still moves it, after TIER_HOLD.
        let to = Instant::now() + TIER_HOLD + Duration::from_millis(500);
        while Instant::now() < to {
            ctl.offer_bitrate(13_000_000);
            std::thread::sleep(Duration::from_millis(100));
        }
        assert_eq!(ctl.tier(), FULL, "a link that stays fast gets full size");
        let to = Instant::now() + TIER_HOLD + Duration::from_millis(500);
        while Instant::now() < to {
            ctl.offer_bitrate(300_000);
            std::thread::sleep(Duration::from_millis(100));
        }
        assert_eq!(
            ctl.tier(),
            QUARTER,
            "and one that stays at 0.3 Mbit/s gets quarter size"
        );
    }

    #[test]
    fn a_browser_that_lost_packets_gets_a_keyframe_and_frames_keep_coming_meanwhile() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (tx, rx) = sync_channel(256);
        let id = p.subscribe(tx);
        std::thread::sleep(Duration::from_millis(1200)); // past KEY_GAP since the first key
        while rx.try_recv().is_ok() {}
        p.control(id).browser_wants_keyframe();
        let start = Instant::now();
        let (mut before_key, mut key_after) = (0, None);
        while start.elapsed() < KEY_GAP * 2 && key_after.is_none() {
            if let Ok(ToViewer::Frame(f, _)) = rx.recv_timeout(Duration::from_millis(50)) {
                if f.keyframe {
                    key_after = Some(start.elapsed());
                } else {
                    before_key += 1;
                }
            }
        }
        assert!(
            key_after.is_some_and(|k| k <= KEY_GAP),
            "a keyframe within KEY_GAP: {key_after:?}"
        );
        assert!(
            !p.control(id).needs_key(),
            "and the stream was never held back"
        );
        let _ = before_key; // 0 when the keyframe is due at once, frames otherwise
    }

    #[test]
    fn a_viewer_waiting_for_a_keyframe_gets_no_frames_it_cannot_decode() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (tx, rx) = sync_channel(256);
        let id = p.subscribe(tx);
        std::thread::sleep(Duration::from_millis(1200)); // past KEY_GAP since the first key
        while rx.try_recv().is_ok() {}
        p.control(id).request_keyframe();
        let ToViewer::Frame(f, _) = rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(f.keyframe, "the first frame after asking is the keyframe");
    }

    #[test]
    fn a_viewer_on_a_poor_link_gets_half_size_and_the_others_keep_full_size() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 48))), true, false);
        let (good_tx, good_rx) = sync_channel(256);
        let (poor_tx, poor_rx) = sync_channel(256);
        let good = p.subscribe(good_tx);
        let poor = p.subscribe(poor_tx);
        // Each holds its estimate past TIER_HOLD, as a steady link does.
        let settle = Instant::now() + TIER_HOLD + Duration::from_millis(300);
        while Instant::now() < settle {
            p.control(good).offer_bitrate(8_000_000);
            p.control(poor).offer_bitrate(1_000_000);
            std::thread::sleep(Duration::from_millis(100));
            let _ = (good_rx.try_iter().count(), poor_rx.try_iter().count());
        }
        let (mut g, mut q) = (0, 0);
        let end = Instant::now() + Duration::from_secs(5);
        while (g <= 10 || q < 3) && Instant::now() < end {
            std::thread::sleep(Duration::from_millis(50));
            g += good_rx.try_iter().count();
            q += poor_rx.try_iter().count();
        }
        assert_eq!(
            c.encoders.load(Ordering::SeqCst),
            2,
            "one encoder per quality"
        );
        // The poor one at 1 Mbit/s runs at 15 fps (lane_fps), the good one at 50.
        assert!(g > 10 && q >= 3, "both get frames: {g} and {q}");
        // The poor link recovers: back to one encoder, which it shares.
        let settle = Instant::now() + TIER_HOLD + Duration::from_millis(300);
        while Instant::now() < settle {
            p.control(poor).offer_bitrate(6_000_000);
            std::thread::sleep(Duration::from_millis(100));
            while poor_rx.try_recv().is_ok() {}
        }
        let ToViewer::Frame(f, _) = poor_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let _ = f;
        assert_eq!(p.control(poor).tier(), FULL);
    }

    #[test]
    fn a_thin_link_gets_fewer_frames_with_more_bits_each() {
        assert_eq!(lane_fps(8_000_000, 30), 30);
        assert_eq!(
            lane_fps(800_000, 30),
            30,
            "0.8 Mbit/s carries light motion at 30"
        );
        assert_eq!(lane_fps(400_000, 30), 15);
        assert_eq!(lane_fps(300_000, 30), 8, "the floor rate (MIN_BITRATE)");
        assert_eq!(lane_fps(300_000, 5), 5, "never above the capture rate");
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 48))), true, false); // 50 fps capture
        let (tx, rx) = sync_channel(512);
        let id = p.subscribe(tx);
        p.control(id).offer_bitrate(300_000);
        std::thread::sleep(Duration::from_millis(300));
        while rx.try_recv().is_ok() {}
        std::thread::sleep(Duration::from_millis(1000));
        let n = rx.try_iter().count();
        assert!((5..=11).contains(&n), "{n} frames in 1 s at 0.3 Mbit/s");
    }

    #[test]
    fn an_encoder_that_drops_frames_gets_fewer_evenly_and_more_again_when_it_stops() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 48))), true, false); // 50 fps capture
        let (tx, rx) = sync_channel(1024);
        let id = p.subscribe(tx);
        p.control(id).offer_bitrate(8_000_000);
        let rate = |secs: u64| {
            while rx.try_recv().is_ok() {}
            let t0 = c.tried.load(Ordering::SeqCst);
            std::thread::sleep(Duration::from_secs(secs));
            let _ = rx.try_iter().count();
            (c.tried.load(Ordering::SeqCst) - t0) as f64 / secs as f64
        };
        std::thread::sleep(Duration::from_millis(500));
        let free = rate(1);
        c.drops.store(6, Ordering::SeqCst); // heavy content: 6 of 10 dropped
        std::thread::sleep(Duration::from_secs(4));
        let heavy = rate(1);
        assert!(
            heavy < free * 0.5,
            "the lane asks for fewer frames once most are dropped: {heavy:.0} of {free:.0}/s"
        );
        assert!(heavy >= 7.0, "never below 8 fps: {heavy:.0}");
        c.drops.store(0, Ordering::SeqCst); // the motion calms down
        std::thread::sleep(Duration::from_secs(5));
        let back = rate(1);
        assert!(
            back > free * 0.8,
            "and gets its frames back: {back:.0} of {free:.0}/s"
        );
    }

    #[test]
    fn a_viewer_on_a_thin_link_gets_its_own_small_stream_and_does_not_slow_the_others() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 48))), true, false);
        let (good_tx, good_rx) = sync_channel(1024);
        let (poor_tx, poor_rx) = sync_channel(1024);
        let good = p.subscribe(good_tx);
        let poor = p.subscribe(poor_tx);
        let settle = Instant::now() + TIER_HOLD + Duration::from_millis(500);
        while Instant::now() < settle {
            p.control(good).offer_bitrate(2_000_000); // half size (busy content would not fit full)
            p.control(poor).offer_bitrate(400_000);
            std::thread::sleep(Duration::from_millis(100));
            let _ = (good_rx.try_iter().count(), poor_rx.try_iter().count());
        }
        assert_eq!(p.control(poor).tier(), QUARTER);
        assert_eq!(width_of(&poor_rx), 16, "a quarter of 64 pixels wide");
        let lanes = lane_rates(
            &[(MAIN, HALF, 2_000_000), (MAIN, QUARTER, 400_000)],
            3_000_000,
        );
        assert_eq!(
            lanes[&(MAIN, HALF)],
            2_000_000,
            "the half-size stream keeps its own rate"
        );
        assert_eq!(lanes[&(MAIN, QUARTER)], 400_000);
    }

    fn width_of(rx: &std::sync::mpsc::Receiver<ToViewer>) -> u8 {
        let ToViewer::Frame(f, _) = rx.recv_timeout(Duration::from_secs(2)).unwrap();
        *f.data.last().unwrap()
    }

    #[test]
    fn two_viewers_can_watch_two_displays_at_once() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 48))), true, false);
        let (atx, arx) = sync_channel(512);
        let (btx, brx) = sync_channel(512);
        let a = p.subscribe(atx);
        let b = p.subscribe(btx);
        p.control(b).set_display(2);
        std::thread::sleep(Duration::from_millis(300));
        while arx.try_recv().is_ok() {}
        while brx.try_recv().is_ok() {}
        assert_eq!(
            width_of(&arx),
            32,
            "a: the main display at half size (64 / 2)"
        );
        assert_eq!(width_of(&brx), 20, "b: display 2 at half size (40 / 2)");
        // a switches too: from its next keyframe it gets display 2.
        p.control(a).set_display(2);
        std::thread::sleep(Duration::from_millis(200));
        while arx.try_recv().is_ok() {}
        assert_eq!(width_of(&arx), 20);
        assert_eq!(p.control(a).display(), 2);
    }

    #[test]
    fn a_display_that_cannot_be_captured_hands_its_viewer_the_main_display() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 48))), true, false);
        let (tx, rx) = sync_channel(512);
        let id = p.subscribe(tx);
        p.control(id).set_display(9); // unplugged, say
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(p.control(id).display(), MAIN);
        while rx.try_recv().is_ok() {}
        assert_eq!(width_of(&rx), 32, "the main display, still streaming");
        assert_eq!(p.viewers(), 1, "not dropped");
    }

    #[test]
    fn an_encoder_that_keeps_failing_ends_the_session_cleanly() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, true);
        let (tx, rx) = sync_channel(8);
        p.subscribe(tx);
        // 50 failures, 100 ms apart.
        let deadline = Instant::now() + Duration::from_secs(10);
        while p.viewers() > 0 && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
        }
        assert_eq!(p.viewers(), 0);
        assert!(
            rx.recv_timeout(Duration::from_millis(100)).is_err(),
            "the viewer's channel is closed"
        );
    }
}

#[cfg(test)]
mod rate_tests {
    use super::*;

    #[test]
    fn each_lane_follows_its_slowest_viewer() {
        let r = lane_rates(
            &[
                (MAIN, FULL, 9_000_000),
                (MAIN, FULL, 5_000_000),
                (MAIN, HALF, 1_200_000),
                (2, FULL, 0),
            ],
            3_000_000,
        );
        assert_eq!(r[&(MAIN, FULL)], 5_000_000, "the slower one sets full size");
        assert_eq!(r[&(MAIN, HALF)], 1_200_000);
        assert_eq!(
            r[&(2, FULL)],
            3_000_000,
            "no estimate yet: the starting rate"
        );
        assert_eq!(r.len(), 3, "nothing for a lane nobody watches");
    }

    #[test]
    fn a_link_losing_packets_steps_down_at_once() {
        let c = Control { s: Arc::default() };
        c.offer_bitrate(1_000_000); // the first change is at once (the start was a guess)
        settle(&c, 8_000_000);
        assert_eq!(c.tier(), FULL);
        c.offer_bitrate(1_000_000);
        assert_eq!(c.tier(), FULL, "a dip alone waits TIER_HOLD_DOWN");
        c.link_lost(1_000_000);
        assert_eq!(c.tier(), HALF, "with packets lost: at once");
        c.link_lost(9_000_000);
        assert_eq!(c.tier(), HALF, "loss never hurries a step up");
    }

    #[test]
    fn full_size_is_judged_by_what_the_content_costs_not_only_the_link() {
        let c = Control { s: Arc::default() };
        c.offer_bitrate(1_000_000); // the first change is at once
        assert_eq!(c.tier(), HALF);
        // Busy content: full size would cost 15 Mbit/s. A 6 Mbit/s link stays half.
        c.s.full_need.store(15_000_000, Ordering::Relaxed);
        settle(&c, 6_000_000);
        assert_eq!(
            c.tier(),
            HALF,
            "busy content stays at half size, and smooth"
        );
        // Light content: full size costs 1 Mbit/s - the same link takes it.
        c.s.full_need.store(1_000_000, Ordering::Relaxed);
        settle(&c, 6_000_000);
        assert_eq!(c.tier(), FULL);
        // And light content fits a thin link at full size too (no fixed 4.5).
        c.s.full_need.store(500_000, Ordering::Relaxed);
        settle(&c, 1_500_000);
        assert_eq!(
            c.tier(),
            FULL,
            "a 1.5 Mbit/s link shows light content at full size"
        );
        c.s.full_need.store(1_000_000, Ordering::Relaxed);
        // The screen gets busy: full size now costs more than the link.
        c.s.full_need.store(12_000_000, Ordering::Relaxed);
        settle(&c, 6_000_000);
        assert_eq!(
            c.tier(),
            HALF,
            "and steps down when the content outgrows the link"
        );
    }

    #[test]
    fn the_cost_of_full_size_comes_from_real_output_and_fades_slowly() {
        // Full lane 4 Mbit/s but its encoder manages only 10 of 30 fps: 12.
        assert_eq!(
            full_cost([Some((4_000_000, 10)), None, None], 30, 0, 1.0),
            12_000_000
        );
        // Only half size running, at full rate: three times its output.
        assert_eq!(
            full_cost([None, Some((1_000_000, 30)), None], 30, 0, 1.0),
            3_000_000
        );
        // The screen rests (nothing out): the cost fades by a tenth a second.
        assert_eq!(
            full_cost([Some((0, 30)), None, None], 30, 10_000_000, 1.0),
            9_000_000
        );
    }

    #[test]
    fn a_thin_link_gets_quarter_size_and_comes_back_when_it_recovers() {
        let c = Control { s: Arc::default() };
        c.offer_bitrate(8_000_000);
        settle(&c, 500_000);
        assert_eq!(
            c.tier(),
            QUARTER,
            "0.5 Mbit/s: quarter size, straight from full"
        );
        settle(&c, 1_000_000);
        assert_eq!(c.tier(), QUARTER, "between the thresholds it stays");
        settle(&c, 2_000_000);
        assert_eq!(c.tier(), HALF, "a better link: half size again");
    }

    #[test]
    fn the_largest_size_that_fits_this_content() {
        // Busy content: full size costs 12 Mbit/s (at 20 fps).
        assert_eq!(best_tier(15_000_000, 12_000_000, 1.2), FULL);
        assert_eq!(best_tier(6_000_000, 12_000_000, 1.2), HALF);
        assert_eq!(best_tier(1_200_000, 12_000_000, 1.2), QUARTER);
        // Light content: full size costs 0.4 - a 1.5 Mbit/s link carries it.
        assert_eq!(best_tier(1_500_000, 400_000, 1.2), FULL);
        assert_eq!(best_tier(700_000, 400_000, 1.2), HALF, "below FULL_MIN_BPS");
        assert_eq!(
            best_tier(300_000, 400_000, 1.2),
            QUARTER,
            "below HALF_MIN_BPS"
        );
    }

    /// An estimate that stays where it is past TIER_HOLD (the wait itself is
    /// tested with the real clock in `a_swinging_link_estimate_...`).
    fn settle(c: &Control, bps: u32) {
        c.offer_bitrate(bps);
        if let Some(t) = c.s.crossed.lock().unwrap().as_mut() {
            *t -= TIER_HOLD;
        }
        c.offer_bitrate(bps);
    }

    #[test]
    fn a_viewer_moves_between_qualities_by_its_link_without_flapping() {
        let c = Control { s: Arc::default() };
        assert_eq!(c.tier(), FULL);
        c.offer_bitrate(1_200_000);
        assert_eq!(c.tier(), HALF, "the first measurement decides at once");
        assert!(
            c.needs_key(),
            "it starts the half-size stream on a keyframe"
        );
        settle(&c, 3_500_000);
        assert_eq!(c.tier(), HALF, "between the thresholds it stays");
        c.offer_bitrate(5_000_000);
        assert_eq!(
            c.tier(),
            HALF,
            "after that, one good estimate is not a good link"
        );
        settle(&c, 5_000_000);
        assert_eq!(c.tier(), FULL, "a good link again: back to full size");
    }
}
