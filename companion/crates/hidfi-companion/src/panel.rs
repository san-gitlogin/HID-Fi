//! The app's one window: this computer and a card per board
//! (docs/COMPANION_WINDOW.md), in HID-Fi's own look. Its content is
//! `window::model`, built from the same per-board model the tray shows, so the
//! two never disagree. Built only when shown and destroyed when closed, so an
//! app sitting in the tray holds no web view.
use tao::dpi::LogicalSize;
use tao::event_loop::EventLoopWindowTarget;
use tao::window::{Window, WindowBuilder, WindowId};
use wry::{WebView, WebViewBuilder};

/// What a click in the window asks for: a button's id (`window::Window::action`
/// says what it does now), or close.
#[derive(Debug, PartialEq, Eq)]
pub enum Click {
    Action(String),
    /// Put this on the clipboard (a MAC, an address).
    Copy(String),
    /// A recovery-mode action (docs/RECOVERY_MODE.md): checked by recovery::admit.
    Recovery(crate::recovery::RecMsg),
    Close,
}

/// A message from the page. Anything else is ignored.
pub fn parse(msg: &str) -> Option<Click> {
    if msg == "close" {
        return Some(Click::Close);
    }
    if msg.starts_with("rec:") {
        return crate::recovery::parse_msg(msg).map(Click::Recovery);
    }
    if let Some(t) = msg.strip_prefix("copy:") {
        // What the page copies is a MAC or an address: short, plain.
        return (!t.is_empty() && t.len() <= 64 && t.bytes().all(|b| b.is_ascii_graphic()))
            .then(|| Click::Copy(t.to_string()));
    }
    let id = msg.strip_prefix("act:")?;
    // Ids are short, plain ASCII (`open:DCB4D90A0BB8`).
    (!id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b':' || b == b'-'))
    .then(|| Click::Action(id.to_string()))
}

pub struct Panel {
    window: Window,
    view: WebView,
}

pub use crate::window::PAGE;

impl Panel {
    pub fn open<T: 'static>(
        target: &EventLoopWindowTarget<T>,
        clicks: impl Fn(Click) + 'static,
    ) -> Result<Panel, String> {
        let window = WindowBuilder::new()
            .with_title("HID-Fi Companion")
            .with_inner_size(LogicalSize::new(920.0, 680.0))
            .with_min_inner_size(LogicalSize::new(380.0, 480.0))
            .build(target)
            .map_err(|e| e.to_string())?;
        app_icon(&window);
        let view = WebViewBuilder::new()
            .with_html(PAGE)
            .with_background_color((7, 9, 15, 255))
            .with_ipc_handler(move |req| {
                if let Some(c) = parse(req.body()) {
                    clicks(c);
                }
            })
            .build(&window)
            .map_err(|e| e.to_string())?;
        window.set_focus();
        Ok(Panel { window, view })
    }

    pub fn id(&self) -> WindowId {
        self.window.id()
    }

    /// A recovery action's answer, to this page only (it may hold the WiFi password
    /// the person asked to see: never logged, never in the model).
    pub fn rec_done(&self, json: &str) {
        let _ = self
            .view
            .evaluate_script(&format!("window.recDone&&window.recDone({json})"));
    }

    pub fn render(&self, json: &str) {
        let _ = self
            .view
            .evaluate_script(&format!("window.render&&window.render({json})"));
    }

    /// To the front, from whatever state a person left it in. tao's set_focus
    /// alone skips a window that is minimized or hidden (macOS and Windows) -
    /// in v4.1 and v4.2 Open then did nothing until the app was quit.
    pub fn bring_forward<T: 'static>(&self, target: &EventLoopWindowTarget<T>) {
        #[cfg(target_os = "macos")]
        {
            // Cmd-H hid the whole app: its windows come back only with it.
            use tao::platform::macos::EventLoopWindowTargetExtMacOS;
            target.show_application();
        }
        #[cfg(not(target_os = "macos"))]
        let _ = target;
        if self.window.is_minimized() {
            self.window.set_minimized(false);
        }
        self.window.set_visible(true);
        self.window.set_focus();
    }

    pub fn window(&self) -> &Window {
        &self.window
    }
}

/// Windows: the title bar and the taskbar button show HID-Fi's icon - icon 1 in
/// the .exe (build.rs, assets/app.ico) - at this display's sizes, so Windows picks
/// the closest drawn size. Without it the running app had a blank, generic icon.
/// macOS and Linux take the app's icon from the bundle and the desktop entry.
fn app_icon(window: &Window) {
    #[cfg(windows)]
    {
        use tao::dpi::PhysicalSize;
        use tao::platform::windows::{IconExtWindows, WindowExtWindows};
        use tao::window::Icon;
        let px = |logical: f64| {
            let n = (logical * window.scale_factor()).round() as u32;
            Some(PhysicalSize::new(n, n))
        };
        match (
            Icon::from_resource(1, px(16.0)),
            Icon::from_resource(1, px(32.0)),
        ) {
            (Ok(small), Ok(big)) => {
                window.set_window_icon(Some(small));
                window.set_taskbar_icon(Some(big));
            }
            (a, b) => log::warn!("app icon: {:?} {:?}", a.err(), b.err()),
        }
    }
    #[cfg(not(windows))]
    let _ = window;
}

/// macOS: a Dock icon (and a place in Cmd-Tab) while the window is open, none
/// while the app lives only in the menu bar. Called with whether the window is
/// open now; does nothing unless that changed. Before this the app was a menu-bar
/// app all the time, so its open window had no Dock icon at all.
pub fn dock<T: 'static>(target: &EventLoopWindowTarget<T>, open: bool, shown: &mut bool) {
    if open == *shown {
        return;
    }
    *shown = open;
    #[cfg(target_os = "macos")]
    {
        use tao::platform::macos::{ActivationPolicy, EventLoopWindowTargetExtMacOS};
        target.set_activation_policy_at_runtime(if open {
            ActivationPolicy::Regular
        } else {
            ActivationPolicy::Accessory
        });
    }
    #[cfg(not(target_os = "macos"))]
    let _ = target;
}

/// The window, asked for ("Open HID-Fi Companion...", the app opened again,
/// something new to do): built if there is none, else brought forward.
pub fn show<'a, T: 'static>(
    panel: &'a mut Option<Panel>,
    target: &EventLoopWindowTarget<T>,
    clicks: impl Fn(Click) + 'static,
) -> Result<&'a Panel, String> {
    match panel {
        Some(p) => {
            p.bring_forward(target);
            Ok(p)
        }
        None => Ok(panel.insert(Panel::open(target, clicks)?)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_page_sends_ids_and_the_app_reads_only_ids() {
        assert_eq!(
            parse("act:open:DCB4D90A0BB8"),
            Some(Click::Action("open:DCB4D90A0BB8".into()))
        );
        assert_eq!(
            parse("act:sound-mic"),
            Some(Click::Action("sound-mic".into()))
        );
        assert_eq!(parse("close"), Some(Click::Close));
        // A position (the old protocol), junk, or something long: nothing.
        assert_eq!(
            parse("copy:DC:B4:D9:0A:0B:B8"),
            Some(Click::Copy("DC:B4:D9:0A:0B:B8".into()))
        );
        assert_eq!(
            parse("copy:192.168.0.104"),
            Some(Click::Copy("192.168.0.104".into()))
        );
        let long = format!("copy:{}", "a".repeat(65));
        for bad in ["copy:", "copy:a b", "copy:$(rm -rf)\n", long.as_str()] {
            assert_eq!(parse(bad), None, "{bad}");
        }
        for bad in [
            "act:",
            "act:<script>",
            "act:a b",
            "open",
            "",
            &format!("act:{}", "a".repeat(65)),
        ] {
            assert_eq!(parse(bad), None, "{bad}");
        }
    }

    #[test]
    fn the_page_builds_text_never_markup_from_data() {
        // Names come from the computer and the board: shown as text, always.
        assert!(PAGE.contains("textContent"));
        assert!(
            !PAGE.contains("innerHTML"),
            "the page never parses data as HTML"
        );
        assert!(!PAGE.contains("insertAdjacentHTML") && !PAGE.contains("outerHTML"));
        assert!(!PAGE.contains("window.confirm") && !PAGE.contains("alert("),
            "WKWebView answers confirm() with no unless the app implements it: the page has its own dialog");
    }
}
