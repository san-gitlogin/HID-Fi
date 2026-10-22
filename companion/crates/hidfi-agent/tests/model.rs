//! The agent against the board model, over real HTTP. The model is held to the
//! firmware by tests/board/test_sim_contract.py, so a pass here means the Rust
//! client and the board agree on the whole pairing and heartbeat exchange.
//!
//! Needs python3 (the model is the suite's own tests/support/board_sim.py).

use hidfi_agent::env::{Http, SystemClock};
use hidfi_agent::real::UreqHttp;
use hidfi_agent::session::{Identity, Listener, Session, Status};
use hidfi_core::pairing;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

struct Model {
    child: Child,
    base: String,
    http: UreqHttp,
}

impl Model {
    fn start(pin: &str) -> Model {
        let script = concat!(env!("CARGO_MANIFEST_DIR"), "/../../tests/model_board.py");
        let mut child = Command::new("python3")
            .arg(script)
            .arg(pin)
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .expect("python3 is needed for the model tests");
        let mut line = String::new();
        BufReader::new(child.stdout.take().unwrap())
            .read_line(&mut line)
            .unwrap();
        let port: u16 = line.trim().parse().expect("model prints its port");
        Model {
            child,
            base: format!("http://127.0.0.1:{port}"),
            http: UreqHttp::new(Duration::from_secs(3)),
        }
    }

    fn post(&self, path: &str, v: Value) -> Value {
        let (_, b) = self
            .http
            .post_json(&format!("{}{path}", self.base), &v.to_string())
            .unwrap();
        serde_json::from_str(&b).unwrap()
    }

    fn dash(&self, v: Value) -> Value {
        self.post("/__dash", v)
    }
}

impl Drop for Model {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn session(m: &Model, name: &str) -> Session {
    let mut id = [0u8; 16];
    getrandom::fill(&mut id).unwrap();
    Session::new(
        Identity {
            id,
            name: name.into(),
            key: None,
        },
        m.base.clone(),
    )
}

/// Steps until `want` holds, without sleeping the waits the session asks for
/// (the model has no wall clock to wait on). Fails after `n` steps.
fn run_until(s: &mut Session, m: &Model, n: usize, want: impl Fn(&Status) -> bool) -> Status {
    for _ in 0..n {
        s.step(&m.http, &SystemClock);
        if want(s.status()) {
            return s.status().clone();
        }
    }
    panic!("never reached the wanted state; last: {:?}", s.status());
}

fn listener() -> Listener {
    Listener {
        port: 47800,
        fp: "ab".repeat(32),
    }
}

fn pair(m: &Model, s: &mut Session) {
    run_until(s, m, 3, |st| *st == Status::WaitingForApproval);
    let id = hex::encode(s.identity.id);
    let list = m.dash(json!({"cmd": "companion_list"}));
    assert_eq!(list["pending"][0]["id"], json!(id));
    assert_eq!(
        m.dash(json!({"cmd": "companion_pair", "id": id}))["state"],
        "confirm"
    );
    let Status::ShowCode(code) = run_until(s, m, 3, |st| matches!(st, Status::ShowCode(_))) else {
        unreachable!()
    };
    assert_eq!(code.len(), 6);
    let r = m.dash(json!({"cmd": "companion_pair", "id": id, "code": code}));
    assert_eq!(r["state"], "paired", "{r}");
    for _ in 0..3 {
        if s.is_paired() {
            return;
        }
        s.step(&m.http, &SystemClock);
    }
    assert!(s.is_paired(), "never paired: {:?}", s.status());
}

#[test]
fn pairs_comes_online_and_its_tickets_verify() {
    let m = Model::start("4826");
    let mut s = session(&m, "Test PC");
    pair(&m, &mut s);
    assert!(s.is_paired() && s.key_changed && s.identity.key.is_some());
    s.set_listener(listener());
    assert_eq!(
        run_until(&mut s, &m, 3, |st| *st == Status::Online),
        Status::Online
    );

    let id = hex::encode(s.identity.id);
    let t = m.dash(json!({"cmd": "screen_ticket", "id": id}));
    assert_eq!(t["reply"], "screen_ticket", "{t}");
    assert_eq!(t["port"], 47800);
    assert_eq!(t["fp"], json!("ab".repeat(32)));
    let key = s.identity.key.clone().unwrap();
    let tn = t["tn"].as_str().unwrap();
    assert!(
        s.nonces.is_live(tn, Instant::now()),
        "the ticket names a nonce this app announced"
    );
    let ticket = hex::decode(t["ticket"].as_str().unwrap()).unwrap();
    assert!(pairing::ticket_ok(
        &key,
        tn,
        t["vn"].as_str().unwrap(),
        &ticket
    ));
    // Wrong vn: refused.
    assert!(!pairing::ticket_ok(&key, tn, &"00".repeat(16), &ticket));
}

#[test]
fn a_wrong_code_three_times_stops_and_waits_for_the_user() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    run_until(&mut s, &m, 3, |st| *st == Status::WaitingForApproval);
    let id = hex::encode(s.identity.id);
    m.dash(json!({"cmd": "companion_pair", "id": id}));
    let Status::ShowCode(code) = run_until(&mut s, &m, 3, |st| matches!(st, Status::ShowCode(_)))
    else {
        unreachable!()
    };
    let wrong = format!("{:06}", (code.parse::<u32>().unwrap() + 1) % 1_000_000);
    for _ in 0..3 {
        m.dash(json!({"cmd": "companion_pair", "id": id, "code": wrong}));
    }
    assert_eq!(
        run_until(&mut s, &m, 3, |st| *st == Status::PairingFailed),
        Status::PairingFailed
    );
    // It does not start again by itself...
    s.step(&m.http, &SystemClock);
    assert_eq!(*s.status(), Status::PairingFailed);
    assert_eq!(
        m.dash(json!({"cmd": "companion_list"}))["pending"][0]["state"],
        "aborted"
    );
    // ...until asked.
    s.request_pairing();
    run_until(&mut s, &m, 3, |st| *st == Status::WaitingForApproval);
}

#[test]
fn not_now_on_the_dashboard_is_not_asked_again() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    run_until(&mut s, &m, 3, |st| *st == Status::WaitingForApproval);
    m.dash(json!({"cmd": "companion_unpair", "id": hex::encode(s.identity.id)}));
    assert_eq!(
        run_until(&mut s, &m, 2, |st| *st == Status::PairingExpired),
        Status::PairingExpired
    );
    for _ in 0..3 {
        s.step(&m.http, &SystemClock);
    }
    assert_eq!(
        m.dash(json!({"cmd": "companion_list"}))["pending"],
        json!([]),
        "the request was not re-made"
    );
    s.request_pairing();
    run_until(&mut s, &m, 3, |st| *st == Status::WaitingForApproval);
}

/// Seen live: the board restarted (a flash) while the app waited for Pair, and
/// the app said the request "was not accepted in time" - nobody had declined
/// it; the board had forgotten it. It asks again by itself.
#[test]
fn a_board_restart_while_asking_asks_again() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    run_until(&mut s, &m, 3, |st| *st == Status::WaitingForApproval);
    std::thread::sleep(Duration::from_millis(1200)); // the board's clock counts whole seconds
    m.post("/__reboot", json!({}));
    for _ in 0..4 {
        s.step(&m.http, &SystemClock);
        assert_ne!(
            *s.status(),
            Status::PairingExpired,
            "a restart is not a refusal"
        );
    }
    assert_eq!(*s.status(), Status::WaitingForApproval);
    let pending = m.dash(json!({"cmd": "companion_list"}))["pending"].clone();
    assert_eq!(
        pending.as_array().map(|a| a.len()),
        Some(1),
        "asked again: {pending}"
    );
}

#[test]
fn an_unapproved_request_expires_quietly() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    run_until(&mut s, &m, 3, |st| *st == Status::WaitingForApproval);
    m.post("/__advance", json!({"s": 121}));
    assert_eq!(
        run_until(&mut s, &m, 2, |st| *st == Status::PairingExpired),
        Status::PairingExpired
    );
    assert_eq!(
        m.dash(json!({"cmd": "companion_list"}))["pending"],
        json!([])
    );
}

#[test]
fn unpaired_on_the_dashboard_means_pair_again() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    pair(&m, &mut s);
    s.set_listener(listener());
    run_until(&mut s, &m, 3, |st| *st == Status::Online);
    m.dash(json!({"cmd": "companion_unpair", "id": hex::encode(s.identity.id)}));
    run_until(&mut s, &m, 4, |st| *st == Status::WaitingForApproval);
    assert!(s.identity.key.is_none());
}

#[test]
fn a_blind_pin_reset_means_pair_again_and_a_pin_change_does_not() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    pair(&m, &mut s);
    s.set_listener(listener());
    run_until(&mut s, &m, 3, |st| *st == Status::Online);
    assert_eq!(
        m.post("/__pin", json!({"token": "5555", "old": "4826"}))["pairings_wiped"],
        false
    );
    run_until(&mut s, &m, 3, |st| *st == Status::Online);
    assert_eq!(
        m.post("/__pin", json!({"token": "7777"}))["pairings_wiped"],
        true
    );
    run_until(&mut s, &m, 4, |st| *st == Status::WaitingForApproval);
}

#[test]
fn no_pin_on_the_board_is_named() {
    let m = Model::start("");
    let mut s = session(&m, "PC");
    assert_eq!(
        run_until(&mut s, &m, 2, |st| *st == Status::NeedsPin),
        Status::NeedsPin
    );
}

#[test]
fn a_board_that_goes_away_is_retried_with_backoff_and_the_pairing_kept() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    pair(&m, &mut s);
    s.set_listener(listener());
    run_until(&mut s, &m, 3, |st| *st == Status::Online);
    let key = s.identity.key.clone();
    drop(m);
    let http = UreqHttp::new(Duration::from_millis(300));
    let waits: Vec<Duration> = (0..7).map(|_| s.step(&http, &SystemClock)).collect();
    assert_eq!(*s.status(), Status::Reconnecting);
    assert!(s.lost());
    assert_eq!(waits[0], Duration::from_secs(1));
    assert!(waits.windows(2).all(|w| w[1] >= w[0]), "{waits:?}");
    assert_eq!(*waits.last().unwrap(), Duration::from_secs(30));
    assert!(
        s.identity.key == key,
        "an unreachable board never costs the pairing"
    );
}

#[test]
fn a_board_found_again_after_a_restart_goes_back_online_with_the_same_pairing() {
    // Seen live: a board restarted (a flash), the session went Reconnecting,
    // the service stopped stepping it and found the board again - but the
    // session never heard, and stayed Reconnecting for good.
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    pair(&m, &mut s);
    s.set_listener(listener());
    run_until(&mut s, &m, 3, |st| *st == Status::Online);
    let key = s.identity.key.clone();
    // The board stops answering (a dead address stands in for a restart).
    s.set_base("http://127.0.0.1:9".into());
    let http = UreqHttp::new(Duration::from_millis(300));
    s.step(&http, &SystemClock);
    assert!(s.lost());
    // The search finds it answering again.
    s.set_base(m.base.clone());
    s.found_again();
    assert!(!s.lost(), "found again, the session must be stepped again");
    assert_eq!(
        run_until(&mut s, &m, 3, |st| *st == Status::Online),
        Status::Online
    );
    assert!(s.identity.key == key, "no new pairing");
}

#[test]
fn firmware_without_the_endpoint_is_named() {
    let m = Model::start("4826");
    let mut s = session(&m, "PC");
    s.set_base(format!("{}/nothing-here", m.base));
    s.step(&m.http, &SystemClock);
    assert_eq!(
        *s.status(),
        Status::Problem("Update the board's firmware to use screen sharing".into())
    );
}
