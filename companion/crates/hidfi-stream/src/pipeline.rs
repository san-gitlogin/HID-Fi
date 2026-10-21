//! Capture → encode → every viewer, on one thread that exists only while
//! someone is watching.
//!
//! With no viewer the thread is parked on a condition variable: no capture, no
//! encoding, no wakeups - the app costs nothing in the tray (A3). A new viewer
//! wakes it and gets a keyframe first.
//!
//! Two qualities, like a video call. The screen is encoded at full size for
//! viewers whose link carries it, and at half size - only while someone needs
//! it - for viewers on a poor link (a phone far from the router). One encoder
//! for everyone made every screen as slow as the worst link: seen live, three
//! phones at 1 fps. Each viewer moves between the two by its own bandwidth
//! estimate, with a gap between the thresholds so it does not flap. A new
//! viewer starts at half size and moves up once its link shows it can take
//! more (a second or two on a good one): starting at full size flooded a poor
//! link, and its estimate took many seconds to recover from that.
//!
//! A viewer that falls behind loses frames (never queues them: queued video is
//! lag that never recovers) and then waits for the next keyframe, skipping the
//! frames in between, which it could not decode anyway. Only that viewer: the
//! others keep every frame. Keyframes asked for after the start are at most one
//! per [`KEY_GAP`] per quality - a keyframe is many times the size of a frame,
//! and a stream of them starves everyone on it.

use crate::convert::halve_bgra;
use crate::frame::{Encoded, Encoder, Frame};
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

/// The two qualities.
pub const FULL: usize = 0;
pub const HALF: usize = 1;
const TIERS: usize = 2;
/// A viewer whose link estimate falls below this moves to half size...
pub const DOWN_BPS: u32 = 2_500_000;
/// ...and back to full size once it rises above this.
pub const UP_BPS: u32 = 4_500_000;

struct Viewer {
    id: u64,
    tx: SyncSender<ToViewer>,
    /// Lost a frame (or changed quality): nothing until the next keyframe.
    need_key: Arc<AtomicBool>,
    tier: Arc<AtomicU8>,
}

#[derive(Default)]
struct Shared {
    viewers: Vec<Viewer>,
    next_id: u64,
    stop: bool,
}

/// What viewers ask of the encoding loop, per quality.
#[derive(Default)]
struct Flags {
    /// A keyframe is wanted (at most one per [`KEY_GAP`]).
    key: [AtomicBool; TIERS],
    /// A viewer joined (or arrived from the other quality): a keyframe now.
    join: [AtomicBool; TIERS],
    bitrate: [AtomicU32; TIERS],
}

pub struct Pipeline {
    shared: Arc<(Mutex<Shared>, Condvar)>,
    flags: Arc<Flags>,
    rates: Rates,
    initial_bps: u32,
    thread: Option<std::thread::JoinHandle<()>>,
}

/// Each viewer's own bandwidth estimate, and so its quality. Each quality's
/// stream follows the slowest viewer on it. A viewer that leaves stops counting.
#[derive(Clone, Default)]
struct Rates(Arc<Mutex<std::collections::HashMap<u64, Estimate>>>);

/// A viewer's latest estimate (bit/s) and its quality.
type Estimate = (u32, Arc<AtomicU8>);

impl Rates {
    fn set(&self, id: u64, est: Option<Estimate>, flags: &Flags, initial: u32, need_key: &AtomicBool) {
        let mut m = self.0.lock().unwrap_or_else(|e| e.into_inner());
        match est {
            Some((bps, tier)) => {
                let now = tier.load(Ordering::Relaxed) as usize;
                let next = match now {
                    FULL if bps < DOWN_BPS => HALF,
                    HALF if bps > UP_BPS => FULL,
                    t => t,
                };
                if next != now {
                    // The other stream's frames only make sense from its keyframe.
                    need_key.store(true, Ordering::Relaxed);
                    tier.store(next as u8, Ordering::Relaxed);
                    flags.join[next].store(true, Ordering::Relaxed);
                }
                m.insert(id, (bps, tier));
            }
            None => {
                m.remove(&id);
            }
        }
        for t in 0..TIERS {
            let low = m
                .values()
                .filter(|(_, tier)| tier.load(Ordering::Relaxed) as usize == t)
                .map(|(b, _)| *b)
                .min()
                .unwrap_or(initial);
            flags.bitrate[t].store(low, Ordering::Relaxed);
        }
    }
}

/// One viewer's handle: ask for a keyframe, report its bandwidth estimate.
#[derive(Clone)]
pub struct Control {
    id: u64,
    flags: Arc<Flags>,
    need_key: Arc<AtomicBool>,
    tier: Arc<AtomicU8>,
    rates: Rates,
    initial_bps: u32,
}

impl Control {
    /// This viewer's picture is broken (a lost frame, or the browser asked):
    /// it gets no more frames until a keyframe, which comes within [`KEY_GAP`].
    pub fn request_keyframe(&self) {
        self.need_key.store(true, Ordering::Relaxed);
        self.flags.key[self.tier()].store(true, Ordering::Relaxed);
    }
    /// The viewer just connected: a keyframe now, not within [`KEY_GAP`].
    pub fn joined(&self) {
        self.need_key.store(true, Ordering::Relaxed);
        self.flags.join[self.tier()].store(true, Ordering::Relaxed);
    }
    /// Whether this viewer is waiting for a keyframe.
    pub fn needs_key(&self) -> bool {
        self.need_key.load(Ordering::Relaxed)
    }
    /// The quality this viewer gets: [`FULL`] or [`HALF`].
    pub fn tier(&self) -> usize {
        (self.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1)
    }
    /// This viewer's congestion-control estimate, in bits per second.
    pub fn offer_bitrate(&self, bps: u32) {
        self.rates.set(
            self.id,
            Some((bps, self.tier.clone())),
            &self.flags,
            self.initial_bps,
            &self.need_key,
        );
    }
}

pub const MIN_BITRATE: u32 = 300_000;
pub const MAX_BITRATE: u32 = 12_000_000;
/// Keyframes asked for while streaming: at most one per this, per quality.
pub const KEY_GAP: Duration = Duration::from_millis(1000);
/// Frames per second a quality's stream runs at, for its bitrate. A thin link
/// gets fewer frames with enough bits each, instead of every frame starved
/// and most of them dropped by the encoder at random.
pub fn lane_fps(bps: u32, fps: u32) -> u32 {
    let cap = match bps {
        b if b >= 2_000_000 => 60,
        b if b >= 900_000 => 15,
        _ => 8,
    };
    fps.min(cap)
}

/// How often the pipeline writes a line to the log while someone watches.
const STATS_EVERY: Duration = Duration::from_secs(10);

impl Pipeline {
    /// `source` and the encoders are built on the pipeline thread when the
    /// first viewer arrives and dropped when the last one leaves, so an idle
    /// app holds no capture session and no encoder. An encoder is rebuilt
    /// whenever the picture changes size (resolution change, rotation,
    /// another display), since an H.264 stream has one size.
    pub fn start<S, E>(fps: u32, initial_bps: u32, source: S, encoder: E) -> Pipeline
    where
        S: Fn() -> Result<Box<dyn Source>, String> + Send + 'static,
        E: Fn(u32, u32, u32, u32) -> Result<Box<dyn Encoder>, String> + Send + 'static,
    {
        let shared: Arc<(Mutex<Shared>, Condvar)> = Arc::default();
        let flags = Arc::new(Flags::default());
        for b in &flags.bitrate {
            b.store(initial_bps, Ordering::Relaxed);
        }
        let (s, f) = (shared.clone(), flags.clone());
        let thread = std::thread::Builder::new()
            .name("pipeline".into())
            .spawn(move || run(s, f, fps.clamp(1, 60), source, encoder))
            .expect("spawning the pipeline thread");
        Pipeline {
            shared,
            flags,
            rates: Rates::default(),
            initial_bps,
            thread: Some(thread),
        }
    }

    /// The handle for viewer `id` (from [`Pipeline::subscribe`]).
    pub fn control(&self, id: u64) -> Control {
        let (need_key, tier) = self
            .shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .iter()
            .find(|v| v.id == id)
            .map(|v| (v.need_key.clone(), v.tier.clone()))
            .unwrap_or_default();
        Control {
            id,
            flags: self.flags.clone(),
            need_key,
            tier,
            rates: self.rates.clone(),
            initial_bps: self.initial_bps,
        }
    }

    /// The bitrate the full-size encoder is currently asked for.
    pub fn target_bitrate(&self) -> u32 {
        self.flags.bitrate[FULL].load(Ordering::Relaxed)
    }

    /// The bitrate a quality's encoder is currently asked for.
    pub fn tier_bitrate(&self, tier: usize) -> u32 {
        self.flags.bitrate[tier.min(TIERS - 1)].load(Ordering::Relaxed)
    }

    /// Add a viewer, at half size until its link shows it can take full size.
    /// Frames arrive on `tx`; drop the receiver to leave.
    pub fn subscribe(&self, tx: SyncSender<ToViewer>) -> u64 {
        let (m, cv) = &*self.shared;
        let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
        s.next_id += 1;
        let id = s.next_id;
        s.viewers.push(Viewer {
            id,
            tx,
            need_key: Arc::new(AtomicBool::new(true)),
            tier: Arc::new(AtomicU8::new(HALF as u8)),
        });
        self.flags.join[HALF].store(true, Ordering::Relaxed);
        cv.notify_all();
        id
    }

    pub fn unsubscribe(&self, id: u64) {
        self.rates
            .set(id, None, &self.flags, self.initial_bps, &AtomicBool::new(false));
        let (m, _) = &*self.shared;
        m.lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .retain(|v| v.id != id);
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

/// One quality's encoder and what it has done since the last log line.
#[derive(Default)]
struct Lane {
    enc: Option<Box<dyn Encoder>>,
    applied_bps: u32,
    last_key: Option<Instant>,
    /// When this quality is next due a frame (it may run below the capture rate).
    due: Option<Instant>,
    encoded: u32,
    keys: u32,
    bytes: u64,
    skipped: u32,
}

fn run<S, E>(
    shared: Arc<(Mutex<Shared>, Condvar)>,
    flags: Arc<Flags>,
    fps: u32,
    make_source: S,
    make_encoder: E,
) where
    S: Fn() -> Result<Box<dyn Source>, String>,
    E: Fn(u32, u32, u32, u32) -> Result<Box<dyn Encoder>, String>,
{
    let period = Duration::from_micros(1_000_000 / fps as u64);
    let t0 = Instant::now();
    let drop_viewers = |why: &str| {
        log::error!("{why}");
        // Their sessions end and the dashboard says so; then wait again.
        shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .clear();
    };
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
        let mut src = match make_source() {
            Ok(x) => x,
            Err(e) => {
                drop_viewers(&format!("cannot start capture: {e}"));
                continue;
            }
        };
        let mut lanes: [Lane; TIERS] = Default::default();
        let mut half_px: Vec<u8> = Vec::new();
        let mut next = Instant::now();
        let mut last_sent = Instant::now();
        let mut failures = 0u32;
        let (mut grabbed_n, mut tally_at) = (0u32, Instant::now());
        loop {
            // Which qualities someone is watching now.
            let mut wanted = [false; TIERS];
            {
                let s = shared.0.lock().unwrap_or_else(|e| e.into_inner());
                if s.viewers.is_empty() {
                    break; // last viewer left: release capture and encoders
                }
                for v in &s.viewers {
                    wanted[(v.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1)] = true;
                }
            }
            // A static screen sends nothing, except a keyframe every 2 s so a
            // viewer that joined (or lost a packet) is never left waiting.
            let stale = last_sent.elapsed() > Duration::from_secs(2);
            let mut force = [false; TIERS];
            for t in 0..TIERS {
                if !wanted[t] {
                    lanes[t].enc = None; // nobody on this quality: no encoder
                    continue;
                }
                let joined = flags.join[t].swap(false, Ordering::Relaxed);
                let may_key = lanes[t].last_key.is_none_or(|k| k.elapsed() >= KEY_GAP);
                let asked = may_key && flags.key[t].swap(false, Ordering::Relaxed);
                force[t] = joined || asked || stale || lanes[t].enc.is_none();
            }
            let pts = t0.elapsed().as_millis() as u64;
            let mut produced: [Option<Encoded>; TIERS] = Default::default();
            let mut err: Option<String> = None;
            let grabbed = src.grab(force.iter().any(|f| *f), &mut |f: &Frame| {
                for t in 0..TIERS {
                    if !wanted[t] {
                        continue;
                    }
                    let lane = &mut lanes[t];
                    let bps = flags.bitrate[t]
                        .load(Ordering::Relaxed)
                        .clamp(MIN_BITRATE, MAX_BITRATE);
                    let now = Instant::now();
                    if !force[t] && lane.due.is_some_and(|d| now < d) {
                        continue; // this quality runs slower than capture
                    }
                    // Due one step from now, less half a loop period, so a 15 fps
                    // quality on a 30 fps loop takes every other frame.
                    let step = Duration::from_micros(1_000_000 / lane_fps(bps, fps) as u64);
                    lane.due = Some(now + step.saturating_sub(period / 2));
                    let frame = if t == HALF {
                        let (w, h) = halve_bgra(
                            f.bgra,
                            f.stride,
                            f.width as usize,
                            f.height as usize,
                            &mut half_px,
                        );
                        Frame {
                            width: w as u32,
                            height: h as u32,
                            stride: w * 4,
                            bgra: &half_px,
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
                        Ok(out) => produced[t] = out,
                        Err(e) => err = Some(e.to_string()),
                    }
                }
            });
            match grabbed {
                Ok(true) => grabbed_n += 1,
                Ok(false) => {}
                Err(e) => err = Some(format!("capture: {e}")),
            }
            match err {
                Some(e) => {
                    failures += 1;
                    log::warn!("{e}");
                    for (t, lane) in lanes.iter_mut().enumerate() {
                        flags.join[t].store(true, Ordering::Relaxed);
                        lane.enc = None; // start clean next time
                    }
                    if failures >= 50 {
                        drop_viewers("capture or encoding keeps failing; stopping");
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                None => failures = 0,
            }
            for (t, out) in produced.into_iter().enumerate() {
                let Some(enc_frame) = out else { continue };
                last_sent = Instant::now();
                let lane = &mut lanes[t];
                if enc_frame.keyframe {
                    lane.last_key = Some(last_sent);
                    lane.keys += 1;
                }
                lane.encoded += 1;
                lane.bytes += enc_frame.data.len() as u64;
                let frame = Arc::new(enc_frame);
                let (m, _) = &*shared;
                let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
                s.viewers.retain(|v| {
                    if (v.tier.load(Ordering::Relaxed) as usize).min(TIERS - 1) != t {
                        return true;
                    }
                    if !frame.keyframe && v.need_key.load(Ordering::Relaxed) {
                        lane.skipped += 1;
                        return true; // undecodable without the keyframe it missed
                    }
                    match v.tx.try_send(ToViewer::Frame(frame.clone(), pts)) {
                        Ok(()) => {
                            if frame.keyframe {
                                v.need_key.store(false, Ordering::Relaxed);
                            }
                            true
                        }
                        Err(TrySendError::Full(_)) => {
                            v.need_key.store(true, Ordering::Relaxed);
                            flags.key[t].store(true, Ordering::Relaxed);
                            lane.skipped += 1;
                            true
                        }
                        Err(TrySendError::Disconnected(_)) => false,
                    }
                });
            }
            if tally_at.elapsed() >= STATS_EVERY {
                let secs = tally_at.elapsed().as_secs_f64();
                let viewers = shared.0.lock().unwrap_or_else(|e| e.into_inner()).viewers.len();
                let mut line = format!(
                    "streaming: {viewers} viewer(s), captured {:.1} fps",
                    grabbed_n as f64 / secs
                );
                for (t, lane) in lanes.iter_mut().enumerate() {
                    if lane.enc.is_none() && lane.encoded == 0 {
                        continue;
                    }
                    let (w, h) = lane.enc.as_ref().map(|e| e.size()).unwrap_or_default();
                    line += &format!(
                        "; {} {w}x{h}: {:.1} fps, {} keyframes, {} kbit/s (target {}), {} held back",
                        if t == FULL { "full" } else { "half" },
                        lane.encoded as f64 / secs,
                        lane.keys,
                        (lane.bytes as f64 * 8.0 / secs / 1000.0) as u64,
                        flags.bitrate[t].load(Ordering::Relaxed) / 1000,
                        lane.skipped
                    );
                    (lane.encoded, lane.keys, lane.bytes, lane.skipped) = (0, 0, 0, 0);
                }
                log::info!("{line}");
                grabbed_n = 0;
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
        drop(src);
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
                data: vec![0, 0, 0, 1, if key { 0x65 } else { 0x41 }],
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
            move || {
                Ok(Box::new(FakeSource {
                    c: c1.clone(),
                    size: size.clone(),
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
        assert!(frames > 60, "the healthy viewer kept its frame rate: {frames} in 2.5 s");
        assert!(keys <= 5, "{keys} keyframes of {frames}: at most one per KEY_GAP after the start");
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
        assert_eq!(c.encoders.load(Ordering::SeqCst), 2, "one encoder per quality");
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
    use std::sync::mpsc::sync_channel;

    fn idle() -> Pipeline {
        Pipeline::start(
            30,
            3_000_000,
            || Err("no source in this test".into()),
            |_, _, _, _| Err("no encoder in this test".into()),
        )
    }

    #[test]
    fn the_slowest_viewer_on_each_quality_sets_its_bitrate() {
        let p = idle();
        let (tx1, _r1) = sync_channel(1);
        let (tx2, _r2) = sync_channel(1);
        let (a, b) = (p.subscribe(tx1), p.subscribe(tx2));
        let (a, b) = (p.control(a), p.control(b));
        assert_eq!(p.target_bitrate(), 3_000_000, "no estimates yet: the starting rate");
        a.offer_bitrate(8_000_000);
        b.offer_bitrate(5_000_000);
        assert_eq!(p.target_bitrate(), 5_000_000, "the slower one sets full size");
        a.offer_bitrate(9_000_000);
        assert_eq!(p.target_bitrate(), 5_000_000, "a fast viewer cannot raise it");
        // b's link gets poor: it moves to half size, and stops holding a back.
        b.offer_bitrate(1_200_000);
        assert_eq!(b.tier(), HALF);
        assert!(b.needs_key(), "it starts the half-size stream on a keyframe");
        assert_eq!(p.target_bitrate(), 9_000_000, "full size is a's alone now");
        assert_eq!(p.tier_bitrate(HALF), 1_200_000);
        // Between the thresholds it stays where it is: no flapping.
        b.offer_bitrate(3_500_000);
        assert_eq!(b.tier(), HALF);
        b.offer_bitrate(5_000_000);
        assert_eq!(b.tier(), FULL, "a good link again: back to full size");
        assert_eq!(p.target_bitrate(), 5_000_000);
        p.unsubscribe(2);
        assert_eq!(p.target_bitrate(), 9_000_000, "the slow one left");
    }
}
