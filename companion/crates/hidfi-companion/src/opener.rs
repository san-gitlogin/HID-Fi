//! "Open the dashboard", made to work for anyone.
//!
//! Seen live: the menu opened `http://192.168.0.101/#screen` and Chrome said
//! ERR_ADDRESS_UNREACHABLE. macOS gives each app its own Local Network switch;
//! HID-Fi Companion had it, Chrome did not, so the board answered the app and
//! the terminal but never the browser. Nothing told the person why.
//!
//! So the menu opens a small page served by this app on 127.0.0.1 instead -
//! loopback is never behind that switch. In the browser, the page asks the
//! board's own name (`esp-hid-xxxxxx.local`, which follows the board when its
//! address changes) and its current address, and goes to the first that
//! answers, the name preferred. When neither answers, the browser cannot reach
//! the network the app reaches: the page says so, names the browser, and its
//! button has the app open the right settings page.
//!
//! Defences: loopback only; the Host header must be this server (a site that
//! rebinds a name to 127.0.0.1 gets nothing); every path carries a token made
//! at start, so no other page can find it or press its button; GET and POST
//! only, nothing else is served; a POST that names an Origin must name this
//! server.
//!
//! On Linux, where the app has no window of its own, the same server serves
//! the window (window.html) to the browser: `/window` the page, `/state` the
//! window's content (a long poll that answers when it changes), `/act` a
//! button's id - the same ids the macOS and Windows window sends.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Ipv4Addr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

/// Requests kept for pages opened from the menu (newest last).
const KEEP: usize = 8;

pub struct Opener {
    port: u16,
    token: String,
    pages: Arc<Mutex<Vec<Page>>>,
    win: Arc<Win>,
}

/// The window, for a browser: what to show, what a click does, and someone looking.
pub struct WindowLink {
    /// The window's JSON now (`window::model(..).json`).
    pub model: Box<dyn Fn() -> String + Send + Sync>,
    /// A message from the page (`act:<id>` or `close`).
    pub act: Box<dyn Fn(&str) + Send + Sync>,
    /// A page asked for the state: someone is looking (latency on).
    pub polled: Box<dyn Fn() + Send + Sync>,
}

#[derive(Default)]
struct Win {
    link: Mutex<Option<Arc<WindowLink>>>,
    version: Mutex<u64>,
    changed: Condvar,
    polls: AtomicUsize,
}

/// How long a page's ask for the state waits for a change.
const POLL_WAIT: Duration = Duration::from_secs(25);
/// Pages asking at once (each holds a thread while it waits).
const MAX_POLLS: usize = 6;

/// One "Open the dashboard": where to look, and the browser it opened in.
#[derive(Clone)]
struct Page {
    candidates: Vec<String>,
    browser: Option<String>,
}

impl Opener {
    /// Starts the server. `open_settings` runs when the page's button is
    /// pressed (the OS's network permission page).
    pub fn start(open_settings: Box<dyn Fn() + Send + Sync>) -> std::io::Result<Opener> {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))?;
        let port = listener.local_addr()?.port();
        let mut t = [0u8; 16];
        getrandom::fill(&mut t).expect("OS randomness");
        let token = hex::encode(t);
        let pages: Arc<Mutex<Vec<Page>>> = Arc::default();
        let win: Arc<Win> = Arc::default();
        let (tk, pg, wn) = (token.clone(), pages.clone(), win.clone());
        let open_settings: Arc<dyn Fn() + Send + Sync> = Arc::from(open_settings);
        std::thread::Builder::new()
            .name("opener".into())
            .spawn(move || {
                for conn in listener.incoming().flatten() {
                    let (tk, pg, os, wn) =
                        (tk.clone(), pg.clone(), open_settings.clone(), wn.clone());
                    std::thread::spawn(move || {
                        let _ = serve(conn, port, &tk, &pg, os.as_ref(), &wn);
                    });
                }
            })?;
        Ok(Opener {
            port,
            token,
            pages,
            win,
        })
    }

    /// Serve the window to browsers (Linux).
    pub fn set_window(&self, link: WindowLink) {
        *self.win.link.lock().unwrap_or_else(|e| e.into_inner()) = Some(Arc::new(link));
    }

    /// The window's content changed: pages waiting for it get it now.
    pub fn changed(&self) {
        *self.win.version.lock().unwrap_or_else(|e| e.into_inner()) += 1;
        self.win.changed.notify_all();
    }

    /// The window's address, for the browser.
    pub fn window_url(&self) -> String {
        format!("http://127.0.0.1:{}/{}/window", self.port, self.token)
    }

    /// The page to open for these addresses of one board, best first.
    /// `browser`: the default browser's name, as the OS tells this app (see
    /// browser.rs) - the page's user agent cannot tell Arc from Chrome.
    pub fn page_for(&self, candidates: Vec<String>, browser: Option<String>) -> String {
        let mut p = self.pages.lock().unwrap_or_else(|e| e.into_inner());
        p.push(Page {
            candidates,
            browser,
        });
        if p.len() > KEEP {
            p.remove(0);
        }
        let n = p.len() - 1;
        format!("http://127.0.0.1:{}/{}/open/{n}", self.port, self.token)
    }
}

/// The addresses to try for a board: its own `.local` name first (it follows
/// the board when the network hands it a new address), then the address the
/// app reached it at. `mac` is 12 hex digits.
pub fn candidates(url: &str, mac: Option<&str>) -> Vec<String> {
    let mut c = Vec::new();
    if let Some(m) = mac.filter(|m| m.len() == 12) {
        c.push(format!("http://esp-hid-{}.local", m[6..].to_lowercase()));
    }
    let u = url.trim_end_matches('/').to_string();
    if !c.contains(&u) {
        c.push(u);
    }
    c
}

fn serve(
    conn: TcpStream,
    port: u16,
    token: &str,
    pages: &Mutex<Vec<Page>>,
    open_settings: &(dyn Fn() + Send + Sync),
    win: &Win,
) -> std::io::Result<()> {
    conn.set_read_timeout(Some(Duration::from_secs(5)))?;
    let rd = conn.try_clone()?;
    hidfi_stream::keep_to_this_process(&rd); // Windows makes a clone inheritable
    let mut r = BufReader::new(rd);
    let mut line = String::new();
    r.read_line(&mut line)?;
    let (mut host, mut origin, mut len) = (None, None, 0usize);
    loop {
        let mut h = String::new();
        if r.read_line(&mut h)? == 0 || h.trim().is_empty() {
            break;
        }
        if let Some((k, v)) = h.split_once(':') {
            let k = k.trim();
            if k.eq_ignore_ascii_case("host") {
                host = Some(v.trim().to_string());
            } else if k.eq_ignore_ascii_case("origin") {
                origin = Some(v.trim().to_string());
            } else if k.eq_ignore_ascii_case("content-length") {
                len = v.trim().parse().unwrap_or(usize::MAX);
            }
        }
    }
    let mut parts = line.split_whitespace();
    let (method, path) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""));
    let here = |h: &str| h == format!("127.0.0.1:{port}") || h == format!("localhost:{port}");
    let ok_host = host.as_deref().is_some_and(here)
        // A POST naming another site is refused, token or not.
        && (method != "POST"
            || origin.as_deref().is_none_or(|o| o.strip_prefix("http://").is_some_and(here)));
    let mut body = String::new();
    if method == "POST" && len <= 256 {
        let mut b = vec![0u8; len];
        r.read_exact(&mut b)?;
        body = String::from_utf8_lossy(&b).into_owned();
    } else if method == "POST" {
        return Ok(());
    }
    let reply = match window_route(method, path, ok_host, token, win, &body) {
        Some(r) => r,
        None => route(method, path, ok_host, token, pages, open_settings),
    };
    let (code, kind, body) = reply;
    let mut w = conn;
    write!(
        w,
        "HTTP/1.1 {code}\r\nContent-Type: {kind}\r\nContent-Length: {}\r\n\
         Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\n\
         X-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n",
        body.len()
    )?;
    w.write_all(body.as_bytes())?;
    w.flush()
}

type Reply = (&'static str, &'static str, String);

/// The window's three paths (Linux); None for any other path.
fn window_route(
    method: &str,
    path: &str,
    ok_host: bool,
    token: &str,
    win: &Win,
    body: &str,
) -> Option<Reply> {
    let rest = path.strip_prefix('/')?.strip_prefix(token)?;
    let (p, query) = rest.split_once('?').unwrap_or((rest, ""));
    if !matches!(p, "/window" | "/state" | "/act") {
        return None;
    }
    let nf = ("404 Not Found", "text/plain", "Not found".to_string());
    let link = win.link.lock().unwrap_or_else(|e| e.into_inner()).clone();
    let Some(link) = link.filter(|_| ok_host) else {
        return Some(nf);
    };
    Some(match (method, p) {
        ("GET", "/window") => (
            "200 OK",
            "text/html; charset=utf-8",
            crate::window::PAGE.replacen(
                "</body>",
                &format!("<script>{BROWSER}</script></body>"),
                1,
            ),
        ),
        ("GET", "/state") => {
            if win.polls.fetch_add(1, Ordering::SeqCst) >= MAX_POLLS {
                win.polls.fetch_sub(1, Ordering::SeqCst);
                return Some(("503 Service Unavailable", "text/plain", "busy".into()));
            }
            (link.polled)();
            let seen: Option<u64> = query.strip_prefix("v=").and_then(|v| v.parse().ok());
            let mut v = win.version.lock().unwrap_or_else(|e| e.into_inner());
            if seen == Some(*v) {
                v = win
                    .changed
                    .wait_timeout_while(v, POLL_WAIT, |now| *now == seen.unwrap_or(u64::MAX))
                    .map(|(g, _)| g)
                    .unwrap_or_else(|e| e.into_inner().0);
            }
            let n = *v;
            drop(v);
            win.polls.fetch_sub(1, Ordering::SeqCst);
            (
                "200 OK",
                "application/json",
                format!("{{\"v\":{n},\"m\":{}}}", (link.model)()),
            )
        }
        ("POST", "/act") => {
            (link.act)(body.trim());
            ("200 OK", "text/plain", "ok".into())
        }
        _ => nf,
    })
}

/// What the browser's copy of the window adds: clicks go to `/act`, the
/// content comes from `/state` as it changes.
const BROWSER: &str = r#"(()=>{const base=location.pathname.replace(/\/window$/,"");let v=-1,gone=false;
window.__hidfiSend=m=>{fetch(base+"/act",{method:"POST",body:m,headers:{"Content-Type":"text/plain"}}).catch(()=>{});};
const off=()=>{if(gone)return;gone=true;document.title="HID-Fi Companion is not running";
  document.body.replaceChildren(Object.assign(document.createElement("p"),{textContent:"HID-Fi Companion is not running. Start it, then open this window from its tray icon.",style:"padding:24px;color:#a3b2bf"}));};
(async()=>{for(;;){try{const r=await fetch(base+"/state?v="+v,{cache:"no-store"});
  if(r.status===404){off();return;}if(!r.ok)throw 0;const j=await r.json();v=j.v;window.render(j.m);}
  catch(e){await new Promise(r=>setTimeout(r,2000));}}})();})();"#;

fn route(
    method: &str,
    path: &str,
    ok_host: bool,
    token: &str,
    pages: &Mutex<Vec<Page>>,
    open_settings: &(dyn Fn() + Send + Sync),
) -> Reply {
    let nf = ("404 Not Found", "text/plain", "Not found".to_string());
    if !ok_host {
        return nf;
    }
    let Some(rest) = path.strip_prefix('/').and_then(|p| p.strip_prefix(token)) else {
        return nf;
    };
    match (method, rest) {
        ("POST", "/settings") => {
            open_settings();
            ("200 OK", "text/plain", "ok".into())
        }
        ("GET", r) if r.starts_with("/open/") => {
            let n: usize = r["/open/".len()..].parse().unwrap_or(usize::MAX);
            let c = pages
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .get(n)
                .cloned();
            match c {
                Some(c) => (
                    "200 OK",
                    "text/html; charset=utf-8",
                    page(&c.candidates, os(), c.browser.as_deref()),
                ),
                None => nf,
            }
        }
        _ => nf,
    }
}

fn os() -> &'static str {
    if cfg!(target_os = "macos") {
        "mac"
    } else if cfg!(windows) {
        "windows"
    } else {
        "linux"
    }
}

/// The page. `candidates` are http:// URLs the app built itself (a `.local`
/// name from the MAC, an address it reached); JSON-encoded into the script.
pub fn page(candidates: &[String], os: &str, browser: Option<&str>) -> String {
    let js = |v: serde_json::Value| v.to_string().replace("</", "<\\/");
    PAGE.replace("@CANDIDATES@", &js(serde_json::json!(candidates)))
        .replace("@BROWSER@", &js(serde_json::json!(browser)))
        .replace("@OS@", os)
}

const PAGE: &str = r##"<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Opening the HID-Fi dashboard</title>
<style>
:root{color-scheme:dark;--bg:#080b11;--card:#0f141d;--fg:#e8edf5;--fg2:#a9b4c4;--acc:#00d2ff;--line:#1f2836}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);
color:var(--fg);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;padding:16px}
.card{max-width:520px;width:100%;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:24px}
h1{font-size:19px;margin:0 0 8px}p{margin:8px 0;color:var(--fg2)}b{color:var(--fg)}
ol{margin:10px 0;padding-left:22px;color:var(--fg2)}li{margin:4px 0}
.row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}
button,a.btn{appearance:none;border:1px solid var(--line);background:#16202d;color:var(--fg);border-radius:10px;
padding:10px 16px;font:inherit;cursor:pointer;text-decoration:none}
button.pri{background:var(--acc);color:#001018;border-color:var(--acc);font-weight:600}
.sp{width:18px;height:18px;border:2px solid var(--line);border-top-color:var(--acc);border-radius:50%;
display:inline-block;vertical-align:-3px;margin-right:8px;animation:s 1s linear infinite}@keyframes s{to{transform:rotate(1turn)}}
code{background:#16202d;padding:1px 6px;border-radius:6px;color:var(--fg)}
[hidden]{display:none!important}
</style></head><body><main class="card">
<div id="wait"><h1><span class="sp"></span>Opening the HID-Fi dashboard</h1><p>Looking for the board from this browser...</p></div>
<div id="fail" hidden>
<h1>This browser cannot reach the board</h1>
<p>HID-Fi Companion reaches the board on your network, so the board is fine. <b id="who">This browser</b> is being kept off the local network.</p>
<div id="mac" hidden><p>macOS gives every app its own switch for this:</p><ol>
<li>Choose <b>Open Privacy &amp; Security</b> below, then choose <b>Local Network</b> in that list
(macOS has no direct link to it).</li>
<li>Switch on <b id="who2">your browser</b>. Listed more than once, or already on? Switch each one off and on
again: macOS adds an entry each time a browser updates itself, and can keep the old setting for the copy that runs.</li>
<li><b>Quit the browser completely</b> (<b>Cmd+Q</b>, not just closing the window) and open it again - macOS
applies the switch only to a browser started after it. Then choose Open the dashboard in HID-Fi Companion.</li></ol></div>
<div id="safari" hidden><p>macOS lets Safari on the local network without asking, so something else is in the way:
a VPN, or a firewall or security app. Is this Mac on the same WiFi as the board?</p></div>
<div id="win" hidden><p>Is a VPN or a security app on, or is this browser set to use a proxy? Those can keep a browser off the local network.
Windows itself does not block it.</p></div>
<div id="lin" hidden><p>Is a VPN, a proxy, or a browser sandbox (Flatpak, Snap) in the way? Those can keep a browser off the local network.</p></div>
<p>Or use another browser: <span id="addrs"></span></p>
<div class="row"><button class="pri" id="again">Try again</button><button id="settings" hidden>Open Privacy &amp; Security</button></div>
</div></main>
<script>
const C=@CANDIDATES@, OS="@OS@", DEF=@BROWSER@;
// Which browser this is. The app says which one it opened (the default);
// the user agent wins only where it is unmistakable - someone who pasted the
// link into another browser. Arc, Vivaldi and Chromium all claim "Chrome".
const OWN_UA=['Brave','Microsoft Edge','Opera','Firefox'], WEBKIT=['Safari','Orion','DuckDuckGo'];
function who(){const u=navigator.userAgent;
  if(navigator.brave)return 'Brave';if(/Edg\//.test(u))return 'Microsoft Edge';if(/OPR\//.test(u))return 'Opera';
  if(/Firefox\//.test(u))return 'Firefox';
  if(/Chrome\//.test(u))return DEF&&!OWN_UA.includes(DEF)&&!WEBKIT.includes(DEF)?DEF:'Google Chrome';
  if(/Safari\//.test(u))return DEF&&WEBKIT.includes(DEF)?DEF:'Safari';
  return DEF||'your browser';}
function probe(u,ms){return new Promise(res=>{const c=new AbortController();const t=setTimeout(()=>{c.abort();res(false)},ms);
  fetch(u+'/api/status',{mode:'no-cors',cache:'no-store',signal:c.signal}).then(()=>{clearTimeout(t);res(true)},()=>{clearTimeout(t);res(false)});});}
async function go(){
  document.getElementById('wait').hidden=false;document.getElementById('fail').hidden=true;
  // All at once; the board's own name wins when it answers (it follows the
  // board to a new address), the address otherwise.
  const ok=await Promise.all(C.map(u=>probe(u,3000)));
  const i=ok.indexOf(true);
  if(i>=0){location.replace(C[i]+'/#screen');return;}
  const b=who();document.getElementById('who').textContent=b;document.getElementById('who2').textContent=b;
  // Safari is never behind macOS's per-app switch: pointing there would mislead.
  const safari=OS==='mac'&&b==='Safari';
  document.getElementById(safari?'safari':OS==='mac'?'mac':OS==='windows'?'win':'lin').hidden=false;
  document.getElementById('settings').hidden=OS!=='mac'||safari;
  document.getElementById('addrs').innerHTML=C.map(u=>'<code>'+u.replace(/[<>&"]/g,'')+'</code>').join(' or ');
  document.getElementById('wait').hidden=true;document.getElementById('fail').hidden=false;
}
document.getElementById('again').onclick=go;
document.getElementById('settings').onclick=()=>fetch(location.pathname.replace(/\/open\/\d+$/,'/settings'),{method:'POST'});
go();
</script></body></html>"##;

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use std::net::SocketAddr;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn get(port: u16, path: &str, host: &str, method: &str) -> (u16, String) {
        let mut s = TcpStream::connect(SocketAddr::from(([127, 0, 0, 1], port))).unwrap();
        write!(
            s,
            "{method} {path} HTTP/1.1\r\nHost: {host}\r\nContent-Length: 0\r\n\r\n"
        )
        .unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).unwrap();
        let code = out[9..12].parse().unwrap();
        (code, out)
    }

    #[test]
    fn the_board_name_comes_first_then_the_address() {
        assert_eq!(
            candidates("http://192.168.0.101/", Some("B81F3FDA1A2C")),
            vec!["http://esp-hid-da1a2c.local", "http://192.168.0.101"]
        );
        assert_eq!(
            candidates("http://esp-hid-da1a2c.local", Some("B81F3FDA1A2C")),
            vec!["http://esp-hid-da1a2c.local"]
        );
        assert_eq!(candidates("http://10.0.0.5", None), vec!["http://10.0.0.5"]);
    }

    #[test]
    fn only_this_app_s_own_pages_and_only_on_its_own_host() {
        let pressed = Arc::new(AtomicUsize::new(0));
        let p2 = pressed.clone();
        let o = Opener::start(Box::new(move || {
            p2.fetch_add(1, Ordering::SeqCst);
        }))
        .unwrap();
        let url = o.page_for(vec!["http://192.168.0.101".into()], Some("Arc".into()));
        let path = url
            .split_once(&format!(":{}", o.port))
            .unwrap()
            .1
            .to_string();
        let host = format!("127.0.0.1:{}", o.port);
        let (code, body) = get(o.port, &path, &host, "GET");
        assert_eq!(code, 200);
        assert!(body.contains(r#"const C=["http://192.168.0.101"]"#));
        assert!(body.contains(r#"DEF="Arc";"#));
        // A site that rebinds its name to 127.0.0.1 sends its own Host.
        assert_eq!(
            get(o.port, &path, &format!("evil.example:{}", o.port), "GET").0,
            404
        );
        // Without the token, nothing - not even the page.
        assert_eq!(get(o.port, "/open/0", &host, "GET").0, 404);
        assert_eq!(
            get(o.port, &format!("/{}/open/9", "0".repeat(32)), &host, "GET").0,
            404
        );
        // The button, only with the token, only by POST.
        let settings = path.replace("/open/0", "/settings");
        assert_eq!(get(o.port, &settings, &host, "GET").0, 404);
        assert_eq!(pressed.load(Ordering::SeqCst), 0);
        assert_eq!(get(o.port, &settings, &host, "POST").0, 200);
        assert_eq!(pressed.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn an_address_cannot_break_out_of_the_script() {
        let p = page(
            &["http://x</script><script>alert(1)//".into()],
            "mac",
            Some("</script>"),
        );
        assert!(!p.contains("</script><script>alert"));
        assert!(!p.contains(r#"DEF="</script>""#));
    }

    /// A raw request with headers and a body; (code, whole reply).
    fn req(port: u16, method: &str, path: &str, headers: &str, body: &str) -> (u16, String) {
        let mut s = TcpStream::connect(SocketAddr::from(([127, 0, 0, 1], port))).unwrap();
        write!(
            s,
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n{headers}Content-Length: {}\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).unwrap();
        (out[9..12].parse().unwrap(), out)
    }

    fn window_server() -> (Opener, Arc<Mutex<Vec<String>>>, Arc<AtomicUsize>, String) {
        let o = Opener::start(Box::new(|| {})).unwrap();
        let (got, polls) = (
            Arc::new(Mutex::new(Vec::new())),
            Arc::new(AtomicUsize::new(0)),
        );
        let (g, p) = (got.clone(), polls.clone());
        let n = Arc::new(AtomicUsize::new(0));
        o.set_window(WindowLink {
            model: Box::new(move || format!("{{\"n\":{}}}", n.fetch_add(1, Ordering::SeqCst))),
            act: Box::new(move |m| g.lock().unwrap().push(m.to_string())),
            polled: Box::new(move || {
                p.fetch_add(1, Ordering::SeqCst);
            }),
        });
        let base = o
            .window_url()
            .split_once(&format!(":{}", o.port))
            .unwrap()
            .1
            .trim_end_matches("/window")
            .to_string();
        (o, got, polls, base)
    }

    #[test]
    fn the_browser_window_is_the_apps_own_page_with_its_way_back() {
        let (o, _, _, base) = window_server();
        let (code, body) = req(o.port, "GET", &format!("{base}/window"), "", "");
        assert_eq!(code, 200);
        assert!(body.contains("window.render=") && body.contains("window.__hidfiSend="));
        assert_eq!(
            body.matches("<script>").count(),
            2,
            "the page's script and the browser's"
        );
        // Without the token, or for another host: nothing.
        assert_eq!(req(o.port, "GET", "/window", "", "").0, 404);
        assert_eq!(
            get(o.port, &format!("{base}/window"), "evil.example", "GET").0,
            404
        );
    }

    #[test]
    fn the_state_answers_at_once_when_it_changed_and_waits_when_not() {
        let (o, _, polls, base) = window_server();
        let (code, body) = req(o.port, "GET", &format!("{base}/state?v=99"), "", "");
        assert_eq!(code, 200);
        assert!(body.ends_with(r#"{"v":0,"m":{"n":0}}"#), "{body}");
        assert_eq!(polls.load(Ordering::SeqCst), 1, "someone is looking");
        // Up to date: it waits for the change, and answers with it.
        let (port, path) = (o.port, format!("{base}/state?v=0"));
        let t = std::time::Instant::now();
        let w = std::thread::spawn(move || req(port, "GET", &path, "", ""));
        std::thread::sleep(Duration::from_millis(300));
        o.changed();
        let (_, body) = w.join().unwrap();
        assert!(body.contains(r#"{"v":1,"#), "{body}");
        assert!(t.elapsed() >= Duration::from_millis(300) && t.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn a_click_reaches_the_app_only_by_post_from_this_page() {
        let (o, got, _, base) = window_server();
        let act = format!("{base}/act");
        assert_eq!(
            req(o.port, "POST", &act, "", "act:open:DCB4D90A0BB8").0,
            200
        );
        let here = format!("Origin: http://127.0.0.1:{}\r\n", o.port);
        assert_eq!(req(o.port, "POST", &act, &here, "act:check-updates").0, 200);
        // Another site cannot press a button, even knowing the path.
        assert_eq!(
            req(
                o.port,
                "POST",
                &act,
                "Origin: http://evil.example\r\n",
                "act:forget:DCB4D90A0BB8"
            )
            .0,
            404
        );
        assert_eq!(
            req(
                o.port,
                "POST",
                &act,
                "Origin: null\r\n",
                "act:forget:DCB4D90A0BB8"
            )
            .0,
            404
        );
        // GET does nothing; a body too large is not read.
        assert_eq!(req(o.port, "GET", &act, "", "").0, 404);
        let _ = std::panic::catch_unwind(|| req(o.port, "POST", &act, "", &"x".repeat(400)));
        assert_eq!(
            *got.lock().unwrap(),
            vec!["act:open:DCB4D90A0BB8", "act:check-updates"]
        );
    }

    #[test]
    fn without_a_window_the_paths_are_not_there() {
        let o = Opener::start(Box::new(|| {})).unwrap();
        let base = o
            .window_url()
            .split_once(&format!(":{}", o.port))
            .unwrap()
            .1
            .trim_end_matches("/window")
            .to_string();
        assert_eq!(req(o.port, "GET", &format!("{base}/window"), "", "").0, 404);
        assert_eq!(req(o.port, "GET", &format!("{base}/state"), "", "").0, 404);
    }
}
