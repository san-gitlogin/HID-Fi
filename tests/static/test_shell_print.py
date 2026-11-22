"""The HID-Fi Core print files (hardware/hid-fi-core), measured as published.

The first print (2026-11-22) fitted the board perfectly and failed at the base: the
front tongue never entered its slot (0.10-0.15 mm round it, less than one layer line
of a service print) and a press-fit peg snapped across its layers when the plate came
off. The design that replaced them (front tabs, a flat spring latch at the back) is
only worth something while the files people download still have its gaps. So this
does not trust the design's own report: it casts rays through the published meshes
(parts-core.json, assembled) and measures every mating gap itself, then checks each
print file is a closed, printable solid carrying its licence. Standard library only.
"""
import base64
import json
import os
import struct
import zipfile
from pathlib import Path

import pytest

from support import paths

pytestmark = pytest.mark.static
# HIDFI_SHELL_CORE points it at another copy, to prove it fails on a known-bad build
CORE = Path(os.environ.get("HIDFI_SHELL_CORE") or paths.TESTS.parent / "hardware" / "hid-fi-core")
PRINT = CORE / "print"
HEADER = b"HID-Fi Core (c) 2026 Santhosh. PolyForm Noncommercial 1.0.0. Not for resale."
FILES = ["kit-all", "kit-test", "kit-core", "unibody", "plate", "plate-feet", "fit-coupon", "latch-coupon", "trim-guide"]
COARSE = 0.28        # what a coarse service print eats from a gap: 0.12-0.14 mm per surface, both parts
EPS = 0.02           # float32 meshes and faceted curves


def tris_of(data):
    n = struct.unpack_from("<I", data, 80)[0]
    assert len(data) == 84 + 50 * n, "a binary STL's size is 84 + 50 bytes per triangle"
    out = []
    for i in range(n):
        v = struct.unpack_from("<9f", data, 84 + 50 * i + 12)
        out.append((v[0:3], v[3:6], v[6:9]))
    return out


@pytest.fixture(scope="module")
def manifest():
    return json.loads((CORE / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def assembled():
    parts = json.loads((CORE / "parts-core.json").read_text(encoding="utf-8"))
    return {k: tris_of(base64.b64decode(v)) for k, v in parts.items()}


def ray(tris, o, d, near=None):
    """Distances along unit axis d from o to every triangle it crosses (Moller-Trumbore)."""
    hits = []
    for a, b, c in tris:
        if near and not all(min(a[k], b[k], c[k]) <= hi + 0.5 and max(a[k], b[k], c[k]) >= lo - 0.5 for k, (lo, hi) in near.items()):
            continue                      # the triangle's box misses the ray's box (big faces still overlap)
        e1 = [b[i] - a[i] for i in range(3)]
        e2 = [c[i] - a[i] for i in range(3)]
        p = [d[1] * e2[2] - d[2] * e2[1], d[2] * e2[0] - d[0] * e2[2], d[0] * e2[1] - d[1] * e2[0]]
        det = sum(e1[i] * p[i] for i in range(3))
        if abs(det) < 1e-12:
            continue
        s = [o[i] - a[i] for i in range(3)]
        u = sum(s[i] * p[i] for i in range(3)) / det
        if u < 0 or u > 1:
            continue
        q = [s[1] * e1[2] - s[2] * e1[1], s[2] * e1[0] - s[0] * e1[2], s[0] * e1[1] - s[1] * e1[0]]
        v = sum(d[i] * q[i] for i in range(3)) / det
        if v < 0 or u + v > 1:
            continue
        t = sum(e2[i] * q[i] for i in range(3)) / det
        if t > 1e-6:
            hits.append(t)
    return sorted(hits)


def gap(tris, o, d, reach=3.0):
    """How far from o (a point on one part's surface) the other part is, along d. There
    must BE another part within reach: a ray that hits nothing measures nothing (this
    test once passed a 0.12 mm design because its rays found no walls at all)."""
    box = {k: (min(o[k], o[k] + d[k] * reach), max(o[k], o[k] + d[k] * reach)) for k in range(3)}
    h = [t for t in ray(tris, o, d, near=box) if t <= reach]
    assert h, f"no wall within {reach} mm of {o} along {d}: the measurement found nothing to measure"
    return h[0]


# ---- every file is a printable, licensed solid ------------------------------------------------------
@pytest.mark.parametrize("name", FILES)
def test_each_print_file_is_one_closed_solid_on_the_bed_with_its_licence(name):
    data = (PRINT / f"{name}.stl").read_bytes()
    assert data[:80].rstrip(b" \x00") == HEADER, "the licence line goes in every STL header"
    tris = tris_of(data)
    edges = {}
    for t in tris:
        pts = [tuple(round(c, 4) for c in p) for p in t]
        for i in range(3):
            e = (pts[i], pts[(i + 1) % 3])
            edges[e] = edges.get(e, 0) + 1
    open_edges = sum(1 for (a, b), n in edges.items() if edges.get((b, a), 0) != n)
    assert open_edges == 0, f"{name}: {open_edges} edges without a partner - not a closed solid, a slicer may fill it wrong"
    zs = [p[2] for t in tris for p in t]
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    assert abs(min(zs)) < 1e-3, f"{name} does not sit on the bed (lowest point z={min(zs):.3f})"
    assert max(xs) - min(xs) < 180 and max(ys) - min(ys) < 180, f"{name} does not fit a 180 mm bed"


def test_the_notes_and_zips_carry_every_file_and_the_licence():
    notes = (PRINT / "PRINT-NOTES.txt").read_text(encoding="utf-8")
    for n in ["kit-test", "unibody", "plate", "plate-feet", "trim-guide"]:
        assert f"{n}.stl" in notes
    assert "0.3 mm" in notes and "No supports" in notes
    for retired in ["peg", "light pipe", "light-pipe", "tongue"]:
        assert retired not in notes.lower(), f"PRINT-NOTES still describes the first print's {retired!r}"
    with zipfile.ZipFile(PRINT / "HID-Fi-Core-print.zip") as z:
        assert set(z.namelist()) == {f"{n}.stl" for n in FILES} | {"PRINT-NOTES.txt", "LICENSE-NOTICE.txt"}
        for n in FILES:
            assert z.read(f"{n}.stl") == (PRINT / f"{n}.stl").read_bytes(), f"{n}.stl in the zip is not the published one"
    with zipfile.ZipFile(PRINT / "HID-Fi-Core-kit-all.zip") as z:
        assert set(z.namelist()) == {"kit-all.stl", "PRINT-NOTES.txt", "LICENSE-NOTICE.txt"}
    assert {p.stem for p in PRINT.glob("*.stl")} == set(FILES), "a print file nobody lists, or a listed one missing"


def test_the_published_design_passed_every_build_check(manifest):
    bad = [c["what"] for c in manifest["checks"] if not c["ok"]]
    assert not bad, f"published with failing checks: {bad}"
    ids = {c.get("id") for c in manifest["checks"]}
    for need in ["coarse-print", "fits", "features", "overhangs", "skins", "swing", "snap", "drop-plate"]:
        assert need in ids, f"the closure check {need!r} is missing from the published build"


def test_the_latch_bends_inside_petgs_repeat_use_strain(manifest):
    c = manifest["features"]["closure"]
    assert c["strain"] <= 2.5 and 3 <= c["closeN"] <= 20 and c["pressN"] <= 10
    assert c["latch"]["t"] >= 1.2 and c["tab"]["z1"] - c["tab"]["z0"] >= 1.0 - 1e-9


# ---- the gaps, measured on the meshes ---------------------------------------------------------------
def test_the_front_tabs_have_a_coarse_prints_room_all_round(manifest, assembled):
    c = manifest["features"]["closure"]
    uni, tab = assembled["unibody"], c["tab"]
    for x0, x1 in tab["xs"]:
        for x in (x0 + 0.5, (x0 + x1) / 2, x1 - 0.5):
            for y in (tab["tip"] + 0.3, 0.0, c["rib"]["y1"] - 0.05):
                assert gap(uni, (x, y, tab["z1"]), (0, 0, 1)) >= COARSE, f"tab top at x={x} y={y}"
                assert gap(uni, (x, y, tab["z0"]), (0, 0, -1)) >= COARSE, f"tab underside at x={x} y={y}"
            assert gap(uni, (x, tab["tip"], (tab["z0"] + tab["z1"]) / 2), (0, -1, 0)) >= COARSE, "tab tip to slot end"
        mid = (0.0, (tab["z0"] + tab["z1"]) / 2)
        assert gap(uni, (x0, *mid), (-1, 0, 0)) >= COARSE and gap(uni, (x1, *mid), (1, 0, 0)) >= COARSE, "tab sides"


def test_the_tabs_really_reach_into_the_wall(manifest, assembled):
    c = manifest["features"]["closure"]
    plate, tab = assembled["plate"], c["tab"]
    for x0, x1 in tab["xs"]:
        ys = [p[1] for t in plate for p in t if x0 - EPS <= p[0] <= x1 + EPS and p[2] > tab["z0"] - EPS]
        assert min(ys) <= tab["tip"] + EPS
        # how far the tab sits inside the rib and wall: at least 0.8 mm of hook
        assert c["rib"]["y1"] - min(ys) >= 0.8


def test_the_catches_click_into_the_window_with_room_to_spare(manifest, assembled):
    c = manifest["features"]["closure"]
    uni, plate, L = assembled["unibody"], assembled["plate"], c["latch"]
    wall = manifest["features"]["cav"]["y1"]
    for x0, x1 in c["catches"]:
        ys = [p[1] for t in plate for p in t if x0 - EPS <= p[0] <= x1 + EPS and L["catch"][0] - EPS <= p[2] <= L["catch"][1] + EPS]
        assert max(ys) - wall >= L["engage"] - EPS, "the catch reaches into the wall by its full engagement"
        y = wall + L["engage"] / 2
        for x in (x0 + 0.3, x1 - 0.3):
            assert gap(uni, (x, y, L["catch"][0]), (0, 0, -1)) >= COARSE, "play over the window's lip"
        outer = (x0, (-1, 0, 0)) if (x0 + x1) / 2 < 13.97 else (x1, (1, 0, 0))   # the side facing the window's edge
        assert gap(uni, (outer[0], y, L["catch"][0] + 0.1), outer[1]) >= COARSE, "catch to the window's side"


def test_the_plate_clears_the_peg_posts_above_it(manifest, assembled):
    c, uni = manifest["features"]["closure"], assembled["unibody"]
    for x, y in manifest["features"]["sockets"]:
        for dx in (-1.8, 1.8):
            if 0.3 < x + dx < 27.6:
                assert gap(uni, (x + dx, y, c["plateT"]), (0, 0, 1)) >= COARSE, f"post over the plate at x={x + dx:.1f}"


def test_the_plate_itself_has_no_pegs_or_press_fits(assembled, manifest):
    """Pegs stand up off the bed and snap across their layers; none may come back on the Core plate."""
    plate = assembled["plate"]
    for x, y in manifest["features"]["sockets"]:
        near = [p for t in plate for p in t if abs(p[0] - x) < 1.6 and abs(p[1] - y) < 1.6]
        assert not near or max(p[2] for p in near) <= manifest["features"]["closure"]["plateT"] + EPS, \
            "something rises off the plate under a peg post"
