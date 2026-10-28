"""The firmware's text typing, compiled on the host against the installed ESP32 core.

What runs is the real TYPE STRING section of hid_fi.ino and the real
USBHIDKeyboard press/release and en_US table from the installed core - sliced
out of the source, not copied. Every HID report and delay is recorded.

    canary     the core still behaves the way the typing code assumes
    properties every Unicode scalar value, multilingual text and ill-formed UTF-8,
               in every OS mode, against expectations computed independently
    golden     plain-ASCII typing (reports AND timing) is exactly the committed
               reference - the "nothing existing changed" guarantee
    mutations  every planted bug in mutations.json makes the tier fail
"""
import hashlib
import json
import random
import subprocess

import pytest

from support import corpus, firmware, paths

pytestmark = pytest.mark.unit

HERE = paths.TESTS / "unit" / "firmware"
BENCH_CPP = HERE / "typing_bench.cpp"
MODES = ["windows", "windows_unibits", "unknown_unibits", "mac_off", "mac_off_linux_on", "mac_on",
         "mac_on_det_win", "linux_off", "linux_on", "linux_caps", "linux_on_det_mac"]


def _slice_core(usb_src):
    core = (usb_src / "USBHIDKeyboard.cpp").read_text(encoding="utf-8")
    a = core.index("size_t USBHIDKeyboard::pressRaw")
    b = core.index("size_t USBHIDKeyboard::write")
    lay = (usb_src / "keyboardLayout" / "KeyboardLayout_en_US.cpp").read_text(encoding="utf-8")
    lay = lay[lay.index("extern const uint8_t KeyboardLayout_en_US"):]
    return core[a:b].replace("USBHIDKeyboard::", "Kbd::"), lay


def build_bench(type_src=None, tag="typing_bench"):
    usb_src, ver = firmware.core_usb_src()
    if not usb_src:
        pytest.skip("ESP32 core not installed (arduino-cli core install esp32:esp32) - the bench compiles its USBHIDKeyboard.cpp")
    core, lay = _slice_core(usb_src)
    gen = paths.CACHE / f"{tag}-src"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "gen_core.inc").write_text(core, encoding="utf-8")
    (gen / "gen_layout.inc").write_text(lay, encoding="utf-8")
    (gen / "gen_type.inc").write_text(type_src or firmware.section(firmware.TYPE_BEGIN, firmware.TYPE_END), encoding="utf-8")
    exe = firmware.build_host(tag, BENCH_CPP, include_dirs=[gen],
                              extra_inputs=[gen / "gen_core.inc", gen / "gen_layout.inc", gen / "gen_type.inc"])
    return exe, ver


@pytest.fixture(scope="module")
def bench():
    exe, ver = build_bench()
    return exe


def _run(exe, *args, timeout=600):
    return subprocess.run([str(exe), *map(str, args)], capture_output=True, text=True, timeout=timeout)


def _dump(exe, rows, held=False):
    f = paths.CACHE / "typing_inputs.txt"
    f.write_text("\n".join(f"{label} {raw.hex() or '00'[:0]} {mode}" for label, raw, mode in rows) + "\n", encoding="utf-8")
    r = _run(exe, "dump-held" if held else "dump", f)
    assert r.returncode == 0, r.stderr
    return [json.loads(l) for l in r.stdout.splitlines()]


ASCII_STRINGS = {f"str{i}": "".join(random.Random(i).choice([chr(c) for c in range(0x20, 0x7F)] + ["\n", "\t"])
                                    for _ in range(random.Random(1000 + i).randint(1, 40))) for i in range(12)}
ASCII_STRINGS.update({"hello": "hello", "pangram": "The quick brown fox jumps over the lazy dog!", "symbols": "~`!@#$%^&*()_+-={}[]|\\:;\"'<>,.?/"})


def test_core_canary(bench):
    r = _run(bench, "canary")
    assert r.returncode == 0, r.stdout


def test_typing_properties(bench):
    cases = paths.CACHE / "typing_cases.txt"
    n = corpus.cases_file(cases)
    r = _run(bench, "check", cases)
    assert r.returncode == 0, f"{n} vectors x {len(MODES)} modes:\n" + r.stdout[-6000:]
    assert ", 0 failed" in r.stdout


def _ascii_rows(modes):
    rows = [(f"chr{c:02x}", bytes([c]), m) for m in modes for c in range(1, 128)]
    rows += [(k, v.encode(), m) for m in modes for k, v in ASCII_STRINGS.items()]
    return rows


def test_ascii_typing_matches_golden(bench, golden):
    """Plain text must type exactly as committed - reports, order and delays."""
    out = _dump(bench, _ascii_rows(["windows"]))
    held = _dump(bench, [(k, ASCII_STRINGS[k].encode(), "windows") for k in ("hello", "symbols")], held=True)
    data = {d["label"]: d["ev"] for d in out}
    data.update({"held_gui+" + d["label"]: d["ev"] for d in held})
    golden("typing_ascii", data)


def test_ascii_typing_identical_in_every_mode(bench):
    """The Unicode setting, the OS and Caps Lock must never touch plain ASCII."""
    out = _dump(bench, _ascii_rows(MODES))
    by = {}
    for d in out:
        by.setdefault(d["label"], {})[d["mode"]] = d["ev"]
    differ = [k for k, v in by.items() if len(set(v.values())) != 1]
    assert not differ, f"ASCII typed differently across modes for: {differ[:10]}"


def test_every_language_traces_match_golden(bench, golden):
    """Fingerprint of the exact reports for every corpus entry in every mode."""
    items = {k: v.encode() for k, v in corpus.CORPUS.items()}
    items.update(corpus.INVALID)
    out = _dump(bench, [(k, raw, m) for m in MODES for k, raw in items.items()])
    data = {f"{d['mode']}/{d['label']}": hashlib.sha256(d["ev"].encode()).hexdigest()[:16] + f" skipped={d['skipped']}"
            for d in out}
    golden("typing_corpus_fingerprints", data)


@pytest.mark.slow
def test_bench_catches_planted_bugs(golden):
    """Mutation testing: each planted bug must make the unit tier fail."""
    src = firmware.section(firmware.TYPE_BEGIN, firmware.TYPE_END)
    muts = json.loads((HERE / "mutations.json").read_text(encoding="utf-8"))["mutations"]
    gfile = paths.GOLDEN / "typing_ascii.json"
    if not gfile.exists():
        pytest.skip("golden typing_ascii.json not created yet - run the unit tier once, then this")
    ascii_golden = json.loads(gfile.read_text(encoding="utf-8"))
    survived = []
    cases = paths.CACHE / "typing_cases_small.txt"
    corpus.cases_file(cases, include_mixes=False)
    for m in muts:
        assert src.count(m["find"]) == 1, f"mutation {m['name']!r}: its 'find' text no longer matches the firmware exactly once - update mutations.json"
        exe, _ = build_bench(src.replace(m["find"], m["replace"]), tag="typing_mutant")
        caught = _run(exe, "check", cases).returncode != 0
        if not caught:
            dumped = {d["label"]: d["ev"] for d in _dump(exe, _ascii_rows(["windows"]))}
            caught = any(ascii_golden.get(k) != v for k, v in dumped.items())
        if not caught:
            survived.append(m["name"])
    assert not survived, f"planted bugs the tests did NOT catch: {survived}"
