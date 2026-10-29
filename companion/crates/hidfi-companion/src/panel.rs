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
    Close,
}

/// A message from the page. Anything else is ignored.
pub fn parse(msg: &str) -> Option<Click> {
    if msg == "close" {
        return Some(Click::Close);
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

    pub fn render(&self, json: &str) {
        let _ = self
            .view
            .evaluate_script(&format!("window.render&&window.render({json})"));
    }

    pub fn focus(&self) {
        self.window.set_focus();
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
