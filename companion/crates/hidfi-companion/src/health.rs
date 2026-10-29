//! How quickly each board answers, measured only while a window shows it
//! (docs/COMPANION_WINDOW.md). The app in the tray measures nothing and wakes
//! for nothing (A3).
//!
//! The probe is `GET /api/ping`: a fixed 30-byte answer the board computes
//! nothing for, in every firmware release. The number shown is the network's
//! round trip - how long the board took to accept the connection (one round
//! trip, nothing else in it); the whole answer's time is kept too (Details). The
//! request is always sent in full: a connection opened and left silent would
//! hold the board's web server until its own timeout.
//!
//! One thread, parked while no window is open; each round asks every board at
//! once (one slow or silent board never delays another's number), every
//! [`PROBE_EVERY`].

use std::collections::{HashMap, VecDeque};
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub const PROBE_EVERY: Duration = Duration::from_secs(3);
/// The median of this many answers is the number shown.
pub const KEEP: usize = 5;
/// Unanswered probes in a row before a board shows as not answering.
pub const FAILS_OFFLINE: u32 = 2;

/// One board's link, as the window shows it.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Link {
    /// Median of the last round trips, in ms. None: nothing measured yet.
    pub ms: Option<u32>,
    /// The last whole answer (connect, ask, read), in ms.
    pub answer_ms: Option<u32>,
    pub last_ms: Option<u32>,
    /// Slowest minus quickest of the kept answers.
    pub spread_ms: Option<u32>,
    /// When it last answered (Unix time, ms): the page says "3 s ago" itself,
    /// so the view does not change every second.
    pub last_ok: Option<u64>,
    /// Probes unanswered in a row.
    pub failing: u32,
}

impl Link {
    /// Two probes in a row went unanswered.
    pub fn not_answering(&self) -> bool {
        self.failing >= FAILS_OFFLINE
    }
}

/// One probe: the round trip, and the whole answer.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Sample {
    pub rtt: Duration,
    pub answer: Duration,
}

/// Asks one board (its base URL); None when it does not answer as a board does.
pub type Probe = Arc<dyn Fn(&str) -> Option<Sample> + Send + Sync>;

#[derive(Default)]
struct Samples {
    ms: VecDeque<u32>,
    answer: Option<u32>,
    last_ok: Option<u64>,
    failing: u32,
}

impl Samples {
    fn ok(&mut self, ms: u32, answer: u32, at: u64) {
        if self.ms.len() == KEEP {
            self.ms.pop_front();
        }
        self.ms.push_back(ms);
        self.answer = Some(answer);
        self.last_ok = Some(at);
        self.failing = 0;
    }

    fn fail(&mut self) {
        self.failing = self.failing.saturating_add(1);
    }

    fn link(&self) -> Link {
        let mut s: Vec<u32> = self.ms.iter().copied().collect();
        s.sort_unstable();
        Link {
            ms: s.get(s.len() / 2).copied(),
            last_ms: self.ms.back().copied(),
            answer_ms: self.answer,
            spread_ms: s.last().zip(s.first()).map(|(a, b)| a - b),
            last_ok: self.last_ok,
            failing: self.failing,
        }
    }
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_millis() as u64)
}

/// Every board's samples, and whether anyone is looking.
#[derive(Clone, Default)]
pub struct Health {
    map: Arc<Mutex<HashMap<String, Samples>>>,
    active: Arc<AtomicBool>,
    thread: Arc<Mutex<Option<std::thread::Thread>>>,
}

impl Health {
    /// The board's link (by MAC); default when not measured.
    pub fn link(&self, mac: &str) -> Link {
        self.map
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(mac)
            .map(Samples::link)
            .unwrap_or_default()
    }

    /// A window opened (true) or the last one closed (false). Closing forgets
    /// the numbers: an old latency is worse than none.
    pub fn set_active(&self, on: bool) {
        if self.active.swap(on, Ordering::SeqCst) != on {
            if !on {
                self.map.lock().unwrap_or_else(|e| e.into_inner()).clear();
            }
            if let Some(t) = self
                .thread
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .as_ref()
            {
                t.unpark();
            }
        }
    }

    pub fn is_active(&self) -> bool {
        self.active.load(Ordering::SeqCst)
    }

    /// One probe's result, as the probe thread records it (and tests).
    pub fn record(&self, mac: &str, sample: Option<Sample>) {
        let ms = |d: Duration| d.as_millis().min(u32::MAX as u128) as u32;
        let mut m = self.map.lock().unwrap_or_else(|e| e.into_inner());
        let s = m.entry(mac.to_string()).or_default();
        match sample {
            Some(x) => s.ok(ms(x.rtt), ms(x.answer), unix_ms()),
            None => s.fail(),
        }
    }

    /// Forget boards not in `keep` (they left).
    fn retain(&self, keep: &[(String, String)]) {
        self.map
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .retain(|mac, _| keep.iter().any(|(m, _)| m == mac));
    }

    /// Starts the probe thread. `targets`: (MAC, base URL) of each board that
    /// answers now; `changed`: after each round, so the window redraws.
    pub fn start(
        &self,
        probe: Probe,
        targets: impl Fn() -> Vec<(String, String)> + Send + 'static,
        changed: impl Fn() + Send + 'static,
    ) {
        let me = self.clone();
        let t = std::thread::Builder::new()
            .name("latency".into())
            .spawn(move || loop {
                if !me.is_active() {
                    std::thread::park();
                    continue;
                }
                let round = Instant::now();
                let ts = targets();
                me.retain(&ts);
                std::thread::scope(|s| {
                    for (mac, url) in &ts {
                        let (probe, me) = (probe.clone(), &me);
                        s.spawn(move || me.record(mac, probe(url)));
                    }
                });
                if me.is_active() {
                    changed();
                } else {
                    // Closed during the round: its answers are not kept either.
                    me.map.lock().unwrap_or_else(|e| e.into_inner()).clear();
                }
                std::thread::park_timeout(PROBE_EVERY.saturating_sub(round.elapsed()));
            })
            .expect("spawning the latency thread");
        *self.thread.lock().unwrap_or_else(|e| e.into_inner()) = Some(t.thread().clone());
    }
}

/// The real probe: `GET /api/ping` over a plain connection, every step bounded
/// by `timeout`.
pub fn tcp_probe(timeout: Duration) -> Probe {
    Arc::new(move |base: &str| ping(base, timeout))
}

/// One probe of the board at `base` (`http://host[:port]`).
pub fn ping(base: &str, timeout: Duration) -> Option<Sample> {
    let rest = base.strip_prefix("http://")?;
    let hostport = rest.split('/').next()?;
    let target = if hostport.contains(':') {
        hostport.to_string()
    } else {
        format!("{hostport}:80")
    };
    let host = hostport.split(':').next()?;
    let addr = target.to_socket_addrs().ok()?.next()?;
    let t = Instant::now();
    let mut s = TcpStream::connect_timeout(&addr, timeout).ok()?;
    let rtt = t.elapsed();
    s.set_read_timeout(Some(timeout)).ok()?;
    s.set_write_timeout(Some(timeout)).ok()?;
    s.set_nodelay(true).ok()?;
    s.write_all(
        format!("GET /api/ping HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n").as_bytes(),
    )
    .ok()?;
    let mut buf = Vec::with_capacity(256);
    s.take(4096).read_to_end(&mut buf).ok()?;
    let answer = t.elapsed();
    let text = String::from_utf8_lossy(&buf);
    (text.starts_with("HTTP/1.")
        && text.split_whitespace().nth(1) == Some("200")
        && text.contains("pong"))
    .then_some(Sample { rtt, answer })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    fn sample(ms: u64) -> Option<Sample> {
        Some(Sample {
            rtt: Duration::from_millis(ms),
            answer: Duration::from_millis(ms * 2),
        })
    }

    #[test]
    fn the_number_is_the_median_of_the_last_five() {
        let h = Health::default();
        for ms in [40, 12, 900, 15, 14, 13] {
            h.record("A", sample(ms));
        }
        // 40 fell out; kept 12 900 15 14 13: one slow answer moves nothing.
        let l = h.link("A");
        assert_eq!(
            (l.ms, l.last_ms, l.spread_ms, l.answer_ms),
            (Some(14), Some(13), Some(888), Some(26))
        );
        assert!(l.last_ok.is_some() && l.failing == 0 && !l.not_answering());
    }

    #[test]
    fn two_silent_probes_in_a_row_mean_not_answering_and_one_answer_ends_it() {
        let h = Health::default();
        h.record("A", sample(20));
        h.record("A", None);
        assert!(!h.link("A").not_answering(), "one lost probe is a blip");
        h.record("A", None);
        let l = h.link("A");
        assert!(l.not_answering());
        assert_eq!(l.ms, Some(20), "the last numbers stay, shown as old");
        h.record("A", sample(25));
        assert!(!h.link("A").not_answering());
    }

    #[test]
    fn closing_the_window_forgets_the_numbers() {
        let h = Health::default();
        h.set_active(true);
        h.record("A", sample(20));
        h.set_active(false);
        assert_eq!(h.link("A"), Link::default());
    }

    /// A board's web server that answers `reply` after `delay`, `n` times.
    fn server(
        reply: &'static str,
        delay: Duration,
    ) -> (String, std::thread::JoinHandle<Vec<String>>) {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", l.local_addr().unwrap());
        let h = std::thread::spawn(move || {
            let (mut c, _) = l.accept().unwrap();
            let mut got = vec![0u8; 512];
            let n = c.read(&mut got).unwrap();
            std::thread::sleep(delay);
            let _ = c.write_all(reply.as_bytes());
            vec![String::from_utf8_lossy(&got[..n]).to_string()]
        });
        (url, h)
    }

    #[test]
    fn a_ping_sends_a_whole_request_and_takes_only_a_board_answering_pong() {
        let pong = "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\",\"reply\":\"pong\"}";
        let (url, h) = server(pong, Duration::from_millis(60));
        let s = ping(&url, Duration::from_secs(2)).expect("a board");
        let req = h.join().unwrap().remove(0);
        assert!(
            req.starts_with("GET /api/ping HTTP/1.0\r\n") && req.ends_with("\r\n\r\n"),
            "{req:?}"
        );
        // The round trip is the connect alone; the board's 60 ms is in the answer.
        assert!(s.rtt < Duration::from_millis(30), "{s:?}");
        assert!(s.answer >= Duration::from_millis(60), "{s:?}");

        let (url, h) = server(
            "HTTP/1.1 200 OK\r\n\r\n<html>a captive portal</html>",
            Duration::ZERO,
        );
        assert!(
            ping(&url, Duration::from_secs(2)).is_none(),
            "a captive portal is not the board"
        );
        h.join().unwrap();
        let (url, h) = server("HTTP/1.1 404 Not Found\r\n\r\npong", Duration::ZERO);
        assert!(ping(&url, Duration::from_secs(2)).is_none());
        h.join().unwrap();
        // Nothing listening, and an answer slower than the timeout.
        let free = TcpListener::bind("127.0.0.1:0")
            .unwrap()
            .local_addr()
            .unwrap();
        assert!(ping(&format!("http://{free}"), Duration::from_millis(300)).is_none());
        let (url, h) = server(pong, Duration::from_millis(900));
        let t = Instant::now();
        assert!(ping(&url, Duration::from_millis(300)).is_none());
        assert!(
            t.elapsed() < Duration::from_millis(800),
            "bounded by the timeout"
        );
        h.join().unwrap();
        assert!(
            ping("https://x", Duration::from_secs(1)).is_none()
                && ping("nonsense", Duration::from_secs(1)).is_none()
        );
    }

    #[test]
    fn the_thread_sleeps_until_a_window_opens_and_asks_every_board_at_once() {
        let calls = Arc::new(Mutex::new(Vec::<String>::new()));
        let c2 = calls.clone();
        let probe: Probe = Arc::new(move |u: &str| {
            c2.lock().unwrap().push(u.to_string());
            std::thread::sleep(Duration::from_millis(300));
            sample(10)
        });
        let h = Health::default();
        let rounds = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let r = rounds.clone();
        h.start(
            probe,
            || {
                vec![
                    ("A".into(), "http://a".into()),
                    ("B".into(), "http://b".into()),
                ]
            },
            move || {
                r.fetch_add(1, Ordering::SeqCst);
            },
        );
        std::thread::sleep(Duration::from_millis(200));
        assert!(calls.lock().unwrap().is_empty(), "no window: no probe");
        let t = Instant::now();
        h.set_active(true);
        while rounds.load(Ordering::SeqCst) == 0 && t.elapsed() < Duration::from_secs(3) {
            std::thread::sleep(Duration::from_millis(10));
        }
        // Two boards answering in 300 ms each: one round, not two in a row.
        assert!(
            t.elapsed() < Duration::from_millis(550),
            "{:?}",
            t.elapsed()
        );
        assert!(h.link("A").ms.is_some() && h.link("B").ms.is_some());
        h.set_active(false);
        let n = calls.lock().unwrap().len();
        std::thread::sleep(PROBE_EVERY + Duration::from_millis(500));
        assert_eq!(calls.lock().unwrap().len(), n, "closed: probing stops");
    }
}
