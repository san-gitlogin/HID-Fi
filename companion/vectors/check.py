"""Independent check of pairing_v1.json with Python's `cryptography` package.

The Rust crate wrote the vectors; this recomputes every value from the secrets
with a different implementation, so a bug in one cannot hide in both. The board
firmware is held to the same file.
"""
import hashlib, hmac, json, pathlib, sys
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes, serialization

v = json.loads((pathlib.Path(__file__).parent / "pairing_v1.json").read_text())
h = lambda *p: hashlib.sha256(b"".join(p)).digest()
x = bytes.fromhex
raw = lambda k: k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)

a = X25519PrivateKey.from_private_bytes(x(v["a_secret"]))
b = X25519PrivateKey.from_private_bytes(x(v["b_secret"]))
A, B, nA, nB = raw(a), raw(b), x(v["n_a"]), x(v["n_b"])
checks = {
    "a_pub": A.hex() == v["a_pub"],
    "b_pub": B.hex() == v["b_pub"],
    "commit": h(b"hidfi-commit", A, nA).hex() == v["commit"],
    "code": "%06d" % (int.from_bytes(h(b"hidfi-sas", A, B, nA, nB)[:4], "big") % 1_000_000) == v["code"],
}
shared = a.exchange(b.public_key())
assert shared == b.exchange(a.public_key())
key = HKDF(hashes.SHA256(), 32, h(A, B, nA, nB), b"hidfi-companion-v1").derive(shared)
checks["key"] = key.hex() == v["key"]
bt = v["beat"]
msg = f'beat|{bt["id"]}|{bt["port"]}|{bt["fp"]}|{bt["tn"]}|{bt["chal"]}'
checks["beat.msg"] = msg == bt["msg"]
checks["beat.mac"] = hmac.new(key, msg.encode(), "sha256").hexdigest() == bt["mac"]
t = v["ticket"]
checks["ticket"] = hmac.new(key, f'tkt|{t["tn"]}|{t["vn"]}'.encode(), "sha256").hexdigest() == t["mac"]
for k, ok in checks.items():
    print(("ok  " if ok else "FAIL"), k)
sys.exit(0 if all(checks.values()) else 1)
