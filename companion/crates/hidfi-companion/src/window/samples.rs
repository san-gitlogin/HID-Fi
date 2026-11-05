//! Every state the window can show, built as the service would - for the
//! page tests (tests/ui/companion) through tests/golden/companion_window_states.tsv,
//! and for `--window-check` in the real engine. Not used by the app itself.

use crate::health::Link;
use crate::service::{BoardState, BoardView, Remembered, SoundView, View};
use crate::updates::center::{BoardUpd, CompUpd, UpdView};
use crate::updates::{Job, Offer};
use hidfi_agent::session::Status;
use hidfi_stream::audio::{Share, State};

fn found(ip: &str) -> BoardState {
    BoardState::Found {
        url: format!("http://{ip}"),
        firmware: "hid_fi_v4.1".into(),
    }
}

fn b(mac: &str, board: BoardState, session: Status, ms: Option<u32>, viewers: usize) -> BoardView {
    let mut v = BoardView::new(mac, board, session);
    v.viewers = viewers;
    if let Some(ms) = ms {
        v.link = Link {
            ms: Some(ms),
            last_ms: Some(ms + 3),
            answer_ms: Some(ms * 2 + 4),
            spread_ms: Some(ms / 4 + 1),
            // Fixed, so the golden file never changes by itself; the page tests set their clock.
            last_ok: Some(LAST_OK),
            failing: 0,
        };
    }
    v
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
        watching: boards.iter().map(|b| b.viewers).sum(),
        boards,
        capture_allowed: true,
        capture_refused: false,
        port: Some(47800),
        name: "SANTHOSH\u{2019}s MacBook Pro".into(),
        problem: None,
        sound: SoundView {
            system: true,
            mic: false,
            state: Some(State {
                system: Share::On,
                mic: Share::Off,
            }),
        },
        updates: UpdView {
            latest: Some("4.1".into()),
            ..UpdView::default()
        },
        remembered: vec![],
        recovery: vec![],
        factory: vec![],
    }
}

fn offer(to: &str, security: bool, over_air: bool) -> Offer {
    Offer::Board {
        to: to.into(),
        size: 1_802_848,
        changes: vec![(
            to.into(),
            "- The Companion shows each board as a card".into(),
        )],
        security,
        over_air,
        cable_once: !over_air,
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

/// When every sample board last answered (Unix ms): 2026-10-29 12:00:00 UTC.
pub const LAST_OK: u64 = 1_793_275_200_000;

/// Every state a person can meet, by name.
pub fn all() -> Vec<(&'static str, View)> {
    let (a, bb, c, d) = (
        "DCB4D90A0BB8",
        "DCB4D909F7C8",
        "DCB4D9DA1A2C",
        "DCB4D9113355",
    );
    let mut states: Vec<(&str, View)> = Vec::new();

    let mut v = view(vec![]);
    v.board = BoardState::Looking;
    states.push(("looking", v));
    let mut v = view(vec![]);
    v.board = BoardState::NotPluggedIn { elsewhere: 2 };
    states.push(("not-plugged", v));

    states.push((
        "one-online",
        view(vec![b(
            a,
            found("192.168.0.104"),
            Status::Online,
            Some(12),
            0,
        )]),
    ));
    states.push((
        "one-live",
        view(vec![b(
            a,
            found("192.168.0.104"),
            Status::Online,
            Some(38),
            2,
        )]),
    ));
    states.push((
        "pairing-code",
        view(vec![b(
            a,
            found("192.168.0.104"),
            Status::ShowCode("042117".into()),
            None,
            0,
        )]),
    ));
    states.push((
        "waiting-for-pair",
        view(vec![b(
            a,
            found("192.168.0.104"),
            Status::WaitingForApproval,
            None,
            0,
        )]),
    ));
    let mut v = view(vec![b(
        a,
        found("192.168.0.104"),
        Status::Online,
        Some(12),
        0,
    )]);
    v.capture_allowed = false;
    states.push(("screen-recording", v));

    // Two boards, as on the owner's Mac: one paired and online, one asking for Pair.
    states.push((
        "two-boards",
        view(vec![
            b(a, found("192.168.0.104"), Status::Online, Some(14), 0),
            b(
                bb,
                found("192.168.0.115"),
                Status::WaitingForApproval,
                None,
                0,
            ),
        ]),
    ));

    // Everything at once: updates, a slow link, one not answering, one not on
    // the network, a paired board elsewhere, and the Companion's own update.
    let mut slow = b(c, found("192.168.0.130"), Status::Online, Some(184), 0);
    slow.link.spread_ms = Some(120);
    let mut silent = b(d, found("192.168.0.140"), Status::Online, Some(22), 0);
    silent.link.failing = 3;
    let mut v = view(vec![
        b(a, found("192.168.0.104"), Status::Online, Some(12), 1),
        b(bb, found("192.168.0.115"), Status::Online, Some(64), 0),
        slow,
        silent,
        b(
            "DCB4D9AA0001",
            BoardState::NotOnNetwork,
            Status::Connecting,
            None,
            0,
        ),
        b(
            "DCB4D9AA0002",
            found("192.168.0.150"),
            Status::PairingExpired,
            None,
            0,
        ),
    ]);
    v.remembered = vec![Remembered {
        mac: "DCB4D9BEEF01".into(),
        url: Some("http://192.168.0.160".into()),
    }];
    v.updates.boards = vec![
        upd(a, offer("4.2", false, true), Job::Idle),
        upd(
            bb,
            offer("4.2", false, true),
            Job::Downloading {
                bytes: 760_000,
                total: 1_802_848,
                paused: false,
            },
        ),
        upd(c, offer("4.2", true, false), Job::Idle),
    ];
    v.updates.companion = CompUpd::Available {
        to: "4.2.0".into(),
        size: 4_705_358,
    };
    states.push(("mixed", v.clone()));
    // The same, with nothing needing the person (a filter that matches nothing).
    let mut calm = v;
    calm.boards.retain(|b| b.mac != "DCB4D9AA0002");
    states.push(("mixed-no-attention", calm));

    // Updates in every hand: one downloaded and waiting, one cable update that
    // stopped part way, two offered over the air.
    let mut v = view(vec![
        b(a, found("192.168.0.104"), Status::Online, Some(12), 0),
        b(bb, found("192.168.0.115"), Status::Online, Some(15), 0),
        b(c, found("192.168.0.130"), Status::Online, Some(20), 0),
        b(d, found("192.168.0.140"), Status::Online, Some(25), 0),
    ]);
    v.updates.boards = vec![
        upd(a, offer("4.2", false, true), Job::Ready),
        BoardUpd {
            resumable: Some("writing the new firmware (2 of 4)".into()),
            ..upd(bb, offer("4.2", false, false), Job::Stopped)
        },
        upd(c, offer("4.2", false, true), Job::Idle),
        upd(d, offer("4.2", false, true), Job::Idle),
    ];
    states.push(("updating", v));

    // Two pairings that did not complete: pair both again at once.
    states.push((
        "pair-again-two",
        view(vec![
            b(
                a,
                found("192.168.0.104"),
                Status::PairingFailed,
                Some(12),
                0,
            ),
            b(
                bb,
                found("192.168.0.115"),
                Status::PairingExpired,
                Some(14),
                0,
            ),
        ]),
    ));

    let mut v = view(vec![b(
        a,
        found("192.168.0.104"),
        Status::Online,
        Some(12),
        0,
    )]);
    v.updates.checking = true;
    states.push(("checking", v));

    let mut v = view(vec![b(
        a,
        found("192.168.0.104"),
        Status::Online,
        Some(12),
        0,
    )]);
    v.updates.companion = CompUpd::Ready {
        to: "4.2.0".into(),
        path: "/tmp/HID-Fi-Companion-4.2.0-macos.dmg".into(),
    };
    states.push(("companion-ready", v));

    let mut v = view(vec![b(
        a,
        found("192.168.0.104"),
        Status::Online,
        Some(12),
        0,
    )]);
    v.sound.state = Some(State {
        system: Share::Unavailable("System Audio Recording is not allowed".into()),
        mic: Share::Unavailable("Microphone access is not allowed for HID-Fi Companion".into()),
    });
    v.sound.mic = true;
    states.push(("sound-blocked", v));

    // Fifty boards: the grid, the filter, the counts.
    let fifty: Vec<BoardView> = (0..50)
        .map(|i| {
            let s = match i % 7 {
                0 => Status::WaitingForApproval,
                1 => Status::Reconnecting,
                _ => Status::Online,
            };
            b(
                &format!("DCB4D9{i:06X}"),
                found(&format!("10.0.{}.{}", i / 200, i % 200 + 2)),
                s,
                Some(8 + (i as u32 * 7) % 190),
                usize::from(i % 11 == 0),
            )
        })
        .collect();
    states.push(("fifty", view(fifty)));

    // Names and text a page must show as text.
    let mut v = view(vec![b(
        a,
        found("192.168.0.104"),
        Status::Problem("<img src=x onerror=window.__pwned=1> & \"quotes\"".into()),
        Some(12),
        0,
    )]);
    v.name = "<script>window.__pwned=1</script>Mac".into();
    states.push(("markup", v));

    // Recovery mode (docs/RECOVERY_MODE.md): BOOT held 8 s on the board.
    let info = |stored: usize, vault_pin: bool| crate::recovery::Info {
        mac: bb.into(),
        firmware: "hid_fi_v4.3".into(),
        left_s: 287,
        ssid: "ESP32-HID-09F7C8".into(),
        open: false,
        generated: true,
        pin_set: true,
        stored,
        vault_pin,
    };
    let rec = |info: Option<crate::recovery::Info>, busy: bool, note: Option<(bool, String)>| {
        let mut v = view(vec![b(
            bb,
            found("192.168.0.103"),
            Status::Online,
            Some(14),
            0,
        )]);
        v.recovery = vec![crate::recovery::RecView {
            mac: bb.into(),
            closes: info
                .as_ref()
                .map(|_| std::time::Instant::now() + std::time::Duration::from_secs(287)),
            info,
            busy,
            note,
        }];
        v
    };
    states.push(("recovery-open", rec(Some(info(0, true)), false, None)));
    states.push(("recovery-erases", rec(Some(info(2, false)), false, None)));
    states.push(("recovery-asking", rec(None, true, None)));
    states.push((
        "recovery-failed",
        rec(
            None,
            false,
            Some((false, crate::recovery::RecError::NotInRecovery.text())),
        ),
    ));

    // Two boards, one in recovery mode: which one must be clear at a glance.
    let mut v = rec(Some(info(0, true)), false, None);
    v.boards
        .insert(0, b(a, found("192.168.0.104"), Status::Online, Some(12), 0));
    states.push(("recovery-two-boards", v));

    // Factory reset (docs/FACTORY_RESET.md): which way it would go, and a reset running.
    use crate::factory::{Path, ResetView, StepState::*};
    let one = |status: Status, board: BoardState| view(vec![b(bb, board, status, Some(14), 0)]);
    states.push((
        "factory-paired",
        one(Status::Online, found("192.168.0.103")),
    ));
    states.push((
        "factory-request",
        one(Status::PairingExpired, found("192.168.0.103")),
    ));
    states.push((
        "factory-unreachable",
        one(Status::Connecting, BoardState::NotOnNetwork),
    ));
    let job = |path: Path,
               steps: &[(&'static str, crate::factory::StepState)],
               note: &str,
               result: Option<bool>,
               left: Option<u64>| {
        let mut v = one(
            if path == Path::Paired {
                Status::Online
            } else {
                Status::PairingExpired
            },
            found("192.168.0.103"),
        );
        v.factory = vec![ResetView {
            mac: bb.into(),
            path,
            steps: steps.to_vec(),
            note: note.into(),
            result,
            left_s: left,
        }];
        v
    };
    states.push((
        "factory-running",
        job(
            Path::Paired,
            &[
                ("Asking the board", Done),
                ("Restarting with its USB link", Done),
                ("Erasing over USB", Doing),
                ("Restarting as new", Todo),
            ],
            "",
            None,
            None,
        ),
    ));
    states.push(("factory-waiting", job(Path::Request, &[("Asking the board", Done),
        ("Waiting for approval on your phone", Doing), ("Erasing and restarting as new", Todo)],
        "Open the board's dashboard on your phone: it asks you to approve \"Studio PC\". Nothing changes until you do.",
        None, Some(95))));
    states.push(("factory-done", job(Path::Paired, &[("Asking the board", Done), ("Restarting with its USB link", Done),
        ("Erasing over USB", Done), ("Restarting as new", Done)],
        "Erased. The board restarted as new: join its WiFi ESP32-HID-09F7C8 again to set it up.", Some(true), None)));
    states.push(("factory-failed", job(Path::Paired, &[("Asking the board", Failed), ("Restarting with its USB link", Todo),
        ("Erasing over USB", Todo), ("Restarting as new", Todo)],
        "An update is running on the board. Try again when it has finished. Nothing was changed.", Some(false), None)));

    states
}

/// One line per state, as tests/golden/companion_window_states.tsv holds them.
/// The version is left out: it changes with every release, the page does not.
pub fn golden() -> String {
    all()
        .into_iter()
        .map(|(name, v)| {
            let mut j = super::model(&v).json;
            j["computer"]["version"] = "TEST".into();
            // Recovery mode's countdown is the clock's; the page only needs a value.
            if let Some(bs) = j["boards"].as_array_mut() {
                for b in bs {
                    if b["recovery"]["left_s"].is_number() {
                        b["recovery"]["left_s"] = 287.into();
                    }
                }
            }
            format!("{name}\t{j}\n")
        })
        .collect()
}
