"""Writes tests/vectors/ota_release_v1.json - run once; the output is committed.

Signed manifests - every one with the TEST key of ota_v1.json, so the signature
is always good - that a Companion must accept or refuse for what they say:
tools/ota_release.py check_manifest and hidfi-core read_release give the same
answer on each.
"""
import base64, copy, datetime, hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import ota_release as R
from cryptography.hazmat.primitives import serialization

V = json.loads((Path(__file__).with_name("ota_v1.json")).read_text(encoding="utf-8"))
key = serialization.load_pem_private_key(V["test_private_key_pem"].encode(), password=None)
image = base64.b64decode(V["image_b64"])
inst = b"companion installer bytes"
comp = {"version": "4.1.0", "assets": {
    "macos": {"name": "HID-Fi-Companion-4.1.0-macos.dmg", "size": len(inst), "sha256": hashlib.sha256(inst).hexdigest()}}}
changelog = "## v4.1.0 - 2026-11-01\n\nUpdates over the air.\n\n## v4.0.4 - 2026-10-27\n\nOlder.\n"
table = bytes([0xAA, 0x50]) + bytes(30) + b"\xff" * (0xC00 - 32)
base = R.build_manifest("4.1.0", image, "4.0.4", "3.3.12", "5.5.5", changelog,
                        released=datetime.date(2026, 11, 1), companion=comp, table_bytes=table)


def mut(f):
    m = copy.deepcopy(base)
    f(m)
    return m


cases = [
    ("full, with a companion", base, None),
    ("no companion part", mut(lambda m: m.pop("companion")), None),
    ("another product", mut(lambda m: m.update(product="not-hid-fi")), "not a HID-Fi ESP32-S3 release"),
    ("another chip", mut(lambda m: m.update(chip="esp32")), "not a HID-Fi ESP32-S3 release"),
    ("a format from the future", mut(lambda m: m.update(format=2)), "unknown manifest format"),
    ("firmware string is not the version", mut(lambda m: m.update(firmware="hid_fi_v9.9.9")), "inconsistent versions"),
    ("min_from not older", mut(lambda m: m.update(min_from="4.1.0")), "inconsistent versions"),
    ("version with a v", mut(lambda m: m.update(version="v4.1.0")), "unreadable version"),
    ("image name is a path", mut(lambda m: m["image"].update(name="../../hid_fi.bin")), "unusable image entry"),
    ("image name is a URL", mut(lambda m: m["image"].update(name="https://elsewhere/x.bin")), "unusable image entry"),
    ("image hash upper case", mut(lambda m: m["image"].update(sha256=m["image"]["sha256"].upper())), "unusable image entry"),
    ("image size zero", mut(lambda m: m["image"].update(size=0)), "unusable image entry"),
    ("companion installer name is a path",
     mut(lambda m: m["companion"]["assets"]["macos"].update(name="../Applications/x.dmg")), "unusable companion entry"),
    ("companion version unreadable", mut(lambda m: m["companion"].update(version="latest")), "unusable companion entry"),
    ("no image", mut(lambda m: m.pop("image")), "missing field"),
    ("no table part", mut(lambda m: m.pop("table")), None),
    ("a table that is not 0xC00 bytes", mut(lambda m: m["table"].update(size=4096)), "unusable table entry"),
    ("table name is a path", mut(lambda m: m["table"].update(name="../p.bin")), "unusable table entry"),
]
out = {
    "_about": "Signed with the TEST key of ota_v1.json (the signature is always good); "
              "'refused' is null when the release must be accepted. TEST KEYS - no release may trust them.",
    "installer_b64": base64.b64encode(inst).decode(),
    "table_b64": base64.b64encode(table).decode(),
    "cases": [],
}
for name, m, why in cases:
    mb = R.canonical(m)
    assert R.check_manifest(json.loads(mb)) == why, (name, R.check_manifest(json.loads(mb)))
    out["cases"].append({"name": name, "manifest_b64": base64.b64encode(mb).decode(),
                         "sig": R.sign(mb, key), "refused": why})
Path(__file__).with_name("ota_release_v1.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print("wrote", len(out["cases"]), "cases")
