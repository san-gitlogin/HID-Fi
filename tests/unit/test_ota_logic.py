"""The update decisions (hid_fi/ota_logic.h), compiled on this computer and run.

The same header is compiled into the firmware, so these are the board's own
decisions: is a version newer, has a new firmware proved itself, what happened
after a restart, may the board restart now. docs/OTA.md says why each rule is
there; every rule has a planted bug in firmware/ota_mutations.json that this
file must catch.
"""
import json
import subprocess

import pytest

from support import firmware, paths

pytestmark = pytest.mark.unit

HERE = paths.TESTS / "unit" / "firmware"
LOGIC = paths.TESTS.parent / "hid_fi" / "ota_logic.h"


def build(logic=LOGIC, tag="ota_bench"):
    return firmware.build_host(tag, HERE / "ota_bench.cpp", extra_inputs=[logic],
                               flags=("-std=c++17", "-O2", f'-DOTA_LOGIC="{logic}"'))


def ask(exe, lines):
    r = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


@pytest.fixture(scope="module")
def bench():
    try:
        return build()
    except RuntimeError as e:
        pytest.skip(str(e))


VERSIONS = [
    ("hid_fi_v4.0.4", "1 4 0 4"),
    ("v4.1.0", "1 4 1 0"),
    ("4.1", "1 4 1 0"),
    ("hid_fi_v3.10", "1 3 10 0"),          # releases before 4.0 had two parts; 3.10 > 3.9
    ("hid_fi_v4.0.4-test", "1 4 0 4"),     # a suffix is allowed (test builds carry one)
    ("4", "0 0 0 0"),                      # one number is not a version
    ("v4.x", "0 0 0 0"),
    ("4.0.4junk", "0 0 0 0"),
    ("-", "0 0 0 0"),
    ("hid_fi_v1234567.0", "0 0 0 0"),      # absurd parts are refused, not overflowed
]


@pytest.mark.parametrize("text, want", VERSIONS)
def test_versions_are_read_or_refused(bench, text, want):
    assert ask(bench, [f"version {text}"]) == [want]


COMPARE = [
    ("4.0.4", "4.0.4", "0"),
    ("4.0.4", "4.0.5", "-1"),
    ("4.1.0", "4.0.9", "1"),
    ("hid_fi_v3.10", "hid_fi_v3.9", "1"),   # numeric, not text: 10 > 9
    ("5.0", "4.99.99", "1"),
    ("4.0", "4.0.0", "0"),
    ("4.0.4", "garbage", "x"),              # never decided on a guess
]


@pytest.mark.parametrize("a, b, want", COMPARE)
def test_versions_compare_by_number(bench, a, b, want):
    assert ask(bench, [f"compare {a} {b}"]) == [want]


# uptime_ms hid need_net net_up forced -> verdict why
SELFTEST = [
    ("0 1 0 0 0", "wait running"),                 # just started
    ("59999 1 0 0 0", "wait running"),             # one ms short of a minute: not yet
    ("60000 1 0 0 0", "pass -"),                   # a minute up, USB up, no network needed
    ("60000 1 1 1 0", "pass -"),                   # network needed and back
    ("60000 1 1 0 0", "wait network"),             # network needed, not back yet: wait
    ("179999 1 1 0 0", "wait network"),
    ("180000 1 1 0 0", "fail network"),            # three minutes without it: go back
    ("29999 0 0 0 0", "wait usb_hid"),
    ("30000 0 0 0 0", "fail usb_hid"),             # USB HID never came up: go back
    ("300000 0 1 1 0", "fail usb_hid"),            # USB is checked before anything else
    ("0 1 0 0 1", "fail test_build"),              # a test build that must fail does, at once
    ("600000 1 0 1 0", "pass -"),                  # network up although not needed: fine
]


@pytest.mark.parametrize("given, want", SELFTEST)
def test_the_self_test_decides_only_on_evidence(bench, given, want):
    assert ask(bench, [f"selftest {given}"]) == [want]


def test_a_sleeping_computer_never_rolls_an_update_back(bench):
    """The self-test asks whether the USB HID stack is up, not whether a host
    mounted it: a computer asleep through the update must not cost the update."""
    assert ask(bench, ["selftest 600000 1 0 0 0"]) == ["pass -"]


# step to slot running running_slot pending aborted_other -> outcome
OUTCOMES = [
    ("- - - hid_fi_v4.0.4 app0 0 0", "none"),
    ("- - - hid_fi_v4.1.0 app1 1 0", "verifying"),                  # a cable-installed image, still proving itself
    ("pending hid_fi_v4.1.0 app1 hid_fi_v4.1.0 app1 1 0", "verifying"),
    ("pending hid_fi_v4.1.0 app1 hid_fi_v4.1.0 app1 0 0", "updated"),
    ("switched hid_fi_v4.1.0 app1 hid_fi_v4.1.0 app1 0 0", "updated"),
    ("pending hid_fi_v4.1.0 app1 hid_fi_v4.0.4 app0 0 0", "rolled_back"),   # recorded an update, the old slot runs
    ("switched hid_fi_v4.1.0 app1 hid_fi_v4.0.4 app0 0 1", "rolled_back"),
    # The slot decides: the same version reinstalled, or a test image, rolled back -
    # the versions compare equal, the slots do not (found writing the board test).
    ("pending hid_fi_v4.0.4 app1 hid_fi_v4.0.4 app0 0 0", "rolled_back"),
    ("pending hid_fi_v4.0.4-test app1 hid_fi_v4.0.4 app0 0 0", "rolled_back"),
    ("pending hid_fi_v4.0.4 app1 hid_fi_v4.0.4 app1 0 0", "updated"),
    # An old record without a slot falls back to the version.
    ("pending hid_fi_v4.1.0 - hid_fi_v4.1.0 app1 0 0", "updated"),
    ("pending hid_fi_v4.1.0 - hid_fi_v4.0.4 app0 0 0", "rolled_back"),
    ("pending garbage - hid_fi_v4.0.4 app0 0 0", "rolled_back"),           # unreadable target: never "updated"
    ("rolled_back hid_fi_v4.1.0 app1 hid_fi_v4.0.4 app0 0 0", "rolled_back"),
    ("valid hid_fi_v4.1.0 app1 hid_fi_v4.1.0 app1 0 0", "updated"),
    ("valid hid_fi_v4.1.0 app1 hid_fi_v4.1.0 app1 0 1", "updated"),          # an old failure does not hide a new success
    ("- - - hid_fi_v4.0.4 app0 0 1", "rolled_back"),                # no record, but the other slot was aborted
]


@pytest.mark.parametrize("given, want", OUTCOMES)
def test_after_a_restart_the_board_knows_what_happened(bench, given, want):
    assert ask(bench, [f"outcome {given}"]) == [want]


SLOT = 0x640000
# version min_from chip size downgrade_ok running slot -> verdict
INSTALL = [
    ("4.1.0 4.0.4 esp32s3 1600000 0 hid_fi_v4.0.4 6553600", "ok"),
    ("4.1.0 4.0.4 esp32s3 1600000 0 hid_fi_v4.0.5 6553600", "ok"),
    ("4.0.4 4.0.0 esp32s3 1600000 0 hid_fi_v4.0.4 6553600", "not_newer"),       # what already runs
    ("4.0.3 4.0.0 esp32s3 1600000 0 hid_fi_v4.0.4 6553600", "not_newer"),       # older: anti-rollback
    ("4.0.3 4.0.0 esp32s3 1600000 1 hid_fi_v4.0.4 6553600", "ok"),              # unless the person chose it
    ("4.0.4 4.0.0 esp32s3 1600000 1 hid_fi_v4.0.4 6553600", "not_newer"),       # never "update" to the same
    ("4.1.0 4.0.5 esp32s3 1600000 0 hid_fi_v4.0.4 6553600", "too_old"),         # needs an intermediate (or the cable)
    ("4.1.0 4.0.4 esp32 1600000 0 hid_fi_v4.0.4 6553600", "wrong_chip"),
    ("4.1.0 4.0.4 - 1600000 0 hid_fi_v4.0.4 6553600", "wrong_chip"),
    ("4.1.0 4.0.4 esp32s3 6553601 0 hid_fi_v4.0.4 6553600", "too_big"),
    ("4.1.0 4.0.4 esp32s3 6553600 0 hid_fi_v4.0.4 6553600", "ok"),              # exactly fits
    ("4.1.0 4.0.4 esp32s3 0 0 hid_fi_v4.0.4 6553600", "empty"),
    ("latest 4.0.4 esp32s3 1600000 0 hid_fi_v4.0.4 6553600", "bad_version"),
    ("4.1.0 4.0.4 esp32s3 1600000 0 garbage 6553600", "bad_version"),
]


@pytest.mark.parametrize("given, want", INSTALL)
def test_what_may_be_installed(bench, given, want):
    assert ask(bench, [f"caninstall {given}"]) == [want]


CANCEL = [("0", "nothing"), ("1", "stop"), ("2", "stop"), ("3", "stop"), ("4", "refused"), ("5", "switch_back")]


@pytest.mark.parametrize("step, want", CANCEL)
def test_cancel_is_defined_at_every_step(bench, step, want):
    """docs/OTA.md 'Checkpoints and cancel': only the 0.1 s otadata write refuses."""
    assert ask(bench, [f"cancel {step}"]) == [want]


RESUME = [
    ("500000 1600000 1 1", "500000"),     # carry on where it broke
    ("500000 1600000 0 1", "0"),          # server ignores Range: start over
    ("0 1600000 1 1", "0"),
    ("1600000 1600000 1 1", "0"),          # "complete" but failed: something is wrong, start over
    ("500000 1600000 1 7", "500000"),
    ("500000 1600000 1 8", "-1"),          # eight attempts: give up, nothing switched
]


@pytest.mark.parametrize("given, want", RESUME)
def test_a_broken_download_resumes_restarts_or_gives_up(bench, given, want):
    assert ask(bench, [f"resume {given}"]) == [want]


def test_retries_back_off_and_are_capped(bench):
    assert ask(bench, [f"delay {a}" for a in (1, 2, 3, 4, 5, 6, 7, 20)]) == \
        ["2000", "4000", "8000", "16000", "32000", "60000", "60000", "60000"]


# enabled online online_for checked_before since_last busy
CHECK = [
    ("1 1 30000 0 0 0", "1"),             # half a minute after coming online, first look
    ("1 1 29999 0 0 0", "0"),
    ("0 1 99999 0 0 0", "0"),             # switched off by the person (ETSI 5.3-6B)
    ("1 0 99999 0 0 0", "0"),             # not on the internet: never from the board's own WiFi
    ("1 1 99999 0 0 1", "0"),             # already busy
    ("1 1 99999 1 86399999 0", "0"),      # looked less than a day ago
    ("1 1 99999 1 86400000 0", "1"),      # once a day
]


@pytest.mark.parametrize("given, want", CHECK)
def test_when_the_board_looks_by_itself(bench, given, want):
    assert ask(bench, [f"shouldcheck {given}"]) == [want]


# asked auto_security security keys_held pending
APPLY = [
    ("1 0 0 0 0", "1"),                   # the person pressed Restart now
    ("0 0 1 0 0", "0"),                   # a security release, but auto-install is off: consent rules
    ("0 1 1 0 0", "1"),                   # auto-install of security releases, which the person switched on
    ("0 1 0 0 0", "0"),                   # ... only for security releases
    ("1 0 0 1 0", "0"),                   # a key is held: never restart under a finger
    ("1 0 0 0 1", "0"),                   # still proving the current one
]


@pytest.mark.parametrize("given, want", APPLY)
def test_when_a_ready_update_may_restart_the_board(bench, given, want):
    assert ask(bench, [f"mayapply {given}"]) == [want]


# Commands that press, type or move something - a restart under them would be
# a key typed by nobody. None may be counted as a mere query.
HID_COMMANDS = ["key_down", "key_up", "key_release_all", "press", "type", "unlock", "lock", "mouse_move",
                "mouse_click", "mouse_abs", "mouse_scroll", "media", "gamepad", "macro_run", "do", "open", "steps",
                "system", "vault_type", "presenter", "gesture", "wiggle_start", "set_lock_state", "test_hid"]


def test_status_polls_are_not_use_but_typing_is(bench):
    """A dashboard polls status every second; that must not keep a ready update
    from applying. Typing and moving must."""
    q = ["status", "ping", "ota_status", "ota_check", "macro_list"]
    assert ask(bench, [f"queryonly {c}" for c in q]) == ["1"] * len(q)
    assert ask(bench, [f"queryonly {c}" for c in HID_COMMANDS + ["no_such_command"]]) == \
        ["0"] * (len(HID_COMMANDS) + 1)


def test_the_query_only_list_names_real_commands_and_nothing_that_presses():
    import re
    src = LOGIC.read_text(encoding="utf-8")
    listed = re.findall(r'"([a-z_]+)"', src[src.index("inline bool otaQueryOnly"):])
    real = set(firmware.dispatch_commands())
    assert set(listed) <= real, f"not commands: {set(listed) - real}"
    assert not set(listed) & set(HID_COMMANDS)


def test_no_restart_while_a_new_version_proves_itself(bench):
    assert ask(bench, ["restart 1", "restart 0"]) == ["0", "1"]


def test_the_firmware_includes_this_logic_and_overrides_the_core():
    """The header is only worth testing if the board runs it, and only safe if the
    core cannot mark the new firmware valid before the self-test."""
    ino = firmware.ino_text()
    ota = (paths.TESTS.parent / "hid_fi" / "ota.h").read_text(encoding="utf-8")
    assert '#include "ota_logic.h"' in ota and '#include "ota.h"' in ino
    assert 'extern "C" bool verifyRollbackLater()' in ota, \
        "without it the core marks every new firmware valid before setup() - rollback would protect nothing"
    assert "esp_ota_mark_app_valid_cancel_rollback" in ota and "otaSelfTest(" in ota


@pytest.mark.slow
def test_every_planted_bug_is_caught(tmp_path):
    """Mutation testing over ota_logic.h: each deliberate bug must fail this file."""
    src = LOGIC.read_text(encoding="utf-8")
    muts = json.loads((HERE / "ota_mutations.json").read_text(encoding="utf-8"))["mutations"]
    tables = [("caninstall", INSTALL), ("cancel", CANCEL), ("resume", RESUME), ("shouldcheck", CHECK),
              ("mayapply", APPLY)]
    lines = ([f"version {t}" for t, _ in VERSIONS] + [f"compare {a} {b}" for a, b, _ in COMPARE]
             + [f"selftest {g}" for g, _ in SELFTEST] + [f"outcome {g}" for g, _ in OUTCOMES] + ["restart 1", "restart 0"]
             + [f"{op} {g}" for op, rows in tables for g, _ in rows] + [f"delay {a}" for a in (1, 2, 6, 20)]
             + [f"queryonly {c}" for c in ["status", "ota_status", *HID_COMMANDS]])
    want = ([w for _, w in VERSIONS] + [w for *_, w in COMPARE] + [w for _, w in SELFTEST]
            + [w for _, w in OUTCOMES] + ["0", "1"] + [w for _, rows in tables for _, w in rows]
            + ["2000", "4000", "60000", "60000"] + ["1", "1"] + ["0"] * len(HID_COMMANDS))
    finds = [m["find"] for m in muts]
    assert len(set(finds)) == len(finds), "two planted bugs edit the same text - one of them is not tested"
    survived = []
    for i, m in enumerate(muts):
        assert m["find"] != m["replace"] and m["find"].split("//")[0].strip(), f"{m['name']!r} changes nothing that runs"
        assert src.count(m["find"]) == 1, f"mutation {m['name']!r}: 'find' must match ota_logic.h exactly once"
        mutant = tmp_path / f"m{i}" / "ota_logic.h"
        mutant.parent.mkdir()
        mutant.write_text(src.replace(m["find"], m["replace"]), encoding="utf-8")
        try:
            out = ask(build(mutant, tag=f"ota_mutant{i}"), lines)
        except (RuntimeError, AssertionError):
            continue                      # does not build or crashes: caught
        if out == want:
            survived.append(m["name"])
    assert not survived, f"planted bugs the tests did NOT catch: {survived}"
