"""Writes tests/vectors/ota_v1.json - run once; the output is committed.

The keys here are TEST keys, deliberately public. No release may trust them:
tools/make_release.sh refuses an image that contains the test public key.
"""
import base64, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import ota_release as R
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

def pem(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption()).decode()

a, b = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
pa, pb = R.public_bytes(a), R.public_bytes(b)
changelog = "## v4.1.0 - 2026-11-01\n\nUpdates over the air.\n\n## v4.0.4 - 2026-10-27\n\nOlder.\n"
image = b"\xe9" + b"\0" * 64 + b"hid_fi_v4.1.0\0" + bytes(range(256)) * 8
import datetime
m = R.canonical(R.build_manifest("4.1.0", image, "4.0.4", "3.3.12", "5.5.5", changelog,
                                 released=datetime.date(2026, 11, 1)))
good = R.sign(m, a)
flip = bytearray(bytes.fromhex(good["sig"])); flip[5] ^= 0x01
other = R.sign(m, b)
tampered = m.replace(b'"size":', b'"size":1', 1)
n = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
r, s = int(good["sig"][:64], 16), int(good["sig"][64:], 16)
cases = [
    ("valid", m, good, True),
    ("manifest changed after signing", tampered, good, False),
    ("one bit of the signature flipped", m, {"key_id": good["key_id"], "sig": flip.hex()}, False),
    ("signed by an untrusted key", m, other, False),
    ("untrusted signature carrying the trusted key's id", m, {"key_id": good["key_id"], "sig": other["sig"]}, False),
    ("r = 0", m, {"key_id": good["key_id"], "sig": "00" * 32 + good["sig"][64:]}, False),
    ("s = 0", m, {"key_id": good["key_id"], "sig": good["sig"][:64] + "00" * 32}, False),
    ("s = n (out of range)", m, {"key_id": good["key_id"], "sig": good["sig"][:64] + n.to_bytes(32, "big").hex()}, False),
    ("signature 63 bytes", m, {"key_id": good["key_id"], "sig": good["sig"][:126]}, False),
    ("signature empty", m, {"key_id": good["key_id"], "sig": ""}, False),
    ("signature not hex", m, {"key_id": good["key_id"], "sig": "zz" * 64}, False),
    ("manifest empty", b"", good, False),
]
out = {
    "_about": "ECDSA P-256 / SHA-256 over the manifest bytes; sig = r||s hex. TEST KEYS, deliberately public - no release may trust them (tools/make_release.sh refuses an image containing test_public_key). Every verifier (tools/ota_release.py, the firmware, the Companion) must give these exact answers.",
    "test_public_key": pa.hex(), "test_key_id": R.key_id(pa),
    "test_private_key_pem": pem(a),
    "untrusted_public_key": pb.hex(),
    "image_b64": base64.b64encode(image).decode(),
    "cases": [{"name": nm, "manifest_b64": base64.b64encode(mb).decode(), "sig": sg, "valid": ok}
              for nm, mb, sg, ok in cases],
}
for c in out["cases"]:
    got = R.verify(base64.b64decode(c["manifest_b64"]), c["sig"], [out["test_public_key"]])
    assert got == c["valid"], c["name"]
Path(__file__).with_name("ota_v1.json").write_text(json.dumps(out, indent=1) + "\n")
print("wrote", len(out["cases"]), "cases")
