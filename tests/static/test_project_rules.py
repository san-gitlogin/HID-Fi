"""Rules that hold for the source tree itself. Needs only Python (and Node for one check).

Each of these exists because the thing it checks was once wrong, or because
getting it wrong is invisible until a user hits it.
"""
import py_compile
import re
import subprocess
import sys

import pytest

from support import firmware, paths

pytestmark = pytest.mark.static


def test_dashboard_is_well_formed():
    """The dashboard is a PROGMEM string: a JS error compiles fine and serves a blank page."""
    r = subprocess.run([sys.executable, str(paths.TESTS / "test_dashboard_static.py")],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert "all checks passed" in r.stdout


def test_every_firmware_command_is_documented():
    doc = (paths.DOCS / "COMMANDS.md").read_text(encoding="utf-8")
    documented = set(re.findall(r'"cmd"\s*:\s*"([a-z_0-9]+)"', doc)) | set(re.findall(r"`([a-z_0-9]+)`", doc))
    missing = [c for c in firmware.dispatch_commands() if c not in documented]
    assert not missing, f"commands the firmware accepts but docs/COMMANDS.md never mentions: {missing}"


def test_docs_name_no_command_the_firmware_lacks():
    doc = (paths.DOCS / "COMMANDS.md").read_text(encoding="utf-8")
    stale = sorted(set(re.findall(r'"cmd"\s*:\s*"([a-z_0-9]+)"', doc)) - set(firmware.dispatch_commands()))
    assert not stale, f"docs/COMMANDS.md documents commands the firmware does not dispatch: {stale}"


def test_firmware_version_matches_changelog():
    top = re.search(r"^## (v[\d.]+)", paths.CHANGELOG.read_text(encoding="utf-8"), re.M)
    assert top, "CHANGELOG.md has no '## vX.Y' heading"
    assert firmware.fw_version() == f"hid_fi_{top.group(1)}", \
        f"FW_VERSION is {firmware.fw_version()} but the newest CHANGELOG entry is {top.group(1)}"


def test_build_settings_agree_everywhere():
    """flash_esp.sh, flash_esp.ps1 and docs/FLASHING.md must build the same thing."""
    fq = firmware.fqbn()
    ps1 = (paths.REPO / "flash_esp.ps1").read_text(encoding="utf-8")
    flashing = (paths.DOCS / "FLASHING.md").read_text(encoding="utf-8")
    assert fq in ps1, "flash_esp.ps1 builds with a different FQBN than flash_esp.sh"
    assert fq in flashing, "docs/FLASHING.md quotes a different FQBN than flash_esp.sh"
    assert "USBMode=default" in fq, "USBMode must be USB-OTG (TinyUSB) or the board never types"
    assert "CDCOnBoot=default" in fq, "CDC on boot must be off - serial runs over the CH343 UART"


@pytest.mark.parametrize("script", ["flash_esp.sh", "flash_esp.ps1"])
def test_flashers_never_write_merged_bin(script):
    """merged.bin at 0x0 erases NVS: every saved setting and password."""
    code = [l for l in (paths.REPO / script).read_text(encoding="utf-8").splitlines()
            if not l.lstrip().startswith("#") and "merged" in l]
    assert not code, f"{script} references merged.bin outside a comment: {code}"


def test_dashboard_has_no_emoji():
    """Icons are SVG sprite symbols - an emoji renders differently on every phone."""
    # Emoji blocks plus the emoji-presentation selector and joiner. Typographic
    # symbols are fine: the Mac modifier glyphs (command, option, control, shift)
    # are deliberate key caps, not emoji.
    html = (paths.WEB_UI).read_text(encoding="utf-8")
    emoji = lambda c: (0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF
                       or ord(c) in (0xFE0F, 0x200D, 0x2B50, 0x2B55, 0x2B1B, 0x2B1C))
    found = sorted({c for c in html if emoji(c)})
    assert not found, f"emoji/symbol characters in web_ui.h: {found} - use an SVG sprite icon"


def test_test_scripts_compile():
    bad = []
    for f in list(paths.TESTS.rglob("*.py")):
        if ".build" in f.parts:
            continue
        try:
            py_compile.compile(str(f), doraise=True)
        except py_compile.PyCompileError as e:
            bad.append(f"{f.relative_to(paths.REPO)}: {e.msg.splitlines()[-1]}")
    assert not bad, "\n".join(bad)


def test_deps_manifest_is_complete():
    import json
    m = json.loads(paths.DEPS.read_text())
    for k in ("esp32:esp32", "ArduinoJson", "WebSockets"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", m["firmware"].get(k, "")), f"tests/deps.json: firmware pin for {k} must be an exact x.y.z"


# ---- the test coverage contract for commands -------------------------------------------
# A command an AI (or anyone) adds to the firmware is not done until it is
# documented, modelled in the simulated board the dashboard scenarios run
# against, and sent by at least one test. Each of the three has been missed
# before: six commands had no test at all when this rule was written.
def _command_uses():
    """Every place a test sends a command by name, across tests/ (the model excluded)."""
    # sent directly, or asserted as sent by the dashboard: received("x"), receive "x"
    pat = re.compile(r"""["']cmd["']\s*:\s*["']([a-z_0-9]+)["']|cmd=["']([a-z_0-9]+)["']"""
                     r"""|received\(["']([a-z_0-9]+)["']|receives? ["]([a-z_0-9]+)["]""")
    used = {}
    for f in list(paths.TESTS.rglob("*.py")) + list(paths.TESTS.rglob("*.feature")):
        if ".build" in f.parts or f.name == "board_sim.py":
            continue
        for m in pat.finditer(f.read_text(encoding="utf-8", errors="ignore")):
            name = next(g for g in m.groups() if g)
            used.setdefault(name, set()).add(f.relative_to(paths.TESTS).as_posix())
    return used


def test_every_firmware_command_is_sent_by_a_test():
    used = _command_uses()
    missing = [c for c in firmware.dispatch_commands() if c not in used]
    assert not missing, (f"commands no test ever sends: {missing}. Add a scenario under tests/ui/bdd/features/ "
                         f"(dashboard) or tests/board/ (serial-only) - see .claude/skills/hidfi-feature")


def test_every_firmware_command_is_modelled_by_the_simulated_board():
    sim = (paths.TESTS / "support" / "board_sim.py").read_text(encoding="utf-8")
    modelled = set(re.findall(r'c == "([a-z_0-9]+)"', sim)) | set(re.findall(r'"([a-z_0-9]+)"', sim))
    missing = [c for c in firmware.dispatch_commands() if c not in modelled]
    assert not missing, (f"tests/support/board_sim.py does not model {missing}, so the dashboard scenarios "
                         f"cannot exercise them. Model the reply exactly (tests/board/test_sim_contract.py checks it)")


# ---- the responsive matrix measures every sheet and overlay ----------------------------
# tests/support/responsive_run.py opens each one on all 500+ screens. One added to
# the dashboard but not to that list would never be measured anywhere.
def _dashboard_js():
    from support import dashboard
    return dashboard.extract_html()


def test_every_sheet_is_measured_on_every_screen():
    from support import responsive_run
    js = _dashboard_js()
    opened = " ".join(e for _, e in responsive_run.SHEET_STATES)
    # Sheets built inline in a handler, by title: each needs a state that reaches it.
    inline = {"Wake did not work": "sheet wake failed"}
    missing = []
    for m in re.finditer(r"function (\w+)\([^)]*\)\{", js):
        body = js[m.end():]
        nxt = re.search(r"\nfunction \w+\(", body)
        body = body[:nxt.start()] if nxt else body
        calls = re.findall(r"\bsheet\(\s*([^,]+),", body)
        if not calls or m.group(1) in ("sheet", "closeSheet") or m.group(1) + "(" in opened:
            continue
        if all(any(t in c for t in inline) for c in calls):
            continue                      # only inline sheets, checked by title below
        missing.append(m.group(1))
    names = {n for n, _ in responsive_run.SHEET_STATES}
    missing += [t for t, st in inline.items() if t in js and st not in names]
    assert not missing, (f"sheets the responsive matrix never opens: {missing} - add them to SHEET_STATES in "
                         f"tests/support/responsive_run.py")


def test_every_overlay_is_measured_on_every_screen():
    from support import responsive_run
    js = _dashboard_js()
    overlays = set(re.findall(r"\$\('(\w+)'\)\.classList\.add\('on'\)", js)) - {"sheet"}
    covered = " ".join(e for _, e, _ in responsive_run.OVERLAY_STATES)
    starters = {"boot": "bootStart(", "apnew": "apStart(", "asw": "aswOpen("}
    missing = sorted(o for o in overlays if starters.get(o, o) not in covered)
    assert not missing, (f"overlays the responsive matrix never shows: {missing} - add them to OVERLAY_STATES "
                         f"in tests/support/responsive_run.py")


def test_the_screen_viewer_matches_the_companion_copy():
    """watchScreen() lives in the dashboard and in companion/web/viewer.js, where
    the companion app's own browser tests run it. One copy edited without the
    other would test code the dashboard does not ship."""
    import re as _re
    js = _dashboard_js()
    src = (paths.REPO / "companion" / "web" / "viewer.js").read_text(encoding="utf-8")
    grab = lambda t: _re.search(r"^function watchScreen\(.*?^}", t, _re.S | _re.M)
    a, b = grab(js), grab(src)
    assert a and b, "watchScreen() missing from the dashboard or companion/web/viewer.js"
    assert a.group(0) == b.group(0), "watchScreen() in web_ui.h and companion/web/viewer.js differ - change both"


def test_every_board_action_matches_its_dashboard_shortcut():
    """{"cmd":"do","a":NAME} and the dashboard's shortcut tiles must press the same
    keys for the same thing on every OS. Each firmware ACTIONS row needs a dashboard
    SC row with the same label and the same three combos."""
    from support.board_sim import ACTIONS
    ui = paths.WEB_UI.read_text(encoding="utf-8") if hasattr(paths, "WEB_UI") else \
        (paths.TESTS.parent / "hid_fi" / "web_ui.h").read_text(encoding="utf-8")
    sc = ui[ui.index("const SC=["):ui.index("const SC_COL=")]
    rows = set(re.findall(r"\['([^']*)','([^']*)','([^']*)','([^']*)'\]", sc))
    assert len(ACTIONS) >= 80, f"the firmware table parsed to {len(ACTIONS)} rows"
    bad = [f"{name}: {row}" for name, row in ACTIONS.items() if tuple(row) not in rows]
    assert not bad, "board actions that differ from the dashboard's shortcut table:\n  " + "\n  ".join(bad)
    assert len(set(ACTIONS)) == len(ACTIONS)
    assert all(re.fullmatch(r"[a-z_]+", n) for n in ACTIONS), "action names are lower_snake words"


AGENT_FILES = ["AGENTS.md", ".claude/CLAUDE.md", ".github/copilot-instructions.md", "GEMINI.md"]


@pytest.mark.parametrize("name", AGENT_FILES)
def test_every_agent_instruction_file_leads_to_the_shared_brief(name):
    """Codex reads AGENTS.md, Copilot its own file, Claude CLAUDE.md, Gemini GEMINI.md.
    Each must lead to AGENTS.md and know how to run the Linux tier locally, or an
    agent on that tool works without the rules."""
    text = (paths.TESTS.parent / name).read_text(encoding="utf-8")
    if name != "AGENTS.md":
        assert "AGENTS.md" in text, f"{name} does not point to AGENTS.md"
    for route in ("tests/setup_linux.sh", "tests/docker/run.sh"):
        assert route in text, f"{name} does not give the Linux-tier route {route} - agents on Linux, WSL and macOS each need theirs"
    assert re.search(r"python tests[/\\]run\.py", text), f"{name} does not give the test command"


def test_the_local_linux_scripts_exist_and_run():
    import os
    for script in ("tests/docker/run.sh", "tests/docker/vm.sh", "tests/setup_linux.sh"):
        f = paths.TESTS.parent / script
        assert f.exists(), f"{script} is missing"
        # On Windows "bash" is usually the WSL launcher, which with no Linux
        # installed prints an install hint instead of checking anything. These are
        # macOS/Linux scripts; the jobs on those systems check them.
        if sys.platform == "win32":
            continue
        assert os.access(f, os.X_OK), f"{script} is not executable"
        r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{script}: {r.stderr}"


def test_every_linux_route_installs_what_ci_installs():
    """One package list (tests/setup_linux.sh) for WSL, Linux, cloud sandboxes, the
    Docker image and Copilot's setup. CI must not need anything it lacks, or a
    contributor's local run passes where CI fails."""
    root = paths.TESTS.parent
    script = (root / "tests" / "setup_linux.sh").read_text(encoding="utf-8")
    block = re.search(r"APT_PACKAGES=\(\n(.*?)\n\)", script, re.S).group(1)
    ours = set(" ".join(l.split("#")[0] for l in block.splitlines()).split())
    ci = (root / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    ci_pkgs = set()
    for block in re.findall(r"apt-get install -y -qq --no-install-recommends \\\n\s*([^\n]+)", ci):
        ci_pkgs |= set(block.split())
    assert ci_pkgs, "could not find CI's apt-get install line"
    missing = ci_pkgs - ours
    assert not missing, f"CI installs {sorted(missing)} but tests/setup_linux.sh does not"
    assert "setup_linux.sh" in (root / "tests" / "docker" / "Dockerfile").read_text(encoding="utf-8")
    copilot = (root / ".github" / "workflows" / "copilot-setup-steps.yml").read_text(encoding="utf-8")
    assert "\n  copilot-setup-steps:" in copilot and "tests/setup_linux.sh" in copilot, \
        "Copilot only runs a job named exactly copilot-setup-steps"


def test_the_board_model_never_drives_playwright_from_a_thread():
    """A Playwright object belongs to its own thread. The model once closed sockets
    from threading.Timer threads; Playwright 1.64 stopped tolerating it and six
    reconnect scenarios failed. Delays go through later()/tick() instead."""
    lines = (paths.TESTS / "support" / "board_sim.py").read_text(encoding="utf-8").splitlines()
    src = "\n".join(l.split("#")[0] for l in lines)           # code, not the comment explaining why
    assert "threading.Timer" not in src and "Thread(" not in src, \
        "board_sim.py starts a thread - schedule it with self.later() instead"


def test_the_github_workflows_only_run_when_started_by_hand():
    """The owner asked for no runs (and no failure mails) on push: the local run is
    the gate. A push, pull-request or schedule trigger must not come back."""
    for wf in (paths.TESTS.parent / ".github" / "workflows").glob("*.yml"):
        text = wf.read_text(encoding="utf-8")
        block = re.search(r"^on:\s*\n((?:[ \t]+.*\n)+)", text, re.M)
        triggers = set(re.findall(r"^[ \t]{2}([a-z_]+):", block.group(1), re.M)) if block else set()
        # workflow_call starts nothing on its own: it lets a manual run call this one.
        assert "workflow_dispatch" in triggers and triggers <= {"workflow_dispatch", "workflow_call"}, \
            f"{wf.name} runs on {sorted(triggers)} - manual only"


def test_the_version_number_has_the_release_format():
    """vMAJOR.MINOR or vMAJOR.MINOR.PATCH (RELEASING.md). That FW_VERSION and the
    newest CHANGELOG entry agree is test_firmware_version_matches_changelog."""
    v = firmware.fw_version()
    assert re.fullmatch(r"hid_fi_v\d+\.\d+(\.\d+)?", v), f"{v} is not hid_fi_vMAJOR.MINOR(.PATCH)"
