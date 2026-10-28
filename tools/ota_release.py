#!/usr/bin/env python3
"""Signed update manifests: what a board and the Companion trust (docs/OTA.md).

    python tools/ota_release.py keygen  --out ~/.hidfi/ota-signing-key.pem
    python tools/ota_release.py manifest --version 4.1.0 --image hid_fi.bin --min-from 4.0.4 \\
                                         --core 3.3.12 --idf 5.5.5 [--security --risk "..."] --out hidfi-ota.json
    python tools/ota_release.py sign    --manifest hidfi-ota.json --key ~/.hidfi/ota-signing-key.pem
    python tools/ota_release.py verify  --manifest hidfi-ota.json --sig hidfi-ota.sig --pub <hex>

The manifest is canonical JSON (sorted keys, no spaces) and signed byte for byte
with ECDSA P-256 over SHA-256. The signature is r||s, 64 bytes, as hex, with the
key's id (the first 8 bytes of SHA-256 of the public key) so a board that
trusts two keys knows which to use. Every party verifies the SAME bytes: the
firmware (mbedTLS), the Companion (Rust) and this file must agree on
tests/vectors/ota_v1.json.

The private key never enters the repository or GitHub: keygen refuses a path
inside the repository, and sign refuses a key file anyone else can read.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMAT = 1
CHANGELOG_CAP = 16 * 1024          # the board reads the manifest into memory
MANIFEST_CAP = 24 * 1024


class ReleaseError(Exception):
    pass


def _crypto():
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature
        return hashes, serialization, ec, decode_dss_signature, encode_dss_signature
    except ImportError:
        raise ReleaseError("needs the 'cryptography' package (pip install -r tests/requirements.txt)") from None


# ---- keys ---------------------------------------------------------------------

def public_bytes(private_or_public):
    _, serialization, ec, *_ = _crypto()
    pub = private_or_public.public_key() if hasattr(private_or_public, "public_key") else private_or_public
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def key_id(pub65):
    return hashlib.sha256(pub65).hexdigest()[:16]


def _inside_repo(path):
    try:
        Path(path).expanduser().resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def keygen(out):
    _, serialization, ec, *_ = _crypto()
    out = Path(out).expanduser()
    if _inside_repo(out):
        raise ReleaseError(f"refusing to write a signing key inside the repository ({out}) - it is public")
    if out.exists():
        raise ReleaseError(f"{out} exists - a key is never overwritten (boards trust the old one)")
    out.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    pub = public_bytes(key)
    return {"public_key": pub.hex(), "key_id": key_id(pub), "path": str(out)}


def load_private(path):
    _, serialization, *_ = _crypto()
    p = Path(path).expanduser()
    if _inside_repo(p):
        raise ReleaseError("refusing a signing key that lives inside the repository")
    if os.name == "posix" and stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise ReleaseError(f"{p} can be read by others (mode {oct(stat.S_IMODE(p.stat().st_mode))}) - chmod 600 it")
    return serialization.load_pem_private_key(p.read_bytes(), password=None)


def load_public(hex65):
    _, _, ec, *_ = _crypto()
    raw = bytes.fromhex(hex65)
    if len(raw) != 65 or raw[0] != 4:
        raise ReleaseError("a public key is 65 bytes, uncompressed (04 || x || y)")
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)


# ---- the manifest ---------------------------------------------------------------

VERSION = re.compile(r"^\d+\.\d+(\.\d+)?$")


def vtuple(v):
    parts = [int(x) for x in v.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def changelog_since(text, version, min_from):
    """The CHANGELOG sections newer than min_from up to and including version,
    newest first, capped: what a board updating from anything >= min_from may
    show. A board shows only the ones newer than what it runs."""
    sections, cur = [], None
    for line in text.splitlines():
        m = re.match(r"^## v(\d+\.\d+(?:\.\d+)?)\b", line)
        if m:
            cur = {"version": m.group(1), "text": ""}
            sections.append(cur)
        elif line.startswith("## "):
            cur = None
        elif cur is not None:
            cur["text"] += line + "\n"
    out, size = [], 0
    for s in sections:
        if vtuple(min_from) < vtuple(s["version"]) <= vtuple(version):
            s["text"] = s["text"].strip().replace("\n---", "").strip()
            if size + len(s["text"]) > CHANGELOG_CAP:
                s["text"] = s["text"][:max(0, CHANGELOG_CAP - size)].rstrip() + "\n(shortened - the full notes are on the release page)"
            size += len(s["text"])
            out.append(s)
            if size >= CHANGELOG_CAP:
                break
    if not any(s["version"] == version for s in out):
        raise ReleaseError(f"CHANGELOG.md has no '## v{version}' section - write what changed first")
    return out


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def build_manifest(version, image_bytes, min_from, core, idf, changelog_text, *, security=False, risk="",
                   released=None, support_months=12, companion=None, comp_api=(1, 1), image_name=None,
                   _tests_allow_test_build=False):
    if not VERSION.match(version) or not VERSION.match(min_from):
        raise ReleaseError("versions are major.minor[.patch]")
    if vtuple(min_from) >= vtuple(version):
        raise ReleaseError("min_from must be older than the version being released")
    if security and not risk.strip():
        raise ReleaseError("a security update says what risk it removes (ETSI EN 303 645, 5.3-11)")
    if b"hid_fi_v" + version.encode() not in image_bytes:
        raise ReleaseError(f"the image does not carry 'hid_fi_v{version}' - it is not the build of this version")
    if not _tests_allow_test_build and b"-test" in image_bytes.split(b"hid_fi_v" + version.encode(), 1)[1][:8]:
        raise ReleaseError("the image is a test build (version ends in -test) - never released")
    released = released or datetime.date.today()
    until = released.replace(year=released.year + (released.month - 1 + support_months) // 12,
                             month=(released.month - 1 + support_months) % 12 + 1, day=min(released.day, 28))
    m = {
        "format": FORMAT, "product": "hid-fi", "chip": "esp32s3",
        "version": version, "firmware": f"hid_fi_v{version}",
        "released": released.isoformat(), "support_until": until.isoformat(),
        "image": {"name": image_name or f"hid_fi-{version}.bin", "size": len(image_bytes),
                  "sha256": hashlib.sha256(image_bytes).hexdigest()},
        "min_from": min_from, "core": core, "idf": idf,
        "security": bool(security), "risk": risk.strip(),
        "comp_api": {"min": comp_api[0], "max": comp_api[1]},
        "changelog": changelog_since(changelog_text, version, min_from),
    }
    if companion:
        m["companion"] = companion
    if len(canonical(m)) > MANIFEST_CAP:
        raise ReleaseError(f"manifest is {len(canonical(m))} bytes - over {MANIFEST_CAP}; shorten the CHANGELOG sections")
    return m


# ---- signing ----------------------------------------------------------------------

def sign(manifest_bytes, private_key):
    hashes, _, ec, decode, _ = _crypto()
    der = private_key.sign(manifest_bytes, ec.ECDSA(hashes.SHA256()))
    r, s = decode(der)
    pub = public_bytes(private_key)
    return {"key_id": key_id(pub), "sig": (r.to_bytes(32, "big") + s.to_bytes(32, "big")).hex()}


def verify(manifest_bytes, sig, trusted_pub_hexes):
    """True only when the signature is from one of the trusted keys and matches the bytes."""
    hashes, _, ec, _, encode = _crypto()
    from cryptography.exceptions import InvalidSignature
    try:
        raw = bytes.fromhex(sig["sig"])
    except (KeyError, TypeError, ValueError):
        return False
    if len(raw) != 64:
        return False
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    for h in trusted_pub_hexes:
        pub = load_public(h)
        if key_id(public_bytes(pub)) != sig.get("key_id"):
            continue
        try:
            pub.verify(encode(r, s), manifest_bytes, ec.ECDSA(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen"); k.add_argument("--out", required=True)
    m = sub.add_parser("manifest")
    for a in ("--version", "--image", "--min-from", "--core", "--idf", "--out"):
        m.add_argument(a, required=True)
    m.add_argument("--security", action="store_true"); m.add_argument("--risk", default="")
    m.add_argument("--changelog", default=str(ROOT / "CHANGELOG.md"))
    s = sub.add_parser("sign"); s.add_argument("--manifest", required=True); s.add_argument("--key", required=True)
    v = sub.add_parser("verify"); v.add_argument("--manifest", required=True); v.add_argument("--sig", required=True)
    v.add_argument("--pub", required=True, action="append")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "keygen":
            print(json.dumps(keygen(a.out), indent=2))
        elif a.cmd == "manifest":
            m = build_manifest(a.version, Path(a.image).read_bytes(), a.min_from, a.core, a.idf,
                               Path(a.changelog).read_text(), security=a.security, risk=a.risk)
            Path(a.out).write_bytes(canonical(m))
            print(a.out)
        elif a.cmd == "sign":
            out = Path(a.manifest).with_suffix(".sig")
            out.write_text(json.dumps(sign(Path(a.manifest).read_bytes(), load_private(a.key))))
            print(out)
        elif a.cmd == "verify":
            ok = verify(Path(a.manifest).read_bytes(), json.loads(Path(a.sig).read_text()), a.pub)
            print("valid" if ok else "INVALID")
            return 0 if ok else 1
    except ReleaseError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
