//! `--window-check OUT.json`: the window's page in this OS's own web view
//! (WKWebView on macOS, WebView2 on Windows) - the engine a person sees, which a
//! test browser only stands in for. Every state the app can show is drawn at
//! the window's size and at its narrowest; a card is selected, opened, copied
//! from and clicked; fifty boards are drawn and updated against the clock.
//! What the page sent the app and what it measured go to OUT, then the app
//! exits. companion/tests/window_real.py runs it and judges (CI: macOS, Windows).

use hidfi_companion::window::{self, samples};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tao::dpi::LogicalSize;
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoopBuilder};
use tao::window::WindowBuilder;
use wry::WebViewBuilder;

/// The narrowest the window can be made (panel.rs).
const NARROW: f64 = 380.0;

const INIT: &str = "window.__errs=[];addEventListener('error',e=>__errs.push(String(e.message)));\
addEventListener('DOMContentLoaded',()=>setTimeout(()=>window.ipc.postMessage('ready'),50));";

const PASS: &str = r#"(async () => {
 const STATES = __STATES__, PASS = __PASS__; const res = {fits: [], engine: navigator.userAgent};
 const frame = () => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
 const shown = e => e.offsetParent && !(e.closest('.details') && !e.closest('.card.open')) && !e.closest('#bulk:not(.on)');
 const check = name => {
   const side = document.scrollingElement.scrollWidth - innerWidth, cut = [], small = [];
   for (const e of document.querySelectorAll('.btn,.chip,.name span,.pill,.seg button,.switch,.link,.lat'))
     if (shown(e) && e.scrollWidth > e.clientWidth + 1) cut.push(e.textContent.trim().slice(0, 30));
   for (const b of document.querySelectorAll('.btn,.switch,.link')) {
     const h = b.getBoundingClientRect().height; if (shown(b) && h < 34) small.push(b.textContent.trim() + ': ' + Math.round(h)); }
   res.fits.push({name, w: innerWidth, side, cut, small, ms: Math.round(__S.lastRender * 100) / 100});
 };
 for (const [name, m] of STATES) { render(m); await frame(); check(name); }
 if (PASS === 1) {
   render(STATES.find(s => s[0] === 'two-boards')[1]); await frame();
   const card = document.querySelector('#grid .card');
   card.querySelector('.statusline').click(); res.selected = __S.sel.size;
   card.querySelector('[data-role=more]').click(); await frame(); res.open = card.classList.contains('open');
   card.querySelector('[data-copy]').click(); await new Promise(r => setTimeout(r, 500));
   res.toast = document.getElementById('toast').textContent;
   card.querySelector('[data-act^="open:"]').click();
   const fifty = JSON.parse(JSON.stringify(STATES.find(s => s[0] === 'fifty')[1]));
   render({...fifty, boards: []}); render(fifty); res.fifty_first = __S.lastRender; let worst = 0;
   for (let i = 0; i < 20; i++) {
     fifty.boards.forEach(c => { if (c.latency && c.latency.ms != null) c.latency.ms = (c.latency.ms + 7) % 240; });
     render(fifty); worst = Math.max(worst, __S.lastRender); }
   res.fifty_update_worst = worst;
 }
 res.errors = window.__errs; window.ipc.postMessage('pass' + PASS + ':' + JSON.stringify(res));
})();"#;

enum Ev {
    Ipc(String),
    Narrow,
}

pub fn run(out: &str) {
    let states: Vec<(String, serde_json::Value)> = samples::all()
        .into_iter()
        .map(|(n, v)| (n.to_string(), window::model(&v).json))
        .collect();
    let states = serde_json::to_string(&states).expect("json");
    let script = move |pass: u8| {
        PASS.replace("__STATES__", &states)
            .replace("__PASS__", &pass.to_string())
    };
    let event_loop = EventLoopBuilder::<Ev>::with_user_event().build();
    let proxy = event_loop.create_proxy();
    let window = WindowBuilder::new()
        .with_title("HID-Fi Companion (window check)")
        .with_inner_size(LogicalSize::new(920.0, 680.0))
        .build(&event_loop)
        .expect("a window");
    let p2 = Mutex::new(proxy.clone());
    let view = WebViewBuilder::new()
        .with_html(window::PAGE)
        .with_initialization_script(INIT)
        .with_background_color((7, 9, 15, 255))
        .with_ipc_handler(move |req| {
            let _ = p2
                .lock()
                .map(|p| p.send_event(Ev::Ipc(req.body().to_string())));
        })
        .build(&window)
        .expect("a web view");
    let report = Arc::new(Mutex::new(serde_json::json!({"acts": []})));
    let out = out.to_string();
    event_loop.run(move |event, _, flow| {
        *flow = ControlFlow::Wait;
        match event {
            Event::UserEvent(Ev::Ipc(m)) => {
                let mut r = report.lock().unwrap_or_else(|e| e.into_inner());
                if m == "ready" {
                    let _ = view.evaluate_script(&script(1));
                } else if let Some(j) = m.strip_prefix("pass1:") {
                    r["pass1"] = serde_json::from_str(j).unwrap_or_default();
                    window.set_inner_size(LogicalSize::new(NARROW, 680.0));
                    let p = proxy.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(Duration::from_millis(600));
                        let _ = p.send_event(Ev::Narrow);
                    });
                } else if let Some(j) = m.strip_prefix("pass2:") {
                    r["pass2"] = serde_json::from_str(j).unwrap_or_default();
                    let _ = std::fs::write(&out, r.to_string());
                    *flow = ControlFlow::Exit;
                } else if let Some(a) = r["acts"].as_array_mut() {
                    a.push(m.into());
                }
            }
            Event::UserEvent(Ev::Narrow) => {
                let _ = view.evaluate_script(&script(2));
            }
            Event::WindowEvent {
                event: WindowEvent::CloseRequested,
                ..
            } => *flow = ControlFlow::Exit,
            _ => {}
        }
    });
}
