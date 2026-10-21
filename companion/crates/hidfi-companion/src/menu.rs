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
    /// The OS's own page for what can block the network (macOS Local Network
    /// privacy, Windows network profile).
    OpenNetworkSettings,
}

/// This OS's likely reason a board on USB cannot be reached, besides the board
/// not being on the same WiFi - the one a user cannot guess.
pub fn net_hint() -> Option<&'static str> {
    if cfg!(target_os = "macos") {
        Some("Or macOS is blocking it: System Settings, Privacy & Security, Local Network: switch on HID-Fi Companion")
    } else if cfg!(windows) {
        Some("Or Windows treats this WiFi as Public: set it to Private in Settings, Network & internet")
    } else {
        None
    }
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
            lines.push("HID-Fi is plugged in, but this computer cannot reach it".into());
            lines.push("Is the board on this computer's WiFi? Dashboard: Settings, Home network".into());
            if let Some(h) = net_hint() {
                lines.push(h.into());
                actions.push(("Open network settings\u{2026}".into(), Action::OpenNetworkSettings));
            }
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
                    lines.push("Waiting for you to tap Pair on the dashboard".into());
                    lines.push(format!(
                        "Screen tab, next to \"{}\" (this computer)",
                        v.name
                    ));
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
        // The first button is the main one: pairing comes first while it is
        // pending (allowing makes macOS restart the app), this right after.
        let paired = matches!(v.session, Status::Online | Status::Reconnecting);
        let at = if paired { 0 } else { actions.len().min(1) };
        actions.insert(
            at,
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

/// Where setting up stands, for the window: three steps a person can follow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Done,
    Now,
    Later,
}

/// The three steps, each with a line saying what happened or what to do -
/// so the window never moves on without saying why (seen live: step 1 ticked
/// itself and the owner thought someone had done something behind their back).
pub fn steps(v: &View) -> [(&'static str, Step, String); 3] {
    let found = matches!(v.board, BoardState::Found { .. });
    let paired = found && matches!(v.session, Status::Online | Status::Reconnecting);
    let ready = paired && v.capture_allowed;
    let st = |done: bool, now: bool| {
        if done {
            Step::Done
        } else if now {
            Step::Now
        } else {
            Step::Later
        }
    };
    let one = match &v.board {
        BoardState::Looking => "Looking on this computer's USB ports\u{2026}".to_string(),
        BoardState::NotPluggedIn { .. } => {
            "Plug the board's USB port (not the one marked COM) into this computer.".to_string()
        }
        BoardState::NotOnNetwork => format!(
            "Plugged in, but not reachable. Is the board on this computer's WiFi? Dashboard, \
             Settings, Home network.{}",
            net_hint().map(|h| format!(" {h}.")).unwrap_or_default()
        ),
        BoardState::Choose(_) => "More than one is plugged in: choose one below.".to_string(),
        BoardState::Found { url, .. } => format!(
            "Found on this computer's USB, and on your WiFi at {}. Nothing to do.",
            short_url(url)
        ),
    };
    let two = if !found {
        "Pairing (once) lets only your dashboard see this screen.".to_string()
    } else {
        match &v.session {
            Status::NeedsPin => {
                "First give the board an access PIN: dashboard, Settings, Access PIN.".to_string()
            }
            Status::WaitingForApproval => format!(
                "Pairing (once) lets only your dashboard see this screen. On your phone: \
                 open the dashboard, Screen tab, and tap Pair next to \"{}\".",
                v.name
            ),
            Status::ShowCode(_) => {
                "Type the code above on your phone and tap Pair. It proves the phone is \
                 talking to this computer and not to someone in between."
                    .to_string()
            }
            Status::PairingFailed | Status::PairingExpired => {
                "Not paired this time. Choose Pair again below.".to_string()
            }
            Status::Online | Status::Reconnecting => "Paired. Nothing to do.".to_string(),
            _ => "Connecting to the board\u{2026}".to_string(),
        }
    };
    let three = if !v.capture_allowed {
        "macOS asks once: click Allow Screen Recording, switch on HID-Fi Companion, \
         then choose Quit & Reopen when macOS offers it. When a screen is first \
         shown, macOS may ask to allow direct access to the screen: choose Allow."
            .to_string()
    } else if !paired {
        "After pairing, the Screen tab shows this screen.".to_string()
    } else {
        match v.watching {
            0 => "Ready. Open the Screen tab on the dashboard to see this screen.".to_string(),
            1 => "Shown on 1 screen now.".to_string(),
            n => format!("Shown on {n} screens now."),
        }
    };
    [
        ("Plug HID-Fi into this computer", st(found, true), one),
        ("Pair with the dashboard", st(paired, found), two),
        ("Show this screen on the Screen tab", st(ready, paired), three),
    ]
}

/// Whether the window should come forward on its own: the user has to do
/// something they cannot see from the menu bar alone.
pub fn needs_window(v: &View) -> bool {
    matches!(
        v.session,
        Status::ShowCode(_) | Status::WaitingForApproval | Status::NeedsPin
    ) && matches!(v.board, BoardState::Found { .. })
        || matches!(v.board, BoardState::NotOnNetwork | BoardState::Choose(_))
}

/// A name for "the thing the user is being asked to do now".
pub fn episode(v: &View) -> String {
    format!("{:?}|{:?}", v.board, v.session)
}

/// When the window comes forward by itself: once for each new thing the user
/// must do, and not for one they closed it on. Not again while it stays the
/// same - it used to take focus twice a second while waiting for Pair, so
/// nothing else on the computer could be used (seen live: "froze like hell").
#[derive(Default)]
pub struct AutoOpen {
    shown_for: Option<String>,
    dismissed: Option<String>,
}

impl AutoOpen {
    /// Whether to bring the window forward now. Call once per tick.
    pub fn tick(&mut self, v: &View) -> bool {
        if !needs_window(v) {
            self.shown_for = None;
            return false;
        }
        let ep = episode(v);
        if self.dismissed.as_deref() == Some(ep.as_str()) || self.shown_for.as_deref() == Some(ep.as_str()) {
            return false;
        }
        self.shown_for = Some(ep);
        true
    }
    /// The user closed the window on this: do not bring it back for it.
    pub fn dismissed(&mut self, v: &View) {
        self.dismissed = Some(episode(v));
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

    #[test]
    fn not_reachable_names_this_os_own_cause_and_opens_its_settings() {
        let m = model(&view(BoardState::NotOnNetwork, Status::Connecting));
        let has = |s: &str| m.lines.iter().any(|l| l.contains(s));
        assert!(has("WiFi"), "the board's own network first: {:?}", m.lines);
        let button = m.actions.iter().any(|a| a.1 == Action::OpenNetworkSettings);
        if cfg!(target_os = "macos") {
            assert!(has("Local Network") && button);
        } else if cfg!(windows) {
            assert!(has("Private") && button);
        } else {
            assert!(!button, "no settings page to open on Linux");
        }
    }

    #[test]
    fn the_window_comes_forward_once_per_thing_to_do_not_every_tick() {
        let mut a = AutoOpen::default();
        let waiting = view(found(), Status::WaitingForApproval);
        let opened = (0..100).filter(|_| a.tick(&waiting)).count();
        assert_eq!(opened, 1, "once while waiting for Pair, not twice a second");
        let code = view(found(), Status::ShowCode("123456".into()));
        assert!(a.tick(&code), "a new thing to do: forward again");
        assert!(!a.tick(&code));
        a.dismissed(&code);
        assert!(!a.tick(&code), "closed on it: stays closed");
        assert!(!a.tick(&view(found(), Status::Online)), "nothing to do");
        assert!(a.tick(&waiting), "asked to pair again later: forward again");
    }

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
    fn steps_follow_setting_up() {
        let s = steps(&view(
            BoardState::NotPluggedIn { elsewhere: 0 },
            Status::Connecting,
        ));
        assert_eq!(s.map(|x| x.1), [Step::Now, Step::Later, Step::Later]);
        let s = steps(&view(found(), Status::ShowCode("123456".into())));
        assert_eq!(s.map(|x| x.1), [Step::Done, Step::Now, Step::Later]);
        let s = steps(&view(found(), Status::Online));
        assert_eq!(s.map(|x| x.1), [Step::Done, Step::Done, Step::Done]);
        let mut v = view(found(), Status::Online);
        v.capture_allowed = false;
        assert_eq!(steps(&v)[2].1, Step::Now);
    }

    #[test]
    fn every_step_says_what_happened_or_what_to_do() {
        let s = steps(&view(found(), Status::WaitingForApproval));
        assert!(s[0].2.contains("USB") && s[0].2.contains("esp-hid-da1a2c.local"), "how it was found: {}", s[0].2);
        assert!(s[1].2.contains("tap Pair") && s[1].2.contains("Studio PC"), "{}", s[1].2);
        assert!(s[1].2.contains("only your dashboard"), "what pairing is for: {}", s[1].2);
        let s = steps(&view(found(), Status::ShowCode("123456".into())));
        assert!(s[1].2.contains("code above"));
        let mut v = view(found(), Status::Online);
        v.capture_allowed = false;
        assert!(steps(&v)[2].2.contains("Quit & Reopen"), "the restart macOS asks for is expected");
        v.capture_allowed = true;
        v.watching = 3;
        assert_eq!(steps(&v)[2].2, "Shown on 3 screens now.");
        for b in [
            BoardState::Looking,
            BoardState::NotPluggedIn { elsewhere: 0 },
            BoardState::NotOnNetwork,
            BoardState::Choose(vec![]),
        ] {
            assert!(!steps(&view(b, Status::Connecting))[0].2.is_empty());
        }
    }

    #[test]
    fn pairing_is_the_main_button_until_paired_then_screen_recording() {
        let mut v = view(found(), Status::WaitingForApproval);
        v.capture_allowed = false;
        assert_eq!(model(&v).actions[0].1, Action::OpenDashboard("http://esp-hid-da1a2c.local".into()));
        assert!(model(&v).actions.iter().any(|a| a.1 == Action::AllowScreenRecording));
        let mut v = view(found(), Status::Online);
        v.capture_allowed = false;
        assert_eq!(model(&v).actions[0].1, Action::AllowScreenRecording);
    }

    #[test]
    fn the_window_comes_forward_only_when_needed() {
        assert!(needs_window(&view(
            found(),
            Status::ShowCode("123456".into())
        )));
        assert!(needs_window(&view(found(), Status::WaitingForApproval)));
        assert!(needs_window(&view(
            BoardState::NotOnNetwork,
            Status::Connecting
        )));
        assert!(!needs_window(&view(found(), Status::Online)));
        assert!(!needs_window(&view(
            BoardState::NotPluggedIn { elsewhere: 0 },
            Status::Connecting
        )));
        assert!(!needs_window(&view(
            BoardState::Looking,
            Status::Connecting
        )));
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
