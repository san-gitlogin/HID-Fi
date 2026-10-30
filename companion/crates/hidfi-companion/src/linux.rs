//! The tray on Linux: the freedesktop StatusNotifierItem over D-Bus (ksni),
//! shown by KDE, XFCE, LXQt, Cinnamon, and GNOME with the AppIndicator
//! extension (which Ubuntu and Fedora ship). Pure Rust, no GTK.
//!
//! The window - the same page as on macOS and Windows, a card per board - opens
//! in the browser from this app's loopback server ("Open window", opener.rs);
//! the menu says the essentials, and the pairing code is also sent once as a
//! desktop notification, so it is seen without opening anything.
//!
//! NOT YET RUN ON LINUX: compiled and checked from macOS only (see
//! companion/PROGRESS.md for how it gets tested).

use crate::{act, autostart, icons};
use hidfi_companion::menu::{self, Action, Model};
use hidfi_companion::opener::WindowLink;
use hidfi_companion::service::{Cmd, Handle};
use hidfi_companion::store::{State, Store};
use hidfi_companion::window;
use ksni::blocking::TrayMethods;
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

enum Ask {
    Do(Action),
    Window,
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
        v.push(
            StandardItem {
                label: "Open window\u{2026}".into(),
                activate: Box::new(|t: &mut Self| {
                    let _ = t.tx.send(Ask::Window);
                }),
                ..Default::default()
            }
            .into(),
        );
        if let Some((sys, mic)) = menu::sound_switches(&self.model) {
            v.push(
                CheckmarkItem {
                    label: "Share this computer's sound".into(),
                    checked: sys,
                    activate: Box::new(move |t: &mut Self| {
                        let _ = t.tx.send(Ask::Do(Action::Sound {
                            system: Some(!sys),
                            mic: None,
                        }));
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
                        let _ = t.tx.send(Ask::Do(Action::Sound {
                            system: None,
                            mic: Some(!mic),
                        }));
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

/// The window for the browser: what it shows, and what its buttons do - by id,
/// in the model of the moment, as on macOS and Windows.
fn serve_window(handle: &Handle, tx: Sender<Ask>) -> Option<Arc<Mutex<Instant>>> {
    let o = act::opener()?;
    let polled = Arc::new(Mutex::new(Instant::now() - IDLE_WINDOW));
    let (v1, v2, h, p) = (
        handle.view.clone(),
        handle.view.clone(),
        handle.health.clone(),
        polled.clone(),
    );
    let tx = Mutex::new(tx);
    o.set_window(WindowLink {
        model: Box::new(move || {
            window::model(&v1.lock().unwrap_or_else(|e| e.into_inner()))
                .json
                .to_string()
        }),
        act: Box::new(move |msg| {
            let Some(id) = msg.strip_prefix("act:") else {
                return;
            };
            let a = window::model(&v2.lock().unwrap_or_else(|e| e.into_inner()))
                .action(id)
                .cloned();
            match a {
                Some(a) => {
                    let _ = tx
                        .lock()
                        .unwrap_or_else(|e| e.into_inner())
                        .send(Ask::Do(a));
                }
                None => log::info!("window: {id} no longer applies"),
            }
        }),
        polled: Box::new(move || {
            *p.lock().unwrap_or_else(|e| e.into_inner()) = Instant::now();
            h.set_active(true);
        }),
    });
    handle.on_change(move || o.changed());
    Some(polled)
}

/// No page has asked for this long: nobody is looking, latency off.
const IDLE_WINDOW: Duration = Duration::from_secs(35);

pub fn run(handle: Handle, mut state: State, store: Store, first_run: bool) {
    let (tx, rx) = channel();
    let polled = serve_window(&handle, tx.clone());
    // `--window-url-file PATH`: where the window is, for tests; `--window`: open it now.
    let args: Vec<String> = std::env::args().collect();
    if let (Some(i), Some(o)) = (
        args.iter().position(|a| a == "--window-url-file"),
        act::opener(),
    ) {
        if let Some(p) = args.get(i + 1) {
            if let Err(e) = std::fs::write(p, o.window_url()) {
                log::warn!("--window-url-file: {e}");
            }
        }
    }
    if args.iter().any(|a| a == "--window") {
        let _ = tx.send(Ask::Window);
    }
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
        } else if hidfi_companion::handover::show_requested() {
            Ok(Ask::Window)
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
            Ok(Ask::Window) => match act::opener() {
                Some(o) => act::open(&o.window_url()),
                None => notify(
                    "The window could not be opened: this app's local page server did not start",
                ),
            },
            Err(_) => {}
        }
        if polled
            .as_ref()
            .is_some_and(|p| p.lock().unwrap_or_else(|e| e.into_inner()).elapsed() >= IDLE_WINDOW)
        {
            handle.health.set_active(false);
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
