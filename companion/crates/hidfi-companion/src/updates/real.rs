//! The real I/O behind the update traits: HTTPS downloads, the signed link to a
//! board, the relay a board without internet downloads from, and the cable.

use std::io::{BufRead, BufReader, Write};
use std::net::{IpAddr, TcpListener, TcpStream, UdpSocket};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use hidfi_core::pairing::{self, PairKey};
use hidfi_core::proto::{RawReply, Request};
use hidfi_flash::flasher::Flasher;
use hidfi_flash::serial::{self, Kind, Serial};
use hidfi_flash::update::{self as flash, Connect, Progress, Step};
use serde_json::Value;

use super::{BoardLink, CablePlan, Fetch, Job, RelayStarter, Seen, Shared};

pub struct UreqFetch {
    agent: ureq::Agent,
}

impl UreqFetch {
    pub fn new() -> UreqFetch {
        let agent = ureq::Agent::config_builder()
            .timeout_connect(Some(Duration::from_secs(15)))
            .timeout_per_read(Some(Duration::from_secs(60)))
            .user_agent(concat!("HID-Fi-Companion/", env!("CARGO_PKG_VERSION")))
            .build()
            .into();
        UreqFetch { agent }
    }
}

impl Default for UreqFetch {
    fn default() -> Self {
        Self::new()
    }
}

impl Fetch for UreqFetch {
    fn get(&self, url: &str, cap: usize) -> Result<Vec<u8>, String> {
        let mut r = self.agent.get(url).call().map_err(|e| e.to_string())?;
        r.body_mut().with_config().limit(cap as u64).read_to_vec().map_err(|e| e.to_string())
    }
}

/// Status and signed update requests to one paired board.
pub struct HttpBoardLink {
    pub id: String,
    pub key: PairKey,
    agent: ureq::Agent,
}

impl HttpBoardLink {
    pub fn new(id: String, key: PairKey) -> HttpBoardLink {
        let agent = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(8)))
            .http_status_as_error(false)
            .build()
            .into();
        HttpBoardLink { id, key, agent }
    }

    fn post(&self, base: &str, req: &Request) -> Result<RawReply, String> {
        let mut r = self
            .agent
            .post(&format!("{base}/api/companion"))
            .header("Content-Type", "application/json")
            .send(req.to_body())
            .map_err(|e| e.to_string())?;
        let code = r.status().as_u16();
        let body = r.body_mut().read_to_string().map_err(|e| e.to_string())?;
        RawReply::parse_http(code, &body).map_err(|e| e.to_string())
    }
}

impl BoardLink for HttpBoardLink {
    fn status(&self, base: &str) -> Result<Value, String> {
        let mut r = self.agent.get(&format!("{base}/api/status")).call().map_err(|e| e.to_string())?;
        let body = r.body_mut().read_to_string().map_err(|e| e.to_string())?;
        serde_json::from_str(&body).map_err(|e| e.to_string())
    }

    fn ota(&self, base: &str, act: &str, url: &str) -> Result<Option<String>, String> {
        // Once more on a stale challenge: the board acted on nothing, and this
        // computer's heartbeat may have asked for challenges at the same moment.
        let stale = hidfi_core::proto::Refusal::StaleChallenge.to_string();
        let mut last = String::new();
        for _ in 0..2 {
            let chal = self.post(base, &Request::Chal { id: self.id.clone() })?.chal.ok_or("no challenge")?;
            let mac = hex::encode(pairing::ota_mac(&self.key, &self.id, act, url, &chal));
            match self.post(base, &Request::Ota { id: self.id.clone(), act: act.into(), url: url.into(), chal, mac }) {
                Ok(r) => return Ok(r.done),
                Err(e) if e == stale => last = e,
                Err(e) => return Err(e),
            }
        }
        Err(last)
    }
}

// ---- the relay ------------------------------------------------------------------------

/// Serves exactly the release's files to exactly one board, until dropped.
pub struct Relay;

struct RelayGuard(Arc<AtomicBool>);
impl Drop for RelayGuard {
    fn drop(&mut self) {
        self.0.store(true, Ordering::SeqCst);
    }
}

/// This computer's address on the way to `host` (no packet is sent).
fn local_ip_towards(host: &str) -> Option<IpAddr> {
    let s = UdpSocket::bind("0.0.0.0:0").ok()?;
    s.connect((host, 80)).ok()?;
    Some(s.local_addr().ok()?.ip())
}

fn host_of(base: &str) -> &str {
    let h = base.trim_start_matches("http://").trim_start_matches("https://");
    h.split(['/', ':']).next().unwrap_or(h)
}

impl RelayStarter for Relay {
    fn start(&self, files: Vec<(String, Vec<u8>)>, board_base: &str) -> Result<(String, Box<dyn Send>), String> {
        let board_host = host_of(board_base).to_string();
        let board_ip: IpAddr = board_host.parse().map_err(|_| "the board's address is not an IP".to_string())?;
        let me = local_ip_towards(&board_host).ok_or("no route to the board")?;
        let l = TcpListener::bind((me, 0)).map_err(|e| e.to_string())?;
        let port = l.local_addr().map_err(|e| e.to_string())?.port();
        l.set_nonblocking(true).map_err(|e| e.to_string())?;
        let stop = Arc::new(AtomicBool::new(false));
        let s2 = stop.clone();
        let files = Arc::new(files);
        std::thread::Builder::new()
            .name("relay".into())
            .spawn(move || {
                // At most 30 minutes, whatever happens.
                let end = Instant::now() + Duration::from_secs(30 * 60);
                while !s2.load(Ordering::SeqCst) && Instant::now() < end {
                    match l.accept() {
                        Ok((c, peer)) if peer.ip() == board_ip => {
                            let f = files.clone();
                            let _ = std::thread::Builder::new().name("relay-conn".into()).spawn(move || serve(c, &f));
                        }
                        Ok(_) => {} // anyone else: closed unanswered
                        Err(_) => std::thread::sleep(Duration::from_millis(100)),
                    }
                }
            })
            .map_err(|e| e.to_string())?;
        Ok((format!("http://{me}:{port}/"), Box::new(RelayGuard(stop))))
    }
}

/// One request: GET /<name>, with "Range: bytes=N-" (the board resumes with it).
fn serve(c: TcpStream, files: &[(String, Vec<u8>)]) {
    let _ = c.set_nonblocking(false);
    let _ = c.set_read_timeout(Some(Duration::from_secs(20)));
    let _ = c.set_write_timeout(Some(Duration::from_secs(60)));
    let mut rd = BufReader::new(match c.try_clone() {
        Ok(x) => x,
        Err(_) => return,
    });
    let mut line = String::new();
    if rd.read_line(&mut line).is_err() || line.len() > 512 {
        return;
    }
    let path = line.split_whitespace().nth(1).unwrap_or("").trim_start_matches('/').to_string();
    let mut from = 0usize;
    for _ in 0..64 {
        let mut h = String::new();
        if rd.read_line(&mut h).is_err() || h.trim().is_empty() {
            break;
        }
        let lower = h.to_ascii_lowercase();
        if let Some(r) = lower.strip_prefix("range:") {
            from = r.trim().trim_start_matches("bytes=").trim_end_matches('-').parse().unwrap_or(0);
        }
    }
    let mut w = c;
    let Some((_, body)) = files.iter().find(|(n, _)| *n == path) else {
        let _ = w.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
        return;
    };
    let from = from.min(body.len());
    let head = if from > 0 {
        format!("HTTP/1.1 206 Partial Content\r\nContent-Length: {}\r\nContent-Range: bytes {}-{}/{}\r\nConnection: close\r\n\r\n",
                body.len() - from, from, body.len().saturating_sub(1), body.len())
    } else {
        format!("HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len())
    };
    let _ = w.write_all(head.as_bytes());
    let _ = w.write_all(&body[from..]);
    let _ = w.flush();
    // "Connection: close" said, now done: the board's client reads to the end.
    let _ = w.shutdown(std::net::Shutdown::Write);
}

// ---- the cable ------------------------------------------------------------------------

fn mac_bytes(mac: &str) -> Option<[u8; 6]> {
    let h: String = mac.chars().filter(|c| c.is_ascii_hexdigit()).collect();
    let mut m = [0u8; 6];
    hex::decode_to_slice(&h, &mut m).ok()?;
    Some(m)
}

fn same_mac(a: &str, b: &str) -> bool {
    mac_bytes(a).is_some() && mac_bytes(a) == mac_bytes(b)
}

pub fn com_ports() -> Vec<String> {
    serial::candidates().into_iter().filter(|c| c.kind == Kind::Com).map(|c| c.path).collect()
}

/// A cable update, checkpointed in `journal` (resumed if one is there for this
/// board and release). `link`/`base`: the board over the network, when it answers.
#[allow(clippy::too_many_arguments)]
pub fn cable_job(plan: CablePlan, mac: &str, link: Option<(&dyn BoardLink, &str)>, image: &[u8], table: &[u8],
                 journal: &Path, sh: &Shared) {
    let fail = |why: String| sh.set(Job::Failed { why });
    let Some(m) = mac_bytes(mac) else { return fail("not a board MAC".into()) };
    let (path, connect) = match &plan {
        CablePlan::Com(p) => (p.clone(), Connect::Reset),
        CablePlan::Native => {
            let Some((l, base)) = link else { return fail("the board is not reachable to ask".into()) };
            sh.set(Job::Cable { step: "restarting into update mode".into(), bytes: 0, total: 0 });
            if let Err(e) = l.ota(base, "download_mode", "") {
                return fail(format!("the board refused update mode: {e}"));
            }
            let end = Instant::now() + Duration::from_secs(20);
            let found = loop {
                if let Some(c) = serial::candidates().into_iter().find(|c| matches!(&c.kind, Kind::Loader { mac: x } if same_mac(x, mac))) {
                    break Some(c.path);
                }
                if Instant::now() > end {
                    break None;
                }
                std::thread::sleep(Duration::from_millis(250));
            };
            match found {
                Some(p) => (p, Connect::InLoader),
                None => return fail("the board did not appear in update mode - unplug and replug its USB port".into()),
            }
        }
    };
    let port = match Serial::open(&path, 115_200) {
        Ok(p) => p,
        Err(e) => return fail(format!("could not open {path}: {e}")),
    };
    let mut f = Flasher::new(port);
    let mut on = |step: Step, n: usize, total: usize| {
        sh.set(Job::Cable { step: step_words(step).into(), bytes: n as u64, total: total as u64 });
    };
    let cancel = || sh.cancel.load(Ordering::SeqCst);
    let rel = flash::Release { image, table };
    match flash::run(&mut f, &m, connect, &rel, journal, Progress { cancel: &cancel, on: &mut on }) {
        Ok(j) => {
            sh.set(Job::Verifying);
            let to = j.to.clone();
            // The COM port stays open (a new open resets the board, and a reset
            // before the self-test rolls it back): read the firmware's status on it.
            let verdict = match connect {
                Connect::Reset => watch_on_port(f.port, &to),
                Connect::InLoader => {
                    drop(f);
                    link.and_then(|(l, base)| watch_on_network(l, base, &to))
                }
            };
            match verdict {
                Some(true) => sh.set(Job::Done { to: to.trim_start_matches("hid_fi_v").into() }),
                Some(false) => sh.set(Job::RolledBack),
                None => fail("written; the board did not report back - check its dashboard".into()),
            }
        }
        Err(flash::UpdateError::Cancelled) => sh.set(Job::Stopped),
        Err(e) => fail(e.to_string()),
    }
}

fn step_words(s: Step) -> &'static str {
    match s {
        Step::Planned | Step::Preflighted => "reading the board",
        Step::ImageWritten => "writing the firmware",
        Step::Switched => "choosing the new firmware",
        Step::TableWritten => "switching to the two-slot layout",
        _ => "finishing",
    }
}

/// Some(true): the new firmware was kept; Some(false): the board went back.
fn watch_on_port(mut port: Serial, to: &str) -> Option<bool> {
    use hidfi_flash::port::Port;
    let end = Instant::now() + Duration::from_secs(180);
    let (mut line, mut asked) = (Vec::new(), Instant::now() - Duration::from_secs(10));
    while Instant::now() < end {
        if asked.elapsed() > Duration::from_secs(3) {
            let _ = port.write_all(b"{\"cmd\":\"ota_status\"}\n");
            asked = Instant::now();
        }
        let mut b = [0u8; 256];
        let n = port.read(&mut b, 100).unwrap_or(0);
        for &c in &b[..n] {
            if c != b'\n' {
                line.push(c);
                continue;
            }
            let v: Value = serde_json::from_slice(&line).unwrap_or_default();
            line.clear();
            if let Some(k) = kept(&v, to) {
                return Some(k);
            }
        }
    }
    None
}

fn watch_on_network(link: &dyn BoardLink, base: &str, to: &str) -> Option<bool> {
    let end = Instant::now() + Duration::from_secs(180);
    while Instant::now() < end {
        if let Ok(v) = link.status(base) {
            if let Some(k) = kept(&v, to) {
                return Some(k);
            }
        }
        std::thread::sleep(Duration::from_secs(2));
    }
    None
}

fn kept(v: &Value, to: &str) -> Option<bool> {
    let s = Seen::from_status(v);
    if s.state.as_deref() != Some("valid") {
        return None;
    }
    let fw = s.firmware.as_deref().and_then(hidfi_core::ota::parse_version);
    Some(fw.is_some() && fw == hidfi_core::ota::parse_version(to))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;

    #[test]
    fn the_relay_serves_its_files_with_range_and_nothing_else() {
        let files = vec![("hidfi-ota.json".to_string(), b"{\"m\":1}".to_vec()), ("img.bin".to_string(), (0..=255u8).collect())];
        // The "board" is this computer itself.
        let (url, guard) = Relay.start(files, "http://127.0.0.1").unwrap();
        let get = |path: &str, range: Option<usize>| -> (u16, Vec<u8>) {
            let mut c = TcpStream::connect(url.trim_start_matches("http://").trim_end_matches('/')).unwrap();
            let r = range.map(|n| format!("Range: bytes={n}-\r\n")).unwrap_or_default();
            write!(c, "GET /{path} HTTP/1.1\r\nHost: x\r\n{r}\r\n").unwrap();
            let mut all = Vec::new();
            c.read_to_end(&mut all).unwrap();
            let at = all.windows(4).position(|w| w == b"\r\n\r\n").unwrap();
            let code = String::from_utf8_lossy(&all[9..12]).parse().unwrap();
            (code, all[at + 4..].to_vec())
        };
        assert_eq!(get("hidfi-ota.json", None), (200, b"{\"m\":1}".to_vec()));
        assert_eq!(get("img.bin", Some(250)), (206, vec![250, 251, 252, 253, 254, 255]));
        assert_eq!(get("../etc/passwd", None).0, 404);
        assert_eq!(get("nothing", None).0, 404);
        drop(guard);
    }

    #[test]
    fn macs_compare_in_any_spelling() {
        assert!(same_mac("B8:1F:3F:DA:1A:2C", "b81f3fda1a2c"));
        assert!(!same_mac("B8:1F:3F:DA:1A:2C", "B8:1F:3F:DA:1A:2D"));
        assert!(!same_mac("", ""), "nothing is never a match");
    }

    #[test]
    fn kept_only_when_valid_and_the_new_version() {
        let v = |fw: &str, st: &str| serde_json::json!({"firmware": fw, "ota_state": st, "ota_layout": "two_slot"});
        assert_eq!(kept(&v("hid_fi_v4.1.0", "valid"), "hid_fi_v4.1.0"), Some(true));
        assert_eq!(kept(&v("hid_fi_v4.0.4", "valid"), "hid_fi_v4.1.0"), Some(false));
        assert_eq!(kept(&v("hid_fi_v4.1.0", "pending_verify"), "hid_fi_v4.1.0"), None);
    }
}
