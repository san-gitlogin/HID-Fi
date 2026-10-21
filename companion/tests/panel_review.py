"""Renders the app's window (crates/hidfi-companion/src/panel.rs) in every state
a person can meet, at its real size, and checks what a reader needs: nothing
cut off or scrolled away, every button visible, a headline, and for each step
a line saying what to do or what happened. Screenshots go to SHOTS for a
person to look at.

Run: cargo run -q -p hidfi-companion --example panel_states > /tmp/states.tsv
     python3 companion/tests/panel_review.py /tmp/states.tsv
"""
import json, os, pathlib, re, sys, tempfile
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
src = (ROOT / "crates/hidfi-companion/src/panel.rs").read_text()
page_html = re.search(r'const PAGE: &str = r##"(.*?)"##;', src, re.S).group(1)
w, h = map(float, re.search(r"with_inner_size\(LogicalSize::new\(([\d.]+), ([\d.]+)\)\)", src).groups())
shots = pathlib.Path(os.environ.get("SHOTS") or tempfile.mkdtemp(prefix="hidfi-panel-"))
ok = True
def check(name, cond, detail=""):
    global ok
    ok &= bool(cond); print(("ok  " if cond else "FAIL"), name, detail, flush=True)

with sync_playwright() as p:
    b = p.chromium.launch()
    for line in open(sys.argv[1]):
        name, js = line.rstrip("\n").split("\t", 1)
        m = json.loads(js)
        pg = b.new_page(viewport={"width": int(w), "height": int(h)}, device_scale_factor=2)
        pg.set_content(page_html.replace("window.ipc.postMessage", "(()=>{})"))
        pg.evaluate("m => render(m)", m)
        r = pg.evaluate("""() => {
          const de = document.scrollingElement, btns = [...document.querySelectorAll('button')];
          const hidden = btns.filter(b => { const r = b.getBoundingClientRect(); return r.bottom > innerHeight || r.top < 0; })
                             .map(b => b.textContent);
          return {scroll: de.scrollHeight - innerHeight, hidden, head: document.getElementById('headline').textContent,
                  steps: [...document.querySelectorAll('#steps li')].map(l => [l.className, (l.querySelector('.d')||{}).textContent || ''])};
        }""")
        pg.screenshot(path=str(shots / f"{name}.png"))
        problems = []
        if r["scroll"] > 0: problems.append(f"{r['scroll']}px does not fit")
        if r["hidden"]: problems.append(f"buttons out of view: {r['hidden']}")
        if not r["head"].strip(): problems.append("no headline")
        now = [s for s in r["steps"] if "now" in s[0]]
        if any(not s[1].strip() for s in now): problems.append("the current step says nothing")
        check(f"{name}: readable, nothing cut off", not problems, "; ".join(problems) or r["head"])
        pg.close()
    b.close()
print("screenshots:", shots)
sys.exit(0 if ok else 1)
