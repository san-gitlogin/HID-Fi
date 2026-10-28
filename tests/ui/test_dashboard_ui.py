"""The dashboard in a real browser, against a fake board. No hardware.

- responsive: 67 viewports, every tab. Ratcheted: the layout problems that
  already exist are recorded in golden/ui_layout_known_issues.json; a NEW one
  fails, a FIXED one asks you to shrink the list.
- gestures / reply queue: the original suites, unchanged.
- controls: every control on every tab, at phone, tablet and desktop width,
  sends exactly what golden/ui_controls.json says. Any change to what a button
  sends is a deliberate --update-golden, never an accident.
- emoji settings and the Test button flow.
"""
import json
import re
import subprocess
import sys

import pytest

from support import dashboard, paths

pytestmark = pytest.mark.ui


def _legacy(script, timeout=900):
    return subprocess.run([sys.executable, str(paths.TESTS / script)], capture_output=True, text=True, timeout=timeout)


def test_gestures():
    r = _legacy("test_gestures.py")
    assert r.returncode == 0, r.stdout[-4000:]


def test_reply_queue_never_drifts():
    r = _legacy("test_dashboard_live.py")
    assert r.returncode == 0, r.stdout[-4000:]


@pytest.mark.slow
def test_layout_has_no_new_problems():
    r = _legacy("test_responsive.py", timeout=1800)
    problems, vp = set(), None
    for line in r.stdout.splitlines():
        m = re.match(r"\s+\[(OK |FAIL)\]\s+(.+?)\s+(\d+x\d+)\s", line)
        if m:
            vp = f"{m.group(2).strip()} {m.group(3)}"
            continue
        m = re.match(r"\s+- (.+)", line)
        if m and vp:
            problems.add(f"{vp} :: {m.group(1).strip()}")
    assert problems or r.returncode == 0, "could not parse test_responsive.py output:\n" + r.stdout[-3000:]
    # Pixel measurements depend on the platform's fonts, so each OS keeps its own baseline.
    import platform as _pf
    known_file = paths.GOLDEN / f"ui_layout_known_issues.{_pf.system().lower()}.json"
    if not known_file.exists():
        known_file.write_text(json.dumps(sorted(problems), indent=1) + "\n")
        pytest.skip(f"recorded {len(problems)} existing layout problems as the baseline - commit {known_file.name}")
    known = set(json.loads(known_file.read_text()))
    new, fixed = sorted(problems - known), sorted(known - problems)
    assert not new, "NEW layout problems (not in the known-issues baseline):\n  " + "\n  ".join(new)
    if fixed:
        pytest.fail("layout problems that are now FIXED - remove them from tests/golden/ui_layout_known_issues.json "
                    "so they can never come back:\n  " + "\n  ".join(fixed), pytrace=False)


# ---- every control, every tab -------------------------------------------------------
CLICKABLES = "button, [data-k], [data-key], .key, .tile"
NOISE = ('"cmd":"status"', '"cmd":"clients"', '"cmd":"storage_info"', '"cmd":"ping"', '"cmd":"inventory"',
         '"cmd":"ui_get"', '"cmd":"macro_list"', '"cmd":"pc_list"', '"cmd":"vault_list"', '"cmd":"wifi_status"')


def _exercise_all_controls(page):
    out = {}
    views = page.evaluate("[...document.querySelectorAll('section.view')].map(s=>s.id)")
    for v in views:
        # A fresh page per tab: what a tab sends must not depend on what other
        # tabs' controls left behind (a symbol layer, a held grab, the wall) -
        # that made this golden change whenever any tab gained a button.
        page.reload(); page.wait_for_timeout(900)
        page.evaluate("id=>{const b=document.querySelector('[data-v=\"'+id+'\"]'); b&&b.click();}", v[2:])
        page.wait_for_timeout(120)
        # Sound controls (docs/AUDIO.md) send nothing to the board and are covered
        # by the Screen scenarios; left out, so the order this harness relies on
        # stays exactly what the golden file was made with.
        n = page.evaluate(
            "([v,sel])=>[...document.querySelectorAll(sel.split(',').map(s=>'#'+v+' '+s.trim()).join(','))].filter(e=>!e.closest('.snd')).length", [v, CLICKABLES])
        for i in range(n):
            desc = page.evaluate("""([v,i,sel])=>{const q=sel.split(',').map(s=>'#'+v+' '+s.trim()).join(',');
              const el=[...document.querySelectorAll(q)].filter(e=>!e.closest('.snd'))[i]; if(!el) return null;
              const ds=Object.entries(el.dataset).map(([k,x])=>k+'='+x).join(',');
              return (el.id||'')+'|'+ds+'|'+(el.textContent||'').trim().replace(/\\s+/g,' ').slice(0,24);}""", [v, i, CLICKABLES])
            if desc is None:
                continue
            page.evaluate("window.__sent=[]")
            page.evaluate("""([v,i,sel])=>{const q=sel.split(',').map(s=>'#'+v+' '+s.trim()).join(',');
              const el=[...document.querySelectorAll(q)].filter(e=>!e.closest('.snd'))[i]; if(!el) return;
              const r=el.getBoundingClientRect(),x=r.left+r.width/2,y=r.top+r.height/2;
              const o={bubbles:true,cancelable:true,clientX:x,clientY:y,pointerId:1,isPrimary:true,pointerType:'touch'};
              el.dispatchEvent(new PointerEvent('pointerdown',o));el.dispatchEvent(new PointerEvent('pointerup',o));el.click();}""",
                          [v, i, CLICKABLES])
            page.wait_for_timeout(40)
            # Edit buttons switch a card's tiles to opening editors: measure every
            # control in the normal state, whatever was tapped before it.
            page.evaluate("()=>{if(typeof EDIT==='object')for(const k in EDIT)if(EDIT[k])toggleEdit(k);}")
            # Background traffic is not a control's output: status/list polls, and the
            # latency ping (binary op 4, OP.PING in web_ui.h) that drives the ms pill.
            sent = [m for m in page.evaluate("window.__sent") if not any(z in m for z in NOISE) and not m.startswith("B04")]
            if sent:
                out.setdefault(f"{v}|{desc}", []).append(sent)
    return out


@pytest.mark.slow
def test_every_control_sends_what_it_always_sent(golden, browser):
    result = {}
    if True:                                     # the shared browser (tests/ui/conftest.py)
        br = browser
        for w, h in ((390, 844), (820, 1180), (1400, 900)):
            page = br.new_page(viewport={"width": w, "height": h})
            errs = []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("dialog", lambda d: d.dismiss())
            dashboard.serve(page)
            page.wait_for_timeout(1200)
            for k, v in _exercise_all_controls(page).items():
                result[f"{w}|{k}"] = v
            assert not errs, f"page errors at {w}px: {errs}"
            page.context.close()
    assert len(result) > 300, f"only {len(result)} controls sent anything - the click harness is broken"
    golden("ui_controls", result)


# ---- emoji and symbols settings -------------------------------------------------------
@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    yield pg
    ctx.close()


def _cls(page, sel):
    return (page.locator(sel).get_attribute("class") or "").split()


@pytest.mark.parametrize("board_has_fields", [True, False], ids=["v3.9-firmware", "older-firmware"])
def test_emoji_switches_follow_the_board(page, board_has_fields):
    # None = the field is absent, as it is from firmware older than v3.9
    status = {"unicode_mac": True, "unicode_linux": False} if board_has_fields else {"unicode_mac": None, "unicode_linux": None}
    dashboard.serve(page, status=status)
    page.wait_for_timeout(1000)
    assert ("on" in _cls(page, '[data-uni="mac"]')) == board_has_fields
    page.evaluate("document.querySelector('[data-uni=\"linux\"]').click()")
    page.wait_for_timeout(200)
    assert dashboard.sent_json(page)[-1] == {"cmd": "set_unicode", "os": "linux", "on": True}
    assert "on" in _cls(page, '[data-uni="linux"]')
    assert not page.errors


def test_typing_reports_skipped_characters(page):
    dashboard.serve(page, replies={"type": "({status:'ok',reply:'typed',length:m.text.length,"
                                           "skipped:(m.text.match(/\\p{Extended_Pictographic}/gu)||[]).length,enter:!!m.enter})"})
    page.wait_for_timeout(800)
    page.evaluate("$('typeText').value='plain'; doType(false)")
    page.wait_for_timeout(200)
    assert page.locator(".toast.bad").count() == 0
    assert dashboard.sent_json(page)[-1] == {"cmd": "type", "text": "plain", "enter": False}
    page.evaluate("$('typeText').value='a \U0001F600\U0001F44D'; doType(true)")
    page.wait_for_timeout(200)
    assert any("2 characters were skipped" in t for t in page.locator(".toast.bad").all_inner_texts())
    assert not page.errors


TEST_REPLY = ("(m.os==='mac'||m.os==='linux')?{status:'ok',reply:'unicode_test_sent',os:m.os,sample:'\\u00e9\\ud83d\\ude00'}"
              ":{status:'error',message:'Unicode typing works on mac or linux only'}")
PC_REFUSAL = "({status:'error',message:'This computer looks like a PC - the macOS test would press Alt shortcuts here, so it was not sent'})"


@pytest.mark.parametrize("answers,expect_on,label", [
    ([False], False, "cancel at the first question types nothing"),
    ([True, True], True, "a yes turns it on"),
    ([True, False], False, "a no leaves it off"),
])
def test_test_button_flow(page, answers, expect_on, label):
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.accept() if answers[len(asked) - 1] else d.dismiss()))
    dashboard.serve(page, replies={"unicode_test": TEST_REPLY})
    page.wait_for_timeout(800)
    page.evaluate("document.querySelector('[data-uni-test=\"mac\"]').click()")
    page.wait_for_timeout(900)
    cmds = [m for m in dashboard.sent_json(page) if m.get("cmd") in ("unicode_test", "set_unicode")]
    if answers == [False]:
        assert cmds == [], label
    else:
        assert cmds[0] == {"cmd": "unicode_test", "os": "mac"}, label
        assert ("é\U0001F600" in asked[1]), "the question must show the real sample"
    assert ("on" in _cls(page, '[data-uni="mac"]')) == expect_on, label
    assert not page.errors


def test_test_button_refused_on_a_pc_says_why(page):
    asked = []
    page.on("dialog", lambda d: (asked.append(d.message), d.accept()))
    dashboard.serve(page, replies={"unicode_test": PC_REFUSAL})
    page.wait_for_timeout(800)
    page.evaluate("document.querySelector('[data-uni-test=\"mac\"]').click()")
    page.wait_for_timeout(900)
    assert len(asked) == 1, "after a refusal there must be no 'did it appear?' question"
    assert any("looks like a PC" in t for t in page.locator(".toast").all_inner_texts())
    assert "on" not in _cls(page, '[data-uni="mac"]')
