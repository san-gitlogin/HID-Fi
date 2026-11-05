"""Which saved network the board joins (hid_fi/net_logic.h), compiled on this computer
and run: the same header the firmware's staSuperviseScan() uses. When the board is not
on a network it joins the strongest saved one in range; once on one it stays there
until it drops (like a phone), so these are the choices it makes at each search. The
board model (tests/support/board_sim.py) must choose the same - checked below on
hundreds of random scenarios - because every dashboard test relies on it.
"""
import random
import subprocess

import pytest

from support import firmware, paths

pytestmark = pytest.mark.unit
HERE = paths.TESTS / "unit" / "firmware"
LOGIC = paths.TESTS.parent / "hid_fi" / "net_logic.h"


@pytest.fixture(scope="module")
def bench():
    try:
        return firmware.build_host("net_bench", HERE / "net_bench.cpp", extra_inputs=[LOGIC],
                                   flags=("-std=c++17", "-O2", f'-DNET_LOGIC="{LOGIC}"'))
    except RuntimeError as e:
        pytest.skip(str(e))


def spec(items):
    return ",".join(f"{n.encode().hex()}:{int(v)}" for n, v in items) or "-"


def pick(bench, saved, seen):
    """saved: [(name, usable)], most recent first; seen: [(name, rssi)] -> (index or None, rssi)."""
    line = f"pick {spec(saved)} {spec(seen)}"
    r = subprocess.run([str(bench)], input=line + "\n", capture_output=True, text=True, timeout=30)
    i, rssi = map(int, r.stdout.split())
    return (None, None) if i < 0 else (i, rssi)


# Three networks in one home, as the owner's hometown has (names alike, one a repeater).
HOME = [("ARVISANTU C6", True), ("ARVISANTU EXT", True), ("ARVISANTU", True)]


@pytest.mark.parametrize("seen, want", [
    ([("ARVISANTU C6", -61), ("ARVISANTU EXT", -44), ("ARVISANTU", -70)], "ARVISANTU EXT"),   # the strongest
    ([("ARVISANTU C6", -40), ("ARVISANTU EXT", -44), ("ARVISANTU", -70)], "ARVISANTU C6"),
    ([("ARVISANTU", -38), ("ARVISANTU EXT", -44)], "ARVISANTU"),                              # C6 out of range
    ([("ARVISANTU C6", -50), ("ARVISANTU EXT", -50)], "ARVISANTU C6"),                         # a tie: the more recent
    ([("Neighbour 5G", -20), ("ARVISANTU EXT", -80)], "ARVISANTU EXT"),                        # strong but not saved: ignored
    ([("Neighbour 5G", -20)], None),                                                           # none saved in range: away
    ([], None),
])
def test_three_networks_in_one_home(bench, seen, want):
    i, _ = pick(bench, HOME, seen)
    assert (HOME[i][0] if i is not None else None) == want


def test_a_network_on_two_access_points_counts_its_strongest(bench):
    seen = [("Mesh", -82), ("Other", -50), ("Mesh", -41)]
    assert pick(bench, [("Other", True), ("Mesh", True)], seen) == (1, -41)


def test_a_refused_or_resting_network_is_skipped_even_when_strongest(bench):
    saved = [("ARVISANTU C6", False), ("ARVISANTU EXT", True), ("ARVISANTU", True)]
    assert pick(bench, saved, [("ARVISANTU C6", -30), ("ARVISANTU EXT", -60), ("ARVISANTU", -75)]) == (1, -60)
    assert pick(bench, [("Only", False)], [("Only", -30)]) == (None, None)


def test_names_match_exactly(bench):
    """Not by prefix, not ignoring case: "ARVISANTU" is not "ARVISANTU EXT" or "arvisantu"."""
    assert pick(bench, [("ARVISANTU", True)], [("ARVISANTU EXT", -30), ("arvisantu", -30)]) == (None, None)
    assert pick(bench, [("ARVISANTU EXT", True)], [("ARVISANTU", -30)]) == (None, None)


def test_names_with_spaces_unicode_and_32_bytes(bench):
    names = ["Café Wi‑Fi", "x" * 32, "a b  c"]
    saved = [(n, True) for n in names]
    for k, n in enumerate(names):
        seen = [(m, -80) for m in names if m != n] + [(n, -30)]
        assert pick(bench, saved, seen) == (k, -30), n


def test_the_board_model_chooses_as_the_firmware_does(bench):
    """BoardSim.net_search() against the header, on random homes: the dashboard tests
    stand on the model, so a model that chose differently would hide a bug."""
    from support.board_sim import BoardSim
    rng = random.Random(4826)
    pool = ["ARVISANTU C6", "ARVISANTU EXT", "ARVISANTU", "Hometown", "Bangalore", "Cafe"]
    lines, cases = [], []
    for _ in range(500):
        names = rng.sample(pool, rng.randint(1, 5))
        refused = {n for n in names if rng.random() < 0.2}
        air = {n: rng.choice(range(-90, -30, 3)) for n in rng.sample(pool, rng.randint(0, len(pool)))}
        saved = [(n, n not in refused) for n in names]
        lines.append(f"pick {spec(saved)} {spec(list(air.items()))}")
        cases.append((names, refused, air))
    out = subprocess.run([str(bench)], input="\n".join(lines) + "\n", capture_output=True, text=True,
                         timeout=60).stdout.splitlines()
    for (names, refused, air), got in zip(cases, out):
        i = int(got.split()[0])
        s = BoardSim(pin="")
        s.nets = [{"ssid": n, "password": "p", "proven": True, "static": False, "refused": n in refused} for n in names]
        s.join_succeeds = set(names)
        s.air = dict(air)
        assert s.net_search() == (names[i] if i >= 0 else None), (names, refused, air)


MUTATIONS = [   # a planted bug each; the tests above must catch every one
    ("seen[k].rssi > bestRssi) {", "seen[k].rssi >= bestRssi) {"),                    # a tie goes to the older
    ("if (!saved[i].usable || !saved[i].ssid) continue;", "if (!saved[i].ssid) continue;"),   # refused not skipped
    ("seen[k].rssi > bestRssi) {", "(best < 0 || seen[k].rssi < bestRssi)) {"),       # the weakest wins
]


@pytest.mark.parametrize("old, new", MUTATIONS)
def test_every_planted_bug_is_caught(bench, tmp_path, old, new):
    src = LOGIC.read_text(encoding="utf-8")
    assert src.count(old) == 1, old
    mutant = tmp_path / "net_logic.h"
    mutant.write_text(src.replace(old, new), encoding="utf-8")
    exe = firmware.build_host(f"net_bench_{abs(hash(new))}", HERE / "net_bench.cpp", extra_inputs=[mutant],
                              flags=("-std=c++17", "-O2", f'-DNET_LOGIC="{mutant}"'))
    caught = 0
    checks = [(test_three_networks_in_one_home, c) for c in [
        ([("ARVISANTU C6", -61), ("ARVISANTU EXT", -44), ("ARVISANTU", -70)], "ARVISANTU EXT"),
        ([("ARVISANTU C6", -50), ("ARVISANTU EXT", -50)], "ARVISANTU C6")]] + [
        (test_a_refused_or_resting_network_is_skipped_even_when_strongest, ()),
        (test_a_network_on_two_access_points_counts_its_strongest, ())]
    for f, args in checks:
        try:
            f(exe, *args)
        except AssertionError:
            caught += 1
    assert caught, f"the planted bug {new!r} went unnoticed"
