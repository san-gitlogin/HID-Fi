//! What the tray says, decided from the service's view. Pure, so every state
//! the user can see is tested for its wording and its next step (A4, A5).
//!
//! Rules: one line says what is happening, in plain words; if the user must do
//! something, the next line says exactly what; actions appear only when they
//! do something in this state.

use crate::service::{BoardState, View};
use hidfi_agent::session::Status;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Icon {
    /// Nothing to do yet / not set up: dimmed.
    Idle,
    /// Paired and ready; nobody watching.
    Ready,
    /// Someone is watching this screen right now.
    Live,
    /// The user needs to do something.
    Attention,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Action {
    OpenDashboard(String),
    AllowScreenRecording,
    PairAgain,
    ChooseBoard(String, String),
    Forget,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Model {
    pub icon: Icon,
    /// Shown beside the icon (macOS menu bar). Only for the pairing code.
    pub title: Option<String>,
    pub tooltip: String,
    /// Plain lines, shown greyed: what is happening and what to do.
    pub lines: Vec<String>,
    pub actions: Vec<(String, Action)>,
}

fn code_spaced(c: &str) -> String {
    if c.len() == 6 {
        format!("{} {}", &c[..3], &c[3..])
    } else {
        c.to_string()
    }
}

pub fn model(v: &View) -> Model {
    let mut lines = Vec::new();
    let mut actions = Vec::new();
    let mut title = None;
    let mut icon = Icon::Idle;

    match &v.board {
        BoardState::Looking => lines.push("Looking for HID-Fi…".into()),
        BoardState::NotPluggedIn { elsewhere } => {
            lines.push("HID-Fi is not plugged into this computer".into());
            lines.push("Plug the board's USB port into this computer".into());
            if *elsewhere > 0 {
                lines.push(format!(
                    "({elsewhere} seen on the network, plugged in elsewhere)"
                ));
            }
        }
        BoardState::NotOnNetwork => {
            icon = Icon::Attention;
            lines.push("HID-Fi is plugged in, but not on this network".into());
            lines.push(
                "Join the board to your WiFi in the dashboard: Settings, Home network".into(),
            );
        }
        BoardState::Choose(boards) => {
            icon = Icon::Attention;
            lines.push("More than one HID-Fi is plugged in".into());
            lines.push("Choose the one to use:".into());
            for (mac, url) in boards {
                actions.push((
                    format!("Use the board at {}", short_url(url)),
                    Action::ChooseBoard(mac.clone(), url.clone()),
                ));
            }
        }
        BoardState::Found { url, .. } => {
            match &v.session {
                Status::Connecting | Status::Reconnecting => {
                    lines.push(format!("Connecting to HID-Fi at {}…", short_url(url)));
                }
                Status::NeedsPin => {
                    icon = Icon::Attention;
                    lines.push("The board has no access PIN yet".into());
                    lines.push("Set one in the dashboard (Settings) to share this screen".into());
                }
                Status::WaitingForApproval => {
                    icon = Icon::Attention;
                    lines.push(format!("\"{}\" is asking to pair", v.name));
                    lines.push("Open the dashboard and tap Pair on the Screen tab".into());
                }
                Status::ShowCode(c) => {
                    icon = Icon::Attention;
                    title = Some(code_spaced(c));
                    lines.push(format!(
                        "Type this code in the HID-Fi dashboard: {}",
                        code_spaced(c)
                    ));
                }
                Status::PairingFailed => {
                    icon = Icon::Attention;
                    lines.push("Pairing did not complete".into());
                    actions.push(("Pair again".into(), Action::PairAgain));
                }
                Status::PairingExpired => {
                    icon = Icon::Attention;
                    lines.push("The pairing request was not accepted in time".into());
                    actions.push(("Pair again".into(), Action::PairAgain));
                }
                Status::Online => {
                    icon = if v.watching > 0 {
                        Icon::Live
                    } else {
                        Icon::Ready
                    };
                    if v.watching > 0 {
                        lines.push(match v.watching {
                            1 => "This screen is being shown on 1 dashboard".to_string(),
                            n => format!("This screen is being shown on {n} dashboards"),
                        });
                    } else {
                        lines.push("Ready: open the Screen tab on the dashboard".into());
                    }
                }
                Status::Problem(p) => {
                    icon = Icon::Attention;
                    lines.push(p.clone());
                }
            }
            actions.insert(
                0,
                (
                    "Open the dashboard".into(),
                    Action::OpenDashboard(url.clone()),
                ),
            );
            if matches!(v.session, Status::Online) || matches!(v.session, Status::Reconnecting) {
                actions.push(("Forget this pairing…".into(), Action::Forget));
            }
        }
    }
    if !v.capture_allowed {
        if icon != Icon::Live {
            icon = Icon::Attention;
        }
        lines.push("Screen Recording is not allowed for this app yet".into());
        actions.insert(
            0,
            (
                "Allow Screen Recording…".into(),
                Action::AllowScreenRecording,
            ),
        );
    }
    if let Some(p) = &v.problem {
        icon = Icon::Attention;
        lines.push(p.clone());
    }
    let tooltip = format!(
        "HID-Fi Companion: {}",
        lines.first().cloned().unwrap_or_default()
    );
    Model {
        icon,
        title,
        tooltip,
        lines,
        actions,
    }
}

fn short_url(u: &str) -> &str {
    u.trim_start_matches("http://")
        .trim_start_matches("https://")
        .trim_end_matches('/')
}

#[cfg(test)]
mod tests {
    use super::*;

    fn view(board: BoardState, session: Status) -> View {
        View {
            board,
            session,
            watching: 0,
            capture_allowed: true,
            port: Some(47800),
            name: "Studio PC".into(),
            problem: None,
        }
    }
    fn found() -> BoardState {
        BoardState::Found {
            url: "http://esp-hid-da1a2c.local".into(),
            firmware: "hid_fi_v3.12".into(),
        }
    }

    #[test]
    fn not_plugged_in_says_what_to_do() {
        let m = model(&view(
            BoardState::NotPluggedIn { elsewhere: 0 },
            Status::Connecting,
        ));
        assert_eq!(m.icon, Icon::Idle);
        assert!(m.lines[1].contains("Plug"));
        assert!(m.actions.is_empty());
    }

    #[test]
    fn the_code_is_beside_the_icon_and_spelled_out() {
        let m = model(&view(found(), Status::ShowCode("012345".into())));
        assert_eq!(m.title.as_deref(), Some("012 345"));
        assert_eq!(m.icon, Icon::Attention);
        assert!(m.lines[0].contains("012 345"));
    }

    #[test]
    fn waiting_for_approval_points_at_the_dashboard() {
        let m = model(&view(found(), Status::WaitingForApproval));
        assert!(m.lines.iter().any(|l| l.contains("tap Pair")));
        assert!(matches!(m.actions[0].1, Action::OpenDashboard(_)));
    }

    #[test]
    fn being_watched_is_never_hidden() {
        let mut v = view(found(), Status::Online);
        v.watching = 2;
        let m = model(&v);
        assert_eq!(m.icon, Icon::Live);
        assert!(m.lines[0].contains("2 dashboards"));
        // Even with something else to fix, the live state is not masked.
        v.capture_allowed = false;
        assert_eq!(model(&v).icon, Icon::Live);
    }

    #[test]
    fn ready_and_one_viewer() {
        let mut v = view(found(), Status::Online);
        assert_eq!(model(&v).icon, Icon::Ready);
        v.watching = 1;
        assert!(model(&v).lines[0].contains("1 dashboard"));
    }

    #[test]
    fn screen_recording_permission_comes_first() {
        let mut v = view(found(), Status::Online);
        v.capture_allowed = false;
        let m = model(&v);
        assert_eq!(m.actions[0].1, Action::AllowScreenRecording);
        assert_eq!(m.icon, Icon::Attention);
    }

    #[test]
    fn failed_and_expired_offer_pair_again_and_nothing_else_does() {
        for s in [Status::PairingFailed, Status::PairingExpired] {
            assert!(model(&view(found(), s))
                .actions
                .iter()
                .any(|a| a.1 == Action::PairAgain));
        }
        for s in [
            Status::Online,
            Status::WaitingForApproval,
            Status::ShowCode("123456".into()),
        ] {
            assert!(!model(&view(found(), s))
                .actions
                .iter()
                .any(|a| a.1 == Action::PairAgain));
        }
    }

    #[test]
    fn several_boards_are_offered_by_address() {
        let m = model(&view(
            BoardState::Choose(vec![
                ("AA".into(), "http://10.0.0.2".into()),
                ("BB".into(), "http://10.0.0.3".into()),
            ]),
            Status::Connecting,
        ));
        assert_eq!(m.actions.len(), 2);
        assert!(m.actions[1].0.contains("10.0.0.3"));
    }

    #[test]
    fn no_pin_and_off_network_are_named() {
        assert!(model(&view(found(), Status::NeedsPin))
            .lines
            .iter()
            .any(|l| l.contains("access PIN")));
        assert!(
            model(&view(BoardState::NotOnNetwork, Status::Connecting)).lines[1].contains("WiFi")
        );
    }

    #[test]
    fn every_state_has_a_first_line_and_a_tooltip() {
        let boards = [
            BoardState::Looking,
            BoardState::NotPluggedIn { elsewhere: 1 },
            BoardState::NotOnNetwork,
            found(),
        ];
        let sessions = [
            Status::Connecting,
            Status::NeedsPin,
            Status::WaitingForApproval,
            Status::ShowCode("999999".into()),
            Status::PairingFailed,
            Status::PairingExpired,
            Status::Online,
            Status::Reconnecting,
            Status::Problem("x".into()),
        ];
        for b in &boards {
            for s in &sessions {
                let m = model(&view(b.clone(), s.clone()));
                assert!(!m.lines.is_empty() && !m.lines[0].is_empty(), "{b:?} {s:?}");
                assert!(m.tooltip.starts_with("HID-Fi Companion: "));
            }
        }
    }
}
