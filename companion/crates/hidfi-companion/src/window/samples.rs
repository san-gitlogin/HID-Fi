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
            format!("{name}\t{j}\n")
        })
        .collect()
}
