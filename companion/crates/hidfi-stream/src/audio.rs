//! The computer's sound - and, when shared, its microphone - for every viewer
//! (docs/AUDIO.md).
//!
//! Capture → conform (48 kHz stereo) → mix → Opus (10 ms, lowest delay) → every
//! viewer, on one thread that exists only while someone watches with sound,
//! like the picture's pipeline. The system sound is the clock: a frame leaves
//! the moment 10 ms of it exist, so nothing waits for a tick. The microphone
//! runs on its own device clock; its resampler is nudged to keep it level with
//! the system sound, so it never drifts into a growing delay and never needs a
//! glitchy drop. When nothing plays, nothing is sent (Windows delivers nothing
//! in silence anyway): each frame's RTP time follows its capture time, so after
//! a pause the browser sees a clean jump and plays the first sound at once -
//! silence frames sent late would instead have taught its jitter buffer to wait.

use rubato::audioadapter_buffers::direct::InterleavedSlice;
use rubato::{
    Adjustable, Async, FixedAsync, Resampler, SincInterpolationParameters, SincInterpolationType,
    WindowFunction,
};
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{channel, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

pub const RATE: u32 = 48_000;
pub const CHANNELS: usize = 2;
/// 10 ms: the smallest frame that keeps Opus efficient at music quality.
pub const FRAME: usize = 480;
pub const FRAME_TIME: Duration = Duration::from_millis(10);
/// Music quality for stereo; a few bytes a frame in silence (VBR).
pub const BITRATE: i32 = 128_000;
/// The system sound silent for this long: the microphone alone sets the pace.
pub const IDLE: Duration = Duration::from_millis(30);
/// A frame later than its predecessor's end by more than this starts after a
/// pause: its RTP time jumps with the clock.
const GAP: Duration = Duration::from_millis(5);
/// The microphone is held about this far behind the system sound...
const MIC_TARGET: usize = FRAME;
/// ...and resynchronised (the excess dropped) beyond this - a stall, not drift.
const MIC_MAX: usize = 6 * FRAME;
/// How hard the microphone's resampler may be pushed to follow: 0.2 %.
const NUDGE: f64 = 0.002;

/// Which sound.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum Input {
    /// What the computer plays (every app, whichever device plays it).
    System,
    Microphone,
}

/// A piece of captured sound: interleaved samples in -1..1, as the device gives them.
#[derive(Clone, Debug)]
pub struct Chunk {
    pub samples: Vec<f32>,
    pub rate: u32,
    pub channels: usize,
    /// When the first sample was captured.
    pub at: Instant,
}

/// Where a capture delivers its chunks; called on the capture's own thread.
pub type Sink = Arc<dyn Fn(Chunk) + Send + Sync>;

/// A running capture; dropping it stops it.
pub trait Running: Send {}
impl<T: Send> Running for T {}

/// A capture that stopped by itself (a device unplugged, the system ended it)
/// says why here; the hub then reports it and tries again.
pub type Ended = Arc<dyn Fn(String) + Send + Sync>;

/// Opens a capture of one input. Real captures per OS; a test sound in tests.
pub type Opener = Arc<dyn Fn(Input, Sink, Ended) -> Result<Box<dyn Running>, String> + Send + Sync>;

/// One encoded 10 ms frame.
#[derive(Debug)]
pub struct Packet {
    pub data: Vec<u8>,
    /// In samples at 48 kHz since the stream began - the RTP time.
    pub rtp: u64,
    /// When its first sample was captured: picture and sound share this clock.
    pub at: Instant,
}

// ---- conform: any device's sound to 48 kHz stereo ------------------------------------------

/// Stereo from any channel layout. Two or more channels follow the WAVE/WASAPI
/// order (FL FR FC LFE BL BR SL SR); the centre and surrounds fold into the
/// front pair at -3 dB, the LFE is left out (ITU-R BS.775).
pub fn to_stereo(samples: &[f32], channels: usize) -> Vec<f32> {
    match channels {
        0 => Vec::new(),
        1 => samples.iter().flat_map(|&s| [s, s]).collect(),
        2 => samples.to_vec(),
        n => {
            const K: f32 = std::f32::consts::FRAC_1_SQRT_2;
            let mut out = Vec::with_capacity(samples.len() / n * 2);
            for f in samples.chunks_exact(n) {
                let c = if n >= 3 { f[2] } else { 0.0 };
                let (mut l, mut r) = (f[0] + K * c, f[1] + K * c);
                // n == 3: L R C. 4: L R BL BR (quad). 5+: L R C LFE BL BR [SL SR].
                match n {
                    3 => {}
                    4 => {
                        l = f[0] + K * f[2];
                        r = f[1] + K * f[3];
                    }
                    _ => {
                        l += K * f[4];
                        if n > 5 {
                            r += K * f[5];
                        }
                        if n >= 8 {
                            l += K * f[6];
                            r += K * f[7];
                        }
                    }
                }
                out.push(l);
                out.push(r);
            }
            out
        }
    }
}

/// One input's way to 48 kHz stereo, with a resampler whose ratio can be nudged.
pub struct Conform {
    rate: u32,
    rs: Option<Async<f32>>,
    /// Stereo at the device's rate, waiting for a whole resampler chunk.
    pending: Vec<f32>,
    chunk: usize,
    ratio: f64,
}

impl Conform {
    pub fn new(rate: u32, adjustable: bool) -> Result<Conform, String> {
        if rate == 0 {
            return Err("a device reported a rate of 0".into());
        }
        let ratio = RATE as f64 / rate as f64;
        // A resampler only where needed: for a 48 kHz system sound, nothing runs;
        // the microphone always gets one, to be nudged.
        let rs = if rate != RATE || adjustable {
            let params = SincInterpolationParameters {
                sinc_len: 64,
                f_cutoff: Some(0.95),
                interpolation: SincInterpolationType::Linear,
                oversampling_factor: 128,
                window: WindowFunction::BlackmanHarris2,
            };
            // Small chunks: the resampler's own delay stays a few ms.
            Some(
                Async::<f32>::new_sinc(ratio, 1.1, &params, 128, CHANNELS, FixedAsync::Input)
                    .map_err(|e| e.to_string())?,
            )
        } else {
            None
        };
        Ok(Conform {
            rate,
            rs,
            pending: Vec::new(),
            chunk: 128,
            ratio: 1.0,
        })
    }

    pub fn rate(&self) -> u32 {
        self.rate
    }

    /// Nudge the speed: `r` > 1 makes more output from the same input.
    pub fn set_relative(&mut self, r: f64) {
        if let Some(rs) = self.rs.as_mut() {
            if (r - self.ratio).abs() > 1e-6 && rs.set_resample_ratio_relative(r, true).is_ok() {
                self.ratio = r;
            }
        }
    }

    /// Appends this chunk, as 48 kHz stereo, to `out`.
    pub fn push(&mut self, c: &Chunk, out: &mut VecDeque<f32>) {
        let st = to_stereo(&c.samples, c.channels);
        let Some(rs) = self.rs.as_mut() else {
            out.extend(st);
            return;
        };
        self.pending.extend(st);
        let mut buf = vec![0f32; rs.output_frames_max() * CHANNELS];
        while self.pending.len() >= self.chunk * CHANNELS {
            let need = rs.input_frames_next();
            if self.pending.len() < need * CHANNELS {
                break;
            }
            let frames_out = rs.output_frames_max();
            if buf.len() < frames_out * CHANNELS {
                buf.resize(frames_out * CHANNELS, 0.0);
            }
            let (Ok(inp), Ok(mut outp)) = (
                InterleavedSlice::new(&self.pending[..need * CHANNELS], CHANNELS, need),
                InterleavedSlice::new_mut(&mut buf[..], CHANNELS, frames_out),
            ) else {
                break;
            };
            match rs.process_into_buffer(&inp, &mut outp, None) {
                Ok((used, made)) => {
                    out.extend(&buf[..made * CHANNELS]);
                    self.pending.drain(..used * CHANNELS);
                }
                Err(_) => {
                    self.pending.clear();
                    break;
                }
            }
        }
    }
}

// ---- the mixer: frames out as soon as the system sound has them ---------------------------

struct Lane {
    conform: Conform,
    fifo: VecDeque<f32>,
    /// Capture time of the sample at the front of `fifo`.
    head_at: Option<Instant>,
    last_input: Option<Instant>,
}

impl Lane {
    fn take(&mut self, n: usize, out: &mut [f32]) -> usize {
        let have = (self.fifo.len() / CHANNELS).min(n);
        for (o, s) in out.iter_mut().zip(self.fifo.drain(..have * CHANNELS)) {
            *o += s;
        }
        if let Some(t) = self.head_at.as_mut() {
            *t += Duration::from_secs_f64(have as f64 / RATE as f64);
        }
        have
    }
}

/// What the mixer hands the encoder: 10 ms of 48 kHz stereo, and when it was captured.
pub struct Mixed {
    pub pcm: Vec<f32>,
    pub at: Instant,
}

#[derive(Default)]
pub struct Mixer {
    system: Option<Lane>,
    mic: Option<Lane>,
    /// The microphone's level was cut back to the target (a stall, not drift).
    pub resyncs: u32,
    /// Frames sent while the microphone had nothing (it fell behind).
    pub mic_underruns: u32,
}

impl Mixer {
    pub fn new() -> Mixer {
        Mixer::default()
    }

    pub fn has(&self, i: Input) -> bool {
        match i {
            Input::System => self.system.is_some(),
            Input::Microphone => self.mic.is_some(),
        }
    }

    pub fn close(&mut self, i: Input) {
        match i {
            Input::System => self.system = None,
            Input::Microphone => self.mic = None,
        }
    }

    pub fn push(&mut self, i: Input, c: &Chunk) {
        let slot = match i {
            Input::System => &mut self.system,
            Input::Microphone => &mut self.mic,
        };
        // A lane per device rate; a device that changed rate gets a new one.
        if slot.as_ref().is_none_or(|l| l.conform.rate() != c.rate) {
            let Ok(conform) = Conform::new(c.rate, i == Input::Microphone) else {
                return;
            };
            *slot = Some(Lane {
                conform,
                fifo: VecDeque::new(),
                head_at: None,
                last_input: None,
            });
        }
        let lane = slot.as_mut().expect("just made");
        if lane.fifo.is_empty() {
            lane.head_at = Some(c.at);
        }
        lane.last_input = Some(c.at);
        lane.conform.push(c, &mut lane.fifo);
    }

    /// The frames ready at `now`.
    pub fn pull(&mut self, now: Instant) -> Vec<Mixed> {
        let mut out = Vec::new();
        loop {
            // The clock: the system sound while it plays; the microphone alone otherwise.
            let sys_has = self
                .system
                .as_ref()
                .is_some_and(|l| l.fifo.len() >= FRAME * CHANNELS);
            let sys_idle = self.system.as_ref().is_none_or(|l| {
                l.last_input
                    .is_none_or(|t| now.saturating_duration_since(t) >= IDLE)
            });
            let mic_has = self
                .mic
                .as_ref()
                .is_some_and(|l| l.fifo.len() >= FRAME * CHANNELS);
            let by_system = sys_has;
            let by_mic = !sys_has && sys_idle && mic_has;
            if !(by_system || by_mic) {
                break;
            }
            let mut pcm = vec![0f32; FRAME * CHANNELS];
            let mut at = None;
            if by_system {
                if let Some(l) = self.system.as_mut() {
                    let h = l.head_at;
                    l.take(FRAME, &mut pcm);
                    at = h;
                }
            }
            if let Some(l) = self.mic.as_mut() {
                // Level control: more than the target in hand, speed up; less, slow down.
                let level = l.fifo.len() / CHANNELS;
                if level > MIC_MAX {
                    let cut = (level - MIC_TARGET) * CHANNELS;
                    l.fifo.drain(..cut);
                    self.resyncs += 1;
                }
                let err = (l.fifo.len() / CHANNELS) as f64 - (MIC_TARGET + FRAME) as f64;
                let r = (1.0 - err / (8.0 * FRAME as f64) * NUDGE).clamp(1.0 - NUDGE, 1.0 + NUDGE);
                l.conform.set_relative(r);
                let h = l.head_at;
                let n = l.take(FRAME, &mut pcm);
                if n < FRAME {
                    self.mic_underruns += 1;
                }
                if at.is_none() {
                    at = h;
                }
            }
            for s in pcm.iter_mut() {
                *s = s.clamp(-1.0, 1.0);
            }
            out.push(Mixed {
                pcm,
                at: at.unwrap_or(now),
            });
            if out.len() > 64 {
                break; // never more than this in one go (a stall); the rest next turn
            }
        }
        out
    }
}

// ---- the encoder ------------------------------------------------------------------------

pub struct Encoder {
    enc: opus_pure::OpusEncoder,
    buf: Vec<u8>,
    rtp: u64,
    /// The previous frame's capture time.
    last: Option<Instant>,
}

impl Encoder {
    pub fn new() -> Result<Encoder, String> {
        let mut enc = opus_pure::OpusEncoder::new(
            RATE as i32,
            CHANNELS,
            opus_pure::Application::RestrictedLowDelay,
        )
        .map_err(|e| format!("{e:?}"))?;
        enc.bitrate_bps = BITRATE;
        Ok(Encoder {
            enc,
            buf: vec![0; 1500],
            rtp: 0,
            last: None,
        })
    }

    pub fn encode(&mut self, m: &Mixed) -> Option<Packet> {
        // Back to back: exactly one frame on. After a pause: on by the time that
        // passed, so RTP time keeps following the clock the picture uses too.
        if let Some(last) = self.last {
            let end = last + FRAME_TIME;
            if m.at > end + GAP {
                let gap = (m.at - end).as_secs_f64();
                self.rtp += (gap * RATE as f64).round() as u64;
            }
        }
        self.last = Some(m.at);
        let n = self.enc.encode(&m.pcm, FRAME, &mut self.buf).ok()?;
        let p = Packet {
            data: self.buf[..n].to_vec(),
            rtp: self.rtp,
            at: m.at,
        };
        self.rtp += FRAME as u64;
        Some(p)
    }
}

// ---- the hub: one capture and one encode for every viewer ----------------------------------

/// What the person chose in the Companion. Applied at once, to every viewer.
pub struct Settings {
    pub system: AtomicBool,
    pub mic: AtomicBool,
}

impl Settings {
    pub fn new(system: bool, mic: bool) -> Arc<Settings> {
        Arc::new(Settings {
            system: AtomicBool::new(system),
            mic: AtomicBool::new(mic),
        })
    }
    pub fn wants(&self, i: Input) -> bool {
        match i {
            Input::System => self.system.load(Ordering::Relaxed),
            Input::Microphone => self.mic.load(Ordering::Relaxed),
        }
    }
}

/// Where one input stands, for the viewer and the tray.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Share {
    /// Switched off in the Companion.
    Off,
    /// Shared and capturing (or ready to, the moment someone watches).
    On,
    /// Wanted, but it cannot be captured: why, in words.
    Unavailable(String),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct State {
    pub system: Share,
    pub mic: Share,
}

/// How long a capture that failed waits before it is tried again (a device
/// plugged in, a permission granted).
pub const RETRY: Duration = Duration::from_secs(3);

struct HubShared {
    viewers: Vec<(u64, SyncSender<Arc<Packet>>)>,
    next_id: u64,
    stop: bool,
    state: State,
}

pub struct AudioHub {
    shared: Arc<(Mutex<HubShared>, Condvar)>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl AudioHub {
    pub fn start(opener: Opener, settings: Arc<Settings>) -> AudioHub {
        let state = State {
            system: if settings.wants(Input::System) {
                Share::On
            } else {
                Share::Off
            },
            mic: if settings.wants(Input::Microphone) {
                Share::On
            } else {
                Share::Off
            },
        };
        let shared = Arc::new((
            Mutex::new(HubShared {
                viewers: Vec::new(),
                next_id: 1,
                stop: false,
                state,
            }),
            Condvar::new(),
        ));
        let s2 = shared.clone();
        let thread = std::thread::Builder::new()
            .name("audio".into())
            .spawn(move || run(s2, opener, settings))
            .ok();
        AudioHub { shared, thread }
    }

    /// A viewer that wants sound. Packets it cannot take in time are dropped.
    pub fn subscribe(&self, tx: SyncSender<Arc<Packet>>) -> u64 {
        let (m, cv) = &*self.shared;
        let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
        let id = s.next_id;
        s.next_id += 1;
        s.viewers.push((id, tx));
        cv.notify_all();
        id
    }

    pub fn unsubscribe(&self, id: u64) {
        let (m, cv) = &*self.shared;
        m.lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .retain(|v| v.0 != id);
        cv.notify_all();
    }

    pub fn state(&self) -> State {
        self.shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .state
            .clone()
    }

    pub fn listeners(&self) -> usize {
        self.shared
            .0
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .viewers
            .len()
    }
}

impl Drop for AudioHub {
    fn drop(&mut self) {
        let (m, cv) = &*self.shared;
        m.lock().unwrap_or_else(|e| e.into_inner()).stop = true;
        cv.notify_all();
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

struct Open {
    running: Option<Box<dyn Running>>,
    retry_at: Option<Instant>,
}

fn run(shared: Arc<(Mutex<HubShared>, Condvar)>, opener: Opener, settings: Arc<Settings>) {
    let (m, cv) = &*shared;
    let (tx, rx) = channel::<(Input, Chunk)>();
    let (etx, erx) = channel::<(Input, String)>();
    let mut opens: [(Input, Open); 2] = [
        (
            Input::System,
            Open {
                running: None,
                retry_at: None,
            },
        ),
        (
            Input::Microphone,
            Open {
                running: None,
                retry_at: None,
            },
        ),
    ];
    let mut mixer = Mixer::new();
    let mut encoder: Option<Encoder> = None;
    loop {
        // Park while nobody listens: no capture, no encode, no wakeups.
        {
            let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
            if s.viewers.is_empty() && !s.stop {
                for (i, o) in opens.iter_mut() {
                    o.running = None;
                    o.retry_at = None;
                    mixer.close(*i);
                }
                encoder = None;
                while s.viewers.is_empty() && !s.stop {
                    // Settings may change while parked: keep the state true.
                    s.state.system = parked_share(&settings, Input::System, &s.state.system);
                    s.state.mic = parked_share(&settings, Input::Microphone, &s.state.mic);
                    s = cv
                        .wait_timeout(s, Duration::from_millis(500))
                        .unwrap_or_else(|e| e.into_inner())
                        .0;
                }
                while rx.try_recv().is_ok() {}
            }
            if s.stop {
                return;
            }
        }
        // Captures that ended by themselves: report, and try again later.
        while let Ok((i, why)) = erx.try_recv() {
            if let Some((_, o)) = opens.iter_mut().find(|(x, _)| *x == i) {
                if o.running.take().is_some() {
                    log::info!("sound: {i:?} capture ended: {why}");
                    mixer.close(i);
                    o.retry_at = Some(Instant::now() + RETRY);
                    let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
                    match i {
                        Input::System => s.state.system = Share::Unavailable(why),
                        Input::Microphone => s.state.mic = Share::Unavailable(why),
                    }
                }
            }
        }
        // Captures follow the settings, every turn.
        let now = Instant::now();
        for (i, o) in opens.iter_mut() {
            let want = settings.wants(*i);
            let share = if !want {
                if o.running.take().is_some() {
                    mixer.close(*i);
                }
                o.retry_at = None;
                Share::Off
            } else if o.running.is_some() {
                Share::On
            } else if o.retry_at.is_some_and(|t| now < t) {
                current_share(m, *i)
            } else {
                let t = tx.clone();
                let which = *i;
                let sink: Sink = Arc::new(move |c| {
                    let _ = t.send((which, c));
                });
                let e = etx.clone();
                let ended: Ended = Arc::new(move |why| {
                    let _ = e.send((which, why));
                });
                match opener(*i, sink, ended) {
                    Ok(r) => {
                        o.running = Some(r);
                        o.retry_at = None;
                        Share::On
                    }
                    Err(why) => {
                        log::info!("sound: cannot capture {i:?}: {why}");
                        o.retry_at = Some(now + RETRY);
                        Share::Unavailable(why)
                    }
                }
            };
            let mut s = m.lock().unwrap_or_else(|e| e.into_inner());
            match i {
                Input::System => s.state.system = share,
                Input::Microphone => s.state.mic = share,
            }
        }
        if encoder.is_none() {
            encoder = Encoder::new()
                .map_err(|e| log::warn!("sound: no encoder: {e}"))
                .ok();
        }
        // Wait for sound (or the silence deadline), then send what is ready.
        match rx.recv_timeout(FRAME_TIME) {
            Ok((i, c)) => {
                if settings.wants(i) {
                    mixer.push(i, &c);
                }
                while let Ok((i, c)) = rx.try_recv() {
                    if settings.wants(i) {
                        mixer.push(i, &c);
                    }
                }
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => return,
        }
        let frames = mixer.pull(Instant::now());
        let Some(enc) = encoder.as_mut() else {
            continue;
        };
        if frames.is_empty() {
            continue;
        }
        let packets: Vec<Arc<Packet>> = frames
            .iter()
            .filter_map(|f| enc.encode(f))
            .map(Arc::new)
            .collect();
        let s = m.lock().unwrap_or_else(|e| e.into_inner());
        for (_, v) in &s.viewers {
            for p in &packets {
                // A viewer that cannot keep up loses this packet, never queues it.
                if let Err(TrySendError::Disconnected(_)) = v.try_send(p.clone()) {
                    break;
                }
            }
        }
    }
}

fn parked_share(settings: &Settings, i: Input, was: &Share) -> Share {
    match (settings.wants(i), was) {
        (false, _) => Share::Off,
        (true, Share::Unavailable(w)) => Share::Unavailable(w.clone()),
        (true, _) => Share::On,
    }
}

fn current_share(m: &Mutex<HubShared>, i: Input) -> Share {
    let s = m.lock().unwrap_or_else(|e| e.into_inner());
    match i {
        Input::System => s.state.system.clone(),
        Input::Microphone => s.state.mic.clone(),
    }
}

// ---- a test sound: a tone with a click at every whole second ------------------------------

/// A 440 Hz tone at -20 dBFS with a 10 ms 2 kHz click starting at every whole
/// second since `origin` - the moments a test checks the picture's flashes
/// against. Delivered in chunks of `chunk` samples at `rate`, paced in real time.
pub fn test_sound(
    origin: Instant,
    rate: u32,
    channels: usize,
    chunk: usize,
    sink: Sink,
) -> Box<dyn Running> {
    struct Stop(Arc<AtomicBool>);
    impl Drop for Stop {
        fn drop(&mut self) {
            self.0.store(true, Ordering::Relaxed);
        }
    }
    let stop = Arc::new(AtomicBool::new(false));
    let s2 = stop.clone();
    std::thread::Builder::new()
        .name("test-sound".into())
        .spawn(move || {
            let start = Instant::now();
            let mut n: u64 = ((start - origin.min(start)).as_secs_f64() * rate as f64) as u64;
            let mut sent = 0u64;
            while !s2.load(Ordering::Relaxed) {
                let at = start + Duration::from_secs_f64(sent as f64 / rate as f64);
                let mut v = Vec::with_capacity(chunk * channels);
                for k in 0..chunk as u64 {
                    let i = n + k;
                    let t = i as f64 / rate as f64;
                    let in_click = (i % rate as u64) < (rate as u64 / 100);
                    let s = if in_click {
                        0.5 * (2.0 * std::f64::consts::PI * 2000.0 * t).sin()
                    } else {
                        0.1 * (2.0 * std::f64::consts::PI * 440.0 * t).sin()
                    } as f32;
                    for _ in 0..channels {
                        v.push(s);
                    }
                }
                sink(Chunk {
                    samples: v,
                    rate,
                    channels,
                    at,
                });
                n += chunk as u64;
                sent += chunk as u64;
                let next = start + Duration::from_secs_f64(sent as f64 / rate as f64);
                if let Some(d) = next.checked_duration_since(Instant::now()) {
                    std::thread::sleep(d);
                }
            }
        })
        .ok();
    Box::new(Stop(stop))
}

#[cfg(test)]
mod tests;
