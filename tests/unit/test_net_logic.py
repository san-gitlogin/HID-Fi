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
    # D-02: what the board remembers too - the model must choose and move as the header does.
    lines, cases = [], []
    for _ in range(1500):
        known, seen = random_home(rng)
        air = {}
        for n, r in seen:
            air[n] = max(r, air.get(n, -1000))                 # the model holds one reading per name
        seen = list(air.items())
        cur = rng.randrange(len(known))
        here, owner = rng.choice([0, 299, 300, 900]), rng.random() < 0.2
        lines += [f"choose {kspec(known)} {spec(seen)}", f"move {kspec(known)} {spec(seen)} {cur} {here * 1000} {int(owner)}"]
        cases.append((known, air, cur, here, owner))
    got = ask(bench, lines)
    for n, (known, air, cur, here, owner) in enumerate(cases):
        s = BoardSim(pin="")
        s.nets = [{"ssid": k[0], "password": "p", "proven": True, "static": False, "refused": not k[1],
                   "subnet": k[2], "used": k[3], "computer": k[4], "tried": k[5], "pw_refused": k[6]} for k in known]
        s.air = dict(air)
        c, m = got[2 * n], got[2 * n + 1]
        assert s.net_choose() == ((c[0], c[1]) if c[0] >= 0 else (None, "")), ("choose", known, air)
        s.sta.update(phase="online", ssid=known[cur][0])
        want = (known[m[0]][0], m[1].replace(" ", "_")) if m[0] >= 0 else (None, "")
        assert s.net_move(here, owner) == want, ("move", known, air, cur, here, owner)


# ---- D-02: the network your devices use (docs/DECISIONS.md) ---------------------------
# A saved network as the board remembers it: (name, usable, subnet, used, anchor, tried, refused).
# refused: it has worked, but its router refused the saved password since start (D-08).
# subnet 0: never joined. used: a counter, higher is more recent, 0 never.
WORKABLE, LOOK_MS = -75, 300000
WHY = {0: None, 1: "preferred", 2: "strongest", 3: "coming home", 4: "looking"}
EXT, C6, ARV = "ARVISANTU EXT", "ARVISANTU C6", "ARVISANTU"
LAN1, LAN0 = 0xC0A80100, 0xC0A80000         # 192.168.1.0 (EXT and ARVISANTU), 192.168.0.0 (C6)


def kspec(known):
    return ",".join(f"{n.encode().hex()}:{int(u)}:{net}:{used}:{int(a)}:{int(t)}:{int(r)}" for n, u, net, used, a, t, r in known) or "-"


def ask(bench, lines):
    out = subprocess.run([str(bench)], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60).stdout.split("\n")
    return [(int(l.split()[0]), WHY[int(l.split()[1])]) for l in out if l.strip()]


def choose(bench, known, seen):
    i, why = ask(bench, [f"choose {kspec(known)} {spec(seen)}"])[0]
    return (known[i][0] if i >= 0 else None), why


def move(bench, known, seen, cur, ms_here=0, owner_now=False):
    i, why = ask(bench, [f"move {kspec(known)} {spec(seen)} {cur} {ms_here} {int(owner_now)}"])[0]
    return (known[i][0] if i >= 0 else None), why


def net(name, usable=True, subnet=0, used=0, anchor=False, tried=False, refused=False):
    return (name, usable, subnet, used, anchor, tried, refused)


# The owner's home on 2026-11-06: C6 is another router (192.168.0.x); EXT and ARVISANTU
# are one network (192.168.1.x). The board sat where C6 was stronger.
AIR = [(C6, -48), (EXT, -62), (ARV, -70)]


def test_the_network_you_use_wins_over_a_stronger_one(bench):
    # Nobody has used it yet: D-01, the strongest - C6.
    fresh = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1), net(ARV, subnet=LAN1)]
    assert choose(bench, fresh, AIR) == (C6, "strongest")
    # The Mac and the iPad reached it over EXT: from then on EXT, though C6 is stronger.
    used = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=3), net(ARV, subnet=LAN1)]
    assert choose(bench, used, AIR) == (EXT, "preferred")
    # Used over C6 instead: C6.
    assert choose(bench, [net(C6, subnet=LAN0, used=3), net(EXT, subnet=LAN1)], AIR) == (C6, "preferred")


@pytest.mark.parametrize("ext_rssi, want", [
    (-75, (EXT, "preferred")),             # exactly workable: still preferred
    (-76, (C6, "strongest")),              # just below: the board stays reachable on the strongest
    (-90, (C6, "strongest")),
    (None, (C6, "strongest")),             # gone
])
def test_a_preferred_network_too_weak_falls_back_to_the_strongest(bench, ext_rssi, want):
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=3)]
    seen = [(C6, -48)] + ([(EXT, ext_rssi)] if ext_rssi is not None else [])
    assert choose(bench, known, seen) == want


def test_one_network_on_two_names_roams_to_the_strongest(bench):
    # Used over EXT; ARVISANTU is the same network (same subnet) and stronger here: ARVISANTU.
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=3), net(ARV, subnet=LAN1)]
    assert choose(bench, known, [(C6, -40), (EXT, -70), (ARV, -55)]) == (ARV, "preferred")
    # A name never joined has no subnet yet: it is only itself, not part of the used network.
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=3), net(ARV)]
    assert choose(bench, known, [(C6, -40), (EXT, -70), (ARV, -55)]) == (EXT, "preferred")
    # The same network on two access points: its strongest counts (D-01 still holds).
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=3)]
    assert choose(bench, known, [(EXT, -80), (C6, -40), (EXT, -60)]) == (EXT, "preferred")


def test_the_paired_computer_anchors_over_a_phone(bench):
    # The phone used it over C6 more recently, but the paired computer is on EXT:
    # screen sharing needs the computer's network, and a phone has the board's own WiFi.
    known = [net(C6, subnet=LAN0, used=9), net(EXT, subnet=LAN1, used=4, anchor=True)]
    assert choose(bench, known, AIR) == (EXT, "preferred")
    # The computer's network gone: the most recent use, C6.
    assert choose(bench, known, [(C6, -50)]) == (C6, "strongest")


def test_the_most_recently_used_wins_without_a_computer(bench):
    known = [net(C6, subnet=LAN0, used=9), net(EXT, subnet=LAN1, used=4)]
    assert choose(bench, known, AIR) == (C6, "preferred")
    known = [net(C6, subnet=LAN0, used=4), net(EXT, subnet=LAN1, used=9)]
    assert choose(bench, known, AIR) == (EXT, "preferred")
    # A refused or resting network never wins, preferred or not.
    known = [net(C6, subnet=LAN0), net(EXT, usable=False, subnet=LAN1, used=9)]
    assert choose(bench, known, AIR) == (C6, "strongest")


def test_a_board_nobody_uses_looks_for_its_people(bench):
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1), net(ARV, subnet=LAN1)]
    # On C6 (strongest) and nobody came: not before 5 minutes...
    assert move(bench, known, AIR, 0, LOOK_MS - 1) == (None, None)
    # ...then it looks on the strongest other network it has not tried.
    assert move(bench, known, AIR, 0, LOOK_MS) == (EXT, "looking")
    # Not on a network of the same subnet as the one it is on (ARVISANTU is EXT's).
    known = [net(C6, subnet=LAN0, tried=True), net(EXT, subnet=LAN1), net(ARV, subnet=LAN1)]
    assert move(bench, known, AIR, 1, LOOK_MS) == (None, None)
    # Every other tried already: it settles where it is.
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, tried=True)]
    assert move(bench, known, AIR, 0, LOOK_MS * 10) == (None, None)
    # Someone used it here, or someone is here now: it stays.
    assert move(bench, [net(C6, subnet=LAN0, used=1), net(EXT, subnet=LAN1)], AIR, 0, LOOK_MS) == (None, None)
    assert move(bench, [net(C6, subnet=LAN0), net(EXT, subnet=LAN1)], AIR, 0, LOOK_MS, owner_now=True) == (None, None)
    # Off a network it tried in vain, choosing again leaves it out while another is in range.
    known = [net(C6, subnet=LAN0, tried=True), net(EXT, subnet=LAN1)]
    assert choose(bench, known, AIR) == (EXT, "strongest")
    assert choose(bench, known, [(C6, -40)]) == (C6, "strongest")      # all that is left: taken anyway


def test_it_comes_home_but_never_away_from_someone_using_it(bench):
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=5)]
    # On C6 because EXT was down; EXT is back and workable: home.
    assert move(bench, known, AIR, 0) == (EXT, "coming home")
    # EXT back but too weak: stays.
    assert move(bench, known, [(C6, -48), (EXT, -80)], 0) == (None, None)
    # Someone is using it on C6 right now: stays, whatever the preference.
    assert move(bench, known, AIR, 0, owner_now=True) == (None, None)
    # Already home (or on the same network under another name): stays.
    assert move(bench, known, AIR, 1) == (None, None)
    assert move(bench, known + [net(ARV, subnet=LAN1)], AIR, 2) == (None, None)


def test_a_changed_password_never_pulls_it_off_a_working_network(bench):
    # EXT is preferred, but its router stopped taking the saved password (changed?).
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=5, refused=True)]
    # On C6 meanwhile: it never leaves a working network to try EXT again...
    assert move(bench, known, AIR, 0) == (None, None)
    assert ask(bench, [f"look {kspec(known)} 0 0"])[0][0] == 0       # ...nor scans for it
    # Off any network: the others first, even when the changed one is the strongest...
    assert choose(bench, known, AIR) == (C6, "strongest")
    assert choose(bench, known, [(EXT, -40), (C6, -66)]) == (C6, "strongest")
    # ...EXT only when nothing else is in range (the router may have misreported).
    assert choose(bench, known, [(EXT, -50)]) == (EXT, "strongest")
    # A router and its extender: the extender still taking the password is home.
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=5, refused=True), net(ARV, subnet=LAN1)]
    assert move(bench, known, AIR, 0) == (ARV, "coming home")
    # The password entered again (or a join that works): home as before.
    known = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=5)]
    assert move(bench, known, AIR, 0) == (EXT, "coming home")


# The written rules (docs/DECISIONS.md D-02), implemented again here from the words,
# not from the header - the two must agree on any home.
def oracle_preferred(k):
    users = [i for i, x in enumerate(k) if x[3] or x[4]]
    if not users:
        return None
    return max(users, key=lambda i: (k[i][4], k[i][3], -i))


def same(k, a, b):
    return a == b or (k[a][2] != 0 and k[a][2] == k[b][2])


def strongest(name, seen):
    r = [v for n, v in seen if n == name]
    return max(r) if r else None


def oracle_best_of(k, p, seen):
    c = [(strongest(k[i][0], seen), -i) for i in range(len(k))
         if k[i][1] and not k[i][6] and same(k, i, p) and strongest(k[i][0], seen) is not None]
    return (-max(c)[1], max(c)[0]) if c else (None, None)


def oracle_choose(k, seen):
    p = oracle_preferred(k)
    if p is not None:
        i, r = oracle_best_of(k, p, seen)
        if i is not None and r >= WORKABLE:
            return i, "preferred"
    for allow_tried in (False, True):
        c = [(strongest(k[i][0], seen), -i) for i in range(len(k))
             if k[i][1] and (allow_tried or (not k[i][5] and not k[i][6])) and strongest(k[i][0], seen) is not None]
        if c:
            return -max(c)[1], "strongest"
    return None, None


def oracle_move(k, seen, cur, ms, owner):
    if owner:
        return None, None
    p = oracle_preferred(k)
    if p is not None and not same(k, cur, p):
        i, r = oracle_best_of(k, p, seen)
        if i is not None and r >= WORKABLE:
            return i, "coming home"
    if any((x[3] or x[4]) and same(k, i, cur) for i, x in enumerate(k)) or ms < LOOK_MS:
        return None, None
    c = [(strongest(k[i][0], seen), -i) for i in range(len(k))
         if k[i][1] and not k[i][5] and not k[i][6] and not same(k, i, cur) and strongest(k[i][0], seen) is not None]
    return (-max(c)[1], "looking") if c else (None, None)


def oracle_look(k, cur, ms):
    p = oracle_preferred(k)
    if p is not None and not same(k, cur, p) and any(k[i][1] and not k[i][6] and same(k, i, p) for i in range(len(k))):
        return True
    if any((x[3] or x[4]) and same(k, i, cur) for i, x in enumerate(k)) or ms < LOOK_MS:
        return False
    return any(k[i][1] and not k[i][5] and not k[i][6] and not same(k, i, cur) for i in range(len(k)))


def test_it_scans_only_when_there_is_something_to_decide(bench):
    def look(known, cur, ms):
        return ask(bench, [f"look {kspec(known)} {cur} {ms}"])[0][0] == 1
    fresh = [net(C6, subnet=LAN0), net(EXT, subnet=LAN1)]
    assert not look(fresh, 0, LOOK_MS - 1)                      # nobody yet, but not 5 minutes
    assert look(fresh, 0, LOOK_MS)                              # 5 minutes, EXT not tried
    assert not look([net(C6, subnet=LAN0), net(EXT, subnet=LAN1, tried=True)], 0, LOOK_MS * 9)   # all tried: no more scans
    assert not look([net(C6, subnet=LAN0, used=2), net(EXT, subnet=LAN1)], 0, LOOK_MS * 9)       # home: no scans
    assert look([net(C6, subnet=LAN0), net(EXT, subnet=LAN1, used=2)], 0, 0)                     # away: watch for home
    rng = random.Random(611)
    cases = []
    for _ in range(3000):
        known, _ = random_home(rng)
        cases.append((known, rng.randrange(len(known)), rng.choice([0, LOOK_MS - 1, LOOK_MS, LOOK_MS * 4])))
    got = ask(bench, [f"look {kspec(k)} {c} {m}" for k, c, m in cases])
    for (k, c, m), g in zip(cases, got):
        assert (g[0] == 1) == oracle_look(k, c, m), (k, c, m)


def random_home(rng):
    pool = [C6, EXT, ARV, "Hometown", "Office", "Cafe", "Phone hotspot"]
    names = rng.sample(pool, rng.randint(1, 5))
    subnets = [0, LAN0, LAN1, 0x0A000000]
    known = []
    for n in names:
        known.append(net(n, rng.random() > 0.15, rng.choice(subnets), rng.choice([0, 0, 0, 1, 2, 3, 7]),
                         rng.random() < 0.15, rng.random() < 0.2, rng.random() < 0.15))
    seen = []
    for n in rng.sample(pool, rng.randint(0, len(pool))):
        for _ in range(rng.choice([1, 1, 1, 2])):          # some on two access points
            seen.append((n, rng.choice(range(-92, -30, 2))))
    return known, seen


def test_random_homes_against_the_written_rules(bench):
    rng = random.Random(20261106)
    cases, lines = [], []
    for _ in range(4000):
        known, seen = random_home(rng)
        cur = rng.randrange(len(known))
        ms, owner = rng.choice([0, LOOK_MS - 1, LOOK_MS, LOOK_MS * 3]), rng.random() < 0.2
        cases.append((known, seen, cur, ms, owner))
        lines += [f"choose {kspec(known)} {spec(seen)}", f"move {kspec(known)} {spec(seen)} {cur} {ms} {int(owner)}"]
    got = ask(bench, lines)
    for n, (known, seen, cur, ms, owner) in enumerate(cases):
        c, m = got[2 * n], got[2 * n + 1]
        c = (None if c[0] < 0 else c[0], c[1])
        m = (None if m[0] < 0 else m[0], m[1])
        assert c == oracle_choose(known, seen), ("choose", known, seen)
        assert m == oracle_move(known, seen, cur, ms, owner), ("move", known, seen, cur, ms, owner)


MUTATIONS = [   # a planted bug each; the tests above must catch every one
    ("if (i >= 0 && r >= NET_WORKABLE) { if (why) *why = NET_PREFERRED; return i; }",
     "if (i >= 0) { if (why) *why = NET_PREFERRED; return i; }"),                 # preferred however weak
    ("(k[i].anchor && !k[p].anchor) ||", ""),
    ("if (!k[i].usable || k[i].refused || !netSameNet(k, i, p)) continue;",
     "if (!k[i].usable || !netSameNet(k, i, p)) continue;"),                       # comes home to a changed password
    ("(pass == 1 || (!k[i].tried && !k[i].refused))", "(pass == 1 || !k[i].tried)"),   # a changed password first                                       # the computer does not anchor
    ("return a == b || (k[a].net && k[a].net == k[b].net);", "return a == b;"),     # no roaming within a network
    ("if (cur < 0 || cur >= n || ownerHereNow) return -1;", "if (cur < 0 || cur >= n) return -1;"),   # moves away from a user
    ("if (curUsed || msHere < NET_LOOK_MS) return -1;", "if (curUsed) return -1;"),                 # looks at once
    ("if (k[i].usable && !k[i].tried && !k[i].refused && !netSameNet(k, i, cur)) return true;",
     "if (k[i].usable && !k[i].refused && !netSameNet(k, i, cur)) return true;"),  # scans forever once all tried
    ("if (!k[i].usable || k[i].tried || k[i].refused || netSameNet(k, i, cur)) continue;",
     "if (!k[i].usable || k[i].refused || netSameNet(k, i, cur)) continue;"),                       # looks in circles
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
    d02 = [(test_a_changed_password_never_pulls_it_off_a_working_network, ()), (test_it_scans_only_when_there_is_something_to_decide, ()), (test_the_network_you_use_wins_over_a_stronger_one, ()),
           (test_one_network_on_two_names_roams_to_the_strongest, ()), (test_the_paired_computer_anchors_over_a_phone, ()),
           (test_a_board_nobody_uses_looks_for_its_people, ()), (test_it_comes_home_but_never_away_from_someone_using_it, ())] + [
           (test_a_preferred_network_too_weak_falls_back_to_the_strongest, a) for a in [(-90, (C6, "strongest"))]]
    checks = d02 + [(test_three_networks_in_one_home, c) for c in [
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
