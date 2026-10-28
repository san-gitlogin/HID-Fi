//! What the tray says, decided from the service's view. Pure, so every state
//! the user can see is tested for its wording and its next step (A4, A5).
//!
//! Rules: one line says what is happening, in plain words; if the user must do
//! something, the next line says exactly what; actions appear only when they
//! do something in this state.

#[cfg(test)]
use crate::service::BoardView;
use crate::updates::center::{BoardUpd, CompUpd, UpdCmd};
use crate::updates::{Job, Offer};
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
    /// For this board (its MAC), or the only one.
    PairAgain(Option<String>),
    Forget(Option<String>),
    /// The OS's own page for what can block the network (macOS Local Network
    /// privacy, Windows network profile).
    OpenNetworkSettings,
    /// An update action; the click is the person's consent.
    Update(UpdCmd),
    /// Share this computer's sound / its microphone (None: leave as is).
    Sound { system: Option<bool>, mic: Option<bool> },
    /// The OS's own page for the Microphone permission.
    AllowMicrophone,
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
    let mut m = model_boards(v);
    sound(&v.sound, &mut m.lines, &mut m.actions);
    companion_update(&v.updates.companion, &mut m.lines, &mut m.actions);
    m
}

/// The two sound switches as a tray shows them, as check marks: (computer's
/// sound on, microphone on) - read from the model's own switch actions, so a
/// tray can never disagree with the window. None: this build has no sound.
pub fn sound_switches(m: &Model) -> Option<(bool, bool)> {
    let sys = m.actions.iter().find_map(|(_, a)| match a {
        Action::Sound { system: Some(to), .. } => Some(!to),
        _ => None,
    })?;
    let mic = m.actions.iter().find_map(|(_, a)| match a {
        Action::Sound { mic: Some(to), .. } => Some(!to),
        _ => None,
    })?;
    Some((sys, mic))
}

/// The model's actions a tray lists as plain items (the sound switches are check marks there).
pub fn tray_actions(m: &Model) -> impl Iterator<Item = &(String, Action)> {
    m.actions.iter().filter(|(_, a)| !matches!(a, Action::Sound { .. }))
}

/// The computer's sound and microphone: what is shared, what stops it, the
/// switches. Once, about this computer (docs/AUDIO.md).
pub fn sound(s: &crate::service::SoundView, lines: &mut Vec<String>, actions: &mut Vec<(String, Action)>) {
    use hidfi_stream::audio::Share;
    let Some(st) = &s.state else { return };
    match &st.system {
        Share::On => lines.push("Sound: this computer's sound is shared with its screen".into()),
        Share::Off => lines.push("Sound: not shared".into()),
        Share::Unavailable(why) => {
            lines.push(format!("Sound cannot be shared: {why}"));
            if why.contains("Screen Recording") && !actions.iter().any(|(_, a)| *a == Action::AllowScreenRecording) {
                actions.push(("Allow Screen Recording\u{2026}".into(), Action::AllowScreenRecording));
            }
        }
    }
    match &st.mic {
        Share::On => lines.push("Microphone: shared - whoever watches hears this room".into()),
        Share::Off => {}
        Share::Unavailable(why) => {
            lines.push(format!("The microphone cannot be shared: {why}"));
            if why.contains("Microphone access") {
                actions.push(("Allow microphone access\u{2026}".into(), Action::AllowMicrophone));
            }
        }
    }
    actions.push((
        if s.system { "Stop sharing this computer's sound" } else { "Share this computer's sound" }.into(),
        Action::Sound { system: Some(!s.system), mic: None },
    ));
    actions.push((
        if s.mic { "Stop sharing the microphone" } else { "Share the microphone" }.into(),
        Action::Sound { system: None, mic: Some(!s.mic) },
    ));
}

fn model_boards(v: &View) -> Model {
    if v.boards.len() <= 1 {
        return model_one(v, None);
    }
    // Several boards plugged in: each one's lines, named by the last six
    // digits of its MAC (what its WiFi and dashboard are called), and its
    // actions for it alone. The icon is the most urgent of them.
    let rank = |i: Icon| match i {
        Icon::Attention => 3,
        Icon::Live => 2,
        Icon::Ready => 1,
        Icon::Idle => 0,
    };
    let mut out = Model {
        icon: Icon::Idle,
        title: None,
        tooltip: String::new(),
        lines: Vec::new(),
        actions: Vec::new(),
    };
    for b in &v.boards {
        let one = View {
            board: b.board.clone(),
            session: b.session.clone(),
            boards: vec![b.clone()],
            ..v.clone()
        };
        let m = model_one(&one, Some(&b.mac));
        let name = crate::service::short_mac(&b.mac);
        if rank(m.icon) > rank(out.icon) {
            out.icon = m.icon;
        }
        if out.title.is_none() {
            out.title = m.title;
        }
        for l in m.lines {
            // Lines about this computer, not a board, once.
            if l.contains("Screen Recording") {
                if !out.lines.contains(&l) {
                    out.lines.push(l);
                }
            } else {
                out.lines.push(format!("HID-Fi {name}: {l}"));
            }
        }
        for (label, a) in m.actions {
            let label = match &a {
                Action::AllowScreenRecording | Action::OpenNetworkSettings => label,
                _ => format!("{label} (HID-Fi {name})"),
            };
            if !out.actions.iter().any(|(l, _)| *l == label) {
                out.actions.push((label, a));
            }
        }
    }
    out.tooltip = format!("HID-Fi Companion: {} boards plugged in", v.boards.len());
    out
}

/// The menu for one board (`mac` names it in actions when there are several).
fn model_one(v: &View, mac: Option<&str>) -> Model {
    let which = mac.map(str::to_string);
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
            lines.push(
                "Is the board on this computer's WiFi? Dashboard: Settings, Home network".into(),
            );
            if let Some(h) = net_hint() {
                lines.push(h.into());
                actions.push((
                    "Open network settings\u{2026}".into(),
                    Action::OpenNetworkSettings,
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
                    actions.push(("Pair again".into(), Action::PairAgain(which.clone())));
                }
                Status::PairingExpired => {
                    icon = Icon::Attention;
                    lines.push("The pairing request was not accepted in time".into());
                    actions.push(("Pair again".into(), Action::PairAgain(which.clone())));
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
                    } else if !v.capture_allowed {
                        // Not "ready" until the screen can be recorded.
                        lines.push("One more step: allow Screen Recording".into());
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
                actions.push(("Forget this pairing…".into(), Action::Forget(which.clone())));
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
    let board_mac = mac.map(str::to_string).or_else(|| v.boards.first().map(|b| b.mac.clone()));
    if let Some(u) = board_mac.as_deref().and_then(|m| v.updates.board(m)) {
        if board_update(u, &mut lines, &mut actions) && icon != Icon::Live {
            icon = Icon::Attention;
        }
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

fn mb(bytes: u64) -> String {
    format!("{:.1} MB", bytes as f64 / 1_000_000.0)
}

fn pct(n: u64, total: u64) -> u64 {
    (n * 100).checked_div(total).unwrap_or(0).min(100)
}

/// The update lines and buttons for one board. True: it needs the person's attention.
pub fn board_update(u: &BoardUpd, lines: &mut Vec<String>, actions: &mut Vec<(String, Action)>) -> bool {
    let mac = u.mac.clone();
    let cancel = || ("Cancel the update".to_string(), Action::Update(UpdCmd::Cancel(mac.clone())));
    match &u.job {
        Job::Checking => {
            lines.push("Update: the board is checking the release…".into());
            actions.push(cancel());
            return false;
        }
        Job::Downloading { bytes, total, paused } => {
            lines.push(format!(
                "Update: downloading, {}%{}",
                pct(*bytes, *total),
                if *paused { " - paused while you use the board" } else { "" }
            ));
            actions.push(cancel());
            return false;
        }
        Job::Ready => {
            lines.push("Update ready: HID-Fi restarts into it once nobody uses it for 5 seconds".into());
            actions.push(("Restart HID-Fi now".into(), Action::Update(UpdCmd::RestartNow(mac.clone()))));
            actions.push((
                "Cancel the update (keep this version)".into(),
                Action::Update(UpdCmd::Cancel(mac.clone())),
            ));
            return false;
        }
        Job::Restarting => {
            lines.push("Update: HID-Fi is restarting into the new version…".into());
            return false;
        }
        Job::Verifying => {
            lines.push("Update: HID-Fi is checking its new firmware (about a minute) - keep it plugged in".into());
            return false;
        }
        Job::Cable { step, bytes, total } => {
            let p = if *total > 0 { format!(", {}%", pct(*bytes, *total)) } else { String::new() };
            lines.push(format!("Updating by cable: {step}{p} - keep both cables in"));
            actions.push(cancel());
            return false;
        }
        Job::Done { to } => lines.push(format!("Updated to {to}")),
        Job::RolledBack => {
            lines.push(
                "The new firmware did not start properly, so HID-Fi went back to the version it had. Nothing was lost"
                    .into(),
            );
            return true;
        }
        Job::Stopped => lines.push("Update cancelled - nothing changed on the board".into()),
        Job::Failed { why } => {
            lines.push(format!("The update did not finish: {why}. The board keeps the version it has"));
            return true;
        }
        Job::Idle => {}
    }
    if let Some(step) = &u.resumable {
        lines.push(format!(
            "A cable update stopped part way ({step}). The board is safe; resuming carries on from there"
        ));
        actions.push((
            "Resume the cable update".into(),
            Action::Update(UpdCmd::Update { mac: mac.clone(), cable: true }),
        ));
        return true;
    }
    match &u.offer {
        Offer::Board { to, size, changes, security, over_air, cable_once } => {
            let fw = u.firmware.as_deref().map(|f| f.trim_start_matches("hid_fi_v").to_string()).unwrap_or_default();
            lines.push(format!(
                "{}Update available: {fw} to {to} ({})",
                if *security { "Security " } else { "" },
                mb(*size)
            ));
            if let Some((_, text)) = changes.first() {
                let first = text.lines().map(str::trim).find(|l| !l.is_empty()).unwrap_or("");
                if !first.is_empty() {
                    lines.push(format!("What's new: {}", first.trim_start_matches(['-', '*', ' '])));
                }
            }
            if *over_air {
                actions.push((format!("Update HID-Fi to {to}"), Action::Update(UpdCmd::Update { mac, cable: false })));
            } else {
                if *cable_once {
                    lines.push("This board needs the cable once: after that it updates over WiFi".into());
                }
                actions.push((
                    format!("Update HID-Fi to {to} by cable…"),
                    Action::Update(UpdCmd::Update { mac, cable: true }),
                ));
            }
        }
        Offer::UpdateCompanionFirst => {
            lines.push("This board's firmware is newer than this Companion: update the Companion".into());
            return true;
        }
        Offer::Verifying => lines.push("HID-Fi is checking its new firmware (about a minute)".into()),
        Offer::UpToDate => {
            if let Some(fw) = &u.firmware {
                lines.push(format!("Firmware {} - up to date", fw.trim_start_matches("hid_fi_v")));
            }
        }
        Offer::Unknown => {}
    }
    false
}

/// This app's own update: once, about this computer.
pub fn companion_update(c: &CompUpd, lines: &mut Vec<String>, actions: &mut Vec<(String, Action)>) {
    match c {
        CompUpd::None => {}
        CompUpd::Available { to, size } => {
            lines.push(format!("HID-Fi Companion {to} is available ({})", mb(*size)));
            actions.push((format!("Download Companion {to}"), Action::Update(UpdCmd::GetCompanion)));
        }
        CompUpd::Downloading { to } => lines.push(format!("Downloading HID-Fi Companion {to}…")),
        CompUpd::Ready { to, .. } => {
            lines.push(format!("HID-Fi Companion {to} is downloaded and checked"));
            actions.push((format!("Install Companion {to}…"), Action::Update(UpdCmd::OpenInstaller)));
        }
        CompUpd::Failed { why } => lines.push(format!("The Companion update did not download: {why}")),
    }
}

/// Where setting up stands, for the window: three steps a person can follow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Done,
    Now,
    Later,
}

/// The window's three steps: a title, where it stands, and what to do.
pub type Steps = [(&'static str, Step, String); 3];

/// The three steps, each with a line saying what happened or what to do -
/// so the window never moves on without saying why (seen live: step 1 ticked
/// itself and the owner thought someone had done something behind their back).
pub fn steps(v: &View) -> Steps {
    if v.boards.len() <= 1 {
        return steps_one(v);
    }
    // Several boards: each step is done when it is done for every board, and
    // says how each one stands.
    let per: Vec<(String, Steps)> = v
        .boards
        .iter()
        .map(|b| {
            let one = View {
                board: b.board.clone(),
                session: b.session.clone(),
                boards: vec![b.clone()],
                ..v.clone()
            };
            (crate::service::short_mac(&b.mac), steps_one(&one))
        })
        .collect();
    let mut out = steps_one(v);
    for (i, step) in out.iter_mut().enumerate() {
        step.1 = if per.iter().all(|(_, s)| s[i].1 == Step::Done) {
            Step::Done
        } else if per.iter().any(|(_, s)| s[i].1 != Step::Later) {
            Step::Now
        } else {
            Step::Later
        };
        step.2 = per
            .iter()
            .map(|(n, s)| format!("HID-Fi {n}: {}", s[i].2))
            .collect::<Vec<_>>()
            .join("\n");
    }
    if out[0].1 == Step::Done {
        out[0].0 = "Plug HID-Fi into this computer (two or more are)";
    }
    out
}

fn steps_one(v: &View) -> Steps {
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
        "Click Allow Screen Recording and switch on HID-Fi Companion (if it already \
         looks on, switch it off and on again), then choose Quit & Reopen. When a \
         screen is first shown, macOS may ask to allow direct access: choose Allow."
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
        (
            "Show this screen on the Screen tab",
            st(ready, paired),
            three,
        ),
    ]
}

/// Whether the window should come forward on its own: the user has to do
/// something they cannot see from the menu bar alone.
pub fn needs_window(v: &View) -> bool {
    if v.boards.len() > 1 {
        return v.boards.iter().any(|b| {
            needs_window(&View {
                board: b.board.clone(),
                session: b.session.clone(),
                boards: vec![],
                ..v.clone()
            })
        });
    }
    matches!(
        v.session,
        Status::ShowCode(_) | Status::WaitingForApproval | Status::NeedsPin
    ) && matches!(v.board, BoardState::Found { .. })
        || matches!(v.board, BoardState::NotOnNetwork)
}

/// A name for "the thing the user is being asked to do now".
pub fn episode(v: &View) -> String {
    if v.boards.len() > 1 {
        return v
            .boards
            .iter()
            .map(|b| format!("{}:{:?}|{:?}", b.mac, b.board, b.session))
            .collect::<Vec<_>>()
            .join(";");
    }
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
        if self.dismissed.as_deref() == Some(ep.as_str())
            || self.shown_for.as_deref() == Some(ep.as_str())
        {
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
            boards: vec![],
            watching: 0,
            capture_allowed: true,
            port: Some(47800),
            name: "Studio PC".into(),
            problem: None,
            updates: Default::default(),
            sound: Default::default(),
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
                .any(|a| a.1 == Action::PairAgain(None)));
        }
        for s in [
            Status::Online,
            Status::WaitingForApproval,
            Status::ShowCode("123456".into()),
        ] {
            assert!(!model(&view(found(), s))
                .actions
                .iter()
                .any(|a| a.1 == Action::PairAgain(None)));
        }
    }

    fn board(mac: &str, board: BoardState, session: Status) -> BoardView {
        BoardView {
            mac: mac.into(),
            board,
            session,
        }
    }

    #[test]
    fn two_boards_plugged_in_are_both_served_each_with_its_own_actions() {
        // Seen live: a second board on the Mac was never offered to pair.
        let found = |ip: &str| BoardState::Found {
            url: format!("http://{ip}"),
            firmware: "hid_fi_v4.0.1".into(),
        };
        let mut v = view(found("192.168.0.101"), Status::Online);
        v.boards = vec![
            board("DCB4D90A0BB8", found("192.168.0.101"), Status::Online),
            board(
                "DCB4D909F7C8",
                found("192.168.0.102"),
                Status::WaitingForApproval,
            ),
        ];
        let m = model(&v);
        assert_eq!(m.icon, Icon::Attention, "one still needs pairing");
        assert!(
            m.lines
                .iter()
                .any(|l| l.starts_with("HID-Fi 0A0BB8: Ready")),
            "{:?}",
            m.lines
        );
        assert!(
            m.lines
                .iter()
                .any(|l| l.starts_with("HID-Fi 09F7C8: Waiting for you to tap Pair")),
            "{:?}",
            m.lines
        );
        assert!(m.actions.iter().any(|(l, a)| l.contains("09F7C8")
            && matches!(a, Action::OpenDashboard(u) if u.ends_with(".102"))));
        assert!(m
            .actions
            .iter()
            .any(|(_, a)| *a == Action::Forget(Some("DCB4D90A0BB8".into()))));
        assert!(
            needs_window(&v),
            "the window comes forward for the one to pair"
        );
        let s = steps(&v);
        assert_eq!(s[0].1, Step::Done, "both plugged in and found");
        assert_eq!(s[1].1, Step::Now, "one still to pair");
        assert!(s[1].2.contains("HID-Fi 09F7C8") && s[1].2.contains("HID-Fi 0A0BB8"));
    }

    #[test]
    fn two_boards_paired_say_ready_and_the_window_stays_away() {
        let found = BoardState::Found {
            url: "http://192.168.0.101".into(),
            firmware: "hid_fi_v4.0.1".into(),
        };
        let mut v = view(found.clone(), Status::Online);
        v.boards = vec![
            board("DCB4D90A0BB8", found.clone(), Status::Online),
            board("DCB4D909F7C8", found, Status::Online),
        ];
        assert_eq!(model(&v).icon, Icon::Ready);
        assert!(!needs_window(&v));
        assert!(steps(&v).iter().all(|s| s.1 == Step::Done));
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
        assert!(
            s[0].2.contains("USB") && s[0].2.contains("esp-hid-da1a2c.local"),
            "how it was found: {}",
            s[0].2
        );
        assert!(
            s[1].2.contains("tap Pair") && s[1].2.contains("Studio PC"),
            "{}",
            s[1].2
        );
        assert!(
            s[1].2.contains("only your dashboard"),
            "what pairing is for: {}",
            s[1].2
        );
        let s = steps(&view(found(), Status::ShowCode("123456".into())));
        assert!(s[1].2.contains("code above"));
        let mut v = view(found(), Status::Online);
        v.capture_allowed = false;
        assert!(
            steps(&v)[2].2.contains("Quit & Reopen"),
            "the restart macOS asks for is expected"
        );
        v.capture_allowed = true;
        v.watching = 3;
        assert_eq!(steps(&v)[2].2, "Shown on 3 screens now.");
        for b in [
            BoardState::Looking,
            BoardState::NotPluggedIn { elsewhere: 0 },
            BoardState::NotOnNetwork,
        ] {
            assert!(!steps(&view(b, Status::Connecting))[0].2.is_empty());
        }
    }

    #[test]
    fn pairing_is_the_main_button_until_paired_then_screen_recording() {
        let mut v = view(found(), Status::WaitingForApproval);
        v.capture_allowed = false;
        assert_eq!(
            model(&v).actions[0].1,
            Action::OpenDashboard("http://esp-hid-da1a2c.local".into())
        );
        assert!(model(&v)
            .actions
            .iter()
            .any(|a| a.1 == Action::AllowScreenRecording));
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

    fn with_update(offer: Offer, job: Job) -> View {
        let mut v = view(found(), Status::Online);
        v.boards = vec![BoardView { mac: "B81F3FDA1A2C".into(), board: found(), session: Status::Online }];
        v.updates.boards = vec![BoardUpd { mac: "B81F3FDA1A2C".into(), firmware: Some("hid_fi_v4.0.4".into()),
                                           offer, job, resumable: None }];
        v
    }

    fn offer(over_air: bool, cable_once: bool) -> Offer {
        Offer::Board { to: "4.1.0".into(), size: 1_534_000, changes: vec![("4.1.0".into(), "- Updates over the air.\n".into())],
                       security: false, over_air, cable_once }
    }

    #[test]
    fn an_update_says_the_versions_the_size_and_what_is_new() {
        let m = model(&with_update(offer(true, false), Job::Idle));
        assert!(m.lines.contains(&"Update available: 4.0.4 to 4.1.0 (1.5 MB)".to_string()), "{:?}", m.lines);
        assert!(m.lines.contains(&"What's new: Updates over the air.".to_string()), "{:?}", m.lines);
        assert!(m.actions.iter().any(|(l, a)| l == "Update HID-Fi to 4.1.0"
            && *a == Action::Update(UpdCmd::Update { mac: "B81F3FDA1A2C".into(), cable: false })));
    }

    #[test]
    fn a_board_that_needs_the_cable_says_so_once() {
        let m = model(&with_update(offer(false, true), Job::Idle));
        assert!(m.lines.iter().any(|l| l.contains("needs the cable once")));
        assert!(m.actions.iter().any(|(_, a)| *a == Action::Update(UpdCmd::Update { mac: "B81F3FDA1A2C".into(), cable: true })));
        assert!(!m.actions.iter().any(|(_, a)| *a == Action::Update(UpdCmd::Update { mac: "B81F3FDA1A2C".into(), cable: false })));
    }

    #[test]
    fn every_running_step_can_be_cancelled_and_ready_can_restart_now() {
        for job in [Job::Checking, Job::Downloading { bytes: 1, total: 2, paused: false },
                    Job::Cable { step: "writing the firmware".into(), bytes: 1, total: 2 }] {
            let m = model(&with_update(offer(true, false), job.clone()));
            assert!(m.actions.iter().any(|(_, a)| *a == Action::Update(UpdCmd::Cancel("B81F3FDA1A2C".into()))), "{job:?}");
            assert!(!m.actions.iter().any(|(l, _)| l.starts_with("Update HID-Fi")), "no second update while one runs: {job:?}");
        }
        let m = model(&with_update(offer(true, false), Job::Ready));
        assert!(m.actions.iter().any(|(_, a)| *a == Action::Update(UpdCmd::RestartNow("B81F3FDA1A2C".into()))));
        assert!(m.actions.iter().any(|(_, a)| *a == Action::Update(UpdCmd::Cancel("B81F3FDA1A2C".into()))));
        // While it restarts and proves itself there is nothing to press: a reset now would roll it back.
        for job in [Job::Restarting, Job::Verifying] {
            let m = model(&with_update(offer(true, false), job));
            assert!(!m.actions.iter().any(|(_, a)| matches!(a, Action::Update(_))));
        }
    }

    #[test]
    fn the_paused_download_says_why() {
        let m = model(&with_update(offer(true, false), Job::Downloading { bytes: 45, total: 100, paused: true }));
        assert!(m.lines.contains(&"Update: downloading, 45% - paused while you use the board".to_string()), "{:?}", m.lines);
    }

    #[test]
    fn failures_and_rollbacks_ask_for_attention_and_say_nothing_was_lost() {
        let m = model(&with_update(offer(true, false), Job::RolledBack));
        assert_eq!(m.icon, Icon::Attention);
        assert!(m.lines.iter().any(|l| l.contains("Nothing was lost")));
        let m = model(&with_update(offer(true, false), Job::Failed { why: "the board could not reach the release".into() }));
        assert_eq!(m.icon, Icon::Attention);
        assert!(m.lines.iter().any(|l| l.contains("keeps the version it has")));
    }

    #[test]
    fn a_stopped_cable_update_offers_resume() {
        let mut v = with_update(offer(false, true), Job::Idle);
        v.updates.boards[0].resumable = Some("ImageWritten".into());
        let m = model(&v);
        assert!(m.actions.iter().any(|(l, _)| l == "Resume the cable update"));
        assert_eq!(m.icon, Icon::Attention);
    }

    #[test]
    fn the_companion_update_is_listed_once_with_several_boards() {
        let mut v = with_update(Offer::UpToDate, Job::Idle);
        v.boards.push(BoardView { mac: "DCB4D909F7C8".into(), board: found(), session: Status::Online });
        v.updates.companion = CompUpd::Available { to: "4.1.0".into(), size: 9_000_000 };
        let m = model(&v);
        assert_eq!(m.lines.iter().filter(|l| l.contains("Companion 4.1.0 is available")).count(), 1, "{:?}", m.lines);
        assert_eq!(m.actions.iter().filter(|(_, a)| *a == Action::Update(UpdCmd::GetCompanion)).count(), 1);
        v.updates.companion = CompUpd::Ready { to: "4.1.0".into(), path: "/tmp/x.dmg".into() };
        assert!(model(&v).actions.iter().any(|(_, a)| *a == Action::Update(UpdCmd::OpenInstaller)));
    }

    #[test]
    fn with_several_boards_each_update_names_its_board() {
        let mut v = with_update(offer(true, false), Job::Idle);
        v.boards.push(BoardView { mac: "DCB4D909F7C8".into(), board: found(), session: Status::Online });
        let m = model(&v);
        assert!(m.lines.iter().any(|l| l.starts_with("HID-Fi DA1A2C: Update available")), "{:?}", m.lines);
        assert!(m.actions.iter().any(|(l, _)| l == "Update HID-Fi to 4.1.0 (HID-Fi DA1A2C)"));
    }

    fn with_sound(system: bool, mic: bool, st: Option<hidfi_stream::audio::State>) -> View {
        let mut v = view(found(), Status::Online);
        v.sound = crate::service::SoundView { system, mic, state: st };
        v
    }

    #[test]
    fn sound_shared_by_default_with_both_switches() {
        use hidfi_stream::audio::{Share, State};
        let m = model(&with_sound(true, false, Some(State { system: Share::On, mic: Share::Off })));
        assert!(m.lines.iter().any(|l| l.starts_with("Sound: this computer's sound is shared")));
        assert!(!m.lines.iter().any(|l| l.starts_with("Microphone")), "nothing said about a microphone that is off");
        assert_eq!(sound_switches(&m), Some((true, false)));
        assert!(m.actions.iter().any(|(l, a)| l == "Share the microphone"
            && *a == Action::Sound { system: None, mic: Some(true) }));
        assert!(!tray_actions(&m).any(|(_, a)| matches!(a, Action::Sound { .. })), "check marks in a tray, not items");
    }

    #[test]
    fn a_shared_microphone_is_said_plainly() {
        use hidfi_stream::audio::{Share, State};
        let m = model(&with_sound(true, true, Some(State { system: Share::On, mic: Share::On })));
        assert!(m.lines.iter().any(|l| l.contains("whoever watches hears this room")));
        assert_eq!(sound_switches(&m), Some((true, true)));
    }

    #[test]
    fn what_stops_sound_is_said_with_the_way_to_fix_it() {
        use hidfi_stream::audio::{Share, State};
        let st = State {
            system: Share::Unavailable("Screen Recording is not allowed for HID-Fi Companion".into()),
            mic: Share::Unavailable("Microphone access is not allowed for HID-Fi Companion".into()),
        };
        let m = model(&with_sound(true, true, Some(st)));
        assert!(m.actions.iter().any(|(_, a)| *a == Action::AllowMicrophone));
        assert_eq!(m.actions.iter().filter(|(_, a)| *a == Action::AllowScreenRecording).count(), 1, "once");
        assert!(m.lines.iter().any(|l| l.starts_with("Sound cannot be shared: Screen Recording")));
    }

    #[test]
    fn a_build_without_sound_says_nothing_about_it() {
        let m = model(&with_sound(true, false, None));
        assert!(sound_switches(&m).is_none());
        assert!(!m.lines.iter().any(|l| l.starts_with("Sound")));
    }
}
