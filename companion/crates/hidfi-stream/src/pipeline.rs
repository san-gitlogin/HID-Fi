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
/// The two qualities.
pub const FULL: usize = 0;
pub const HALF: usize = 1;
const TIERS: usize = 2;
/// A viewer whose link estimate falls below this moves to half size...
pub const DOWN_BPS: u32 = 2_500_000;
/// ...and back to full size once it rises above this.
pub const UP_BPS: u32 = 4_500_000;
pub const MIN_BITRATE: u32 = 300_000;
pub const MAX_BITRATE: u32 = 12_000_000;
/// Keyframes asked for while streaming: at most one per this, per lane.
pub const KEY_GAP: Duration = Duration::from_millis(1000);
/// How often the pipeline writes a line to the log while someone watches.
const STATS_EVERY: Duration = Duration::from_secs(10);

/// One viewer's state, shared between its session and the encoding loop.
#[derive(Default)]
struct Shared1 {
    /// Lost a frame (or changed lane): nothing until the next keyframe.
    need_key: AtomicBool,
    /// Just joined (or changed lane): a keyframe now, not within KEY_GAP.
    join: AtomicBool,
    tier: AtomicU8,
    /// Its latest bandwidth estimate, bit/s; 0 = none yet.
    est: AtomicU32,
    display: AtomicU32,
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
    /// This viewer's congestion-control estimate, in bits per second. It also
    /// decides the viewer's quality.
    pub fn offer_bitrate(&self, bps: u32) {
        self.s.est.store(bps, Ordering::Relaxed);
        let now = self.tier();
        let next = match now {
            FULL if bps < DOWN_BPS => HALF,
            HALF if bps > UP_BPS => FULL,
            t => t,
        };
        if next != now {
            // The other stream's frames only make sense from its keyframe.
            self.s.tier.store(next as u8, Ordering::Relaxed);
            self.restart();
        }
    }
}

/// Frames per second a lane runs at, for its bitrate. A thin link gets fewer
/// frames with enough bits each, instead of every frame starved and most of
/// them dropped by the encoder at random.
pub fn lane_fps(bps: u32, fps: u32) -> u32 {
    let cap = match bps {
        b if b >= 2_000_000 => 60,
        b if b >= 900_000 => 15,
        _ => 8,
    };
    fps.min(cap)
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
            join: AtomicBool::new(true),
            tier: AtomicU8::new(HALF as u8),
            est: AtomicU32::new(0),
            display: AtomicU32::new(MAIN),
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
}

/// One display's capture.
struct Capture {
    src: Box<dyn Source>,
    half_px: Vec<u8>,
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
        loop {
            // Who watches what, this turn.
            let seen: Vec<Seen> = {
                let s = shared.0.lock().unwrap_or_else(|e| e.into_inner());
                s.viewers
                    .iter()
                    .map(|v| Seen {
                        display: v.s.display.load(Ordering::Relaxed),
                        tier: (v.s.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1),
                        est: v.s.est.load(Ordering::Relaxed),
                        need_key: v.s.need_key.load(Ordering::Relaxed),
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
                    // A still screen sends nothing, but a keyframe every 2 s so a
                    // viewer that joined or lost a packet is never left waiting.
                    let stale = lane
                        .last_sent
                        .is_none_or(|s| now.duration_since(s) > Duration::from_secs(2));
                    force[t] = joined || asked || stale || lane.enc.is_none();
                    let due = lane.due.is_none_or(|due| now >= due);
                    want[t] = force[t] || due;
                    let _ = bps;
                }
                if !want.iter().any(|w| *w) {
                    continue;
                }
                let mut err: Option<String> = None;
                let half_px = &mut cap.half_px;
                let grabbed = cap.src.grab(force.iter().any(|f| *f), &mut |f: &Frame| {
                    for t in 0..TIERS {
                        if !want[t] {
                            continue;
                        }
                        let bps = rates[&(d, t)].clamp(MIN_BITRATE, MAX_BITRATE);
                        let lane = lanes.get_mut(&(d, t)).expect("made above");
                        // Due one step from now, less half a loop period, so a 15 fps
                        // lane on a 30 fps loop takes every other frame.
                        let step = Duration::from_micros(1_000_000 / lane_fps(bps, fps) as u64);
                        lane.due = Some(now + step.saturating_sub(period / 2));
                        let frame = if t == HALF {
                            let (w, h) = halve_bgra(
                                f.bgra,
                                f.stride,
                                f.width as usize,
                                f.height as usize,
                                half_px,
                            );
                            Frame {
                                width: w as u32,
                                height: h as u32,
                                stride: w * 4,
                                bgra: &half_px[..],
                            }
                        } else {
                            Frame { ..*f }
                        };
                        let (w, h) = (frame.width & !1, frame.height & !1);
                        let mut key = force[t];
                        if lane.enc.as_ref().is_none_or(|e| e.size() != (w, h)) {
                            // New size (or first frame): a new encoder, starting on a keyframe.
                            lane.enc = None;
                            match make_encoder(w, h, fps, bps) {
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
                            Ok(Some(out)) => produced.push(((d, t), out)),
                            Ok(None) => {}
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
                        log::warn!("{}: {e}", display_name(d));
                        for t in 0..TIERS {
                            if let Some(l) = lanes.get_mut(&(d, t)) {
                                l.enc = None; // start clean next time
                            }
                        }
                        if cap.failures >= 50 {
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
                let frame = Arc::new(enc_frame);
                let (m, _) = &*shared;
                let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
                s.viewers.retain(|v| {
                    let on = v.s.display.load(Ordering::Relaxed) == k.0
                        && (v.s.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1) == k.1;
                    if !on {
                        return true;
                    }
                    if !frame.keyframe && v.s.need_key.load(Ordering::Relaxed) {
                        lane.skipped += 1;
                        return true; // undecodable without the keyframe it missed
                    }
                    match v.tx.try_send(ToViewer::Frame(frame.clone(), pts)) {
                        Ok(()) => {
                            if frame.keyframe {
                                v.s.need_key.store(false, Ordering::Relaxed);
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

            if tally_at.elapsed() >= STATS_EVERY {
                let secs = tally_at.elapsed().as_secs_f64();
                let mut line = format!("streaming: {} viewer(s)", seen.len());
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
                        if k.1 == FULL { "full" } else { "half" },
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
    fn a_static_screen_sends_only_a_keyframe_every_two_seconds() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), false, false);
        let (tx, _rx) = sync_channel(256);
        p.subscribe(tx);
        std::thread::sleep(Duration::from_millis(2600));
        let frames = c.frames.load(Ordering::SeqCst);
        assert!(
            (1..=3).contains(&frames),
            "{frames} frames for a still screen in 2.6 s"
        );
        assert_eq!(
            frames,
            c.keys.load(Ordering::SeqCst),
            "every one a keyframe"
        );
    }

    #[test]
    fn a_slow_viewer_loses_frames_instead_of_queueing_them() {
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 64))), true, false);
        let (tx, rx) = sync_channel(2);
        p.subscribe(tx);
        std::thread::sleep(Duration::from_millis(400));
        // ~20 frames made, only 2 waiting: the rest were dropped.
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
        let (mut frames, mut keys) = (0, 0);
        while start.elapsed() < Duration::from_millis(2500) {
            p.control(slow).request_keyframe();
            if let Ok(ToViewer::Frame(f, _)) = fast_rx.recv_timeout(Duration::from_millis(20)) {
                frames += 1;
                keys += f.keyframe as usize;
            }
        }
        assert!(
            frames > 60,
            "the healthy viewer kept its frame rate: {frames} in 2.5 s"
        );
        assert!(
            keys <= 5,
            "{keys} keyframes of {frames}: at most one per KEY_GAP after the start"
        );
        drop(slow_rx);
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
        p.control(good).offer_bitrate(8_000_000);
        p.control(poor).offer_bitrate(1_000_000);
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(
            c.encoders.load(Ordering::SeqCst),
            2,
            "one encoder per quality"
        );
        let (g, q) = (good_rx.try_iter().count(), poor_rx.try_iter().count());
        // The poor one at 1 Mbit/s runs at 15 fps (lane_fps), the good one at 50.
        assert!(g > 10 && q >= 3, "both get frames: {g} and {q}");
        // The poor link recovers: back to one encoder, which it shares.
        p.control(poor).offer_bitrate(6_000_000);
        std::thread::sleep(Duration::from_millis(200));
        while poor_rx.try_recv().is_ok() {}
        let ToViewer::Frame(f, _) = poor_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        let _ = f;
        assert_eq!(p.control(poor).tier(), FULL);
    }

    #[test]
    fn a_thin_link_gets_fewer_frames_with_more_bits_each() {
        assert_eq!(lane_fps(8_000_000, 30), 30);
        assert_eq!(lane_fps(1_200_000, 30), 15);
        assert_eq!(lane_fps(400_000, 30), 8);
        assert_eq!(lane_fps(400_000, 5), 5, "never above the capture rate");
        let c = Counts::default();
        let p = pipe(&c, Arc::new(Mutex::new((64, 48))), true, false); // 50 fps capture
        let (tx, rx) = sync_channel(512);
        let id = p.subscribe(tx);
        p.control(id).offer_bitrate(400_000);
        std::thread::sleep(Duration::from_millis(300));
        while rx.try_recv().is_ok() {}
        std::thread::sleep(Duration::from_millis(1000));
        let n = rx.try_iter().count();
        assert!((5..=11).contains(&n), "{n} frames in 1 s at 0.4 Mbit/s");
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
    fn a_viewer_moves_between_qualities_by_its_link_without_flapping() {
        let c = Control { s: Arc::default() };
        assert_eq!(c.tier(), FULL);
        c.offer_bitrate(1_200_000);
        assert_eq!(c.tier(), HALF);
        assert!(
            c.needs_key(),
            "it starts the half-size stream on a keyframe"
        );
        c.offer_bitrate(3_500_000);
        assert_eq!(c.tier(), HALF, "between the thresholds it stays");
        c.offer_bitrate(5_000_000);
        assert_eq!(c.tier(), FULL, "a good link again: back to full size");
    }
}
