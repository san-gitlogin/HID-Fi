//! The tray on Linux: the freedesktop StatusNotifierItem over D-Bus (ksni),
//! shown by KDE, XFCE, LXQt, Cinnamon, and GNOME with the AppIndicator
//! extension (which Ubuntu and Fedora ship). Pure Rust, no GTK.
//!
//! There is no window here: what the macOS and Windows window says, the menu
//! says, and the pairing code is also sent once as a desktop notification, so
//! it is seen without opening anything.
//!
//! NOT YET RUN ON LINUX: compiled and checked from macOS only (see
//! companion/PROGRESS.md for how it gets tested).

use crate::{act, autostart, icons};
use hidfi_companion::menu::{self, Action, Model};
use hidfi_companion::service::{Cmd, Handle};
use hidfi_companion::store::{State, Store};
use ksni::blocking::TrayMethods;
use std::sync::mpsc::{channel, Sender};
use std::time::Duration;

enum Ask {
    Do(Action),
    Login(bool),
    Quit,
}

struct Tray {
    model: Model,
    start_at_login: bool,
    tx: Sender<Ask>,
}

/// ksni wants ARGB32 in network byte order.
fn pixmap(kind: menu::Icon) -> ksni::Icon {
    let (rgba, w, h) = icons::rgba(kind);
    let mut data = Vec::with_capacity(rgba.len());
    for [r, g, b, a] in rgba.as_chunks::<4>().0 {
        data.extend_from_slice(&[*a, *r, *g, *b]);
    }
    ksni::Icon {
        width: w as i32,
        height: h as i32,
        data,
    }
}

impl ksni::Tray for Tray {
    fn id(&self) -> String {
        "hidfi-companion".into()
    }
    fn title(&self) -> String {
        "HID-Fi Companion".into()
    }
    fn icon_pixmap(&self) -> Vec<ksni::Icon> {
        vec![pixmap(self.model.icon)]
    }
    fn status(&self) -> ksni::Status {
        if self.model.icon == menu::Icon::Attention {
            ksni::Status::NeedsAttention
        } else {
            ksni::Status::Active
        }
    }
    fn tool_tip(&self) -> ksni::ToolTip {
        ksni::ToolTip {
            title: "HID-Fi Companion".into(),
            description: self.model.lines.join("\n"),
            ..Default::default()
        }
    }
    fn menu(&self) -> Vec<ksni::MenuItem<Self>> {
        use ksni::menu::*;
        let mut v: Vec<ksni::MenuItem<Self>> = Vec::new();
        for l in &self.model.lines {
            v.push(
                StandardItem {
                    label: l.clone(),
                    enabled: false,
                    ..Default::default()
                }
                .into(),
            );
        }
        if !self.model.actions.is_empty() {
            v.push(MenuItem::Separator);
        }
        for (label, a) in menu::tray_actions(&self.model) {
            let a = a.clone();
            v.push(
                StandardItem {
                    label: label.clone(),
                    activate: Box::new(move |t: &mut Self| {
                        let _ = t.tx.send(Ask::Do(a.clone()));
                    }),
                    ..Default::default()
                }
                .into(),
            );
        }
        v.push(MenuItem::Separator);
        if let Some((sys, mic)) = menu::sound_switches(&self.model) {
            v.push(
                CheckmarkItem {
                    label: "Share this computer's sound".into(),
                    checked: sys,
                    activate: Box::new(move |t: &mut Self| {
                        let _ = t.tx.send(Ask::Do(Action::Sound { system: Some(!sys), mic: None }));
                    }),
                    ..Default::default()
                }
                .into(),
            );
            v.push(
                CheckmarkItem {
                    label: "Share the microphone".into(),
                    checked: mic,
                    activate: Box::new(move |t: &mut Self| {
                        let _ = t.tx.send(Ask::Do(Action::Sound { system: None, mic: Some(!mic) }));
                    }),
                    ..Default::default()
                }
                .into(),
            );
        }
        v.push(
            CheckmarkItem {
                label: "Start at login".into(),
                checked: self.start_at_login,
                activate: Box::new(|t: &mut Self| {
                    t.start_at_login = !t.start_at_login;
                    let _ = t.tx.send(Ask::Login(t.start_at_login));
                }),
                ..Default::default()
            }
            .into(),
        );
        v.push(
            StandardItem {
                label: format!("Version {}", hidfi_companion::handover::VERSION),
                enabled: false,
                ..Default::default()
            }
            .into(),
        );
        v.push(
            StandardItem {
                label: "Quit HID-Fi Companion".into(),
                activate: Box::new(|t: &mut Self| {
                    let _ = t.tx.send(Ask::Quit);
                }),
                ..Default::default()
            }
            .into(),
        );
        v
    }
}

fn notify(body: &str) {
    let r = notify_rust::Notification::new()
        .appname("HID-Fi Companion")
        .summary("HID-Fi Companion")
        .body(body)
        .timeout(notify_rust::Timeout::Milliseconds(60_000))
        .show();
    if let Err(e) = r {
        log::warn!("notification: {e}");
    }
}

pub fn run(handle: Handle, mut state: State, store: Store, first_run: bool) {
    let (tx, rx) = channel();
    let view = || {
        handle
            .view
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    };
    let mut shown = menu::model(&view());
    let tray = Tray {
        model: shown.clone(),
        start_at_login: state.start_at_login,
        tx,
    };
    let tray = match tray.spawn() {
        Ok(t) => Some(t),
        Err(e) => {
            // No tray host (a desktop without StatusNotifier support): keep
            // serving, and say what is going on through notifications instead.
            log::warn!("no tray on this desktop: {e}");
            notify("Running without a tray icon on this desktop. It will notify you when there is something to do.");
            None
        }
    };
    if first_run {
        notify(
            shown
                .lines
                .first()
                .map(String::as_str)
                .unwrap_or("Looking for HID-Fi"),
        );
    }
    let mut told_code: Option<String> = None;
    hidfi_companion::handover::watch(store.dir().to_path_buf(), || {});
    loop {
        let asked = if hidfi_companion::handover::quit_requested() {
            Ok(Ask::Quit)
        } else {
            rx.recv_timeout(Duration::from_millis(500))
        };
        match asked {
            Ok(Ask::Quit) => {
                let _ = handle.tx.send(Cmd::Quit);
                if let Some(t) = &tray {
                    t.shutdown().wait();
                }
                std::thread::sleep(Duration::from_millis(200));
                return;
            }
            Ok(Ask::Login(on)) => {
                state.start_at_login = on;
                if let Err(e) = autostart::set(on) {
                    log::warn!("start at login: {e}");
                }
                let mut s = store.state();
                s.start_at_login = on;
                let _ = store.save_state(&s);
            }
            Ok(Ask::Do(a)) => act::run(&handle, a),
            Err(_) => {}
        }
        let m = menu::model(&view());
        if m != shown {
            // The code, once per code, where it cannot be missed.
            if let Some(code) = &m.title {
                if told_code.as_deref() != Some(code) {
                    notify(&format!(
                        "Type this code on the HID-Fi dashboard's Screen tab: {code}"
                    ));
                    told_code = Some(code.clone());
                }
            }
            if let Some(t) = &tray {
                let mm = m.clone();
                t.update(move |tr| tr.model = mm);
            }
            shown = m;
        }
    }
}
