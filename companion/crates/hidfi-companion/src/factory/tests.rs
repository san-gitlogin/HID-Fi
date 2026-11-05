//! The three ways against a model board: what each step shows, what is sent down the
//! cable, and that every refusal ends with "Nothing was changed".
use super::*;
use crate::recovery::{DynPort, Link, Ports, RecError};
use std::collections::VecDeque;
use std::io;
use std::sync::mpsc;

const MAC: &str = "DCB4D909F7C8";
const ID: &str = "00112233445566778899aabbccddeeff";

fn mac() -> Mac {
    Mac::parse(MAC).unwrap()
}

fn key() -> PairKey {
    PairKey::from_bytes([7; 32])
}

/// The board, as far as these flows reach it.
#[derive(Default)]
struct Board {
    /// Its link is on this USB (recovery mode or a paired-reset link).
    link: bool,
    token: String,
    /// What came down the cable.
    sent: Vec<Value>,
    /// The board says no to the erase with this reply.
    refuse_erase: Option<&'static str>,
    erased: bool,
}

type Shared = Arc<Mutex<Board>>;

struct FakeNet {
    board: Shared,
    reset: Result<(), &'static str>,
    request: Result<(), &'static str>,
    statuses: Mutex<VecDeque<&'static str>>,
    link_comes: bool,
}

impl Net for FakeNet {
    fn reset(&self, _: &str, id: &str, k: &PairKey) -> Result<String, String> {
        assert_eq!((id, k.as_bytes()), (ID, key().as_bytes()));
        self.reset.map_err(str::to_string)?;
        let mut b = self.board.lock().unwrap();
        b.token = "ab".repeat(16);
        b.link = self.link_comes;
        Ok(b.token.clone())
    }
    fn request(&self, _: &str, name: &str) -> Result<String, String> {
        assert_eq!(name, "LAPTOP-X");
        self.request
            .map(|_| "cd".repeat(16))
            .map_err(str::to_string)
    }
    fn request_status(&self, _: &str, r: &str) -> Result<String, String> {
        assert_eq!(r, "cd".repeat(16));
        let s = self
            .statuses
            .lock()
            .unwrap()
            .pop_front()
            .unwrap_or("pending");
        if s == "approved" {
            self.board.lock().unwrap().erased = true;
        }
        if s == "offline" {
            return Err("timed out".into());
        }
        Ok(s.into())
    }
}

/// The link: one JSON line in, one out, as the firmware answers it.
struct FakeLink {
    board: Shared,
    out: Vec<u8>,
}

impl Port for FakeLink {
    fn write_all(&mut self, d: &[u8]) -> io::Result<()> {
        let v: Value = serde_json::from_slice(d.trim_ascii()).unwrap();
        let mut b = self.board.lock().unwrap();
        b.sent.push(v.clone());
        let reply = if v["cmd"] == "factory_reset" {
            if let Some(r) = b.refuse_erase {
                json!({"status": "error", "reply": r})
            } else {
                let want = hex::encode(hidfi_core::pairing::factory_proof(&key(), ID, &b.token));
                let ok = v["confirm"] == true || v["proof"] == want.as_str();
                if ok {
                    b.erased = true;
                    b.link = false; // it restarts
                    json!({"status": "ok", "reply": "factory_reset", "restart": true})
                } else {
                    json!({"status": "error", "reply": "bad_proof"})
                }
            }
        } else {
            json!({"status": "error", "reply": "not_allowed"})
        };
        self.out = (reply.to_string() + "\n").into_bytes();
        Ok(())
    }
    fn read(&mut self, buf: &mut [u8], _: u32) -> io::Result<usize> {
        let n = self.out.len().min(buf.len());
        buf[..n].copy_from_slice(&self.out[..n]);
        self.out.drain(..n);
        Ok(n)
    }
    fn set_dtr(&mut self, _: bool) -> io::Result<()> {
        Ok(())
    }
    fn set_rts(&mut self, _: bool) -> io::Result<()> {
        Ok(())
    }
    fn set_baud(&mut self, _: u32) -> io::Result<()> {
        Ok(())
    }
    fn sleep_ms(&mut self, _: u32) {}
}

struct FakePorts(Shared);

impl Ports for FakePorts {
    fn find(&self, _: &Mac) -> Option<String> {
        self.0.lock().unwrap().link.then(|| "/dev/fake".into())
    }
    fn open(&self, _: &str) -> Result<Link<DynPort>, RecError> {
        Link::new(DynPort(Box::new(FakeLink {
            board: self.0.clone(),
            out: vec![],
        })))
    }
}

fn quick() -> Timing {
    Timing {
        link_appears: Duration::from_millis(600),
        link_goes: Duration::from_millis(600),
        poll: Duration::from_millis(5),
        approval: Duration::from_millis(400),
    }
}

struct Rig {
    w: Worker,
    state: Arc<Mutex<Vec<ResetView>>>,
    board: Shared,
    rx: mpsc::Receiver<()>,
}

fn rig(net: impl FnOnce(Shared) -> FakeNet) -> Rig {
    let board: Shared = Arc::default();
    let n = net(board.clone());
    let b2 = board.clone();
    let usb: UsbLinks = Arc::new(move || {
        if b2.lock().unwrap().link {
            vec![mac()]
        } else {
            vec![]
        }
    });
    let state: Arc<Mutex<Vec<ResetView>>> = Arc::default();
    let (tx, rx) = mpsc::channel();
    let tx = Mutex::new(tx);
    let w = Worker::start(
        Box::new(n),
        Box::new(FakePorts(board.clone())),
        usb,
        state.clone(),
        move || {
            let _ = tx.lock().unwrap().send(());
        },
        quick(),
    );
    Rig {
        w,
        state,
        board,
        rx,
    }
}

fn net(board: Shared) -> FakeNet {
    FakeNet {
        board,
        reset: Ok(()),
        request: Ok(()),
        statuses: Mutex::new(VecDeque::new()),
        link_comes: true,
    }
}

fn job(path: Path) -> Job {
    Job {
        mac: mac(),
        path,
        base: Some("http://192.168.0.103".into()),
        id: ID.into(),
        key: Some(key()),
        name: "LAPTOP-X".into(),
        ssid: "ESP32-HID-09F7C8".into(),
    }
}

/// Runs the job; the view once it has a result.
fn finished(r: &Rig, j: Job) -> ResetView {
    r.w.run(j);
    let end = Instant::now() + Duration::from_secs(5);
    loop {
        let _ = r.rx.recv_timeout(Duration::from_millis(50));
        if let Some(v) = r
            .state
            .lock()
            .unwrap()
            .first()
            .filter(|v| v.result.is_some())
            .cloned()
        {
            return v;
        }
        assert!(
            Instant::now() < end,
            "no result: {:?}",
            r.state.lock().unwrap()
        );
    }
}

fn states(v: &ResetView) -> Vec<StepState> {
    v.steps.iter().map(|s| s.1).collect()
}

use StepState::*;

#[test]
fn paired_the_erase_goes_down_the_cable_signed_over_the_token() {
    let r = rig(net);
    let v = finished(&r, job(Path::Paired));
    assert_eq!(v.result, Some(true), "{v:?}");
    assert_eq!(states(&v), [Done, Done, Done, Done]);
    assert!(v.note.contains("ESP32-HID-09F7C8"), "{}", v.note);
    let b = r.board.lock().unwrap();
    assert!(b.erased);
    let want = hex::encode(hidfi_core::pairing::factory_proof(
        &key(),
        ID,
        &"ab".repeat(16),
    ));
    assert_eq!(b.sent, [json!({"cmd": "factory_reset", "proof": want})]);
}

#[test]
fn paired_refused_while_an_update_runs_changes_nothing() {
    let r = rig(|b| FakeNet {
        reset: Err("busy"),
        ..net(b)
    });
    let v = finished(&r, job(Path::Paired));
    assert_eq!(
        (v.result, states(&v)),
        (Some(false), vec![Failed, Todo, Todo, Todo])
    );
    assert!(
        v.note.contains("update is running") && v.note.contains("Nothing was changed"),
        "{}",
        v.note
    );
    assert!(r.board.lock().unwrap().sent.is_empty());
}

#[test]
fn paired_says_so_when_the_link_never_comes() {
    let r = rig(|b| FakeNet {
        link_comes: false,
        ..net(b)
    });
    let v = finished(&r, job(Path::Paired));
    assert_eq!(states(&v), [Done, Failed, Todo, Todo]);
    assert!(v.note.contains("Nothing was changed"), "{}", v.note);
}

#[test]
fn paired_a_refused_proof_changes_nothing() {
    let r = rig(net);
    r.board.lock().unwrap().refuse_erase = Some("bad_proof");
    let v = finished(&r, job(Path::Paired));
    assert_eq!(states(&v), [Done, Done, Failed, Todo]);
    assert!(!r.board.lock().unwrap().erased);
    assert!(v.note.contains("Nothing was changed"), "{}", v.note);
}

#[test]
fn paired_without_an_address_or_key_never_asks() {
    let r = rig(net);
    let v = finished(
        &r,
        Job {
            base: None,
            ..job(Path::Paired)
        },
    );
    assert_eq!(states(&v), [Failed, Todo, Todo, Todo]);
}

#[test]
fn request_approved_on_the_phone() {
    let r = rig(|b| {
        let n = net(b);
        *n.statuses.lock().unwrap() = VecDeque::from(["pending", "pending", "approved"]);
        n
    });
    let v = finished(&r, job(Path::Request));
    assert_eq!((v.result, states(&v)), (Some(true), vec![Done, Done, Done]));
    assert!(v.note.starts_with("Approved on your phone."), "{}", v.note);
    assert!(r.board.lock().unwrap().erased);
}

#[test]
fn request_declined_changes_nothing() {
    let r = rig(|b| {
        let n = net(b);
        *n.statuses.lock().unwrap() = VecDeque::from(["pending", "declined"]);
        n
    });
    let v = finished(&r, job(Path::Request));
    assert_eq!(
        (v.result, states(&v)),
        (Some(false), vec![Done, Failed, Todo])
    );
    assert_eq!(v.note, "Declined on the dashboard. Nothing was changed.");
}

#[test]
fn request_nobody_approves_in_time() {
    let r = rig(net); // always pending
    let v = finished(&r, job(Path::Request));
    assert_eq!(v.result, Some(false));
    assert!(
        v.note.contains("in time") && v.note.contains("Nothing was changed"),
        "{}",
        v.note
    );
}

#[test]
fn request_to_a_board_without_a_pin_says_where_to_go() {
    let r = rig(|b| FakeNet {
        request: Err("pin_required"),
        ..net(b)
    });
    let v = finished(&r, job(Path::Request));
    assert_eq!(states(&v), [Failed, Todo, Todo]);
    assert!(v.note.contains("Settings, Factory reset"), "{}", v.note);
}

#[test]
fn request_while_waiting_the_note_says_what_to_do_and_time_counts_down() {
    let r = rig(net);
    r.w.run(job(Path::Request));
    let end = Instant::now() + Duration::from_secs(3);
    let v = loop {
        let _ = r.rx.recv_timeout(Duration::from_millis(20));
        if let Some(v) = r
            .state
            .lock()
            .unwrap()
            .first()
            .filter(|v| v.left_s.is_some())
            .cloned()
        {
            break v;
        }
        assert!(Instant::now() < end);
    };
    assert_eq!(states(&v)[..2], [Done, Doing]);
    assert!(
        v.note.contains("approve \"LAPTOP-X\"") && v.note.contains("Nothing changes until you do"),
        "{}",
        v.note
    );
}

#[test]
fn recovery_erases_with_confirm() {
    let r = rig(net);
    r.board.lock().unwrap().link = true;
    let v = finished(&r, job(Path::Recovery));
    assert_eq!((v.result, states(&v)), (Some(true), vec![Done, Done]));
    assert_eq!(
        r.board.lock().unwrap().sent,
        [json!({"cmd": "factory_reset", "confirm": true})]
    );
}

#[test]
fn a_board_being_reset_is_claimed_then_let_go_and_a_finished_result_can_be_closed() {
    let r = rig(|b| {
        let n = net(b);
        *n.statuses.lock().unwrap() = VecDeque::from(["pending", "pending", "pending", "approved"]);
        n
    });
    r.w.run(job(Path::Request));
    let end = Instant::now() + Duration::from_secs(3);
    while r.w.claimed().is_empty() {
        assert!(Instant::now() < end);
        std::thread::sleep(Duration::from_millis(2));
    }
    assert_eq!(r.w.claimed(), [MAC]);
    r.w.run(job(Path::Request)); // a second start while it runs is ignored
    let v = loop {
        let _ = r.rx.recv_timeout(Duration::from_millis(20));
        if let Some(v) = r
            .state
            .lock()
            .unwrap()
            .first()
            .filter(|v| v.result.is_some())
            .cloned()
        {
            break v;
        }
        assert!(Instant::now() < end);
    };
    assert_eq!(v.result, Some(true));
    std::thread::sleep(Duration::from_millis(30));
    assert!(r.w.claimed().is_empty());
    assert_eq!(r.state.lock().unwrap().len(), 1);
    r.w.clear(MAC);
    let end = Instant::now() + Duration::from_secs(2);
    while !r.state.lock().unwrap().is_empty() {
        assert!(Instant::now() < end);
        std::thread::sleep(Duration::from_millis(5));
    }
}
