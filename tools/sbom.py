"""CycloneDX 1.7 software bills of materials for a release (EU CRA Annex I,
part II (1): the components a product is made of, machine-readable).

    python tools/sbom.py firmware 4.1.0 > hid_fi-4.1.0.cdx.json
    python tools/sbom.py companion 4.1.0 > companion-4.1.0.cdx.json

Generated from what pins the build - tests/deps.json (the core and libraries
compiled in), the IDF and mbedTLS that core ships (docs/OTA.md, checked at the
source), companion/Cargo.lock (every crate, with its registry checksum) - never
written by hand, so it cannot drift from the product.
"""
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = "1.7"


def _doc(name, version, components, kind):
    return {
        "bomFormat": "CycloneDX", "specVersion": SPEC, "version": 1,
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "metadata": {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "component": {"type": kind, "bom-ref": name, "name": name, "version": version,
                          "supplier": {"name": "HID-Fi"},
                          "licenses": [{"license": {"name": "PolyForm-Noncommercial-1.0.0"}}]},
            "tools": {"components": [{"type": "application", "name": "tools/sbom.py"}]},
        },
        "components": components,
        "dependencies": [{"ref": name, "dependsOn": [c["bom-ref"] for c in components]}],
    }


def firmware(version):
    deps = json.loads((ROOT / "tests" / "deps.json").read_text(encoding="utf-8"))["firmware"]
    core = deps["esp32:esp32"]
    idf = json.loads((ROOT / "tools" / "release_history.json").read_text(encoding="utf-8"))
    mbed = re.search(r'^ver="?([0-9.]+)', (ROOT / "companion" / "vectors" / "check_board.sh").read_text(encoding="utf-8"), re.M).group(1)
    idf_v = idf.get("cores", {}).get(core)
    if idf_v is None:
        raise SystemExit(f"the IDF of core {core} is not on record (tools/release_history.json)")
    comps = [
        ("arduino-esp32", core, f"pkg:github/espressif/arduino-esp32@{core}", "LGPL-2.1-or-later"),
        ("esp-idf", idf_v, f"pkg:github/espressif/esp-idf@v{idf_v}", "Apache-2.0"),
        ("mbedtls", mbed, f"pkg:github/Mbed-TLS/mbedtls@v{mbed}", "Apache-2.0"),
        ("ArduinoJson", deps["ArduinoJson"], f"pkg:github/bblanchon/ArduinoJson@v{deps['ArduinoJson']}", "MIT"),
        ("WebSockets", deps["WebSockets"], f"pkg:github/Links2004/arduinoWebSockets@{deps['WebSockets']}", "LGPL-2.1-or-later"),
    ]
    return _doc("hid-fi-firmware", version, [
        {"type": "library", "bom-ref": p, "name": n, "version": v, "purl": p, "licenses": [{"license": {"id": lic}}]}
        for n, v, p, lic in comps], "firmware")


def companion(version):
    lock = (ROOT / "companion" / "Cargo.lock").read_text(encoding="utf-8")
    comps = []
    for block in lock.split("[[package]]")[1:]:
        f = dict(re.findall(r'^(\w+) = "([^"]*)"', block, re.M))
        if "source" not in f:          # our own crates
            continue
        purl = f"pkg:cargo/{f['name']}@{f['version']}"
        c = {"type": "library", "bom-ref": purl, "name": f["name"], "version": f["version"], "purl": purl}
        if "checksum" in f:
            c["hashes"] = [{"alg": "SHA-256", "content": f["checksum"]}]
        comps.append(c)
    return _doc("hid-fi-companion", version, comps, "application")


def main(argv):
    if len(argv) != 2 or argv[0] not in ("firmware", "companion"):
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps((firmware if argv[0] == "firmware" else companion)(argv[1]), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
