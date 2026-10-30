use super::*;
use crate::health::Link;
use crate::service::SoundView;
use crate::updates::center::UpdView;
use hidfi_stream::audio::{Share, State};

const A: &str = "DCB4D90A0BB8";
const B: &str = "DCB4D909F7C8";
const C: &str = "DCB4D9DA1A2C";

fn found(ip: &str) -> BoardState {
    BoardState::Found {
        url: format!("http://{ip}"),
        firmware: "hid_fi_v4.1".into(),
    }
}

fn board(mac: &str, board: BoardState, session: Status) -> BoardView {
    BoardView::new(mac, board, session)
}

fn online(mac: &str, ip: &str) -> BoardView {
    board(mac, found(ip), Status::Online)
}

fn view(boards: Vec<BoardView>) -> View {
    View {
        board: boards
            .first()
            .map_or(BoardState::NotPluggedIn { elsewhere: 0 }, |b| {
                b.board.clone()
            }),
        session: boards
            .first()
            .map_or(Status::Connecting, |b| b.session.clone()),
        boards,
        watching: 0,
        capture_allowed: true,
        capture_refused: false,
        port: Some(47800),
        name: "Studio Mac".into(),
        problem: None,
        sound: SoundView::default(),
        updates: UpdView::default(),
        remembered: vec![],
    }
}

fn card<'a>(w: &'a Window, mac: &str) -> &'a Value {
    w.json["boards"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["mac"] == mac)
        .unwrap_or_else(|| panic!("no card for {mac}"))
}

/// Every button a card has: its row, its update panel's, Forget under Details.
fn buttons(c: &Value) -> Vec<&Value> {
    let mut v: Vec<&Value> = c["actions"].as_array().unwrap().iter().collect();
    if let Some(u) = c["update"]["actions"].as_array() {
        v.extend(u.iter());
    }
    if !c["forget"].is_null() {
        v.push(&c["forget"]);
    }
    v
}

fn ids(c: &Value) -> Vec<String> {
    buttons(c)
        .iter()
        .map(|a| a["id"].as_str().unwrap().to_string())
        .collect()
}

fn latency(ms: u32) -> Link {
    Link {
        ms: Some(ms),
        last_ms: Some(ms + 2),
        answer_ms: Some(ms * 2),
        spread_ms: Some(4),
        last_ok: Some(1_790_000_000_000),
        failing: 0,
    }
}

#[test]
fn no_board_yet_says_what_to_do_and_draws_no_card() {
    let mut v = view(vec![]);
    v.board = BoardState::Looking;
    let w = model(&v);
    assert_eq!(w.json["empty"]["title"], "Looking for HID-Fi\u{2026}");
    assert_eq!(w.json["boards"].as_array().unwrap().len(), 0);

    v.board = BoardState::NotPluggedIn { elsewhere: 2 };
    let w = model(&v);
    assert_eq!(w.json["empty"]["title"], "Plug HID-Fi into this computer");
    assert!(w.json["empty"]["text"]
        .as_str()
        .unwrap()
        .contains("2 HID-Fi boards are on the network"));
    assert_eq!(w.json["empty"]["steps"].as_array().unwrap().len(), 3);
    assert_eq!(w.json["summary"]["total"], 0);
}

#[test]
fn an_online_board_is_a_card_with_its_address_version_and_latency() {
    let mut b = online(A, "192.168.0.104");
    b.link = latency(12);
    let w = model(&view(vec![b]));
    let c = card(&w, A);
    assert_eq!(
        (c["name"].as_str(), c["state"].as_str(), c["label"].as_str()),
        (Some("HID-Fi 0A0BB8"), Some("online"), Some("Online"))
    );
    assert_eq!(c["ip"], "192.168.0.104");
    assert_eq!(c["firmware"], "4.1");
    assert_eq!(c["latency"]["ms"], 12);
    assert_eq!(c["latency"]["last"], 14);
    assert_eq!(c["paired"], true);
    assert!(w.json["empty"].is_null());
    // The next step first, filled; forgetting is under Details, marked, and asks first.
    assert_eq!(ids(c), vec![format!("open:{A}"), format!("forget:{A}")]);
    assert_eq!(
        c["actions"].as_array().unwrap().len(),
        1,
        "only the next step on the card's face"
    );
    assert_eq!(
        (
            c["actions"][0]["kind"].as_str(),
            c["actions"][0]["label"].as_str()
        ),
        (Some("primary"), Some("Open dashboard"))
    );
    assert_eq!(c["forget"]["kind"], "danger");
    assert!(c["forget"]["confirm"]
        .as_str()
        .unwrap()
        .contains("HID-Fi 0A0BB8"));
    assert_eq!(c["forget"]["label"], "Forget pairing");
    assert_eq!(c["line"], "", "online and unwatched: the state says it all");
    assert_eq!(
        w.action(&format!("open:{A}")),
        Some(&Action::OpenDashboard("http://192.168.0.104".into()))
    );
    assert_eq!(
        w.action(&format!("forget:{A}")),
        Some(&Action::Forget(Some(A.into())))
    );
    // Details: the MAC as people write it, copyable.
    assert!(c["details"]
        .as_array()
        .unwrap()
        .iter()
        .any(|d| d[0] == "MAC" && d[1] == "DC:B4:D9:0A:0B:B8" && d[2] == true));
}

#[test]
fn a_board_being_watched_is_live_with_its_own_count() {
    let mut b = online(A, "192.168.0.104");
    b.viewers = 2;
    let mut v = view(vec![b, online(B, "192.168.0.115")]);
    v.watching = 2;
    let w = model(&v);
    assert_eq!(card(&w, A)["state"], "live");
    assert_eq!(card(&w, A)["viewers"], 2);
    assert!(card(&w, A)["line"]
        .as_str()
        .unwrap()
        .contains("2 dashboards"));
    assert_eq!(
        card(&w, B)["state"],
        "online",
        "the other board is not the one watched"
    );
    assert_eq!(w.json["boards"][0]["mac"], A, "live before online");
}

#[test]
fn a_board_that_stops_answering_goes_offline_before_its_heartbeat_notices() {
    let mut b = online(A, "192.168.0.104");
    b.link = latency(12);
    b.link.failing = 2;
    let w = model(&view(vec![b]));
    let c = card(&w, A);
    assert_eq!(
        (c["state"].as_str(), c["label"].as_str()),
        (Some("offline"), Some("Not answering"))
    );
    assert!(
        c["line"].as_str().unwrap().contains("stopped answering"),
        "not the last heartbeat's words"
    );
    assert_eq!(c["latency"]["failing"], 2);
}

#[test]
fn pairing_shows_its_code_in_its_own_card_and_two_boards_can_each_have_one() {
    let v = view(vec![
        board(A, found("192.168.0.104"), Status::ShowCode("042117".into())),
        board(B, found("192.168.0.115"), Status::ShowCode("903551".into())),
    ]);
    let w = model(&v);
    assert_eq!(card(&w, A)["code"], "042 117");
    assert_eq!(card(&w, B)["code"], "903 551");
    assert_eq!(card(&w, A)["state"], "attention");
    assert!(
        !ids(card(&w, A)).iter().any(|i| i.starts_with("forget")),
        "nothing to forget yet"
    );
}

#[test]
fn every_pairing_state_has_a_word_and_a_next_step() {
    for (s, label, want) in [
        (Status::WaitingForApproval, "Tap Pair", None),
        (Status::NeedsPin, "Needs a PIN", None),
        (Status::PairingFailed, "Pairing failed", Some("pair")),
        (Status::PairingExpired, "Not paired", Some("pair")),
        (Status::Connecting, "Connecting", None),
        (Status::Reconnecting, "Reconnecting", Some("forget")),
        (Status::Problem("The board is busy".into()), "Problem", None),
    ] {
        let w = model(&view(vec![board(A, found("192.168.0.104"), s.clone())]));
        let c = card(&w, A);
        assert_eq!(c["label"], label, "{s:?}");
        assert!(
            !c["line"].as_str().unwrap().is_empty(),
            "{s:?}: a line saying what is happening"
        );
        assert_eq!(
            ids(c)[0],
            format!("open:{A}"),
            "{s:?}: the dashboard is always one click away"
        );
        if let Some(w2) = want {
            assert!(ids(c).contains(&format!("{w2}:{A}")), "{s:?}: {:?}", ids(c));
        }
    }
}

#[test]
fn what_needs_the_person_comes_first_then_live_online_connecting_offline_and_away() {
    let mut live = online(B, "192.168.0.115");
    live.viewers = 1;
    let mut v = view(vec![
        board("DCB4D9000001", BoardState::NotOnNetwork, Status::Connecting),
        online("DCB4D9000002", "192.168.0.120"),
        live,
        board(A, found("192.168.0.104"), Status::ShowCode("042117".into())),
        board("DCB4D9000003", found("192.168.0.121"), Status::Connecting),
    ]);
    v.remembered = vec![Remembered {
        mac: C.into(),
        url: Some("http://192.168.0.130".into()),
    }];
    let w = model(&v);
    let order: Vec<&str> = w.json["boards"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["state"].as_str().unwrap())
        .collect();
    assert_eq!(
        order,
        [
            "attention",
            "live",
            "online",
            "connecting",
            "offline",
            "away"
        ]
    );
    let s = &w.json["summary"];
    assert_eq!(
        (
            s["total"].as_u64(),
            s["online"].as_u64(),
            s["attention"].as_u64(),
            s["offline"].as_u64()
        ),
        (Some(6), Some(2), Some(1), Some(2))
    );
}

#[test]
fn a_paired_board_not_plugged_in_is_shown_offline_and_can_be_forgotten() {
    let mut v = view(vec![online(A, "192.168.0.104")]);
    v.remembered = vec![Remembered {
        mac: C.into(),
        url: Some("http://192.168.0.130".into()),
    }];
    let w = model(&v);
    let c = card(&w, C);
    assert_eq!(
        (c["state"].as_str(), c["here"].as_bool()),
        (Some("away"), Some(false))
    );
    assert_eq!(c["ip"], "192.168.0.130");
    assert!(c["latency"].is_null(), "not measured: not plugged in");
    assert_eq!(ids(c), vec![format!("forget:{C}")]);
    assert_eq!(
        w.action(&format!("forget:{C}")),
        Some(&Action::Forget(Some(C.into())))
    );
}

#[test]
fn screen_recording_is_said_once_for_the_computer_not_on_every_card() {
    let mut v = view(vec![online(A, "192.168.0.104"), online(B, "192.168.0.115")]);
    v.capture_allowed = false;
    let w = model(&v);
    assert_eq!(w.json["banners"][0]["id"], "screen");
    assert_eq!(
        w.action("allow-screen"),
        Some(&Action::AllowScreenRecording)
    );
    for mac in [A, B] {
        let c = card(&w, mac);
        assert!(!ids(c).contains(&"allow-screen".to_string()));
        let text = format!("{} {}", c["line"], c["notes"]);
        assert!(!text.contains("Screen Recording"), "{mac}: {text}");
        assert_eq!(c["state"], "online", "the board itself is fine");
    }
}

#[test]
fn a_board_not_on_the_network_is_offline_and_the_permission_hint_is_a_banner() {
    let w = model(&view(vec![board(
        A,
        BoardState::NotOnNetwork,
        Status::Connecting,
    )]));
    let c = card(&w, A);
    assert_eq!(
        (c["state"].as_str(), c["label"].as_str()),
        (Some("offline"), Some("Not on this network"))
    );
    assert!(c["latency"].is_null());
    assert!(!ids(c).contains(&"net-settings".to_string()));
    if menu::net_hint().is_some() {
        assert!(w.json["banners"]
            .as_array()
            .unwrap()
            .iter()
            .any(|b| b["id"] == "network"));
        assert_eq!(w.action("net-settings"), Some(&Action::OpenNetworkSettings));
    }
}

fn upd(mac: &str, offer: Offer, job: Job) -> BoardUpd {
    BoardUpd {
        mac: mac.into(),
        firmware: Some("hid_fi_v4.1".into()),
        offer,
        job,
        resumable: None,
    }
}

fn offer(security: bool, over_air: bool) -> Offer {
    Offer::Board {
        to: "4.2".into(),
        size: 1_800_000,
        changes: vec![("4.2".into(), "- The Companion shows boards as cards".into())],
        security,
        over_air,
        cable_once: false,
    }
}

#[test]
fn an_update_is_a_badge_and_a_button_on_its_card() {
    let mut v = view(vec![online(A, "192.168.0.104"), online(B, "192.168.0.115")]);
    v.updates.boards = vec![
        upd(A, offer(false, true), Job::Idle),
        upd(B, offer(true, false), Job::Idle),
    ];
    let w = model(&v);
    let a = card(&w, A);
    assert_eq!(a["badge"]["text"], "Update 4.2");
    assert_eq!(a["badge"]["tone"], "info");
    assert!(ids(a).contains(&format!("update:{A}")));
    let up = buttons(a)
        .into_iter()
        .find(|x| x["id"] == format!("update:{A}"))
        .unwrap();
    assert_eq!(
        (
            up["bulk"].as_str(),
            up["label"].as_str(),
            up["kind"].as_str()
        ),
        (Some("update"), Some("Update to 4.2"), Some("primary"))
    );
    // One panel says it: what, how big, how, what is new.
    let p = &a["update"];
    assert_eq!(p["title"], "Update 4.2");
    assert_eq!(p["sub"], "from 4.1 \u{b7} 1.8 MB \u{b7} over WiFi");
    assert_eq!(p["text"][0], "The Companion shows boards as cards");
    assert!(
        !a["line"].as_str().unwrap().contains("Update"),
        "the update has its own place"
    );
    let b = card(&w, B);
    assert_eq!(
        (b["badge"]["text"].as_str(), b["badge"]["tone"].as_str()),
        (Some("Security update 4.2"), Some("warn"))
    );
    assert!(ids(b).contains(&format!("update-cable:{B}")));
    assert!(b["update"]["sub"].as_str().unwrap().ends_with("by cable"));
    let cable = buttons(b)
        .into_iter()
        .find(|x| x["id"] == format!("update-cable:{B}"))
        .unwrap();
    assert_eq!(cable["label"], "Update to 4.2 by cable");
}

#[test]
fn an_update_in_progress_shows_how_far_and_can_be_cancelled() {
    let mut v = view(vec![online(A, "192.168.0.104")]);
    v.updates.boards = vec![upd(
        A,
        offer(false, true),
        Job::Downloading {
            bytes: 420,
            total: 1000,
            paused: false,
        },
    )];
    let w = model(&v);
    let c = card(&w, A);
    assert_eq!(c["badge"]["progress"], 42);
    assert_eq!(c["update"]["progress"], 42);
    assert_eq!(c["update"]["title"], "Downloading update");
    let cancel = buttons(c)
        .into_iter()
        .find(|x| x["id"] == format!("cancel:{A}"))
        .unwrap();
    assert_eq!(cancel["kind"], "", "stopping is never the main button");
    assert!(ids(c).contains(&format!("cancel:{A}")));
    v.updates.boards = vec![upd(A, offer(false, true), Job::Ready)];
    let w = model(&v);
    assert!(ids(card(&w, A)).contains(&format!("restart:{A}")));
    let labels: Vec<&str> = buttons(card(&w, A))
        .iter()
        .map(|b| b["label"].as_str().unwrap())
        .collect();
    assert!(
        labels.contains(&"Restart now") && labels.contains(&"Keep this version"),
        "{labels:?}"
    );
    v.updates.boards = vec![upd(A, Offer::UpToDate, Job::RolledBack)];
    assert_eq!(model(&v).json["boards"][0]["badge"]["tone"], "warn");
}

#[test]
fn the_companion_update_is_one_banner_for_the_computer() {
    let mut v = view(vec![online(A, "192.168.0.104")]);
    v.updates.companion = CompUpd::Available {
        to: "4.2.0".into(),
        size: 4_700_000,
    };
    let w = model(&v);
    let b = w.json["banners"]
        .as_array()
        .unwrap()
        .iter()
        .find(|b| b["id"] == "companion")
        .unwrap();
    assert!(b["title"].as_str().unwrap().contains("4.2.0"));
    assert_eq!(b["actions"][0]["id"], "companion-get");
    assert_eq!(
        w.action("companion-get"),
        Some(&Action::Update(UpdCmd::GetCompanion))
    );
    v.updates.companion = CompUpd::Ready {
        to: "4.2.0".into(),
        path: "/tmp/x.dmg".into(),
    };
    let w = model(&v);
    assert_eq!(
        w.action("companion-install"),
        Some(&Action::Update(UpdCmd::OpenInstaller))
    );
}

#[test]
fn sound_switches_are_the_computers_and_say_why_when_they_cannot_work() {
    let mut v = view(vec![online(A, "192.168.0.104")]);
    assert!(
        model(&v).json["sound"].is_null(),
        "a build without sound shows no switches"
    );
    v.sound = SoundView {
        system: true,
        mic: false,
        state: Some(State {
            system: Share::On,
            mic: Share::Unavailable("Microphone access is not allowed".into()),
        }),
    };
    let w = model(&v);
    assert_eq!(w.json["sound"]["system"], true);
    assert!(w.json["sound"]["notes"]
        .to_string()
        .contains("microphone cannot be shared"));
    assert_eq!(
        w.action("sound-system"),
        Some(&Action::Sound {
            system: Some(false),
            mic: None
        })
    );
    assert_eq!(
        w.action("sound-mic"),
        Some(&Action::Sound {
            system: None,
            mic: Some(true)
        })
    );
    assert_eq!(w.action("allow-mic"), Some(&Action::AllowMicrophone));
}

#[test]
fn a_click_on_a_button_that_is_gone_does_nothing() {
    let v = view(vec![online(A, "192.168.0.104"), online(B, "192.168.0.115")]);
    let before = model(&v);
    assert!(before.action(&format!("forget:{B}")).is_some());
    // B leaves between drawing and clicking: its id no longer means anything,
    // and nobody else's button takes its place.
    let after = model(&view(vec![online(A, "192.168.0.104")]));
    assert!(after.action(&format!("forget:{B}")).is_none());
    assert!(after.action("open").is_none() && after.action("forget").is_none());
    assert!(after.action("").is_none());
}

#[test]
fn every_id_is_unique_and_every_card_button_has_one() {
    let mut boards: Vec<BoardView> = (0..50)
        .map(|i| online(&format!("DCB4D9{i:06X}"), &format!("192.168.1.{i}")))
        .collect();
    boards[3].session = Status::PairingFailed;
    let mut v = view(boards);
    v.capture_allowed = false;
    let t = std::time::Instant::now();
    let w = model(&v);
    let took = t.elapsed();
    assert_eq!(w.json["boards"].as_array().unwrap().len(), 50);
    let all = w.ids();
    let mut uniq = all.clone();
    uniq.sort();
    uniq.dedup();
    assert_eq!(all.len(), uniq.len(), "duplicate ids");
    for c in w.json["boards"].as_array().unwrap() {
        for id in ids(c) {
            assert!(
                w.action(&id).is_some(),
                "{id} on a card but not in the table"
            );
        }
    }
    // Fifty boards are drawn on every change: building the model stays tiny.
    assert!(took < std::time::Duration::from_millis(50), "{took:?}");
}

#[test]
fn names_are_data_never_markup() {
    // The page escapes; the model passes names through untouched and never
    // builds markup itself.
    let mut v = view(vec![online(A, "192.168.0.104")]);
    v.name = "<img src=x onerror=alert(1)>".into();
    let s = model(&v).json.to_string();
    assert!(s.contains("<img src=x onerror=alert(1)>"));
    assert!(!s.contains("<div") && !s.contains("<button"));
}

#[test]
fn the_golden_states_are_current() {
    // The page tests read this file; it must be what the app sends today.
    let p = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../tests/golden/companion_window_states.tsv"
    );
    let have = std::fs::read_to_string(p)
        .unwrap_or_default()
        .replace("\r\n", "\n");
    // Made on macOS: other systems word their own hints (the network one, the
    // steps), so there the file must hold the same states, not the same words.
    if !cfg!(target_os = "macos") {
        let names = |s: &str| -> Vec<String> {
            s.lines()
                .map(|l| l.split('\t').next().unwrap_or("").to_string())
                .collect()
        };
        assert_eq!(
            names(&have),
            names(&samples::golden()),
            "a state was added or renamed: regenerate the file on macOS"
        );
        return;
    }
    assert!(
        have == samples::golden(),
        "tests/golden/companion_window_states.tsv is out of date: from companion/, run\n  \
         cargo run -p hidfi-companion --example window_states > ../tests/golden/companion_window_states.tsv"
    );
}
