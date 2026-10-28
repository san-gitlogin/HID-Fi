"""tools/sbom.py: the release's bills of materials come from the pins, all of them."""
import json
import re
import sys

import pytest

from support import paths

pytestmark = pytest.mark.unit
sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import sbom  # noqa: E402


def test_firmware_lists_every_compiled_pin():
    d = sbom.firmware("4.1.0")
    deps = json.loads((paths.TESTS / "deps.json").read_text())["firmware"]
    got = {c["name"]: c["version"] for c in d["components"]}
    assert d["specVersion"] == "1.7" and d["metadata"]["component"]["version"] == "4.1.0"
    assert got["arduino-esp32"] == deps["esp32:esp32"]
    assert got["ArduinoJson"] == deps["ArduinoJson"] and got["WebSockets"] == deps["WebSockets"]
    assert got["esp-idf"] and got["mbedtls"], "what the core ships, compiled in"
    assert all(c["purl"].startswith("pkg:") for c in d["components"])


def test_companion_lists_every_registry_crate_with_its_checksum():
    lock = (paths.TESTS.parent / "companion" / "Cargo.lock").read_text()
    want = {(n, v) for n, v, s in re.findall(r'name = "([^"]+)"\nversion = "([^"]+)"\nsource = "([^"]+)"', lock)}
    d = sbom.companion("4.1.0")
    got = {(c["name"], c["version"]) for c in d["components"]}
    assert want and want == got
    assert all(c.get("hashes") for c in d["components"]), "every crate pinned by its registry checksum"
    assert {x["ref"] for x in d["dependencies"]} == {"hid-fi-companion"}
