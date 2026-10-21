//! Capture → encode → every viewer, on one thread that exists only while
//! someone is watching.
//!
//! With no viewer the thread is parked on a condition variable: no capture, no
//! encoding, no wakeups - the app costs nothing in the tray (A3). A new viewer
//! wakes it and gets a keyframe first. A viewer that falls behind loses frames
//! (never queues them: queued video is lag that never recovers) and is sent a
//! keyframe so its picture repairs itself.

use crate::frame::{Encoded, Encoder, Frame};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
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

struct Viewer {
    id: u64,
    tx: SyncSender<ToViewer>,
}

#[derive(Default)]
struct Shared {
    viewers: Vec<Viewer>,
    next_id: u64,
    stop: bool,
}

pub struct Pipeline {
    shared: Arc<(Mutex<Shared>, Condvar)>,
    key: Arc<AtomicBool>,
    bitrate: Arc<AtomicU32>,
    thread: Option<std::thread::JoinHandle<()>>,
}

/// A handle a viewer session uses to ask for a keyframe or change the bitrate.
#[derive(Clone)]
pub struct Control {
    key: Arc<AtomicBool>,
    bitrate: Arc<AtomicU32>,
}

impl Control {
    pub fn request_keyframe(&self) {
        self.key.store(true, Ordering::Relaxed);
    }
    /// Congestion control's estimate, in bits per second. With two viewers the
    /// slower one wins, because one encoder feeds both.
    pub fn offer_bitrate(&self, bps: u32) {
        self.bitrate.store(bps, Ordering::Relaxed);
    }
}

pub const MIN_BITRATE: u32 = 300_000;
pub const MAX_BITRATE: u32 = 12_000_000;

impl Pipeline {
    /// `source` and `encoder` are built on the pipeline thread when the first
    /// viewer arrives and dropped when the last one leaves, so an idle app
    /// holds no capture session and no encoder. The encoder is rebuilt
    /// whenever the picture changes size (resolution change, rotation,
    /// another display), since an H.264 stream has one size.
    pub fn start<S, E>(fps: u32, initial_bps: u32, source: S, encoder: E) -> Pipeline
    where
        S: Fn() -> Result<Box<dyn Source>, String> + Send + 'static,
        E: Fn(u32, u32, u32, u32) -> Result<Box<dyn Encoder>, String> + Send + 'static,
    {
        let shared: Arc<(Mutex<Shared>, Condvar)> = Arc::default();
        let key = Arc::new(AtomicBool::new(true));
        let bitrate = Arc::new(AtomicU32::new(initial_bps));
        let (s, k, b) = (shared.clone(), key.clone(), bitrate.clone());
        let thread = std::thread::Builder::new()
            .name("pipeline".into())
            .spawn(move || run(s, k, b, fps.clamp(1, 60), source, encoder))
            .expect("spawning the pipeline thread");
        Pipeline {
            shared,
            key,
            bitrate,
            thread: Some(thread),
        }
    }

    pub fn control(&self) -> Control {
        Control {
            key: self.key.clone(),
            bitrate: self.bitrate.clone(),
        }
    }

    /// Add a viewer. Frames arrive on `tx`; drop the receiver to leave.
    pub fn subscribe(&self, tx: SyncSender<ToViewer>) -> u64 {
        let (m, cv) = &*self.shared;
        let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
        s.next_id += 1;
        let id = s.next_id;
        s.viewers.push(Viewer { id, tx });
        self.key.store(true, Ordering::Relaxed);
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

fn run<S, E>(
    shared: Arc<(Mutex<Shared>, Condvar)>,
    key: Arc<AtomicBool>,
    bitrate: Arc<AtomicU32>,
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
        let mut enc: Option<Box<dyn Encoder>> = None;
        let mut applied_bps = 0u32;
        let mut next = Instant::now();
        let mut last_sent = Instant::now();
        let mut failures = 0u32;
        loop {
            if shared
                .0
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .viewers
                .is_empty()
            {
                break; // last viewer left: release capture and encoder
            }
            // A static screen sends nothing, except a keyframe every 2 s so a
            // viewer that joined (or lost a packet) is never left waiting.
            let mut force =
                key.swap(false, Ordering::Relaxed) || last_sent.elapsed() > Duration::from_secs(2);
            let pts = t0.elapsed().as_millis() as u64;
            let mut produced: Option<Encoded> = None;
            let mut err: Option<String> = None;
            let bps = bitrate
                .load(Ordering::Relaxed)
                .clamp(MIN_BITRATE, MAX_BITRATE);
            let grabbed = src.grab(force, &mut |f: &Frame| {
                let (w, h) = (f.width & !1, f.height & !1);
                if enc.as_ref().is_none_or(|e| e.size() != (w, h)) {
                    // New size (or first frame): a new encoder, starting on a keyframe.
                    enc = None;
                    match make_encoder(w, h, fps, bps) {
                        Ok(e) => {
                            enc = Some(e);
                            applied_bps = bps;
                            force = true;
                        }
                        Err(e) => {
                            err = Some(e);
                            return;
                        }
                    }
                }
                let Some(e) = enc.as_mut() else { return };
                if bps.abs_diff(applied_bps) > applied_bps / 10 {
                    e.set_bitrate(bps);
                    applied_bps = bps;
                }
                match e.encode(f, pts, force) {
                    Ok(out) => produced = out,
                    Err(e) => err = Some(e.to_string()),
                }
            });
            if let Err(e) = grabbed {
                err = Some(format!("capture: {e}"));
            }
            match err {
                Some(e) => {
                    failures += 1;
                    log::warn!("{e}");
                    key.store(true, Ordering::Relaxed);
                    enc = None; // start clean next time
                    if failures >= 50 {
                        drop_viewers("capture or encoding keeps failing; stopping");
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                None => failures = 0,
            }
            if let Some(enc_frame) = produced {
                last_sent = Instant::now();
                let frame = Arc::new(enc_frame);
                let (m, _) = &*shared;
                let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
                s.viewers.retain(
                    |v| match v.tx.try_send(ToViewer::Frame(frame.clone(), pts)) {
                        Ok(()) => true,
                        Err(TrySendError::Full(_)) => {
                            key.store(true, Ordering::Relaxed);
                            true
                        }
                        Err(TrySendError::Disconnected(_)) => false,
                    },
                );
            }
            next += period;
            let now = Instant::now();
            if next > now {
                std::thread::sleep(next - now);
            } else {
                next = now; // fell behind: do not try to catch up with a burst
            }
        }
        drop(enc);
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
            1_000_000,
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
        // ~20 frames made, only 2 waiting: the rest were dropped, and keyframes
        // were asked for so the picture repairs itself.
        assert!(c.frames.load(Ordering::SeqCst) > 10);
        assert!(c.keys.load(Ordering::SeqCst) > 2);
        let mut waiting = 0;
        while rx.try_recv().is_ok() {
            waiting += 1;
        }
        assert!(waiting <= 2);
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
