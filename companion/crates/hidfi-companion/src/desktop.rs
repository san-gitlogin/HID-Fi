//! The tray, menu and window on macOS and Windows (tray-icon, tao, wry).

use crate::{act, autostart, icons};
use hidfi_companion::menu::{self, Action, Icon};
use hidfi_companion::panel::{self, Click, Panel};
use hidfi_companion::service::{Cmd, Handle};
use hidfi_companion::store::{State, Store};
use std::time::{Duration, Instant};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tray_icon::menu::{CheckMenuItem, Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{TrayIcon, TrayIconBuilder};

fn icon(kind: Icon) -> tray_icon::Icon {
    let (px, w, h) = icons::rgba(kind);
    tray_icon::Icon::from_rgba(px, w, h).expect("bundled icon")
}

/// The menu as built, with the ids that map back to actions.
struct Built {
    menu: Menu,
    actions: Vec<(tray_icon::menu::MenuId, Action)>,
    window: MenuItem,
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
    let _ = menu.append(&PredefinedMenuItem::separator());
    let window = MenuItem::new("Open HID-Fi Companion\u{2026}", true, None);
    let _ = menu.append(&window);
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
        window,
        login,
        quit,
    }
}

pub fn run(handle: Handle, mut state: State, store_for_settings: Store, first_run: bool) {
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
    let (click_tx, click_rx) = std::sync::mpsc::channel::<Click>();
    let mut panel: Option<Panel> = None;
    // The window comes forward by itself once per new thing to do.
    let mut auto = menu::AutoOpen::default();
    let mut want_open = first_run;

    event_loop.run(move |event, target, flow| {
        *flow = ControlFlow::WaitUntil(Instant::now() + Duration::from_millis(500));
        let view = handle
            .view
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone();
        match event {
            Event::NewEvents(tao::event::StartCause::Init) => {
                let m = menu::model(&view);
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
            Event::WindowEvent {
                window_id,
                event: WindowEvent::CloseRequested,
                ..
            } if panel.as_ref().is_some_and(|p| p.id() == window_id) => {
                panel = None;
                auto.dismissed(&view);
            }
            _ => {}
        }

        let mut act: Vec<Action> = Vec::new();
        while let Ok(ev) = menu_rx.try_recv() {
            let Some(b) = built.as_ref() else { continue };
            if ev.id == *b.quit.id() {
                let _ = handle.tx.send(Cmd::Quit);
                panel = None;
                tray = None;
                *flow = ControlFlow::Exit;
                return;
            }
            if ev.id == *b.window.id() {
                want_open = true;
                continue;
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
                act.push(a.clone());
            }
        }
        while let Ok(c) = click_rx.try_recv() {
            match c {
                Click::Close => {
                    panel = None;
                    auto.dismissed(&view);
                }
                Click::Action(i) => {
                    if let Some((_, a)) = shown.as_ref().and_then(|m| m.actions.get(i)) {
                        act.push(a.clone());
                    }
                }
            }
        }
        for a in act {
            act::run(&handle, a);
        }

        // The window: opened when asked, or by itself when there is something
        // new to do that the menu bar alone would not show (always so on
        // Windows, which has no menu-bar text for the code).
        if auto.tick(&view) {
            want_open = true;
        }
        let m = menu::model(&view);
        if want_open {
            want_open = false;
            match panel.as_ref() {
                Some(p) => p.focus(),
                None => match Panel::open(target, click_tx.clone()) {
                    Ok(p) => {
                        p.render(&panel::view_json(&m, &view));
                        panel = Some(p);
                    }
                    Err(e) => log::warn!("window: {e}"),
                },
            }
        }

        // Redraw only when what is shown has changed.
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
            if let Some(p) = panel.as_ref() {
                p.render(&panel::view_json(&m, &view));
            }
            shown = Some(m);
        }
    });
}
