//! What the window shows: this computer, what concerns it (banners), and a card
//! per board (docs/COMPANION_WINDOW.md). Pure, so every state is tested for what
//! a person reads and what each button does.
//!
//! Each board's words and buttons come from the tray's own model
//! (`menu::model_one`), so the window and the tray never disagree; what the
//! window adds is structure - a state, a latency, a badge, details.
//!
//! Every button has an *id* (`open:DCB4D90A0BB8`), never a position: the page
//! sends the id, and [`Window::action`] looks it up in the model of the moment.
//! A board that appeared between drawing and clicking can never turn a click
//! into another button's action, and an id that no longer applies does nothing.

use crate::menu::{self, Action, Step};
use crate::service::{short_mac, BoardState, BoardView, Remembered, View};
use crate::updates::center::{BoardUpd, CompUpd, UpdCmd};
use crate::updates::{Job, Offer};
use hidfi_agent::session::Status;
use serde_json::{json, Value};

/// The page (window.html): cards, grid, details, selection. The macOS and
/// Windows window shows it in a web view; on Linux the tray opens it in the
/// browser from this app's loopback server (opener.rs).
pub const PAGE: &str = include_str!("window.html");

pub struct Window {
    /// What `render()` on the page takes.
    pub json: Value,
    actions: Vec<(String, Action)>,
}

impl Window {
    /// The action behind a button's id, if that button is still there.
    pub fn action(&self, id: &str) -> Option<&Action> {
        self.actions.iter().find(|(i, _)| i == id).map(|(_, a)| a)
    }

    /// Every id a button carries now (tests).
    pub fn ids(&self) -> Vec<&str> {
        self.actions.iter().map(|(i, _)| i.as_str()).collect()
    }
}

/// The button's id: what it does, and for which board.
fn id_of(a: &Action) -> String {
    match a {
        Action::OpenDashboard(_) => "open".into(),
        Action::AllowScreenRecording => "allow-screen".into(),
        Action::PairAgain(m) => with("pair", m),
        Action::Forget(m) => with("forget", m),
        Action::OpenNetworkSettings => "net-settings".into(),
        Action::AllowMicrophone => "allow-mic".into(),
        Action::Sound {
            system: Some(_), ..
        } => "sound-system".into(),
        Action::Sound { .. } => "sound-mic".into(),
        Action::Update(c) => match c {
            UpdCmd::CheckNow => "check-updates".into(),
            UpdCmd::Update { mac, cable: false } => format!("update:{mac}"),
            UpdCmd::Update { mac, cable: true } => format!("update-cable:{mac}"),
            UpdCmd::Cancel(mac) => format!("cancel:{mac}"),
            UpdCmd::RestartNow(mac) => format!("restart:{mac}"),
            UpdCmd::GetCompanion => "companion-get".into(),
            UpdCmd::OpenInstaller => "companion-install".into(),
        },
    }
}

fn with(what: &str, mac: &Option<String>) -> String {
    match mac {
        Some(m) => format!("{what}:{m}"),
        None => what.into(),
    }
}

/// What a selection of several cards can do together, by kind.
fn bulk_of(a: &Action) -> Option<&'static str> {
    match a {
        Action::OpenDashboard(_) => Some("open"),
        Action::PairAgain(_) => Some("pair"),
        Action::Forget(_) => Some("forget"),
        Action::Update(UpdCmd::Update { .. }) => Some("update"),
        _ => None,
    }
}

/// Actions about this computer, not a board: banners show them, once.
fn about_computer(a: &Action) -> bool {
    matches!(
        a,
        Action::AllowScreenRecording | Action::OpenNetworkSettings | Action::AllowMicrophone
    )
}

/// "DCB4D909F7C8" -> "DC:B4:D9:09:F7:C8".
fn colons(mac: &str) -> String {
    mac.as_bytes()
        .chunks(2)
        .map(|c| String::from_utf8_lossy(c).to_uppercase())
        .collect::<Vec<_>>()
        .join(":")
}

/// "http://192.168.0.104:80/" -> "192.168.0.104".
fn host(url: &str) -> String {
    let h = url
        .split("://")
        .nth(1)
        .unwrap_or(url)
        .split(['/', ':'])
        .next()
        .unwrap_or("");
    h.to_string()
}

fn fw(f: &str) -> String {
    f.trim_start_matches("hid_fi_v").to_string()
}

/// A card's state: a key the page colours by, and the word it shows.
fn state_of(b: &BoardView) -> (&'static str, &'static str) {
    match &b.board {
        BoardState::Looking | BoardState::NotPluggedIn { .. } => ("connecting", "Looking"),
        BoardState::NotOnNetwork => ("offline", "Not on this network"),
        BoardState::Found { .. } => match &b.session {
            Status::Online if b.link.not_answering() => ("offline", "Not answering"),
            Status::Online if b.viewers > 0 => ("live", "Live"),
            Status::Online => ("online", "Online"),
            Status::Reconnecting => ("offline", "Reconnecting"),
            Status::Connecting => ("connecting", "Connecting"),
            Status::NeedsPin => ("attention", "Needs a PIN"),
            Status::WaitingForApproval => ("attention", "Tap Pair"),
            Status::ShowCode(_) => ("attention", "Type the code"),
            Status::PairingFailed => ("attention", "Pairing failed"),
            Status::PairingExpired => ("attention", "Not paired"),
            Status::Problem(_) => ("attention", "Problem"),
        },
    }
}

/// Order on screen: what needs the person first, then live, online,
/// connecting, offline; not plugged in last.
fn rank(state: &str) -> u8 {
    match state {
        "attention" => 0,
        "live" => 1,
        "online" => 2,
        "connecting" => 3,
        "offline" => 4,
        _ => 5,
    }
}

/// The update badge: (text, tone, progress %).
fn badge(u: &BoardUpd) -> Option<(String, &'static str, Option<u64>)> {
    let pct = |b: u64, t: u64| (b * 100).checked_div(t).map(|p| p.min(100));
    Some(match &u.job {
        Job::Checking => ("Checking for updates".into(), "info", None),
        Job::Downloading { bytes, total, .. } => {
            ("Downloading update".into(), "accent", pct(*bytes, *total))
        }
        Job::Cable { bytes, total, .. } => {
            ("Updating by cable".into(), "accent", pct(*bytes, *total))
        }
        Job::Ready => ("Update ready".into(), "accent", Some(100)),
        Job::Restarting => ("Restarting".into(), "accent", None),
        Job::Verifying => ("Checking new firmware".into(), "accent", None),
        Job::RolledBack => ("Update rolled back".into(), "warn", None),
        Job::Failed { .. } => ("Update failed".into(), "warn", None),
        Job::Done { to } => (format!("Updated to {to}"), "ok", None),
        Job::Stopped | Job::Idle => {
            if u.resumable.is_some() {
                ("Update stopped part way".into(), "warn", None)
            } else {
                match &u.offer {
                    Offer::Board { to, security, .. } => (
                        format!(
                            "{}{to}",
                            if *security {
                                "Security update "
                            } else {
                                "Update "
                            }
                        ),
                        if *security { "warn" } else { "info" },
                        None,
                    ),
                    Offer::UpdateCompanionFirst => ("Needs a newer Companion".into(), "warn", None),
                    _ => return None,
                }
            }
        }
    })
}

/// What a card's button says: short, the card already names the board.
fn card_label<'a>(a: &Action, tray: &'a str, upd: Option<&BoardUpd>) -> std::borrow::Cow<'a, str> {
    let to = || match upd.map(|u| &u.offer) {
        Some(Offer::Board { to, .. }) => format!(" to {to}"),
        _ => String::new(),
    };
    match a {
        Action::OpenDashboard(_) => "Open dashboard".into(),
        Action::PairAgain(_) => "Pair again".into(),
        Action::Forget(_) => "Forget pairing".into(),
        Action::Update(UpdCmd::Update { cable: false, .. }) => format!("Update{}", to()).into(),
        Action::Update(UpdCmd::Update { cable: true, .. })
            if upd.is_some_and(|u| u.resumable.is_some()) =>
        {
            "Resume update".into()
        }
        Action::Update(UpdCmd::Update { cable: true, .. }) => {
            format!("Update{} by cable", to()).into()
        }
        Action::Update(UpdCmd::Cancel(_)) if upd.is_some_and(|u| u.job == Job::Ready) => {
            "Keep this version".into()
        }
        Action::Update(UpdCmd::Cancel(_)) => "Cancel update".into(),
        Action::Update(UpdCmd::RestartNow(_)) => "Restart now".into(),
        _ => tray.into(),
    }
}

fn mb(bytes: u64) -> String {
    format!("{:.1} MB", bytes as f64 / 1_000_000.0)
}

fn button(id: String, label: &str, a: &Action, first: bool, board: Option<&str>) -> Value {
    let kind = if matches!(a, Action::Forget(_)) {
        "danger"
    } else if first {
        "primary"
    } else {
        ""
    };
    let confirm = match (a, board) {
        (Action::Forget(_), Some(n)) => Some(format!(
            "Forget the pairing with {n}? Its dashboard will not show this screen until you pair again."
        )),
        _ => None,
    };
    json!({"id": id, "label": label.trim_end_matches('\u{2026}').trim_end_matches("..."), "kind": kind,
           "bulk": bulk_of(a), "confirm": confirm})
}

pub fn model(v: &View) -> Window {
    let mut acts: Vec<(String, Action)> = Vec::new();
    let mut add = |id: String, a: Action| {
        if !acts.iter().any(|(i, _)| *i == id) {
            acts.push((id, a));
        }
    };

    // ---- the boards ----
    let mut cards: Vec<(u8, String, Value)> = Vec::new();
    for b in &v.boards {
        let name = format!("HID-Fi {}", short_mac(&b.mac));
        // This board alone, as the tray would show it. Screen Recording and
        // problems of the app are the computer's: banners, not every card.
        let one = View {
            board: b.board.clone(),
            session: b.session.clone(),
            boards: vec![b.clone()],
            watching: b.viewers,
            capture_allowed: true,
            problem: None,
            ..v.clone()
        };
        let m = menu::model_one(&one, Some(&b.mac));
        let upd = v.updates.board(&b.mac);
        let mut upd_lines = Vec::new();
        if let Some(u) = upd {
            menu::board_update(u, &mut upd_lines, &mut Vec::new());
        }
        let net = menu::net_hint();
        let mut lines: Vec<String> = m
            .lines
            .into_iter()
            .filter(|l| !upd_lines.contains(l) && Some(l.as_str()) != net)
            .collect();
        let line = if lines.is_empty() {
            String::new()
        } else {
            lines.remove(0)
        };
        let (state, label) = state_of(b);
        // The card's buttons: the next steps; the update's own in its panel;
        // forgetting, rarely wanted and not undone, under Details.
        let (mut buttons, mut upd_buttons, mut forget) = (Vec::new(), Vec::new(), None);
        for (tray, a) in m.actions.iter().filter(|(_, a)| !about_computer(a)) {
            let id = match a {
                Action::OpenDashboard(_) => format!("open:{}", b.mac),
                _ => id_of(a),
            };
            let label = card_label(a, tray, upd);
            match a {
                Action::Update(c) => {
                    // Going ahead is the main button; stopping never is.
                    let main = matches!(c, UpdCmd::Update { .. } | UpdCmd::RestartNow(_));
                    upd_buttons.push(button(id.clone(), &label, a, main, Some(&name)))
                }
                Action::Forget(_) => {
                    forget = Some(button(id.clone(), &label, a, false, Some(&name)))
                }
                _ => {
                    let first = buttons.is_empty();
                    buttons.push(button(id.clone(), &label, a, first, Some(&name)))
                }
            }
            add(id, a.clone());
        }
        // Online and nobody watching: the state says it all. Not answering: say
        // so, not what the last heartbeat said.
        let line = if matches!(b.session, Status::Online) && b.link.not_answering() {
            "It stopped answering: it may have lost WiFi, or be restarting.".to_string()
        } else if matches!(b.session, Status::Online) && b.viewers == 0
            || matches!(b.session, Status::ShowCode(_))
        {
            // The code box says it, large, with its own hint.
            String::new()
        } else {
            line
        };
        let update = upd.and_then(|u| update_panel(u, &upd_lines, upd_buttons));
        let (url, firmware) = match &b.board {
            BoardState::Found { url, firmware } => (Some(url.clone()), Some(fw(firmware))),
            _ => (None, None),
        };
        let pairing = match &b.session {
            Status::Online | Status::Reconnecting => "Paired with this computer",
            Status::ShowCode(_) => "Pairing: type the code on the dashboard",
            Status::WaitingForApproval => "Pairing: waiting for Pair on the dashboard",
            Status::NeedsPin => "Not paired: the board needs an access PIN first",
            Status::PairingFailed | Status::PairingExpired => "Not paired",
            _ => "Not paired yet",
        };
        let mut details = vec![
            json!(["MAC", colons(&b.mac), true]),
            json!([
                "Address",
                url.as_deref().map(host).unwrap_or_else(|| "-".into()),
                url.is_some()
            ]),
            json!([
                "Firmware",
                firmware.clone().unwrap_or_else(|| "-".into()),
                false
            ]),
        ];
        if let Some(l) = &v.updates.latest {
            details.push(json!(["Newest release", l, false]));
        }
        if let Some(l) = upd_lines.first() {
            details.push(json!(["Update", l, false]));
        }
        details.push(json!(["Pairing", pairing, false]));
        details.push(json!([
            "Watching",
            match b.viewers {
                0 => "Nobody".to_string(),
                1 => "1 dashboard".to_string(),
                n => format!("{n} dashboards"),
            },
            false
        ]));
        details.push(json!(["Plugged into", "This computer (USB)", false]));
        let code = match &b.session {
            Status::ShowCode(c) if c.len() == 6 => Some(format!("{} {}", &c[..3], &c[3..])),
            Status::ShowCode(c) => Some(c.clone()),
            _ => None,
        };
        let card = json!({
            "mac": b.mac, "name": name, "state": state, "label": label,
            "line": line, "notes": lines, "update_notes": upd_lines,
            "code": code, "url": url, "ip": url.as_deref().map(host), "firmware": firmware,
            "badge": upd.and_then(badge).map(|(t, tone, p)| json!({"text": t, "tone": tone, "progress": p})),
            "update": update, "forget": forget,
            "latency": if url.is_some() { json!({
                "ms": b.link.ms, "last": b.link.last_ms, "spread": b.link.spread_ms, "answer": b.link.answer_ms,
                "last_ok": b.link.last_ok, "failing": b.link.failing}) } else { Value::Null },
            "viewers": b.viewers, "here": true,
            "paired": matches!(b.session, Status::Online | Status::Reconnecting),
            "actions": buttons, "details": details,
        });
        cards.push((rank(state), b.mac.clone(), card));
    }
    for r in &v.remembered {
        cards.push((5, r.mac.clone(), remembered_card(r, &mut add)));
    }
    cards.sort_by(|a, b| a.0.cmp(&b.0).then_with(|| a.1.cmp(&b.1)));
    let count = |s: &[&str]| {
        cards
            .iter()
            .filter(|c| s.contains(&c.2["state"].as_str().unwrap_or("")))
            .count()
    };
    let summary = json!({
        "total": cards.len(),
        "online": count(&["online", "live"]),
        "attention": count(&["attention"]),
        "offline": count(&["offline", "away"]),
        "connecting": count(&["connecting"]),
    });

    // ---- this computer ----
    let mut banners = Vec::new();
    if !v.capture_allowed {
        add("allow-screen".into(), Action::AllowScreenRecording);
        banners.push(json!({"id": "screen", "tone": "warn", "title": "Allow Screen Recording",
            "text": "HID-Fi Companion needs it to show this screen. macOS asks to reopen the app after you allow it.",
            "actions": [{"id": "allow-screen", "label": "Allow Screen Recording", "kind": "primary"}]}));
    }
    if let Some(p) = &v.problem {
        banners.push(json!({"id": "problem", "tone": "warn", "title": "Something needs attention", "text": p, "actions": []}));
    }
    if v.boards.iter().any(|b| b.board == BoardState::NotOnNetwork) {
        let mut a = vec![];
        if let Some(h) = menu::net_hint() {
            add("net-settings".into(), Action::OpenNetworkSettings);
            a.push(json!({"id": "net-settings", "label": "Open network settings", "kind": ""}));
            banners.push(json!({"id": "network", "tone": "info", "title": "A board cannot be reached",
                "text": format!("It is plugged in but not answering on the network. Is it on this computer's WiFi (dashboard: Settings, Home network)? {h}."),
                "actions": a}));
        }
    }
    let mut cl = Vec::new();
    let mut ca = Vec::new();
    menu::companion_update(&v.updates.companion, &mut cl, &mut ca);
    if let Some(title) = cl.first() {
        let tone = match &v.updates.companion {
            CompUpd::Failed { .. } => "warn",
            CompUpd::Ready { .. } => "ok",
            _ => "info",
        };
        let actions: Vec<Value> = ca
            .iter()
            .map(|(l, a)| {
                add(id_of(a), a.clone());
                json!({"id": id_of(a), "label": l.trim_end_matches('\u{2026}'), "kind": "primary"})
            })
            .collect();
        banners.push(json!({"id": "companion", "tone": tone, "title": title,
            "text": match &v.updates.companion {
                CompUpd::Ready { .. } => "Install it when you like: the running Companion hands over to it without losing your pairings.",
                CompUpd::Available { .. } => "Checked against HID-Fi's signed release before it is opened.",
                _ => "",
            }, "actions": actions}));
    }

    let sound = {
        let mut lines = Vec::new();
        let mut sa = Vec::new();
        menu::sound(&v.sound, &mut lines, &mut sa);
        let mut notes = Vec::new();
        let mut extra = Vec::new();
        for (l, a) in &sa {
            add(id_of(a), a.clone());
            if !matches!(a, Action::Sound { .. }) {
                extra.push(
                    json!({"id": id_of(a), "label": l.trim_end_matches('\u{2026}'), "kind": ""}),
                );
            }
        }
        for l in lines {
            if l.contains("cannot be shared") || l.starts_with("Microphone: shared") {
                notes.push(l);
            }
        }
        v.sound.state.as_ref().map(|_| {
            json!({"system": v.sound.system, "mic": v.sound.mic, "notes": notes, "actions": extra})
        })
    };

    add("check-updates".into(), Action::Update(UpdCmd::CheckNow));
    let empty = if v.boards.is_empty() {
        let (title, text) = match &v.board {
            BoardState::Looking => (
                "Looking for HID-Fi\u{2026}".to_string(),
                "This takes a few seconds.".to_string(),
            ),
            BoardState::NotPluggedIn { elsewhere } => (
                "Plug HID-Fi into this computer".to_string(),
                match elsewhere {
                    0 => "Use the board's USB port (the one that types). The Companion finds it by itself.".to_string(),
                    1 => "Use the board's USB port. One HID-Fi is on the network, plugged into another computer.".to_string(),
                    n => format!("Use the board's USB port. {n} HID-Fi boards are on the network, plugged into other computers."),
                },
            ),
            _ => (String::new(), String::new()),
        };
        let st = |s: Step| match s {
            Step::Done => "done",
            Step::Now => "now",
            Step::Later => "",
        };
        Some(json!({"title": title, "text": text,
            "steps": menu::steps(v).iter().map(|(l, s, d)| json!([l, st(*s), d])).collect::<Vec<_>>()}))
    } else {
        None
    };

    let json = json!({
        "computer": {"name": v.name, "version": crate::handover::VERSION, "watching": v.watching},
        "banners": banners,
        "sound": sound,
        "updates": {"latest": v.updates.latest, "problem": v.updates.problem, "checking": v.updates.checking},
        "summary": summary,
        "empty": empty,
        "boards": cards.into_iter().map(|c| c.2).collect::<Vec<_>>(),
    });
    Window {
        json,
        actions: acts,
    }
}

/// The update, as a panel in its card: what, how big, what is new, how far,
/// and its buttons. None when there is nothing to say.
fn update_panel(u: &BoardUpd, lines: &[String], actions: Vec<Value>) -> Option<Value> {
    let (title, tone, progress) = badge(u)?;
    let mut sub = None;
    let mut text: Vec<String> = Vec::new();
    match (&u.job, &u.offer, &u.resumable) {
        (
            Job::Idle | Job::Stopped,
            Offer::Board {
                size,
                changes,
                over_air,
                cable_once,
                ..
            },
            None,
        ) => {
            let from = u.firmware.as_deref().map(fw).unwrap_or_default();
            sub = Some(format!(
                "{}{} \u{b7} {}",
                if from.is_empty() {
                    String::new()
                } else {
                    format!("from {from} \u{b7} ")
                },
                mb(*size),
                if *over_air {
                    "over WiFi"
                } else if *cable_once {
                    "by cable, once"
                } else {
                    "by cable"
                }
            ));
            if let Some((_, c)) = changes.first() {
                if let Some(first) = c.lines().map(str::trim).find(|l| !l.is_empty()) {
                    text.push(first.trim_start_matches(['-', '*', ' ']).to_string());
                }
            }
            if *cable_once {
                text.push("After this one cable update it updates over WiFi.".into());
            }
        }
        (Job::Downloading { paused: true, .. }, ..) => {
            text.push("Paused while you use the board: it carries on when you stop.".into())
        }
        (Job::Downloading { .. }, ..) => {
            text.push("Typing and the pointer stay as quick as ever meanwhile.".into())
        }
        (Job::Checking, ..) => text.push("The board is reading the signed release.".into()),
        (Job::Ready, ..) => {
            text.push("HID-Fi restarts into it once nobody has used it for 5 seconds.".into())
        }
        (Job::Restarting, ..) => text.push("Keep it plugged in.".into()),
        (Job::Verifying, ..) => text
            .push("The new firmware proves itself (about a minute) - keep it plugged in.".into()),
        (Job::Cable { step, .. }, ..) => text.push(format!("{step} - keep both cables in.")),
        (Job::Done { .. }, ..) => {}
        _ => text.extend(lines.iter().cloned()),
    }
    Some(
        json!({"title": title, "tone": tone, "progress": progress, "sub": sub, "text": text, "actions": actions}),
    )
}

fn remembered_card(r: &Remembered, add: &mut impl FnMut(String, Action)) -> Value {
    let name = format!("HID-Fi {}", short_mac(&r.mac));
    let a = Action::Forget(Some(r.mac.clone()));
    let id = id_of(&a);
    add(id.clone(), a.clone());
    json!({
        "mac": r.mac, "name": name, "state": "away", "label": "Not plugged in",
        "line": "Paired with this computer. Plug it in here to share this screen through it.",
        "notes": [], "update_notes": [], "code": null, "url": null,
        "ip": r.url.as_deref().map(host), "firmware": null, "badge": null, "latency": null,
        "viewers": 0, "here": false, "paired": true,
        "actions": [button(id, "Forget pairing", &a, false, Some(&name))], "update": null, "forget": null,
        "details": [
            ["MAC", colons(&r.mac), true],
            ["Last address", r.url.as_deref().map(host).unwrap_or_else(|| "-".into()), r.url.is_some()],
            ["Pairing", "Paired with this computer", false],
            ["Plugged into", "Not this computer", false],
        ],
    })
}

pub mod samples;

#[cfg(test)]
mod tests;
