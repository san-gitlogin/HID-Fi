"""tools/dep_watch.py - pinned versus latest, and advisories against what HID-Fi pins.

No network: the internet is replaced by a fake (`Net`), and the pinned core by
a fake Arduino data folder. The ranges below are copied from real advisories,
because repository advisories write them by hand and the parser has to read
what is really out there (AGENTS.md, "Outside facts and dependencies").
"""
import json
import sys

import pytest

from support import paths

sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import dep_watch as dw  # noqa: E402

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ ranges --

@pytest.mark.parametrize("rng, version, hit", [
    # arduino-esp32, GHSA-8cmm-3887-r32j - the one that reached HID-Fi v4.0.4
    ("<= 3.3.7", "3.3.1", True),
    ("<= 3.3.7", "3.3.7", True),
    ("<= 3.3.7", "3.3.8", False),
    ("<= 3.3.7", "3.3.12", False),
    # GHSA-w887: a list of branches, not one range
    ("<=3.3.11, 4.0.0-alpha1", "3.3.11", True),
    ("<=3.3.11, 4.0.0-alpha1", "3.3.12", False),
    ("<=3.3.11, 4.0.0-alpha1", "4.0.0-alpha1", True),
    ("<=3.3.11, 4.0.0-alpha1", "4.0.0", False),
    # GHSA-5476: two upper bounds = two branches (OR)
    ("<= 3.3.0-alpha1, <= 3.2.0", "3.2.0", True),
    ("<= 3.3.0-alpha1, <= 3.2.0", "3.3.0-alpha1", True),
    ("<= 3.3.0-alpha1, <= 3.2.0", "3.3.1", False),
    # a real bounded range (AND)
    (">= 1.2, < 2.0", "1.5", True),
    (">= 1.2, < 2.0", "2.0", False),
    (">= 1.2, < 2.0", "1.1", False),
    # ESP-IDF lists the last affected release of each branch
    ("v5.5.4", "5.5.1", True),
    ("v5.5.4", "5.5.4", True),
    ("v5.5.4", "5.5.5", False),
    ("v5.5.4", "5.4.1", False),        # another branch: that branch has its own entry
    ("v6.0; v5.5.4; v5.4.4", "5.4.2", True),
    ("v6.0; v5.5.4; v5.4.4", "6.0.1", False),
    # pre-releases sort before their release
    ("< 3.3.0", "3.3.0-rc1", True),
    ("", "3.3.1", False),
    ("<= 3.3.7", "master", False),     # not a version: never claimed covered or safe by itself
])
def test_ranges_as_advisories_write_them(rng, version, hit):
    assert dw.in_range(rng, version) is hit


def test_latest_ignores_prereleases_and_finds_the_branch():
    tags = ["v6.1", "v6.1-rc2", "v5.5.6", "v5.5.5", "v5.4.4", "v6.2-beta1"]
    assert dw.latest_of(tags) == "v6.1"
    assert dw.latest_of(tags, same_branch_as="5.5.1") == "v5.5.6"
    assert dw.latest_of(tags, same_branch_as="7.0") is None


# --------------------------------------------------------------- the run --

def fake_core(tmp_path, core="3.3.1", idf="5.5.1", mbed="3.6.4", esptool="5.1.0", per_chip=False):
    """per_chip: the 3.3.12 layout (esp32s3-libs/<ver>/) instead of one esp32-arduino-libs tool."""
    data = tmp_path / "Arduino15"
    plat = data / "packages" / "esp32" / "hardware" / "esp32" / core
    plat.mkdir(parents=True)
    lib_tool = {"name": "esp32s3-libs", "version": core} if per_chip else {"name": "esp32-arduino-libs", "version": "libs-x"}
    (plat / "installed.json").write_text(json.dumps({"packages": [
        {"name": "esp32", "platforms": [{"version": core, "toolsDependencies": [
            lib_tool, {"name": "esptool_py", "version": esptool}]}]},
        {"name": "arduino", "platforms": None}]}))
    tools = data / "packages" / "esp32" / "tools"
    libs = tools / "esp32s3-libs" / core if per_chip else tools / "esp32-arduino-libs" / "libs-x" / "esp32s3"
    (libs / "include" / "mbedtls" / "mbedtls").mkdir(parents=True)
    (libs / "sdkconfig").write_text(f'CONFIG_IDF_INIT_VERSION="{idf}"\n')
    (libs / "include" / "mbedtls" / "mbedtls" / "build_info.h").write_text(
        f'#define MBEDTLS_VERSION_STRING         "{mbed}"\n')
    return data


class FakeNet:
    def __init__(self, advisories=None, osv=None, down=()):
        self.adv = advisories or {}
        self.osv_hits = osv or {}
        self.down = set(down)

    def _maybe_down(self, what):
        if what in self.down:
            raise dw.CannotCheck(f"{what}: network down")

    def releases(self, repo):
        self._maybe_down(repo)
        return {"espressif/arduino-esp32": ["3.3.12", "3.3.1"], "espressif/esp-idf": ["v5.5.6", "v6.1"]}.get(repo, ["1.0.0"])

    def advisories(self, repo):
        self._maybe_down(repo)
        return self.adv.get(repo, [])

    def osv(self, eco, packages):
        self._maybe_down("osv")
        return {k: v for k, v in self.osv_hits.items() if k in packages}

    def osv_detail(self, vid):
        return {"id": vid, "summary": "s", "affected": [{"package": {"name": "x"}, "ranges": [{"events": [{"fixed": "9"}]}]}]}

    def crate_latest(self, name):
        return "1.0.0"

    def pypi_latest(self, name):
        return "1.0.0"


def adv(gid, rng, patched="?"):
    return {"id": gid, "severity": "critical", "summary": "x", "published": "2026-01-01",
            "ranges": [rng], "patched": [patched]}


@pytest.fixture
def setup(tmp_path):
    deps = tmp_path / "deps.json"
    deps.write_text(json.dumps({"firmware": {"esp32:esp32": "3.3.1", "ArduinoJson": "7.4.3", "WebSockets": "2.7.2"}}))
    ack = tmp_path / "ack.json"
    ack.write_text("{}")
    lock = tmp_path / "Cargo.lock"
    lock.write_text('[[package]]\nname = "tokio"\nversion = "1.54.0"\nsource = "registry+https://github.com/rust-lang/crates.io-index"\n\n'
                    '[[package]]\nname = "hidfi-core"\nversion = "4.0.4"\n')
    return {"deps_path": deps, "ack_path": ack, "data_dir": fake_core(tmp_path), "lock": lock, "today": "2026-10-28"}


def test_everything_checked_and_nothing_open_is_0(setup):
    r = dw.run(FakeNet(), **setup)
    assert r["exit"] == 0 and r["open"] == 0 and not r["cannot_check"]
    parts = {x["part"]: x for x in r["rows"]}
    assert parts["ESP-IDF"]["pinned"] == "5.5.1" and parts["mbedTLS"]["pinned"] == "3.6.4"
    assert parts["esptool"]["pinned"] == "5.1.0", "derived from the pinned core, not typed in"
    assert parts["ESP-IDF"]["latest_branch"] == "v5.5.6"


def test_an_advisory_on_a_pin_is_open_and_exits_1(setup):
    net = FakeNet(advisories={"espressif/arduino-esp32": [adv("GHSA-8cmm-3887-r32j", "<= 3.3.7")]})
    r = dw.run(net, **setup)
    assert r["exit"] == 1 and r["open"] == 1
    assert r["advisories"][0]["part"] == "esp32:esp32"


def test_an_advisory_for_the_derived_idf_is_found(setup):
    net = FakeNet(advisories={"espressif/esp-idf": [adv("GHSA-g764-gwc3-75m5", "v5.5.4; v6.0.1")]})
    r = dw.run(net, **setup)
    assert r["exit"] == 1 and r["advisories"][0]["part"] == "ESP-IDF"


def test_a_fixed_version_is_not_flagged(setup):
    net = FakeNet(advisories={"espressif/arduino-esp32": [adv("GHSA-x", "<= 3.3.0")]})
    assert dw.run(net, **setup)["exit"] == 0


def test_an_acknowledged_advisory_is_not_open(setup):
    setup["ack_path"].write_text(json.dumps({"GHSA-x": {"reason": "not used"}}))
    net = FakeNet(advisories={"espressif/arduino-esp32": [adv("GHSA-x", "<= 3.3.7")]})
    r = dw.run(net, **setup)
    assert r["exit"] == 0 and r["advisories"][0]["ack"]["reason"] == "not used"


def test_crates_are_checked_through_osv(setup):
    net = FakeNet(osv={("tokio", "1.54.0"): ["RUSTSEC-2026-0384"]})
    r = dw.run(net, **setup)
    assert r["exit"] == 1
    a = r["advisories"][0]
    assert (a["part"], a["pinned"], a["patched"]) == ("tokio", "1.54.0", [])  # detail names package x, not tokio


def test_only_registry_crates_are_queried(setup):
    assert dw.cargo_lock_packages(setup["lock"]) == [("tokio", "1.54.0")]


@pytest.mark.parametrize("down", ["espressif/arduino-esp32", "espressif/esp-idf", "osv"])
def test_an_unreachable_source_is_never_a_pass(setup, down):
    r = dw.run(FakeNet(down=[down]), **setup)
    assert r["exit"] == 2 and r["cannot_check"]


def test_open_wins_over_incomplete(setup):
    net = FakeNet(advisories={"espressif/arduino-esp32": [adv("GHSA-x", "<= 3.3.7")]}, down=["osv"])
    assert dw.run(net, **setup)["exit"] == 1


def test_the_per_chip_library_layout_of_3_3_12_is_read(tmp_path):
    got = dw.derived_from_core("3.3.12", fake_core(tmp_path, core="3.3.12", idf="5.5.5", mbed="3.6.6",
                                                   esptool="5.3.1", per_chip=True))
    assert got == {"ESP-IDF": "5.5.5", "mbedTLS": "3.6.6", "esptool": "5.3.1"}


def test_a_core_that_is_not_installed_is_reported(setup, tmp_path):
    setup["data_dir"] = tmp_path / "nothing"
    r = dw.run(FakeNet(), **setup)
    assert r["exit"] == 2
    assert any("not installed" in c for c in r["cannot_check"])
    assert "ESP-IDF" not in {x["part"] for x in r["rows"]}


# ------------------------------------------------- the acknowledgement file --

# Advisories proven to reach HID-Fi (docs/OTA.md). They are fixed, never
# acknowledged - a reason written for one of these would be a lie.
REACHES_HIDFI = {"GHSA-8cmm-3887-r32j", "GHSA-g764-gwc3-75m5"}


def test_the_real_acknowledgements_are_explained():
    acks = json.loads(dw.ACK.read_text())
    for gid, a in acks.items():
        if gid.startswith("_"):
            continue
        missing = {"reason", "evidence", "date", "revisit"} - set(a)
        assert not missing, f"{gid} lacks {missing}"
        assert len(a["reason"]) > 20, f"{gid}: say why it does not reach HID-Fi"


def test_an_advisory_that_reaches_hidfi_is_never_acknowledged():
    acks = json.loads(dw.ACK.read_text())
    assert not REACHES_HIDFI & set(acks)


def test_the_pairing_crypto_check_uses_the_mbedtls_the_core_ships():
    """companion/vectors/check_board.sh compiles the board's pairing crypto against
    one mbedTLS release; it must be the one the pinned core compiles in, or the
    check proves the bytes on a library the board does not run (it said 3.6.4
    while core 3.3.12 ships 3.6.6)."""
    import re
    deps = json.loads(dw.DEPS.read_text())
    try:
        shipped = dw.derived_from_core(deps["firmware"]["esp32:esp32"], dw.arduino_data_dir())["mbedTLS"]
    except dw.CannotCheck as e:
        pytest.skip(f"the pinned core is not installed here: {e}")
    script = (paths.TESTS.parent / "companion" / "vectors" / "check_board.sh").read_text()
    assert re.search(r"^ver=(\S+)$", script, re.M).group(1) == shipped


def test_renamed_and_path_dependencies_are_read_as_cargo_means_them():
    toml = """
[dependencies]
hex = "0.4.3"
sha2 = "0.10.9"
sha2_011 = { package = "sha2", version = "0.11.1" }
hidfi-core = { path = "../hidfi-core" }

[target.'cfg(windows)'.dependencies]
windows = { version = "0.61", features = ["x"] }

[package]
name = "not-a-dependency"
"""
    assert dw.crates_in_toml(toml) == {"hex", "sha2", "windows"}
