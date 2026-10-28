use super::*;
use std::sync::atomic::AtomicU64;

fn b64(s: &str) -> Vec<u8> {
    let t = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let (mut buf, mut bits, mut out) = (0u32, 0, Vec::new());
    for c in s.bytes().filter(|c| *c != b'=') {
        buf = (buf << 6) | t.iter().position(|x| *x == c).unwrap() as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buf >> bits) as u8);
        }
    }
    out
}

fn vectors() -> (Value, Value) {
    (serde_json::from_str(include_str!("../../../../../tests/vectors/ota_v1.json")).unwrap(),
     serde_json::from_str(include_str!("../../../../../tests/vectors/ota_release_v1.json")).unwrap())
}

/// The signed 4.1.0 release of the vectors: (release, manifest, signature file).
fn release() -> (Release, Vec<u8>, Vec<u8>) {
    let (_, rv) = vectors();
    let c = &rv["cases"][0];
    let m = b64(c["manifest_b64"].as_str().unwrap());
    let s = serde_json::to_vec(&c["sig"]).unwrap();
    (ota::read_release(&m, &s, &[test_key()]).unwrap(), m, s)
}

fn seen(fw: &str, layout: Option<&str>) -> Seen {
    Seen { firmware: Some(fw.into()), layout: layout.map(Into::into), state: Some("valid".into()), step: "idle".into(),
           ..Default::default() }
}

// ---- offers -------------------------------------------------------------------------------
#[test]
fn a_two_slot_board_is_offered_the_update_over_the_air_with_what_changed() {
    let (r, ..) = release();
    match offer(Some(&seen("hid_fi_v4.0.4", Some("two_slot"))), Some(&r), "4.0.4", true) {
        Offer::Board { to, size, changes, over_air, cable_once, .. } => {
            assert_eq!((to.as_str(), size, over_air, cable_once), ("4.1.0", r.image.size, true, false));
            assert_eq!(changes.iter().map(|c| c.0.as_str()).collect::<Vec<_>>(), ["4.1.0"], "only what is newer");
        }
        o => panic!("{o:?}"),
    }
}

#[test]
fn a_single_slot_board_needs_the_cable_once() {
    let (r, ..) = release();
    let o = offer(Some(&seen("hid_fi_v4.0.4", Some("single"))), Some(&r), "4.0.4", true);
    assert!(matches!(o, Offer::Board { over_air: false, cable_once: true, .. }), "{o:?}");
}

#[test]
fn firmware_without_the_engine_is_offered_the_cable() {
    // v4.0.4 as released reports no layout at all.
    let (r, ..) = release();
    let o = offer(Some(&seen("hid_fi_v4.0.4", None)), Some(&r), "4.0.4", true);
    assert!(matches!(o, Offer::Board { over_air: false, cable_once: true, .. }), "{o:?}");
}

#[test]
fn not_over_the_air_when_unpaired_or_older_than_min_from() {
    let (r, ..) = release();
    let o = offer(Some(&seen("hid_fi_v4.0.4", Some("two_slot"))), Some(&r), "4.0.4", false);
    assert!(matches!(o, Offer::Board { over_air: false, cable_once: false, .. }), "unpaired: {o:?}");
    let o = offer(Some(&seen("hid_fi_v4.0.3", Some("two_slot"))), Some(&r), "4.0.4", true);
    assert!(matches!(o, Offer::Board { over_air: false, .. }), "older than min_from: {o:?}");
}

#[test]
fn nothing_is_offered_while_verifying_or_without_facts() {
    let (r, ..) = release();
    let mut s = seen("hid_fi_v4.0.4", Some("two_slot"));
    s.state = Some("pending_verify".into());
    assert_eq!(offer(Some(&s), Some(&r), "4.0.4", true), Offer::Verifying);
    assert_eq!(offer(None, Some(&r), "4.0.4", true), Offer::Unknown);
    assert_eq!(offer(Some(&seen("hid_fi_v4.0.4", Some("two_slot"))), None, "4.0.4", true), Offer::Unknown,
               "no release read: never claim it is up to date");
    assert_eq!(offer(Some(&seen("garbage", Some("two_slot"))), Some(&r), "4.0.4", true), Offer::Unknown);
    assert_eq!(offer(Some(&seen("hid_fi_v4.1.0", Some("two_slot"))), Some(&r), "4.0.4", true), Offer::UpToDate);
}

#[test]
fn a_board_with_a_newer_protocol_asks_for_a_newer_companion() {
    let (r, ..) = release();
    let mut s = seen("hid_fi_v4.0.4", Some("two_slot"));
    s.comp_api = Some(ota::COMP_API_MAX + 1);
    assert_eq!(offer(Some(&s), Some(&r), "4.0.4", true), Offer::UpdateCompanionFirst);
}

#[test]
fn the_companion_is_offered_only_its_own_systems_installer() {
    let (r, ..) = release();
    let got = companion_offer(Some(&r), "4.0.4");
    assert_eq!(got.is_some(), os_name() == "macos", "the vectors carry a macOS installer only");
    assert!(companion_offer(Some(&r), "4.1.0").is_none(), "same version: nothing");
    assert!(companion_offer(Some(&r), "garbage").is_none());
    assert!(companion_offer(None, "4.0.4").is_none());
}

// ---- reading the board's progress ----------------------------------------------------------
#[test]
fn the_boards_progress_reads_as_the_person_should_see_it() {
    let mut s = seen("hid_fi_v4.0.4", Some("two_slot"));
    s.uptime = 500;
    s.step = "downloading".into();
    s.bytes = 10;
    s.total = 100;
    s.paused = true;
    assert_eq!(air_phase(&s, 400, "4.1.0"), Job::Downloading { bytes: 10, total: 100, paused: true });
    s.step = "ready".into();
    assert_eq!(air_phase(&s, 400, "4.1.0"), Job::Ready);
    s.step = "idle".into();
    s.error = "hash".into();
    assert!(matches!(air_phase(&s, 400, "4.1.0"), Job::Failed { .. }));
    s.error = "cancelled".into();
    assert_eq!(air_phase(&s, 400, "4.1.0"), Job::Stopped);
    // After the restart: uptime went back.
    let mut n = seen("hid_fi_v4.1.0", Some("two_slot"));
    n.uptime = 3;
    n.state = Some("pending_verify".into());
    assert_eq!(air_phase(&n, 400, "4.1.0"), Job::Verifying);
    n.state = Some("valid".into());
    n.result = Some("updated".into());
    assert_eq!(air_phase(&n, 400, "4.1.0"), Job::Done { to: "4.1.0".into() });
    let mut back = seen("hid_fi_v4.0.4", Some("two_slot"));
    back.uptime = 3;
    back.result = Some("rolled_back".into());
    assert_eq!(air_phase(&back, 400, "4.1.0"), Job::RolledBack);
    back.result = Some("none".into());
    assert_eq!(air_phase(&back, 400, "4.1.0"), Job::RolledBack, "restarted, still the old version: not done");
}

// ---- the cable -----------------------------------------------------------------------------
#[test]
fn the_cable_goes_native_when_the_board_can_be_asked() {
    let s = seen("hid_fi_v4.1.0", Some("two_slot"));
    assert_eq!(cable_plan(Some(&s), true, &[]), Ok(CablePlan::Native));
    assert_eq!(cable_plan(Some(&s), false, &["/dev/a".into()]), Ok(CablePlan::Com("/dev/a".into())));
    assert_eq!(cable_plan(Some(&seen("hid_fi_v4.0.4", None)), true, &["/dev/a".into()]), Ok(CablePlan::Com("/dev/a".into())));
    assert_eq!(cable_plan(None, false, &["/dev/a".into()]), Ok(CablePlan::Com("/dev/a".into())));
}

#[test]
fn the_cable_never_guesses_and_never_resets_a_board_proving_itself() {
    let mut s = seen("hid_fi_v4.1.0", Some("two_slot"));
    s.state = Some("pending_verify".into());
    assert!(cable_plan(Some(&s), true, &["/dev/a".into()]).is_err(), "a port open would roll it back");
    s.state = Some("valid".into());
    s.step = "downloading".into();
    assert!(cable_plan(Some(&s), true, &[]).is_err());
    assert!(cable_plan(None, false, &[]).is_err());
    assert!(cable_plan(None, false, &["/dev/a".into(), "/dev/b".into()]).is_err(), "two COM ports: which board?");
}

// ---- fetching ------------------------------------------------------------------------------
struct FakeFetch(Mutex<std::collections::HashMap<String, Vec<u8>>>);
impl Fetch for FakeFetch {
    fn get(&self, url: &str, cap: usize) -> Result<Vec<u8>, String> {
        let b = self.0.lock().unwrap().get(url).cloned().ok_or("404")?;
        if b.len() > cap { Err("too large".into()) } else { Ok(b) }
    }
}

fn source() -> (FakeFetch, Vec<u8>) {
    let (v, rv) = vectors();
    let (_, m, s) = release();
    let image = b64(v["image_b64"].as_str().unwrap());
    let inst = b64(rv["installer_b64"].as_str().unwrap());
    let r = release().0;
    let mut f = std::collections::HashMap::new();
    f.insert(format!("S/{MANIFEST}"), m);
    f.insert(format!("S/{SIGNATURE}"), s);
    f.insert(format!("S/{}", r.image.name), image.clone());
    if let Some(a) = r.companion_asset("macos") {
        f.insert(format!("S/{}", a.name), inst);
    }
    (FakeFetch(Mutex::new(f)), image)
}

#[test]
fn a_release_is_believed_only_with_its_signature() {
    let (f, image) = source();
    let (r, ..) = fetch_release(&f, "S/", &[test_key()]).unwrap();
    assert_eq!(fetch_asset(&f, "S/", &r.image, image_cap()).unwrap(), image);
    assert!(fetch_release(&f, "S/", &[]).unwrap_err().contains("not signed"), "no trusted key: nothing believed");
    f.0.lock().unwrap().get_mut(&format!("S/{MANIFEST}")).unwrap()[10] ^= 1;
    assert!(fetch_release(&f, "S/", &[test_key()]).is_err());
}

#[test]
fn a_file_that_is_not_the_pinned_one_is_refused() {
    let (f, _) = source();
    let (r, ..) = fetch_release(&f, "S/", &[test_key()]).unwrap();
    f.0.lock().unwrap().get_mut(&format!("S/{}", r.image.name)).unwrap()[100] ^= 1;
    assert!(fetch_asset(&f, "S/", &r.image, image_cap()).is_err());
}

#[test]
fn the_companion_installer_lands_whole_or_not_at_all() {
    let (f, _) = source();
    let (r, ..) = fetch_release(&f, "S/", &[test_key()]).unwrap();
    let Some(a) = r.companion_asset("macos").cloned() else { return };
    let dir = std::env::temp_dir().join(format!("hidfi-inst-{}", std::process::id()));
    let p = fetch_installer(&f, "S/", &a, &dir).unwrap();
    assert!(ota::asset_matches(&a, &std::fs::read(&p).unwrap()));
    std::fs::remove_file(&p).unwrap();
    f.0.lock().unwrap().get_mut(&format!("S/{}", a.name)).unwrap()[0] ^= 1;
    assert!(fetch_installer(&f, "S/", &a, &dir).is_err());
    assert!(!dir.join(&a.name).exists(), "a wrong installer is never left where the person would open it");
    let _ = std::fs::remove_dir_all(&dir);
}

// ---- the over-the-air job, against a board engine like the firmware's ----------------------------
#[derive(Default)]
struct Engine {
    online: bool,          // can reach the release itself
    relayed: bool,         // a relay was given for the next check
    step: String,
    bytes: u64,
    error: String,
    available: String,
    uptime: u64,
    firmware: String,
    state: String,
    result: String,
    restart_in: Option<u32>,
    verify_in: u32,
    self_test_fails: bool,
    pause_for: u32,
    acts: Vec<String>,
}

struct FakeBoard(Mutex<Engine>);
const TOTAL: u64 = 1000;

impl BoardLink for FakeBoard {
    fn status(&self, _: &str) -> Result<Value, String> {
        let mut e = self.0.lock().unwrap();
        e.uptime += 1;
        // One status poll = one second of the board's life.
        if let Some(n) = e.restart_in.as_mut() {
            if *n == 0 {
                e.restart_in = None;
                e.uptime = 1;
                e.state = "pending_verify".into();
                e.firmware = "hid_fi_v4.1.0".into();
                e.step = "idle".into();
                e.verify_in = 3;
                return Err("restarting".into());
            }
            *n -= 1;
        }
        if e.state == "pending_verify" {
            if e.verify_in == 0 {
                if e.self_test_fails {
                    e.firmware = "hid_fi_v4.0.4".into();
                    e.result = "rolled_back".into();
                } else {
                    e.result = "updated".into();
                }
                e.state = "valid".into();
            } else {
                e.verify_in -= 1;
            }
        }
        match e.step.as_str() {
            "checking" => {
                e.step = "idle".into();
                if e.online || std::mem::take(&mut e.relayed) {
                    e.available = "4.1.0".into();
                    e.error.clear();
                } else {
                    e.available.clear();
                    e.error = "no_connection".into();
                }
            }
            "downloading" => {
                if e.pause_for > 0 {
                    e.pause_for -= 1;
                } else {
                    e.bytes = (e.bytes + 250).min(TOTAL);
                    if e.bytes == TOTAL {
                        e.step = "ready".into();
                    }
                }
            }
            _ => {}
        }
        Ok(serde_json::json!({"firmware": e.firmware, "ota_layout": "two_slot", "ota_state": e.state,
            "ota_result": e.result, "ota_step": e.step, "ota_bytes": e.bytes, "ota_total": TOTAL,
            "ota_paused": e.pause_for > 0, "ota_available": e.available, "ota_error": e.error, "uptime_sec": e.uptime}))
    }

    fn ota(&self, _: &str, act: &str, url: &str) -> Result<Option<String>, String> {
        let mut e = self.0.lock().unwrap();
        e.acts.push(act.into());
        match act {
            "check" => e.step = "checking".into(),
            "from" => {
                assert!(url.starts_with("http://"), "{url}");
                e.relayed = true;
                e.step = "checking".into();
            }
            "install" => {
                if e.available.is_empty() {
                    return Err("nothing_to_install".into());
                }
                e.step = "downloading".into();
                e.bytes = 0;
            }
            "cancel" => {
                e.step = "idle".into();
                e.error = "cancelled".into();
                return Ok(Some("stopping".into()));
            }
            "apply" => {
                if e.step != "ready" {
                    return Err("not_ready".into());
                }
                e.restart_in = Some(0);
            }
            _ => return Err("bad_request".into()),
        }
        Ok(None)
    }
}

fn board() -> FakeBoard {
    FakeBoard(Mutex::new(Engine { online: true, step: "idle".into(), uptime: 400, firmware: "hid_fi_v4.0.4".into(),
                                  state: "valid".into(), result: "none".into(), ..Default::default() }))
}

struct FastClock(AtomicU64);
impl Clock for FastClock {
    fn now(&self) -> Instant {
        static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();
        *START.get_or_init(Instant::now) + Duration::from_millis(self.0.load(Ordering::SeqCst))
    }
    fn sleep(&self, d: Duration) {
        self.0.fetch_add(d.as_millis() as u64, Ordering::SeqCst);
        std::thread::sleep(Duration::from_micros(300));   // the "person" thread gets to look
    }
}

struct FakeRelay(AtomicBool);
impl RelayStarter for FakeRelay {
    fn start(&self, files: Vec<(String, Vec<u8>)>, _: &str) -> Result<(String, Box<dyn Send>), String> {
        assert!(files.iter().any(|f| f.0 == MANIFEST) && files.iter().any(|f| f.0 == SIGNATURE), "manifest and signature");
        self.0.store(true, Ordering::SeqCst);
        Ok(("http://192.168.0.5:47801/".into(), Box::new(())))
    }
}

fn run(b: &FakeBoard, sh: &Shared, relay: &FakeRelay) {
    let (r, m, s) = release();
    let files = move || Ok(vec![(MANIFEST.to_string(), m.clone()), (SIGNATURE.to_string(), s.clone())]);
    air_job(b, "http://board", &r, &files, relay, &FastClock(AtomicU64::new(0)), sh);
}

#[test]
fn over_the_air_start_to_finish_with_restart_now() {
    let b = board();
    let sh = Shared::new();
    b.0.lock().unwrap().pause_for = 2;
    std::thread::scope(|t| {
        t.spawn(|| run(&b, &sh, &FakeRelay(AtomicBool::new(false))));
        // The person taps "Restart now" when it is ready.
        for _ in 0..2000 {
            if sh.get() == Job::Ready {
                sh.restart_now.store(true, Ordering::SeqCst);
                break;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    });
    assert_eq!(sh.get(), Job::Done { to: "4.1.0".into() });
    assert_eq!(b.0.lock().unwrap().acts, ["check", "install", "apply"]);
}

#[test]
fn a_board_without_internet_gets_the_release_from_this_computer() {
    let b = board();
    b.0.lock().unwrap().online = false;
    b.0.lock().unwrap().restart_in = None;
    let relay = FakeRelay(AtomicBool::new(false));
    let sh = Shared::new();
    // The board applies by itself when idle: model it by restarting once ready.
    std::thread::scope(|t| {
        t.spawn(|| run(&b, &sh, &relay));
        for _ in 0..2000 {
            if sh.get() == Job::Ready {
                b.0.lock().unwrap().restart_in = Some(1);
                break;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    });
    assert!(relay.0.load(Ordering::SeqCst), "the relay was started");
    assert_eq!(b.0.lock().unwrap().acts, ["check", "from", "install"]);
    assert_eq!(sh.get(), Job::Done { to: "4.1.0".into() });
}

#[test]
fn cancel_while_downloading_stops_and_says_so() {
    let b = board();
    b.0.lock().unwrap().pause_for = 50;
    let sh = Shared::new();
    std::thread::scope(|t| {
        t.spawn(|| run(&b, &sh, &FakeRelay(AtomicBool::new(false))));
        for _ in 0..2000 {
            if matches!(sh.get(), Job::Downloading { .. }) {
                sh.cancel.store(true, Ordering::SeqCst);
                break;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    });
    assert_eq!(sh.get(), Job::Stopped);
    assert!(b.0.lock().unwrap().acts.contains(&"cancel".to_string()));
}

#[test]
fn a_failed_self_test_is_reported_as_rolled_back() {
    let b = board();
    b.0.lock().unwrap().self_test_fails = true;
    let sh = Shared::new();
    std::thread::scope(|t| {
        t.spawn(|| run(&b, &sh, &FakeRelay(AtomicBool::new(false))));
        for _ in 0..2000 {
            if sh.get() == Job::Ready {
                b.0.lock().unwrap().restart_in = Some(0);
                break;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    });
    assert_eq!(sh.get(), Job::RolledBack);
}

#[test]
fn a_board_that_cannot_see_the_release_even_relayed_fails_plainly() {
    struct NoRelay;
    impl RelayStarter for NoRelay {
        fn start(&self, _: Vec<(String, Vec<u8>)>, _: &str) -> Result<(String, Box<dyn Send>), String> {
            Err("no network address".into())
        }
    }
    let b = board();
    b.0.lock().unwrap().online = false;
    let sh = Shared::new();
    let (r, m, s) = release();
    let files = move || Ok(vec![(MANIFEST.to_string(), m.clone()), (SIGNATURE.to_string(), s.clone())]);
    air_job(&b, "http://board", &r, &files, &NoRelay, &FastClock(AtomicU64::new(0)), &sh);
    assert!(matches!(sh.get(), Job::Failed { why } if why.contains("serve")));
    assert!(!b.0.lock().unwrap().acts.contains(&"install".to_string()), "nothing installed");
}

#[test]
fn a_board_that_stops_moving_fails_but_a_ready_one_waits() {
    struct Frozen;
    impl BoardLink for Frozen {
        fn status(&self, _: &str) -> Result<Value, String> {
            Ok(serde_json::json!({"firmware": "hid_fi_v4.0.4", "ota_layout": "two_slot", "ota_state": "valid",
                "ota_step": "downloading", "ota_bytes": 5, "ota_total": 10, "ota_available": "4.1.0", "uptime_sec": 900}))
        }
        fn ota(&self, _: &str, _: &str, _: &str) -> Result<Option<String>, String> {
            Ok(None)
        }
    }
    // Frozen at 5 of 10 bytes, not paused: the check sees "downloading", never idle - so
    // drive the stall from the install loop by starting at "idle" with the release found.
    struct Stuck(Mutex<u32>);
    impl BoardLink for Stuck {
        fn status(&self, b: &str) -> Result<Value, String> {
            let mut n = self.0.lock().unwrap();
            *n += 1;
            if *n <= 2 {
                return Ok(serde_json::json!({"firmware": "hid_fi_v4.0.4", "ota_layout": "two_slot", "ota_state": "valid",
                    "ota_step": "idle", "ota_available": "4.1.0", "uptime_sec": 900}));
            }
            Frozen.status(b)
        }
        fn ota(&self, _: &str, _: &str, _: &str) -> Result<Option<String>, String> {
            Ok(None)
        }
    }
    let (r, ..) = release();
    let sh = Shared::new();
    let files = || Ok(Vec::new());
    air_job(&Stuck(Mutex::new(0)), "b", &r, &files, &FakeRelay(AtomicBool::new(false)), &FastClock(AtomicU64::new(0)), &sh);
    assert!(matches!(sh.get(), Job::Failed { why } if why.contains("progress")));
}

#[test]
fn a_ready_update_waits_longer_than_the_stall_limit_for_an_idle_board() {
    // Someone keeps using the board for 25 minutes; then it applies by itself.
    let b = board();
    let sh = Shared::new();
    std::thread::scope(|t| {
        t.spawn(|| run(&b, &sh, &FakeRelay(AtomicBool::new(false))));
        for _ in 0..4000 {
            if sh.get() == Job::Ready {
                b.0.lock().unwrap().restart_in = Some(25 * 60);
                break;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
    });
    assert!(STALL < Duration::from_secs(25 * 60));
    assert_eq!(sh.get(), Job::Done { to: "4.1.0".into() });
}
