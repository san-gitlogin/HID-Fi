//! HID-Fi Companion: a tray / menu-bar app that shows this computer's screen
//! on the HID-Fi dashboard. All the work is in `service`; this file is the
//! tray, the single-instance guard and the first-run setup.

#![cfg_attr(windows, windows_subsystem = "windows")]

mod autostart;
mod logfile;

use hidfi_agent::real::{MdnsBrowse, NusbList, UreqHttp};
use hidfi_companion::menu::{self, Action, Icon};
use hidfi_companion::service::{self, Cmd, Env};
use hidfi_companion::store::Store;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tao::event::Event;
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tray_icon::menu::{CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{TrayIcon, TrayIconBuilder};

fn icon(kind: Icon) -> tray_icon::Icon {
    let name = match kind {
        Icon::Idle => "idle",
        Icon::Ready => "ready",
        Icon::Live => "live",
        Icon::Attention => "attention",
    };
    #[cfg(target_os = "macos")]
    let bytes: &[u8] = match name {
        "idle" => include_bytes!("../assets/mac-idle-44.png"),
        "ready" => include_bytes!("../assets/mac-ready-44.png"),
        "live" => include_bytes!("../assets/mac-live-44.png"),
        _ => include_bytes!("../assets/mac-attention-44.png"),
    };
    #[cfg(not(target_os = "macos"))]
    let bytes: &[u8] = match name {
        "idle" => include_bytes!("../assets/color-idle-64.png"),
        "ready" => include_bytes!("../assets/color-ready-64.png"),
        "live" => include_bytes!("../assets/color-live-64.png"),
        _ => include_bytes!("../assets/color-attention-64.png"),
    };
    let dec = png::Decoder::new(std::io::Cursor::new(bytes));
    let mut r = dec.read_info().expect("bundled icon");
    let mut buf = vec![0; r.output_buffer_size().expect("icon size")];
    let info = r.next_frame(&mut buf).expect("bundled icon");
    buf.truncate(info.buffer_size());
    tray_icon::Icon::from_rgba(buf, info.width, info.height).expect("bundled icon")
}

fn open(target: &str) {
    let r = if cfg!(target_os = "macos") {
        std::process::Command::new("open").arg(target).spawn()
    } else if cfg!(windows) {
        std::process::Command::new("cmd")
            .args(["/C", "start", "", target])
            .spawn()
    } else {
        std::process::Command::new("xdg-open").arg(target).spawn()
    };
    if let Err(e) = r {
        log::warn!("could not open {target}: {e}");
    }
}

const SCREEN_RECORDING_SETTINGS: &str =
    "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture";

/// The menu as built, with the ids that map back to actions.
struct Built {
    menu: Menu,
    actions: Vec<(tray_icon::menu::MenuId, Action)>,
    login: CheckMenuItem,
    quit: MenuItem,
}

fn build(m: &menu::Model, start_at_login: bool) -> Built {
    let menu = Menu::new();
    let _ = menu.append(&MenuItem::new("HID-Fi Companion", false, None));
    let _ = menu.append(&PredefinedMenuItem::separator());
    for l in &m.lines {
        let _ = menu.append(&MenuItem::new(l, false, None));
    }
    let mut actions = Vec::new();
    if !m.actions.is_empty() {
        let _ = menu.append(&PredefinedMenuItem::separator());
    }
    for (label, a) in &m.actions {
        let it = MenuItem::new(label, true, None);
        actions.push((it.id().clone(), a.clone()));
        let _ = menu.append(&it);
    }
    let _ = menu.append(&PredefinedMenuItem::separator());
    let login = CheckMenuItem::new("Start at login", true, start_at_login, None);
    let _ = menu.append(&login);
    let quit = MenuItem::new("Quit HID-Fi Companion", true, None);
    let _ = menu.append(&quit);
    Built {
        menu,
        actions,
        login,
        quit,
    }
}

fn main() {
    // HIDFI_HOME and HIDFI_NO_AUTOSTART exist for development and tests: a
    // test run must not install a login item or touch the real app folder.
    let dir = std::env::var_os("HIDFI_HOME").map(std::path::PathBuf::from).unwrap_or_else(Store::default_dir);
    let no_autostart = std::env::var_os("HIDFI_NO_AUTOSTART").is_some();
    let store = match Store::open(dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("HID-Fi Companion cannot use its folder: {e}");
            std::process::exit(1);
        }
    };
    logfile::FileLog::install(store.dir(), log::LevelFilter::Info);

    // One instance only: a second launch (a double-click, login + manual start)
    // leaves quietly instead of fighting the first for the port.
    let lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .write(true)
        .open(store.dir().join("instance.lock"));
    let _lock = match lock {
        Ok(f) if f.try_lock().is_ok() => f,
        _ => {
            log::info!("already running");
            return;
        }
    };
    log::info!("starting, version {}", env!("CARGO_PKG_VERSION"));
    std::panic::set_hook(Box::new(|p| log::error!("panic: {p}")));

    let mut state = store.state();
    // First run: start at login unless the user turned it off.
    if state.start_at_login && !no_autostart {
        if let Err(e) = autostart::set(true) {
            log::warn!("start at login: {e}");
        }
    }

    let env = Env {
        usb: Box::new(NusbList),
        http: Box::new(UreqHttp::new(Duration::from_secs(7))),
        browse: Box::new(MdnsBrowse::start()),
        source: Arc::new(service::real_source),
        capture_allowed: Arc::new(service::capture_allowed),
    };
    let store_for_settings = Store::open(store.dir().to_path_buf()).expect("same folder");
    let handle = service::start(store, env);

    let mut event_loop = EventLoopBuilder::new().build();
    #[cfg(target_os = "macos")]
    {
        use tao::platform::macos::{ActivationPolicy, EventLoopExtMacOS};
        // A menu-bar app: no Dock icon, no app switcher entry.
        event_loop.set_activation_policy(ActivationPolicy::Accessory);
    }
    let _ = &mut event_loop;

    let mut tray: Option<TrayIcon> = None;
    let mut built: Option<Built> = None;
    let mut shown: Option<menu::Model> = None;
    let menu_rx = MenuEvent::receiver();

    event_loop.run(move |event, _, flow| {
        *flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(500));
        if let Event::NewEvents(tao::event::StartCause::Init) = event {
            let m = menu::model(
                &handle
                    .view
                    .lock()
                    .unwrap_or_else(|e| e.into_inner())
                    .clone(),
            );
            let b = build(&m, state.start_at_login);
            let t = TrayIconBuilder::new()
                .with_icon(icon(m.icon))
                .with_icon_as_template(cfg!(target_os = "macos"))
                .with_tooltip(&m.tooltip)
                .with_menu(Box::new(b.menu.clone()))
                .build();
            match t {
                Ok(t) => {
                    t.set_title(m.title.as_deref());
                    tray = Some(t);
                }
                Err(e) => log::error!("no tray icon: {e}"),
            }
            built = Some(b);
            shown = Some(m);
        }

        while let Ok(ev) = menu_rx.try_recv() {
            let Some(b) = built.as_ref() else { continue };
            if ev.id == *b.quit.id() {
                let _ = handle.tx.send(Cmd::Quit);
                tray = None;
                *flow = ControlFlow::Exit;
                return;
            }
            if ev.id == *b.login.id() {
                state.start_at_login = b.login.is_checked();
                if let Err(e) = autostart::set(state.start_at_login) {
                    log::warn!("start at login: {e}");
                }
                let mut s = store_for_settings.state();
                s.start_at_login = state.start_at_login;
                let _ = store_for_settings.save_state(&s);
                continue;
            }
            if let Some((_, a)) = b.actions.iter().find(|(id, _)| *id == ev.id) {
                match a {
                    Action::OpenDashboard(u) => open(&format!("{u}/#screen")),
                    Action::AllowScreenRecording => {
                        #[cfg(target_os = "macos")]
                        hidfi_stream::sck::ask();
                        open(SCREEN_RECORDING_SETTINGS);
                    }
                    Action::PairAgain => {
                        let _ = handle.tx.send(Cmd::PairAgain);
                    }
                    Action::ChooseBoard(mac, _) => {
                        let _ = handle.tx.send(Cmd::ChooseBoard(mac.clone()));
                    }
                    Action::Forget => {
                        let _ = handle.tx.send(Cmd::Forget);
                    }
                }
            }
        }

        // Redraw only when what the tray says has changed.
        let m = menu::model(
            &handle
                .view
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .clone(),
        );
        if shown.as_ref() != Some(&m) {
            if let Some(t) = tray.as_ref() {
                if shown.as_ref().map(|s| s.icon) != Some(m.icon) {
                    let _ = t.set_icon(Some(icon(m.icon)));
                    t.set_icon_as_template(cfg!(target_os = "macos"));
                }
                t.set_title(m.title.as_deref());
                let _ = t.set_tooltip(Some(&m.tooltip));
                let b = build(&m, state.start_at_login);
                t.set_menu(Some(Box::new(b.menu.clone())));
                built = Some(b);
            }
            shown = Some(m);
        }
    });
}
