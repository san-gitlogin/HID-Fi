//! `--check-sound system|mic SECS OUT`: captures this computer's sound (or its
//! microphone) as the app does, and writes one JSON line a second to OUT -
//! chunks, rate, channels, RMS, the dominant frequency, the oldest chunk's age.
//! Run as the app (`open -n -W ... --args --check-sound ...`) so it has the
//! app's permissions; the real-capture tests (companion/tests/sound_mac.py) use it.

use hidfi_stream::audio::{Chunk, Input};
use std::io::Write;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub fn run(args: &[String]) {
    let which = if args.first().map(String::as_str) == Some("mic") {
        Input::Microphone
    } else {
        Input::System
    };
    let secs: u64 = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(5);
    let out = args.get(2).cloned().unwrap_or_else(|| "/dev/stdout".into());
    let Ok(mut f) = std::fs::File::create(&out) else {
        return;
    };
    let got: Arc<Mutex<Vec<(Chunk, Instant)>>> = Arc::default();
    let g = got.clone();
    let ended_log: Arc<Mutex<Vec<String>>> = Arc::default();
    let e = ended_log.clone();
    let cap = hidfi_stream::audio_capture(
        which,
        Arc::new(move |c| {
            g.lock()
                .unwrap_or_else(|e| e.into_inner())
                .push((c, Instant::now()))
        }),
        Arc::new(move |why| e.lock().unwrap_or_else(|e| e.into_inner()).push(why)),
    );
    let _cap = match cap {
        Ok(c) => c,
        Err(e) => {
            let _ = writeln!(f, "{}", serde_json::json!({"error": e}));
            return;
        }
    };
    let _ = writeln!(f, "{}", serde_json::json!({"started": true}));
    for _ in 0..secs {
        std::thread::sleep(Duration::from_secs(1));
        let v: Vec<(Chunk, Instant)> =
            std::mem::take(&mut *got.lock().unwrap_or_else(|e| e.into_inner()));
        let (rate, ch) = v
            .first()
            .map(|(c, _)| (c.rate, c.channels))
            .unwrap_or((0, 0));
        let left: Vec<f32> = v
            .iter()
            .flat_map(|(c, _)| c.samples.chunks(c.channels.max(1)).map(|fr| fr[0]))
            .collect();
        let rms = (left.iter().map(|x| x * x).sum::<f32>() / left.len().max(1) as f32).sqrt();
        let zc = left
            .windows(2)
            .filter(|w| (w[0] < 0.0) != (w[1] < 0.0))
            .count();
        let freq = if rate > 0 && !left.is_empty() {
            zc as f64 / 2.0 / (left.len() as f64 / rate as f64)
        } else {
            0.0
        };
        let age = v
            .iter()
            .map(|(c, got)| got.duration_since(c.at).as_millis() as u64)
            .max()
            .unwrap_or(0);
        let frames: Vec<usize> = v
            .iter()
            .map(|(c, _)| c.samples.len() / c.channels.max(1))
            .collect();
        let _ = writeln!(
            f,
            "{}",
            serde_json::json!({"chunks": v.len(), "rate": rate, "channels": ch, "rms": rms, "freq": freq.round(),
                               "age_ms_max": age, "frames_min": frames.iter().min(), "frames_max": frames.iter().max(),
                               "ended": ended_log.lock().unwrap_or_else(|e| e.into_inner()).clone(),
                               "delivered": diag().0, "unreadable": diag().1, "last_format": diag().2})
        );
    }
}

/// ScreenCaptureKit's own counts on macOS: buffers delivered, unreadable, the last
/// unreadable format (flags << 32 | bits << 16 | channels).
fn diag() -> (usize, usize, String) {
    #[cfg(target_os = "macos")]
    {
        use hidfi_stream::sck_audio as s;
        use std::sync::atomic::Ordering::Relaxed;
        let f = s::LAST_FORMAT.load(Relaxed);
        (
            s::DELIVERED.load(Relaxed),
            s::UNREADABLE.load(Relaxed),
            format!(
                "flags={:#x} bits={} ch={} status={}",
                f >> 32,
                (f >> 16) & 0xffff,
                f & 0xffff,
                s::LAST_STATUS.load(Relaxed)
            ),
        )
    }
    #[cfg(not(target_os = "macos"))]
    {
        (0, 0, String::new())
    }
}
