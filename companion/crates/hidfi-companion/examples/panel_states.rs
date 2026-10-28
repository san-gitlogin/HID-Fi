//! Prints the window's JSON for every state a person can meet, one per line
//! (name<TAB>json), for companion/tests/panel_review.py to render and check.
//! Test tool only.

#[cfg(any(target_os = "macos", windows))]
fn main() {
    use hidfi_agent::session::Status;
    use hidfi_companion::menu;
    use hidfi_companion::panel::view_json;
    use hidfi_companion::service::{BoardState, BoardView, View};
    let found = || BoardState::Found {
        url: "http://192.168.0.106".into(),
        firmware: "hid_fi_v3.11".into(),
    };
    let v = |board: BoardState, session: Status, allowed: bool, watching: usize| View {
        board,
        session,
        boards: vec![],
        watching,
        capture_allowed: allowed,
        port: Some(47800),
        name: "SANTHOSH\u{2019}s MacBook Pro".into(),
        problem: None,
        updates: Default::default(),
        sound: Default::default(),
    };
    let states = [
        (
            "looking",
            v(BoardState::Looking, Status::Connecting, true, 0),
        ),
        (
            "not-plugged",
            v(
                BoardState::NotPluggedIn { elsewhere: 0 },
                Status::Connecting,
                true,
                0,
            ),
        ),
        (
            "not-reachable",
            v(BoardState::NotOnNetwork, Status::Connecting, true, 0),
        ),
        (
            // Two boards plugged in: one paired, one waiting for Pair.
            "two-boards",
            View {
                boards: vec![
                    BoardView {
                        mac: "DCB4D90A0BB8".into(),
                        board: found(),
                        session: Status::Online,
                    },
                    BoardView {
                        mac: "DCB4D909F7C8".into(),
                        board: BoardState::Found {
                            url: "http://192.168.0.102".into(),
                            firmware: "hid_fi_v4.0.1".into(),
                        },
                        session: Status::WaitingForApproval,
                    },
                ],
                ..v(found(), Status::Online, true, 0)
            },
        ),
        ("needs-pin", v(found(), Status::NeedsPin, true, 0)),
        ("waiting", v(found(), Status::WaitingForApproval, false, 0)),
        (
            "code",
            v(found(), Status::ShowCode("482913".into()), false, 0),
        ),
        ("expired", v(found(), Status::PairingExpired, false, 0)),
        ("permission", v(found(), Status::Online, false, 0)),
        ("ready", v(found(), Status::Online, true, 0)),
        ("live", v(found(), Status::Online, true, 3)),
        ("reconnecting", v(found(), Status::Reconnecting, true, 0)),
    ];
    for (name, view) in states {
        println!("{name}\t{}", view_json(&menu::model(&view), &view));
    }
}

#[cfg(not(any(target_os = "macos", windows)))]
fn main() {}
