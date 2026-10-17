"""The dashboard, pulled out of its PROGMEM string, and a fake board to serve it.

The fake replaces window.WebSocket before the page's own script runs. It records
every frame the page sends - JSON as text, binary frames as hex - and answers
each JSON command with one reply, in order, because the dashboard matches
replies to callbacks by position (see tests/test_dashboard_live.py).
"""
import json

from support.paths import WEB_UI


def extract_html(text=None):
    text = text or WEB_UI.read_text(encoding="utf-8")
    start = text.index('R"rawliteral(')
    start = text.index("\n", start) + 1
    end = text.index(')rawliteral"', start)
    return text[start:end]


DEFAULT_STATUS = {
    "status": "ok", "reply": "status", "firmware": "hid_fi_test", "hid_ready": True,
    "host_os": "mac", "host_os_source": "auto", "host_os_detected": "mac", "detect_phase": "settled",
    "pointer_mode": "relative", "unicode_mac": False, "unicode_linux": False,
}

# Replies the fake gives to specific commands; anything else gets {"status":"ok"}.
# Values are JS expressions evaluated with `m` (the command) and `ST` (the status) in scope.
DEFAULT_REPLIES = {
    "status": "Object.assign({}, ST)",
    "set_unicode": "(ST['unicode_'+m.os]=m.on, {status:'ok',reply:'unicode_set',unicode_mac:!!ST.unicode_mac,unicode_linux:!!ST.unicode_linux})",
    "type": "({status:'ok',reply:'typed',length:m.text.length,skipped:0,enter:!!m.enter})",
}


def fake_board_script(status=None, replies=None, seed_random=True):
    st = {k: v for k, v in dict(DEFAULT_STATUS, **(status or {})).items() if v is not None}  # None = absent
    rep = dict(DEFAULT_REPLIES, **(replies or {}))
    cases = "\n".join(f"      case {json.dumps(k)}: return {v};" for k, v in rep.items())
    rnd = ("Math.random=(()=>{let s=1;return()=>{s=(s*16807)%2147483647;return s/2147483647}})();"
           if seed_random else "")
    return f"""(() => {{
  const ST = {json.dumps(st)};
  window.__sent = [];
  function answer(m) {{
    switch (m.cmd) {{
{cases}
      default: return {{status:'ok'}};
    }}
  }}
  class FakeWS {{
    constructor() {{ this.readyState = 0; this.binaryType = 'blob';
      setTimeout(() => {{ this.readyState = 1; this.onopen && this.onopen({{}}); }}, 30); }}
    send(d) {{
      if (typeof d === 'string') {{
        window.__sent.push('J' + d);
        let m = {{}}; try {{ m = JSON.parse(d); }} catch (e) {{}}
        const r = answer(m);
        setTimeout(() => this.onmessage && this.onmessage({{data: JSON.stringify(r)}}), 5);
      }} else {{
        const u = new Uint8Array(d instanceof ArrayBuffer ? d : d.buffer ? d.buffer.slice(d.byteOffset, d.byteOffset + d.byteLength) : d);
        window.__sent.push('B' + Array.from(u).map(x => x.toString(16).padStart(2, '0')).join(''));
      }}
    }}
    close() {{ this.readyState = 3; }}
  }}
  FakeWS.OPEN = 1; FakeWS.CONNECTING = 0; FakeWS.CLOSED = 3;
  window.WebSocket = FakeWS;
  {rnd}
}})();"""


def serve(page, html=None, status=None, replies=None):
    """Route a Playwright page to the dashboard with the fake board installed."""
    html = html or extract_html()
    page.add_init_script(fake_board_script(status, replies))
    st = {k: v for k, v in dict(DEFAULT_STATUS, **(status or {})).items() if v is not None}
    page.route("http://hidfi.test/**",
               lambda r, q: r.fulfill(json=st) if "/api/" in q.url else r.fulfill(body=html, content_type="text/html"))
    page.goto("http://hidfi.test/")


def sent_json(page):
    return [json.loads(m[1:]) for m in page.evaluate("window.__sent") if m.startswith("J")]
