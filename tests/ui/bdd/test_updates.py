import pytest
from pytest_bdd import given, parsers, scenarios, then

pytestmark = pytest.mark.ui
scenarios("features/updates.feature")

MB15 = 1572864        # 1.5 MB


def _wait(ui, pred, what, tries=60):
    for _ in range(tries):
        if pred():
            return
        ui.idle(100)
    raise AssertionError(what)


def _release(sim, version, sections=None, **kw):
    sim.ota["release"] = dict({"version": version, "size": MB15,
                               "changelog": [{"version": v, "text": f"What changed in {v}."}
                                             for v in (sections or [version])]}, **kw)


# ---- the board ---------------------------------------------------------------------

@given(parsers.parse('the board runs "{fw}"'))
def runs(ctx, fw):
    ctx["sim"].firmware = fw


@given(parsers.parse('a release "{v}" is published with sections "{secs}"'))
def published(ctx, v, secs):
    _release(ctx["sim"], v, [s.strip() for s in secs.split(",")])


@given(parsers.parse('a security release "{v}" is published fixing "{risk}"'))
def security(ctx, v, risk):
    _release(ctx["sim"], v, security=True, risk=risk)


@given(parsers.parse('a release "{v}" is published but signed by someone else'))
def bad_sig(ctx, v):
    _release(ctx["sim"], v, bad_signature=True)


@given("the update server cannot be reached")
def unreachable(ctx):
    ctx["sim"].ota["release"] = None


@given(parsers.parse('a release "{v}" is published that updates only from "{m}"'))
def too_old(ctx, v, m):
    _release(ctx["sim"], v, refusal="too_old")


@given("the board has found that release")
def found(ctx):
    ctx["sim"]._ota_checked()


@given(parsers.parse('the board is downloading "{v}" and has {n:d} percent'))
def downloading(ctx, v, n):
    _release(ctx["sim"], v)
    ctx["sim"].ota.update(step="downloading", available=v, total=MB15, bytes=MB15 * n // 100, hold=True)


@given("the download is paused because I am using the board")
def paused(ctx):
    ctx["sim"].ota["paused"] = True


@given(parsers.parse('the board is switching to "{v}"'))
def switching(ctx, v):
    _release(ctx["sim"], v)
    ctx["sim"].ota.update(step="switching", available=v, total=MB15, bytes=MB15, hold=True)


@given(parsers.parse('the board is ready to restart into "{v}"'))
def ready(ctx, v):
    _release(ctx["sim"], v)
    ctx["sim"].ota.update(step="ready", available=v, total=MB15, bytes=MB15)


@given(parsers.parse('the board is proving the new version "{fw}"'))
def proving(ctx, fw):
    ctx["sim"].firmware = fw
    ctx["sim"].ota.update(state="pending_verify", result="verifying", slot="app1", to=fw)


@given(parsers.parse('the board was updated from "{a}" to "{b}"'))
def updated(ctx, a, b):
    ctx["sim"].firmware = b
    ctx["sim"].ota.update(result="updated", slot="app1", to=b, **{"from": a})


@given(parsers.parse('the update to "{fw}" was not kept because "{why}"'))
def rolled_back(ctx, fw, why):
    ctx["sim"].ota.update(result="rolled_back", to=fw, why=why)


@given("the board has the single-slot layout")
def single(ctx):
    ctx["sim"].ota.update(layout="single", slot="app0", state="single_slot")


# ---- what the page shows ---------------------------------------------------------------

@then(parsers.parse('"#{el_id}" eventually reads "{text}"'))
def eventually_reads(ctx, el_id, text):
    loc = ctx["ui"].page.locator(f"#{el_id}")
    _wait(ctx["ui"], lambda: loc.inner_text().strip() == text, f"#{el_id} reads {loc.inner_text()!r}")


@then(parsers.parse('"#{el_id}" eventually contains "{text}"'))
def eventually_contains(ctx, el_id, text):
    loc = ctx["ui"].page.locator(f"#{el_id}")
    _wait(ctx["ui"], lambda: text in loc.inner_text(), f"#{el_id} says {loc.inner_text()!r}")


@then(parsers.parse('"#{el_id}" is shown'))
def shown(ctx, el_id):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is hidden")


@then(parsers.parse('"#{el_id}" is hidden'))
def hidden(ctx, el_id):
    _wait(ctx["ui"], lambda: not ctx["ui"].page.locator(f"#{el_id}").is_visible(), f"#{el_id} is visible")


@then(parsers.parse('"#{el_id}" is disabled'))
def disabled(ctx, el_id):
    _wait(ctx["ui"], lambda: ctx["ui"].page.locator(f"#{el_id}").is_disabled(), f"#{el_id} is enabled")


@then(parsers.parse('"#{el_id}" is on'))
def is_on(ctx, el_id):
    _wait(ctx["ui"], lambda: "on" in (ctx["ui"].page.locator(f"#{el_id}").get_attribute("class") or "").split(), "off")


@then(parsers.parse('"#{el_id}" is off'))
def is_off(ctx, el_id):
    _wait(ctx["ui"], lambda: "on" not in (ctx["ui"].page.locator(f"#{el_id}").get_attribute("class") or "").split(), "on")


@then(parsers.parse('the message "{text}" was shown once'))
def shown_once(ctx, text):
    """After a reload the board still reports the outcome; the page must not say it again."""
    ui = ctx["ui"]
    ui.idle(2500)
    assert not any(text in t for t in ui.toasts()), f"said again after a reload: {ui.toasts()}"
