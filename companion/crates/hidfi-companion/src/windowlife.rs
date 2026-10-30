//! `--window-life OUT.json`: the window's own life on this OS, through the code
//! the app runs (panel::show). Each way a person can leave the window - closed,
//! minimized, hidden, behind another window, the whole app hidden (macOS) - is
//! set up for real, then the window is asked for the way "Open HID-Fi
//! Companion...", opening the app again and "something new to do" ask for it.
//! The OS's own answer (on screen, not minimized, in front) goes to OUT, then
//! the app exits. companion/tests/window_life.py runs it and judges (CI: macOS,
//! Windows).
//!
//! Why it exists: v4.1 and v4.2 asked tao to focus a minimized window, which
//! tao skips on both OSes - Open did nothing until the app was quit. The
//! page's own tests (window_real.py) never minimize anything.

use hidfi_companion::panel::{self, Panel};
use std::time::{Duration, Instant};
use tao::dpi::LogicalSize;
use tao::event::Event;
use tao::event_loop::{ControlFlow, EventLoopBuilder, EventLoopWindowTarget};
use tao::window::{Window, WindowBuilder};

/// How long the OS gets to finish an animation (minimize, restore).
const SETTLE: Duration = Duration::from_secs(4);

#[derive(Clone, Copy, Debug)]
enum Way {
    /// Never opened: Open builds it.
    NotOpen,
    /// The red/X button: the window is gone, Open builds it again.
    Closed,
    /// The yellow button / Cmd-M / Windows minimize.
    Minimized,
    /// Ordered out without closing.
    Hidden,
    /// Another window in front (another app's, in life).
    Behind,
    /// macOS: Cmd-H hides the whole app.
    #[cfg(target_os = "macos")]
    AppHidden,
}

const WAYS: &[Way] = &[
    Way::NotOpen,
    Way::Minimized,
    Way::Hidden,
    Way::Behind,
    #[cfg(target_os = "macos")]
    Way::AppHidden,
    Way::Closed,
    // Twice in a row: the first fix must not leave a state the second trips on.
    Way::Minimized,
];

enum Phase {
    /// Put the window in this state; wait until the OS says it is.
    Setup(usize, Instant),
    /// Ask for the window; wait until it is on screen and in front.
    Asked(usize, Instant, Instant),
}

fn shown(w: &Window) -> bool {
    w.is_visible() && !w.is_minimized()
}

/// In front, taking input, as the OS sees it. On Windows that is the foreground
/// window: tao's is_focused follows the window's own focus, which WebView2's
/// child takes, so it can read false for a window that is in front.
#[cfg(windows)]
fn front(w: &Window) -> bool {
    use tao::platform::windows::WindowExtWindows;
    #[link(name = "user32")]
    extern "system" {
        fn GetForegroundWindow() -> isize;
        fn GetAncestor(hwnd: isize, flags: u32) -> isize;
    }
    // GA_ROOT: the top-level window of whatever is in front.
    unsafe { GetAncestor(GetForegroundWindow(), 2) == w.hwnd() }
}

#[cfg(not(windows))]
fn front(w: &Window) -> bool {
    w.is_focused()
}

pub fn run(out: &str) {
    let event_loop = EventLoopBuilder::<()>::with_user_event().build();
    #[cfg(target_os = "macos")]
    let event_loop = {
        // As the app runs: a menu-bar app with no Dock icon.
        use tao::platform::macos::{ActivationPolicy, EventLoopExtMacOS};
        let mut e = event_loop;
        e.set_activation_policy(ActivationPolicy::Accessory);
        e
    };
    let mut panel: Option<Panel> = None;
    let mut other: Option<Window> = None;
    let mut phase: Option<Phase> = None;
    let mut results: Vec<serde_json::Value> = Vec::new();
    let started = Instant::now();
    let mut last_step: Option<String> = None;
    let out = out.to_string();
    let open = |target: &EventLoopWindowTarget<()>, panel: &mut Option<Panel>| {
        panel::show(panel, target, |_| {}).map(|_| ())
    };

    // The loop is woken the way the app's is: by events from another thread
    // (desktop.rs sleeps in ControlFlow::Wait). tao's timed wake (WaitUntil)
    // was lost on Windows right after a WebView2 window opened, and the check
    // then waited for ever. A watchdog says when the loop stops answering.
    let tick = std::sync::Arc::new(std::sync::atomic::AtomicU64::new(0));
    {
        let (tick, proxy) = (tick.clone(), event_loop.create_proxy());
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_millis(50));
            if proxy.send_event(()).is_err() {
                return;
            }
            let at = tick.load(std::sync::atomic::Ordering::SeqCst);
            let now = started.elapsed().as_millis() as u64;
            if now.saturating_sub(at) > 2000 && now % 2000 < 50 {
                eprintln!("        watchdog: the loop last ran at {at} ms");
            }
        });
    }
    event_loop.run(move |event, target, flow| {
        tick.store(started.elapsed().as_millis() as u64, std::sync::atomic::Ordering::SeqCst);
        *flow = ControlFlow::Wait;
        if let Event::NewEvents(tao::event::StartCause::Init) = event {
            phase = Some(Phase::Setup(0, Instant::now()));
        }
        let Some(ph) = phase.take() else { return };
        let step = match &ph {
            Phase::Setup(i, _) => format!("setup {i}"),
            Phase::Asked(i, ..) => format!("asked {i}"),
        };
        if last_step.as_deref() != Some(step.as_str()) {
            eprintln!("{:>6} ms  {step}", started.elapsed().as_millis());
            last_step = Some(step);
        }
        phase = Some(match ph {
            Phase::Setup(i, since) => {
                let Some(&way) = WAYS.get(i) else {
                    let _ = std::fs::write(&out, serde_json::Value::from(results.clone()).to_string());
                    panel = None;
                    other = None;
                    *flow = ControlFlow::Exit;
                    return;
                };
                // Every case but the first starts from a window on screen.
                if !matches!(way, Way::NotOpen) && panel.is_none() {
                    if let Err(e) = open(target, &mut panel) {
                        results.push(serde_json::json!({"way": format!("{way:?}"), "error": e}));
                        Phase::Setup(i + 1, Instant::now())
                    } else {
                        Phase::Setup(i, Instant::now())
                    }
                } else if since.elapsed() < Duration::from_millis(300) {
                    // Let the OS draw it first: a person never acts within a frame of it opening.
                    Phase::Setup(i, since)
                } else {
                    let w = panel.as_ref().map(Panel::window);
                    let took = match (way, w) {
                        (Way::NotOpen, _) => panel.is_none(),
                        (Way::Closed, Some(_)) => {
                            panel = None; // what CloseRequested does in desktop.rs
                            true
                        }
                        (Way::Closed, None) => true,
                        (Way::Minimized, Some(w)) => {
                            w.set_minimized(true);
                            w.is_minimized()
                        }
                        (Way::Hidden, Some(w)) => {
                            w.set_visible(false);
                            !w.is_visible()
                        }
                        (Way::Behind, Some(w)) => {
                            if other.is_none() {
                                other = WindowBuilder::new()
                                    .with_title("Another window")
                                    .with_inner_size(LogicalSize::new(1000.0, 760.0))
                                    .build(target)
                                    .ok();
                            }
                            if let Some(o) = &other {
                                o.set_focus();
                            }
                            !front(w)
                        }
                        #[cfg(target_os = "macos")]
                        (Way::AppHidden, Some(w)) => {
                            use tao::platform::macos::EventLoopWindowTargetExtMacOS;
                            target.hide_application();
                            !w.is_visible()
                        }
                        _ => false,
                    };
                    if took {
                        let _ = open(target, &mut panel);
                        Phase::Asked(i, Instant::now(), Instant::now())
                    } else if since.elapsed() > SETTLE {
                        results.push(serde_json::json!({"way": format!("{way:?}"), "error": "the OS never reached this state"}));
                        Phase::Setup(i + 1, Instant::now())
                    } else {
                        Phase::Setup(i, since)
                    }
                }
            }
            Phase::Asked(i, at, last) => {
                let way = WAYS[i];
                let w = panel.as_ref().map(Panel::window);
                let ok = w.is_some_and(|w| shown(w) && front(w));
                if ok || at.elapsed() > SETTLE {
                    results.push(serde_json::json!({
                        "way": format!("{way:?}"),
                        "open": w.is_some(),
                        "visible": w.is_some_and(Window::is_visible),
                        "minimized": w.is_some_and(Window::is_minimized),
                        "focused": w.is_some_and(Window::is_focused),
                        "front": w.is_some_and(front),
                        "ms": at.elapsed().as_millis() as u64,
                    }));
                    if let Some(o) = other.take() {
                        drop(o);
                    }
                    if !ok {
                        // Judge each way on its own: start the next from a new window.
                        panel = None;
                    }
                    Phase::Setup(i + 1, Instant::now())
                } else {
                    Phase::Asked(i, at, last)
                }
            }
        });
    });
}
