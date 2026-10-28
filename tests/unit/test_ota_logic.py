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


def test_no_restart_while_a_new_version_proves_itself(bench):
    assert ask(bench, ["restart 1", "restart 0"]) == ["0", "1"]


def test_the_firmware_includes_this_logic_and_overrides_the_core():
    """The header is only worth testing if the board runs it, and only safe if the
    core cannot mark the new firmware valid before the self-test."""
    ino = firmware.ino_text()
    ota = (paths.TESTS.parent / "hid_fi" / "ota.h").read_text()
    assert '#include "ota_logic.h"' in ota and '#include "ota.h"' in ino
    assert 'extern "C" bool verifyRollbackLater()' in ota, \
        "without it the core marks every new firmware valid before setup() - rollback would protect nothing"
    assert "esp_ota_mark_app_valid_cancel_rollback" in ota and "otaSelfTest(" in ota


@pytest.mark.slow
def test_every_planted_bug_is_caught(tmp_path):
    """Mutation testing over ota_logic.h: each deliberate bug must fail this file."""
    src = LOGIC.read_text()
    muts = json.loads((HERE / "ota_mutations.json").read_text())["mutations"]
    lines = ([f"version {t}" for t, _ in VERSIONS] + [f"compare {a} {b}" for a, b, _ in COMPARE]
             + [f"selftest {g}" for g, _ in SELFTEST] + [f"outcome {g}" for g, _ in OUTCOMES] + ["restart 1", "restart 0"])
    want = ([w for _, w in VERSIONS] + [w for *_, w in COMPARE] + [w for _, w in SELFTEST]
            + [w for _, w in OUTCOMES] + ["0", "1"])
    finds = [m["find"] for m in muts]
    assert len(set(finds)) == len(finds), "two planted bugs edit the same text - one of them is not tested"
    survived = []
    for i, m in enumerate(muts):
        assert m["find"] != m["replace"] and m["find"].split("//")[0].strip(), f"{m['name']!r} changes nothing that runs"
        assert src.count(m["find"]) == 1, f"mutation {m['name']!r}: 'find' must match ota_logic.h exactly once"
        mutant = tmp_path / f"m{i}" / "ota_logic.h"
        mutant.parent.mkdir()
        mutant.write_text(src.replace(m["find"], m["replace"]))
        try:
            out = ask(build(mutant, tag=f"ota_mutant{i}"), lines)
        except (RuntimeError, AssertionError):
            continue                      # does not build or crashes: caught
        if out == want:
            survived.append(m["name"])
    assert not survived, f"planted bugs the tests did NOT catch: {survived}"
