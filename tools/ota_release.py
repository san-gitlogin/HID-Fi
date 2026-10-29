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
                   table_bytes=None, _tests_allow_test_build=False):
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
    if table_bytes is not None:
        # The two-slot partition table, pinned: a Companion moving a board to the
        # two-slot layout by cable writes exactly these bytes, never its own.
        m["table"] = {"name": "partitions.bin", "size": len(table_bytes),
                      "sha256": hashlib.sha256(table_bytes).hexdigest()}
    why = check_manifest(m)
    if why:
        raise ReleaseError(f"the manifest would be refused: {why}")
    if len(canonical(m)) > MANIFEST_CAP:
        raise ReleaseError(f"manifest is {len(canonical(m))} bytes - over {MANIFEST_CAP}; shorten the CHANGELOG sections")
    return m


_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


def check_manifest(m):
    """None when a (signature-checked) manifest is one a Companion and a board will
    act on; else why not. companion/crates/hidfi-core/src/ota.rs read_release gives
    the same answers on tests/vectors/ota_release_v1.json."""
    try:
        if m.get("format") != FORMAT:
            return "unknown manifest format"
        if m.get("product") != "hid-fi" or m.get("chip") != "esp32s3":
            return "not a HID-Fi ESP32-S3 release"
        v, f = m["version"], m["min_from"]
        if not VERSION.match(v) or not VERSION.match(f):
            return "unreadable version"
        if m["firmware"] != f"hid_fi_v{v}" or vtuple(f) >= vtuple(v):
            return "inconsistent versions"
        img = m["image"]
        if not _SHA.match(img["sha256"]) or not isinstance(img["size"], int) or img["size"] <= 0 \
                or not _SAFE_NAME.match(img["name"]) or img["name"] in (".", ".."):
            return "unusable image entry"
        tb = m.get("table")
        if tb is not None and (not _SHA.match(tb["sha256"]) or not isinstance(tb["size"], int)
                               or tb["size"] != 0xC00 or not _SAFE_NAME.match(tb["name"])):
            return "unusable table entry"
        c = m.get("companion")
        if c is not None:
            if not VERSION.match(c["version"]):
                return "unusable companion entry"
            for a in c.get("assets", {}).values():
                if not _SHA.match(a["sha256"]) or not isinstance(a["size"], int) or a["size"] <= 0 \
                        or not _SAFE_NAME.match(a["name"]) or a["name"] in (".", ".."):
                    return "unusable companion entry"
        m["comp_api"]["min"], m["comp_api"]["max"], m["released"], m["support_until"]
    except (KeyError, TypeError, AttributeError):
        return "missing field"
    return None


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


# ---- the release gate ------------------------------------------------------------

def release_gate(manifest, image, release_pub_hexes, test_pub_hex, history, nvs_formats):
    """Everything that must hold before a release is published - each a reason to
    refuse, never a warning. Returns the list of refusals (empty: publish)."""
    out = []
    why = check_manifest(manifest)
    if why:
        out.append(f"manifest: {why}")
    low = image.lower()
    if test_pub_hex.lower().encode() in low or bytes.fromhex(test_pub_hex) in image:
        out.append("the image trusts the public TEST key - it was built with HIDFI_TEST_OTA_KEY")
    if b"-test" in image.split(b"hid_fi_v" + manifest["version"].encode(), 1)[-1][:8]:
        out.append("the image is a test build")
    trusted = [h for h in release_pub_hexes if h.lower().encode() in low or bytes.fromhex(h) in image]
    if not trusted:
        out.append("the image does not contain the release key it is signed with - "
                   "every board on it would refuse every later update")
    fmt = nvs_formats.get("idf", {})
    mine = fmt.get(manifest["idf"])
    base = history.get("releases", {}).get(manifest["min_from"])
    if mine is None:
        out.append(f"the NVS format of IDF {manifest['idf']} has not been read at the source (tools/nvs_format.json)")
    if base is None:
        out.append(f"min_from {manifest['min_from']} is not a published release (tools/release_history.json)")
    elif fmt.get(base["idf"]) is None:
        out.append(f"the NVS format of IDF {base['idf']} (v{manifest['min_from']}) has not been read at the source")
    elif mine is not None and fmt[base["idf"]] != mine:
        out.append(f"IDF {base['idf']} -> {manifest['idf']} changes the NVS format ({fmt[base['idf']]} -> {mine}): "
                   "a rollback would erase every setting - publish this one for the cable only")
    return out


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
    r = sub.add_parser("release", help="manifest + signature + every gate, for tools/make_release.sh")
    for x in ("--version", "--image", "--table", "--min-from", "--core", "--idf", "--key", "--out-dir"):
        r.add_argument(x, required=True)
    r.add_argument("--security", action="store_true"); r.add_argument("--risk", default="")
    r.add_argument("--companion-dir", help="the companion workflow's files (RELEASING.md)")
    r.add_argument("--companion-version")
    r.add_argument("--changelog", default=str(ROOT / "CHANGELOG.md"))
    a = ap.parse_args(argv)
    try:
        if a.cmd == "keygen":
            print(json.dumps(keygen(a.out), indent=2))
        elif a.cmd == "manifest":
            m = build_manifest(a.version, Path(a.image).read_bytes(), a.min_from, a.core, a.idf,
                               Path(a.changelog).read_text(encoding="utf-8"), security=a.security, risk=a.risk)
            Path(a.out).write_bytes(canonical(m))
            print(a.out)
        elif a.cmd == "sign":
            out = Path(a.manifest).with_suffix(".sig")
            out.write_text(json.dumps(sign(Path(a.manifest).read_bytes(), load_private(a.key))), encoding="utf-8")
            print(out)
        elif a.cmd == "release":
            return release_cli(a)
        elif a.cmd == "verify":
            ok = verify(Path(a.manifest).read_bytes(), json.loads(Path(a.sig).read_text(encoding="utf-8")), a.pub)
            print("valid" if ok else "INVALID")
            return 0 if ok else 1
    except ReleaseError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    return 0


# The files the companion workflow builds (RELEASING.md), by the name the
# install scripts and "latest" links expect - one per system the Companion's
# self-update knows (updates.rs os_name). Linux ships two architectures under one
# system name, so it is left out until the Companion asks per architecture: an
# ARM64 computer must never be offered the x86-64 file.
def _companion_names(version):
    return {"macos": rf"HID-Fi-Companion-{re.escape(version)}-macos\.dmg",
            "windows": r"hidfi-companion-windows-x86_64\.exe"}


def companion_assets(folder, version):
    """{os: {name, size, sha256}} for the Companion installers in folder."""
    out = {}
    names = _companion_names(version)
    for p in sorted(Path(folder).iterdir()):
        system = next((k for k, rx in names.items() if re.fullmatch(rx, p.name)), None)
        if system:
            if system in out:
                raise ReleaseError(f"two installers for {system}: {out[system]['name']} and {p.name}")
            b = p.read_bytes()
            out[system] = {"name": p.name, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
    missing = sorted(set(names) - set(out))
    if out and missing:
        raise ReleaseError(f"no Companion installer for {', '.join(missing)} in {folder}")
    if not out:
        raise ReleaseError(f"no Companion installers for {version} in {folder}")
    return out


RELEASE_KEYS_FILE = ROOT / "tools" / "release_keys.json"


def release_cli(a):
    # The key first: a release signed with anything else is refused by every board.
    key = load_private(a.key)
    pub = public_bytes(key).hex()
    published = [k["public_key"] for k in json.loads(Path(RELEASE_KEYS_FILE).read_text(encoding="utf-8"))["keys"]]
    if pub not in published:
        print("refused: the signing key is not one of tools/release_keys.json - boards would refuse the release",
              file=sys.stderr)
        return 2
    image, table = Path(a.image).read_bytes(), Path(a.table).read_bytes()
    comp = None
    if a.companion_dir:
        comp = {"version": a.companion_version or a.version, "assets": companion_assets(a.companion_dir, a.companion_version or a.version)}
    m = build_manifest(a.version, image, a.min_from, a.core, a.idf, Path(a.changelog).read_text(encoding="utf-8"),
                       security=a.security, risk=a.risk, companion=comp, table_bytes=table,
                       image_name=f"hid_fi-{a.version}.bin")

    vec = json.loads((ROOT / "tests" / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
    refusals = release_gate(m, image, [pub], vec["test_public_key"],
                            json.loads((ROOT / "tools" / "release_history.json").read_text(encoding="utf-8")),
                            json.loads((ROOT / "tools" / "nvs_format.json").read_text(encoding="utf-8")))
    if refusals:
        for r in refusals:
            print(f"refused: {r}", file=sys.stderr)
        return 2
    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    mb = canonical(m)
    sig = sign(mb, key)
    assert verify(mb, sig, [pub]), "a signature that does not verify"
    (out / "hidfi-ota.json").write_bytes(mb)
    (out / "hidfi-ota.sig").write_text(json.dumps(sig), encoding="utf-8")
    (out / m["image"]["name"]).write_bytes(image)
    (out / "partitions.bin").write_bytes(table)
    print(json.dumps({"version": a.version, "key_id": sig["key_id"], "image": m["image"], "support_until": m["support_until"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
