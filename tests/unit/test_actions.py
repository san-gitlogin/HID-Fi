"""Actions for agents: do, actions, open, steps - against the board model.

The model reads the firmware's ACTIONS table out of hid_fi.ino, and
tests/board/test_sim_contract.py holds the model's replies to the real board's,
so what is checked here is what the board does. Pressing real keys is left to
the e2e tier: these commands type into whatever computer the board is in.
"""
import pytest

from support.board_sim import ACTIONS, BoardSim

pytestmark = pytest.mark.unit

OSES = ["mac", "windows", "linux"]


def ask(body, os_="windows"):
    return BoardSim(host_os=os_).handle(body, "serial")[0]


@pytest.mark.parametrize("os_", OSES)
def test_every_action_presses_this_oss_keys_or_says_there_are_none(os_):
    col = {"mac": 1, "windows": 2, "linux": 3}[os_]
    for name, row in ACTIONS.items():
        r = ask({"cmd": "do", "a": name}, os_)
        if row[col]:
            assert r == {"status": "ok", "reply": "done", "a": name, "keys": row[col], "os": os_}, r
        else:
            assert r["reply"] == "no_equivalent" and r["os"] == os_, r


@pytest.mark.parametrize("name,mac,win,linux", [
    ("copy", "GUI+C", "CTRL+C", "CTRL+C"),
    ("lock", "CTRL+GUI+Q", "GUI+L", "GUI+L"),
    ("screenshot_area", "GUI+SHIFT+4", "GUI+SHIFT+S", "PRINTSCREEN"),
    ("redo", "GUI+SHIFT+Z", "CTRL+Y", "CTRL+SHIFT+Z"),
    ("switch_app", "GUI+TAB", "ALT+TAB", "ALT+TAB"),
])
def test_the_same_word_means_the_right_keys_on_each_computer(name, mac, win, linux):
    assert [ask({"cmd": "do", "a": name}, o)["keys"] for o in OSES] == [mac, win, linux]


def test_the_os_can_be_named_instead_of_detected():
    assert ask({"cmd": "do", "a": "copy", "os": "mac"}, "windows")["keys"] == "GUI+C"


def test_action_is_also_accepted_as_the_long_name():
    assert ask({"cmd": "do", "action": "paste"})["keys"] == "CTRL+V"


def test_an_unknown_action_points_at_the_list():
    r = ask({"cmd": "do", "a": "make_coffee"})
    assert r["status"] == "error" and r["reply"] == "unknown_action" and "actions" in r["message"]


@pytest.mark.parametrize("os_", OSES)
def test_the_list_is_only_what_works_here_and_is_cheap(os_):
    r = ask({"cmd": "actions"}, os_)
    names = r["a"].split(" ")
    col = {"mac": 1, "windows": 2, "linux": 3}[os_]
    assert names == [n for n, row in ACTIONS.items() if row[col]]
    assert len(r["a"]) < 1000, "the list is meant to cost a model a couple of hundred tokens"
    assert "open" in r["also"] and "wait" in r["also"]


def test_the_list_with_keys():
    r = ask({"cmd": "actions", "keys": True}, "mac")
    assert r["a"]["copy"] == "GUI+C" and "snap_left" not in r["a"]


@pytest.mark.parametrize("os_,via", [("windows", "start"), ("mac", "spotlight"), ("linux", "overview")])
def test_open_an_app_through_the_oss_own_launcher(os_, via):
    r = ask({"cmd": "open", "app": "Calculator"}, os_)
    assert r == {"status": "ok", "reply": "opened", "via": via, "os": os_}


@pytest.mark.parametrize("os_", ["windows", "linux"])
def test_run_a_command_or_url(os_):
    assert ask({"cmd": "open", "run": "https://example.com"}, os_)["via"] == "run"


def test_run_is_refused_on_a_mac_with_what_to_do_instead():
    r = ask({"cmd": "open", "run": "calc"}, "mac")
    assert r["reply"] == "no_equivalent" and "app" in r["message"]


def test_open_with_nothing_to_open():
    assert ask({"cmd": "open"})["status"] == "error"


def test_steps_run_in_one_call():
    sim = BoardSim(host_os="windows")
    r = sim.handle({"cmd": "steps", "s": ["open Notepad", "wait 1500", "type Hello; it's me", "do save",
                                          "key ENTER", "click left", "run calc"]}, "serial")[0]
    assert r == {"status": "ok", "reply": "steps_done", "done": 7, "os": "windows"}
    assert ("step", "type Hello; it's me") in sim.actions_done


@pytest.mark.parametrize("steps,at,why", [
    (["do copy", "do make_coffee", "do paste"], 1, "unknown action"),
    (["do copy", "do snap_left"], 1, "no equivalent"),
    (["wait 5000", "wait 5000", "wait 1"], 2, "10 s"),
    (["dance"], 0, "unknown step"),
    (["open "], 0, "app name"),
    (["key"], 0, "combo"),
])
def test_steps_stop_at_the_first_they_cannot_do_and_say_which(steps, at, why):
    r = ask({"cmd": "steps", "s": steps}, "mac")
    assert r["reply"] == "steps_stopped" and r["at"] == at and r["done"] == at and why in r["message"], r


@pytest.mark.parametrize("s", [[], ["do copy"] * 33, "do copy", None])
def test_steps_needs_an_array_of_1_to_32(s):
    r = ask({"cmd": "steps", "s": s})
    assert r["status"] == "error" and "1 to 32" in r["message"]


def test_over_the_home_network_without_a_pin_they_are_locked_like_any_command():
    sim = BoardSim()
    sim.sta.update(phase="online", ssid="home", ip="192.168.1.50")
    sim.phone_on_lan = True
    assert sim.handle({"cmd": "do", "a": "copy"}, "ws")[0]["reply"] == "lan_locked"


# ---- macro lines -------------------------------------------------------------------
# Seen live, hours before a time-critical booking: macros made on a phone typed
# nothing. The phone capitalised each line ("Text ...") and runMacroLine() only
# knew lowercase verbs, skipping everything else without a word.
def test_macro_verbs_in_any_case_and_a_bare_line_is_typed():
    from support.board_sim import macro_line
    assert macro_line("text Thirumalai T") == ("text", "Thirumalai T")
    assert macro_line("Text Thirumalai T") == ("text", "Thirumalai T")
    assert macro_line("TEXT 60") == ("text", "60")
    assert macro_line("Key ENTER") == ("key", "ENTER")
    assert macro_line("Delay 300") == ("delay", "300")
    assert macro_line("Click left") == ("click", "left")
    assert macro_line("Pramila Revathi S N") == ("text", "Pramila Revathi S N")
    assert macro_line("307978327645") == ("text", "307978327645")
    assert macro_line("texture") == ("text", "texture"), "a word that starts like a verb is text"


def test_the_firmware_parses_macro_lines_the_same_way():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "hid_fi" / "hid_fi.ino").read_text(encoding="utf-8")
    body = src[src.index("static void runMacroLine(const String& ln) {"):src.index("void handleMacroRun(const JsonDocument& inDoc) {")]
    assert "strncasecmp" in src[src.index("static bool macroVerb"):src.index("static void runMacroLine")]
    assert "else                              typeString(ln.c_str());" in body, \
        "a line without a verb must be typed, not skipped"


def test_the_model_has_the_firmwares_macro_and_storage_limits():
    # The dashboard scenarios run against board_sim: if its limits drift from
    # hid_fi.ino, every macro and storage scenario proves the wrong thing.
    import pathlib
    import re
    from support import board_sim
    src = (pathlib.Path(__file__).resolve().parents[2] / "hid_fi" / "hid_fi.ino").read_text(encoding="utf-8")
    define = lambda n: int(re.search(rf"#define\s+{n}\s+(\d+)", src).group(1))
    assert define("MACRO_SLOTS") == board_sim.MACRO_SLOTS == 32
    assert define("NVS_RESERVE") == board_sim.NVS_RESERVE
    assert (define("MACRO_STEPS_MAX"), define("MACRO_NAME_MAX"), define("MACRO_TOTAL_MAX")) == (1000, 40, 4000)
    assert define("UI_CONFIG_MAX") == 3000 and define("WS_TEXT_MAX") == 4096 and define("SERIAL_LINE_MAX") == 4096
    for n in (0, 31, 32, 1000, 3000):
        assert board_sim.nvs_entries(n) == 1 + (n + 1 + 31) // 32
