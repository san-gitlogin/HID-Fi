use super::*;
use std::sync::atomic::AtomicUsize;
use std::sync::mpsc::sync_channel;

fn tone(rate: u32, channels: usize, freq: f64, n: usize, offset: usize) -> Vec<f32> {
    (0..n)
        .flat_map(|i| {
            let s = (0.3 * (2.0 * std::f64::consts::PI * freq * (i + offset) as f64 / rate as f64).sin()) as f32;
            std::iter::repeat_n(s, channels)
        })
        .collect()
}

/// Zero crossings of the left channel, per second: twice the frequency.
fn freq_of(stereo: &[f32], rate: u32) -> f64 {
    let l: Vec<f32> = stereo.iter().step_by(2).copied().collect();
    let z = l.windows(2).filter(|w| (w[0] < 0.0) != (w[1] < 0.0)).count();
    z as f64 / 2.0 / (l.len() as f64 / rate as f64)
}

fn chunk(samples: Vec<f32>, rate: u32, channels: usize, at: Instant) -> Chunk {
    Chunk { samples, rate, channels, at }
}

// ---- conform ----------------------------------------------------------------------------
#[test]
fn every_layout_becomes_stereo() {
    assert_eq!(to_stereo(&[0.5, -0.5], 1), vec![0.5, 0.5, -0.5, -0.5], "mono to both sides");
    assert_eq!(to_stereo(&[0.1, 0.2], 2), vec![0.1, 0.2]);
    // 5.1: FL FR FC LFE BL BR - centre and surrounds at -3 dB, no LFE.
    let k = std::f32::consts::FRAC_1_SQRT_2;
    let s = to_stereo(&[1.0, 0.0, 0.0, 1.0, 0.0, 0.0], 6);
    assert_eq!(s, vec![1.0, 0.0], "the LFE is left out");
    let s = to_stereo(&[0.0, 0.0, 1.0, 0.0, 0.0, 0.0], 6);
    assert!((s[0] - k).abs() < 1e-6 && (s[1] - k).abs() < 1e-6, "the centre to both sides: {s:?}");
    let s = to_stereo(&[0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0], 8);
    assert!((s[0] - k).abs() < 1e-6 && (s[1] - k).abs() < 1e-6, "7.1 surrounds: {s:?}");
    assert!(to_stereo(&[], 0).is_empty());
}

#[test]
fn a_44_1_khz_device_becomes_48_khz_at_the_same_pitch() {
    let mut c = Conform::new(44_100, false).unwrap();
    let mut out = VecDeque::new();
    let t0 = Instant::now();
    let n = 441; // 10 ms chunks, 10 s
    for k in 0..1000 {
        c.push(&chunk(tone(44_100, 2, 1000.0, n, k * n), 44_100, 2, t0), &mut out);
    }
    let v: Vec<f32> = out.into_iter().collect();
    let frames = v.len() / 2;
    assert!((frames as f64 - 480_000.0).abs() < 480_000.0 * 0.002, "10 s at 48 kHz: {frames}");
    let f = freq_of(&v[48_000..], RATE);
    assert!((f - 1000.0).abs() < 2.0, "1 kHz stays 1 kHz: {f}");
}

#[test]
fn a_48_khz_system_sound_passes_untouched() {
    let mut c = Conform::new(RATE, false).unwrap();
    let mut out = VecDeque::new();
    let x = tone(RATE, 2, 440.0, 480, 0);
    c.push(&chunk(x.clone(), RATE, 2, Instant::now()), &mut out);
    assert_eq!(out.into_iter().collect::<Vec<_>>(), x, "no resampler, no delay");
}

// ---- mixer ------------------------------------------------------------------------------
#[test]
fn a_frame_leaves_the_moment_ten_ms_exist() {
    let mut m = Mixer::new();
    let t0 = Instant::now();
    m.push(Input::System, &chunk(tone(RATE, 2, 440.0, 300, 0), RATE, 2, t0));
    assert!(m.pull(t0).is_empty(), "6.25 ms: not a frame yet");
    // The device delivers 1024 samples at once (macOS): two frames at once, the rest kept.
    m.push(Input::System, &chunk(tone(RATE, 2, 440.0, 1024, 300), RATE, 2, t0 + Duration::from_micros(6250)));
    let f = m.pull(t0);
    assert_eq!(f.len(), 2);
    assert_eq!(f[0].at, t0, "captured at the first sample");
    assert_eq!(f[1].at, t0 + Duration::from_millis(10), "the second, 10 ms later");
}

#[test]
fn the_microphone_alone_sets_the_pace_when_the_system_is_silent() {
    let mut m = Mixer::new();
    let t0 = Instant::now();
    for k in 0..100 {
        let at = t0 + Duration::from_millis(10 * k);
        m.push(Input::Microphone, &chunk(tone(RATE, 1, 300.0, 480, 480 * k as usize), RATE, 1, at));
        let _ = m.pull(at);
    }
    // The resampler holds a little back; frames flowed with no system sound at all.
    let mut total = 0;
    for k in 100..200 {
        let at = t0 + Duration::from_millis(10 * k);
        m.push(Input::Microphone, &chunk(tone(RATE, 1, 300.0, 480, 480 * k as usize), RATE, 1, at));
        total += m.pull(at).len();
    }
    assert!((95..=105).contains(&total), "one frame per 10 ms: {total}");
}

#[test]
fn a_drifting_microphone_stays_level_without_glitches() {
    // The microphone's clock runs 0.1 % fast against the system sound's, for two minutes.
    let mut m = Mixer::new();
    let t0 = Instant::now();
    let (mut mic_acc, mut mic_sent) = (0.0f64, 0usize);
    let mut levels = Vec::new();
    for k in 0..12_000u64 {
        let at = t0 + Duration::from_millis(10 * k);
        m.push(Input::System, &chunk(tone(RATE, 2, 440.0, 480, 480 * k as usize), RATE, 2, at));
        mic_acc += 480.0 * 1.001;
        let n = mic_acc as usize - mic_sent;
        mic_sent += n;
        m.push(Input::Microphone, &chunk(tone(RATE, 1, 300.0, n, mic_sent), RATE, 1, at));
        assert_eq!(m.pull(at).len(), 1, "one frame per 10 ms of system sound");
        levels.push(m.mic.as_ref().map(|l| l.fifo.len() / 2).unwrap_or(0));
    }
    let late = &levels[6000..];
    assert_eq!(m.resyncs, 0, "never cut back: drift is followed, not dropped");
    assert!(late.iter().all(|&l| l <= MIC_MAX), "bounded: max {}", late.iter().max().unwrap());
    assert!(m.mic_underruns < 20, "settled, the microphone keeps up: {} short frames", m.mic_underruns);
}

#[test]
fn a_stalled_microphone_is_cut_back_not_left_late() {
    let mut m = Mixer::new();
    let t0 = Instant::now();
    // 200 ms of microphone arrives at once (a stalled device catching up).
    m.push(Input::Microphone, &chunk(tone(RATE, 1, 300.0, 9600, 0), RATE, 1, t0));
    m.push(Input::System, &chunk(tone(RATE, 2, 440.0, 480, 0), RATE, 2, t0));
    m.pull(t0);
    assert_eq!(m.resyncs, 1);
    assert!(m.mic.as_ref().unwrap().fifo.len() / 2 <= MIC_TARGET + FRAME);
}

// ---- encoder ----------------------------------------------------------------------------
#[test]
fn packets_carry_continuous_rtp_time_and_jump_after_a_pause() {
    let mut e = Encoder::new().unwrap();
    let t0 = Instant::now();
    let pcm = vec![0f32; FRAME * 2];
    let p: Vec<Packet> = (0..3)
        .map(|k| e.encode(&Mixed { pcm: pcm.clone(), at: t0 + Duration::from_millis(10 * k) }).unwrap())
        .collect();
    assert_eq!(p.iter().map(|p| p.rtp).collect::<Vec<_>>(), [0, 480, 960]);
    // Nothing played for 200 ms, then sound again.
    let q = e.encode(&Mixed { pcm, at: t0 + Duration::from_millis(230) }).unwrap();
    assert_eq!(q.rtp, 230 * 48, "RTP time follows the clock across the pause");
}

#[test]
fn packets_decode_back_to_the_same_sound() {
    let mut e = Encoder::new().unwrap();
    let mut d = opus_pure::OpusDecoder::new(RATE as i32, CHANNELS).unwrap();
    let t0 = Instant::now();
    let mut out = Vec::new();
    let mut buf = vec![0f32; FRAME * 2];
    for k in 0..200 {
        let m = Mixed { pcm: tone(RATE, 2, 440.0, FRAME, FRAME * k), at: t0 + Duration::from_millis(10 * k as u64) };
        let p = e.encode(&m).unwrap();
        assert!(p.data.len() < 400, "about 160 bytes at 128 kbit/s: {}", p.data.len());
        let n = d.decode(&p.data, FRAME, &mut buf).unwrap();
        out.extend_from_slice(&buf[..n * 2]);
    }
    let f = freq_of(&out[9600..], RATE);
    assert!((f - 440.0).abs() < 2.0, "440 Hz in, 440 Hz out: {f}");
}

// ---- hub --------------------------------------------------------------------------------
struct Probe {
    opens: AtomicUsize,
    live: Arc<AtomicUsize>,
    fail_first: AtomicUsize,
}

fn probe_opener(p: Arc<Probe>) -> Opener {
    Arc::new(move |i, sink, _ended| {
        if i == Input::System && p.fail_first.load(Ordering::SeqCst) > 0 {
            p.fail_first.fetch_sub(1, Ordering::SeqCst);
            return Err("no sound device".into());
        }
        p.opens.fetch_add(1, Ordering::SeqCst);
        p.live.fetch_add(1, Ordering::SeqCst);
        struct Live(Arc<AtomicUsize>, #[allow(dead_code)] Box<dyn Running>);
        impl Drop for Live {
            fn drop(&mut self) {
                self.0.fetch_sub(1, Ordering::SeqCst);
            }
        }
        let freq_rate = if i == Input::System { (RATE, 2) } else { (44_100, 1) };
        Ok(Box::new(Live(p.live.clone(), test_sound(Instant::now(), freq_rate.0, freq_rate.1, 512, sink))) as Box<dyn Running>)
    })
}

fn wait(f: impl Fn() -> bool, ms: u64) -> bool {
    let end = Instant::now() + Duration::from_millis(ms);
    while Instant::now() < end {
        if f() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    f()
}

#[test]
fn nothing_is_captured_until_someone_listens_and_after_the_last_leaves() {
    let p = Arc::new(Probe { opens: AtomicUsize::new(0), live: Arc::default(), fail_first: AtomicUsize::new(0) });
    let hub = AudioHub::start(probe_opener(p.clone()), Settings::new(true, false));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(p.opens.load(Ordering::SeqCst), 0, "no listener, no capture");
    let (tx, rx) = sync_channel(100);
    let id = hub.subscribe(tx);
    assert!(wait(|| rx.try_recv().is_ok(), 2000), "packets flow");
    assert_eq!(p.live.load(Ordering::SeqCst), 1, "the system sound only: the microphone is off");
    hub.unsubscribe(id);
    assert!(wait(|| p.live.load(Ordering::SeqCst) == 0, 2000), "the last listener left: capture stopped");
}

#[test]
fn settings_apply_at_once_to_a_running_stream() {
    let p = Arc::new(Probe { opens: AtomicUsize::new(0), live: Arc::default(), fail_first: AtomicUsize::new(0) });
    let s = Settings::new(true, false);
    let hub = AudioHub::start(probe_opener(p.clone()), s.clone());
    let (tx, _rx) = sync_channel(1000);
    hub.subscribe(tx);
    assert!(wait(|| p.live.load(Ordering::SeqCst) == 1, 2000));
    s.mic.store(true, Ordering::SeqCst);
    assert!(wait(|| p.live.load(Ordering::SeqCst) == 2, 2000), "the microphone joined");
    assert!(wait(|| hub.state() == State { system: Share::On, mic: Share::On }, 1000));
    s.system.store(false, Ordering::SeqCst);
    s.mic.store(false, Ordering::SeqCst);
    assert!(wait(|| p.live.load(Ordering::SeqCst) == 0, 2000), "both off: nothing captured");
    assert_eq!(hub.state(), State { system: Share::Off, mic: Share::Off });
}

#[test]
fn a_capture_that_fails_says_why_and_is_tried_again() {
    let p = Arc::new(Probe { opens: AtomicUsize::new(0), live: Arc::default(), fail_first: AtomicUsize::new(1) });
    let hub = AudioHub::start(probe_opener(p.clone()), Settings::new(true, false));
    let (tx, rx) = sync_channel(1000);
    hub.subscribe(tx);
    assert!(wait(|| hub.state().system == Share::Unavailable("no sound device".into()), 1000));
    assert!(wait(|| rx.try_recv().is_ok(), RETRY.as_millis() as u64 + 2000), "plugged in again: sound");
    assert_eq!(hub.state().system, Share::On);
}

#[test]
fn a_slow_viewer_loses_packets_and_never_holds_up_another() {
    let p = Arc::new(Probe { opens: AtomicUsize::new(0), live: Arc::default(), fail_first: AtomicUsize::new(0) });
    let hub = AudioHub::start(probe_opener(p.clone()), Settings::new(true, false));
    let (slow_tx, slow_rx) = sync_channel(2);
    let (fast_tx, fast_rx) = sync_channel(1000);
    hub.subscribe(slow_tx);
    hub.subscribe(fast_tx);
    std::thread::sleep(Duration::from_millis(1000));
    let fast: Vec<_> = fast_rx.try_iter().collect();
    let slow: Vec<_> = slow_rx.try_iter().collect();
    assert!(fast.len() >= 80, "about 100 packets a second for the fast one: {}", fast.len());
    assert_eq!(slow.len(), 2, "the slow one keeps only what fits");
    assert!(fast.windows(2).all(|w| w[1].rtp == w[0].rtp + FRAME as u64), "the fast one missed none");
}
