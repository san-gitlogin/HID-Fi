//! The app's one window: what is happening, the pairing code in large type,
//! and the next step - in HID-Fi's own look (the dashboard's colours, logo and
//! icon style). It shows the same `menu::Model` the tray does, so the two can
//! never disagree. Built only when shown and destroyed when closed, so an app
//! sitting in the tray holds no web view.

use crate::menu::{self, Model, Step};
use crate::service::View;
use tao::dpi::LogicalSize;
use tao::event_loop::EventLoopWindowTarget;
use tao::window::{Window, WindowBuilder, WindowId};
use wry::{WebView, WebViewBuilder};

/// What a click in the window asks for: the index of a model action, or close.
pub enum Click {
    Action(usize),
    Close,
}

pub struct Panel {
    window: Window,
    view: WebView,
}

const PAGE: &str = r##"<!doctype html><html><head><meta charset="utf-8">
<style>
:root{--bg:#07090f;--surface:rgba(255,255,255,.045);--line:rgba(255,255,255,.10);--line2:rgba(0,210,255,.28);
  --fg:#eaf2f7;--fg2:#93a4b3;--fg3:#5f7180;--acc:#00d2ff;--acc2:#3a7bd5;--ok:#22d39a;--warn:#ffb02e}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{font:400 14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:var(--fg);
  background:radial-gradient(90% 60% at 80% -10%,rgba(0,210,255,.13),transparent 60%),
  linear-gradient(170deg,#07090f,#0d1119 60%,#0a0f18);padding:22px;user-select:none;-webkit-user-select:none;
  display:flex;flex-direction:column;gap:16px;cursor:default;overflow-y:auto}
.brand{display:flex;align-items:center;gap:12px}
.brand b{font-size:15px;font-weight:650;display:block}
.brand span{font-size:12px;color:var(--fg3)}
.tile{width:40px;height:40px;flex:0 0 40px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px}
.head{display:flex;gap:10px;align-items:flex-start}
.head .ic{flex:0 0 22px;margin-top:1px}
#headline{font-weight:620;font-size:15px}
.detail{color:var(--fg2);font-size:13px;margin-top:4px}
.ic{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}
.acc{color:var(--acc)}.ok{color:var(--ok)}.warn{color:var(--warn)}
#code{display:none;text-align:center;padding:18px 10px 14px;border:1px solid var(--line2);border-radius:14px;
  background:rgba(0,210,255,.06)}
#code .digits{font:600 44px/1.1 ui-monospace,'SF Mono',Menlo,Consolas,monospace;letter-spacing:.12em;
  font-variant-numeric:tabular-nums;color:var(--fg)}
#code .hint{color:var(--fg2);font-size:12.5px;margin-top:8px}
ol{list-style:none;display:flex;flex-direction:column;gap:10px}
li{display:flex;gap:10px;align-items:flex-start;color:var(--fg3);font-size:13.5px}
li .t{padding-top:2px}li .d{font-size:12.5px;color:var(--fg3);margin-top:2px;line-height:1.45}
li.now .d{color:var(--fg2)}
li .n{width:24px;height:24px;flex:0 0 24px;border-radius:50%;display:grid;place-items:center;
  border:1px solid var(--line);font-size:12px;font-weight:600}
li.now{color:var(--fg)}li.now .n{border-color:var(--acc);color:var(--acc);box-shadow:0 0 0 3px rgba(0,210,255,.12)}
li.done{color:var(--fg2)}li.done .n{background:rgba(34,211,154,.16);border-color:var(--ok);color:var(--ok)}
.btns{display:flex;flex-direction:column;gap:8px;margin-top:auto}
button{font:inherit;font-weight:560;color:var(--fg);background:var(--surface);border:1px solid var(--line);
  border-radius:10px;min-height:40px;padding:0 14px;cursor:pointer;transition:.15s}
button:hover{border-color:var(--line2)}
button.primary{background:linear-gradient(140deg,var(--acc),var(--acc2));border:none;color:#04121a}
button:active{transform:scale(.98)}
</style></head><body>
<div class="brand">
  <svg class="tile" viewBox="0 0 512 512" aria-hidden="true"><defs><linearGradient id="t" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="#00d2ff"/><stop offset="1" stop-color="#3a7bd5"/></linearGradient></defs>
  <rect width="512" height="512" rx="114" fill="url(#t)"/><g transform="translate(102.4 102.4) scale(12.8)" fill="none"
  stroke="#04121a" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M11.67 21.45A8.9 8.9 0 1 1 21.45 11.67"/>
  <path d="M8.64 8.64 14.96 20.66 15.7 15.7 20.66 14.96Z" fill="#04121a" stroke-width="1.5"/></g></svg>
  <div><b>HID-Fi Companion</b><span id="sub">Shows this computer's screen on the dashboard</span></div>
</div>
<div class="card"><div class="head"><svg class="ic" id="hicon"><use href="#i-info"/></svg>
  <div><div id="headline"></div><div id="details"></div></div></div></div>
<div id="code"><div class="digits" id="digits"></div><div class="hint">Type this on the dashboard's Screen tab</div></div>
<ol id="steps"></ol>
<div class="btns" id="btns"></div>
<svg width="0" height="0" style="position:absolute"><defs>
<symbol id="i-info" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.6v.1"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="m5 12.5 4.5 4.5L19 7.5"/></symbol>
<symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 3.5 21.5 20h-19z"/><path d="M12 10v4.5M12 17.4v.1"/></symbol>
<symbol id="i-live" viewBox="0 0 24 24"><rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8.5 20.5h7M12 17v3.5"/><circle cx="12" cy="10.5" r="2.4" fill="currentColor"/></symbol>
</defs></svg>
<script>
const $=id=>document.getElementById(id);
const esc=s=>{const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;};
window.render=m=>{
  const ic={idle:['i-info',''],ready:['i-check','ok'],live:['i-live','acc'],attention:['i-alert','warn']}[m.icon]||['i-info',''];
  $('hicon').innerHTML='<use href="#'+ic[0]+'"/>';$('hicon').setAttribute('class','ic '+ic[1]);
  // With a code on screen it is said once, large, not again in the headline.
  $('headline').textContent=m.code?'Pair this computer with HID-Fi':(m.lines[0]||'');
  // The steps below say what to do; the card adds only a problem, if any.
  $('details').innerHTML=(m.problem?[m.problem]:[]).map(l=>'<div class="detail">'+esc(l)+'</div>').join('');
  $('code').style.display=m.code?'block':'none';
  $('digits').textContent=m.code||'';
  $('steps').innerHTML=m.steps.map((s,i)=>'<li class="'+s[1]+'"><span class="n">'+
    (s[1]==='done'?'<svg class="ic" style="width:14px;height:14px"><use href="#i-check"/></svg>':(i+1))+'</span>'+
    // What happened (done) and what to do (now); a later step is only its title.
    '<div class="t">'+esc(s[0])+(s[2]&&s[1]?'<div class="d">'+esc(s[2])+'</div>':'')+'</div></li>').join('');
  $('btns').innerHTML=m.actions.map((a,i)=>'<button data-i="'+i+'" class="'+(i===0?'primary':'')+'">'+esc(a)+'</button>').join('')+
    '<button data-close="1">Close</button>';
};
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
  window.ipc.postMessage(b.dataset.close?'close':'act:'+b.dataset.i);});
document.addEventListener('keydown',e=>{if(e.key==='Escape')window.ipc.postMessage('close');});
</script></body></html>"##;

/// The JSON `render()` takes.
pub fn view_json(m: &Model, v: &View) -> String {
    let step = |s: Step| match s {
        Step::Done => "done",
        Step::Now => "now",
        Step::Later => "",
    };
    let icon = match m.icon {
        menu::Icon::Idle => "idle",
        menu::Icon::Ready => "ready",
        menu::Icon::Live => "live",
        menu::Icon::Attention => "attention",
    };
    serde_json::json!({
        "icon": icon,
        "lines": m.lines,
        "problem": v.problem,
        "code": m.title,
        "steps": menu::steps(v).iter().map(|(l, s, d)| [*l, step(*s), d.as_str()]).collect::<Vec<_>>(),
        "actions": m.actions.iter().map(|(l, _)| l).collect::<Vec<_>>(),
    })
    .to_string()
}

impl Panel {
    pub fn open<T: 'static>(
        target: &EventLoopWindowTarget<T>,
        clicks: impl Fn(Click) + 'static,
    ) -> Result<Panel, String> {
        let window = WindowBuilder::new()
            .with_title("HID-Fi Companion")
            .with_inner_size(LogicalSize::new(400.0, 640.0))
            .with_min_inner_size(LogicalSize::new(360.0, 480.0))
            .build(target)
            .map_err(|e| e.to_string())?;
        let view = WebViewBuilder::new()
            .with_html(PAGE)
            .with_background_color((7, 9, 15, 255))
            .with_ipc_handler(move |req| {
                let b = req.body();
                let c = if b == "close" {
                    Some(Click::Close)
                } else {
                    b.strip_prefix("act:")
                        .and_then(|i| i.parse().ok())
                        .map(Click::Action)
                };
                if let Some(c) = c {
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
    use crate::service::BoardState;
    use hidfi_agent::session::Status;

    #[test]
    fn the_window_gets_the_code_steps_and_actions() {
        let v = View {
            board: BoardState::Found {
                url: "http://192.168.0.106".into(),
                firmware: "hid_fi_v3.12".into(),
            },
            session: Status::ShowCode("042117".into()),
            boards: vec![],
            watching: 0,
            capture_allowed: true,
            port: Some(47800),
            name: "Studio PC".into(),
            problem: None,
        };
        let m = menu::model(&v);
        let j: serde_json::Value = serde_json::from_str(&view_json(&m, &v)).unwrap();
        assert_eq!(j["code"], "042 117");
        assert_eq!(j["icon"], "attention");
        assert_eq!(j["steps"][1][1], "now");
        assert_eq!(j["actions"][0], "Open the dashboard");
    }

    #[test]
    fn the_page_escapes_what_it_shows() {
        // Names come from the computer and the board: never parsed as HTML.
        assert!(PAGE.contains("const esc="));
        assert!(PAGE.contains("esc(l)") && PAGE.contains("esc(s[0])") && PAGE.contains("esc(a)"));
        assert!(!PAGE.contains("innerHTML=m.lines[0]"));
    }
}
