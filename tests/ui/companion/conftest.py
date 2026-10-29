"""The Companion's window page (companion/crates/hidfi-companion/src/window.html)
in Chromium (WebView2 on Windows is Chromium) and WebKit (WKWebView on macOS is
WebKit), fed the states the app's own model produces. docs/COMPANION_WINDOW.md.

Each test gets a fresh page: `window.ipc.postMessage` - what the app's web view
provides - records what the page asks the app; a capture-phase listener records
which controls a person touched, for the window's coverage contract
(support/window_contract.py), and in Chromium which of the page's functions ran.
"""
import json

import pytest

from support import paths, window_contract

PAGE = paths.REPO / "companion" / "crates" / "hidfi-companion" / "src" / "window.html"
STATES_FILE = paths.TESTS / "golden" / "companion_window_states.tsv"
URL = "http://localhost/companion-window"     # a secure context, as the app's own page is

# What the page asks the app, and what the person touched - before the page's own script.
INIT = """
window.__sent = [];
if (!window.__inBrowser) window.ipc = {postMessage: m => window.__sent.push(m)};
else window.__hidfiSend = m => window.__sent.push(m);
window.__touched = new Set();
const fam = t => {
  if (t.dataset.act) return 'act:' + t.dataset.act.split(':')[0];
  if (t.dataset.role) return 'role:' + t.dataset.role;
  if (t.dataset.filter) return 'filter:' + t.dataset.filter;
  if (t.dataset.bulk) return 'bulk:' + t.dataset.bulk;
  if (t.dataset.copy !== undefined) return 'copy';
  if (t.id) return '#' + t.id;
  if (t.classList.contains('card')) return 'card';
  return null;
};
addEventListener('click', e => {
  const t = e.target.closest && e.target.closest('[data-act],[data-role],[data-filter],[data-bulk],[data-copy],#bclear,#dyes,#dno,.card');
  const f = t && fam(t); if (f) window.__touched.add(f);
}, true);
addEventListener('keydown', e => {
  const k = (e.metaKey || e.ctrlKey ? 'Mod+' : '') + (e.shiftKey && e.key !== 'Shift' ? 'Shift+' : '') + (e.key === ' ' ? 'Space' : e.key);
  window.__touched.add('key:' + k);
}, true);
"""


def load_states():
    out = {}
    for line in STATES_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            name, js = line.split("\t", 1)
            out[name] = json.loads(js)
    return out


@pytest.fixture(scope="session")
def states():
    return load_states()


ENGINES = ["chromium", "webkit"]


def pytest_collection_modifyitems(items):
    # After the parallel phase, alone (run.py, marker "alone"): two browser
    # engines beside the parallel dashboard tests starved its large-screen
    # layout checks ("nothing is drawn", "the board model never answered") on a
    # 4-CPU runner and once on this Mac.
    for it in items:
        if "/ui/companion/" in str(it.fspath).replace("\\", "/"):
            it.add_marker(pytest.mark.alone)


def pytest_generate_tests(metafunc):
    # Every scenario, in both engines (a parametrised fixture does not reach
    # pytest-bdd's generated tests; a test parameter does).
    if "/ui/companion/" in str(metafunc.definition.fspath).replace("\\", "/"):
        metafunc.fixturenames.append("engine_name")
        metafunc.parametrize("engine_name", ENGINES)


# Per module: the browsers close when the window's tests are done, so they never
# sit beside the dashboard's speed tests that run after them in the same phase.
@pytest.fixture(scope="module")
def _browsers():
    out = {}
    yield out
    for b in out.values():
        if b and not isinstance(b, str):
            b.close()


@pytest.fixture
def engine(engine_name, pw, _browsers):
    if engine_name not in _browsers:
        try:
            _browsers[engine_name] = getattr(pw, engine_name).launch()
        except Exception as e:      # noqa: BLE001 - the reason goes in the skip
            _browsers[engine_name] = None
            _browsers[engine_name + ":why"] = str(e).splitlines()[0]
    b = _browsers[engine_name]
    if b is None:
        pytest.skip(f"{engine_name} is not installed for Playwright "
                    f"(python -m playwright install {engine_name}): {_browsers[engine_name + ':why']}")
    return engine_name, b


class Win:
    """The window, as a test drives it."""

    def __init__(self, engine, states):
        self.engine, self.browser = engine
        self.states = states
        self.width, self.height = 880, 720
        self.page = None
        self.errors = []
        self.cdp = None
        self.reduced = False
        self.clock_at = None
        self.ctx = None
        self.in_browser = False     # Linux: the page in a browser, no app channel

    def open(self):
        if self.page:
            return self.page
        self.ctx = self.browser.new_context(viewport={"width": self.width, "height": self.height},
                                           reduced_motion="reduce" if self.reduced else "no-preference")
        if self.engine == "chromium":
            self.ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="http://localhost")
        p = self.ctx.new_page()
        p.on("pageerror", lambda e: self.errors.append(str(e)))
        p.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        if self.clock_at is not None:
            # Paused: a slow machine must not add seconds of its own ("9 s ago").
            p.clock.install(time=self.clock_at)
            p.clock.pause_at(self.clock_at)
        html = PAGE.read_text(encoding="utf-8")
        p.route(URL, lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=html))
        if self.in_browser:
            p.add_init_script("window.__inBrowser = true;")
        p.add_init_script(INIT)
        if self.engine == "chromium":
            self.cdp = self.ctx.new_cdp_session(p)
            self.cdp.send("Profiler.enable")
            self.cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": False})
        p.goto(URL)
        self.page = p
        return p

    def show(self, name, data=None):
        p = self.open()
        p.evaluate("m => window.render(m)", data if data is not None else self.states[name])
        return p

    @property
    def sent(self):
        return self.page.evaluate("window.__sent") if self.page else []

    def close(self):
        if not self.page:
            return
        touched = set(self.page.evaluate("[...window.__touched]"))
        ran = set()
        if self.cdp:
            for s in self.cdp.send("Profiler.takePreciseCoverage")["result"]:
                for f in s["functions"]:
                    if f["functionName"] and any(r["count"] > 0 for r in f["ranges"]):
                        ran.add(f["functionName"])
        window_contract.record(self.engine, touched, ran)
        self.ctx.close()
        self.page = None


@pytest.fixture
def win(engine, states):
    w = Win(engine, states)
    yield w
    w.close()

