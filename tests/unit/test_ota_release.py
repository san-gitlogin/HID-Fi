"""tools/ota_release.py - the signed manifest every board and Companion trusts.

The vectors (tests/vectors/ota_v1.json) are shared: the firmware and the
Companion are held to the same answers. Everything else here is a refusal the
release must make, because a wrong manifest reaches every board.
"""
import base64
import datetime
import json
import os
import subprocess
import sys

import pytest

from support import paths

sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import ota_release as R  # noqa: E402

pytestmark = pytest.mark.unit

VEC = json.loads((paths.TESTS / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
CHANGELOG = """# Changelog

## Unreleased

Not yet.

## v4.1.0 - 2026-11-01

Updates over the air.

---

## v4.0.5 - 2026-10-30

Security: the web server crash.

## v4.0.4 - 2026-10-27

The Companion shows its version.

## v4.0.3 - 2026-10-25

Text shortcuts.
"""
IMAGE = b"\xe9" + b"\0" * 40 + b"hid_fi_v4.1.0\0" + b"x" * 1000


@pytest.mark.parametrize("case", VEC["cases"], ids=[c["name"] for c in VEC["cases"]])
def test_the_shared_vectors(case):
    got = R.verify(base64.b64decode(case["manifest_b64"]), case["sig"], [VEC["test_public_key"]])
    assert got is case["valid"]


def test_two_trusted_keys_the_id_picks_the_key():
    case = VEC["cases"][0]
    m = base64.b64decode(case["manifest_b64"])
    assert R.verify(m, case["sig"], [VEC["untrusted_public_key"], VEC["test_public_key"]])
    assert not R.verify(m, case["sig"], [VEC["untrusted_public_key"]])


def manifest(**kw):
    args = dict(version="4.1.0", image_bytes=IMAGE, min_from="4.0.4", core="3.3.12", idf="5.5.5",
                changelog_text=CHANGELOG, released=datetime.date(2026, 11, 1))
    args.update(kw)
    return R.build_manifest(**args)


def test_a_manifest_says_what_a_board_needs():
    m = manifest()
    assert m["version"] == "4.1.0" and m["firmware"] == "hid_fi_v4.1.0" and m["chip"] == "esp32s3"
    assert m["image"]["size"] == len(IMAGE) and len(m["image"]["sha256"]) == 64
    assert m["support_until"] == "2027-11-01", "the rolling support statement: release + 12 months"
    assert [s["version"] for s in m["changelog"]] == ["4.1.0", "4.0.5"], \
        "every section newer than min_from, newest first - not only the newest"
    assert "Updates over the air." in m["changelog"][0]["text"] and "---" not in m["changelog"][0]["text"]


def test_canonical_bytes_are_stable():
    assert R.canonical(manifest()) == R.canonical(json.loads(R.canonical(manifest())))


@pytest.mark.parametrize("kw, why", [
    ({"version": "4.1"}, "no '## v4.1'"),
    ({"version": "latest"}, "versions are"),
    ({"min_from": "4.1.0"}, "must be older"),
    ({"min_from": "4.2.0"}, "must be older"),
    ({"security": True}, "what risk"),
    ({"image_bytes": b"no version here"}, "does not carry"),
    ({"image_bytes": b"hid_fi_v4.1.0-test\0"}, "test build"),
    ({"changelog_text": "## v4.0.4\n"}, "no '## v4.1.0'"),
])
def test_what_a_release_refuses(kw, why):
    with pytest.raises(R.ReleaseError, match=why):
        manifest(**kw)


def test_the_changelog_is_capped_and_says_so():
    long = CHANGELOG.replace("Updates over the air.", "x" * (R.CHANGELOG_CAP + 500))
    m = manifest(changelog_text=long)
    assert m["changelog"][0]["text"].endswith("(shortened - the full notes are on the release page)")
    assert len(R.canonical(m)) <= R.MANIFEST_CAP


def test_sign_and_verify_round_trip(tmp_path):
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    m = R.canonical(manifest())
    sig = R.sign(m, key)
    pub = R.public_bytes(key).hex()
    assert R.verify(m, sig, [pub])
    assert not R.verify(m + b" ", sig, [pub])


def test_keygen_never_writes_inside_the_repository():
    with pytest.raises(R.ReleaseError, match="inside the repository"):
        R.keygen(paths.TESTS / ".cache" / "key.pem")


def test_keygen_never_overwrites_and_keeps_the_key_private(tmp_path):
    out = tmp_path / "k.pem"
    info = R.keygen(out)
    assert len(bytes.fromhex(info["public_key"])) == 65 and len(info["key_id"]) == 16
    if os.name == "posix":
        assert oct(out.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(R.ReleaseError, match="never overwritten"):
        R.keygen(out)


def test_signing_refuses_a_key_others_can_read(tmp_path):
    out = tmp_path / "k.pem"
    R.keygen(out)
    if os.name != "posix":
        pytest.skip("file modes are a POSIX check")
    out.chmod(0o644)
    with pytest.raises(R.ReleaseError, match="read by others"):
        R.load_private(out)


def test_the_command_line_end_to_end(tmp_path):
    tool = [sys.executable, str(paths.TESTS.parent / "tools" / "ota_release.py")]
    key = tmp_path / "keys" / "k.pem"
    info = json.loads(subprocess.run(tool + ["keygen", "--out", str(key)], capture_output=True, text=True, check=True).stdout)
    img = tmp_path / "hid_fi.bin"; img.write_bytes(IMAGE)
    cl = tmp_path / "CHANGELOG.md"; cl.write_text(CHANGELOG, encoding="utf-8")
    man = tmp_path / "hidfi-ota.json"
    subprocess.run(tool + ["manifest", "--version", "4.1.0", "--image", str(img), "--min-from", "4.0.4",
                           "--core", "3.3.12", "--idf", "5.5.5", "--changelog", str(cl), "--out", str(man)], check=True,
                   capture_output=True)
    subprocess.run(tool + ["sign", "--manifest", str(man), "--key", str(key)], check=True, capture_output=True)
    ok = subprocess.run(tool + ["verify", "--manifest", str(man), "--sig", str(man.with_suffix(".sig")),
                                "--pub", info["public_key"]], capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.strip() == "valid"
    man.write_bytes(man.read_bytes().replace(b"4.0.4", b"4.0.3"))
    bad = subprocess.run(tool + ["verify", "--manifest", str(man), "--sig", str(man.with_suffix(".sig")),
                                 "--pub", info["public_key"]], capture_output=True, text=True)
    assert bad.returncode == 1 and bad.stdout.strip() == "INVALID"
    refused = subprocess.run(tool + ["manifest", "--version", "4.1.0", "--image", str(img), "--min-from", "4.0.4",
                                     "--core", "3.3.12", "--idf", "5.5.5", "--security", "--changelog", str(cl),
                                     "--out", str(man)], capture_output=True, text=True)
    assert refused.returncode == 2 and "risk" in refused.stderr


def test_release_checks_agree_with_the_companion_vectors():
    """tests/vectors/ota_release_v1.json: the Companion (hidfi-core read_release)
    is held to the same file - a release the tool accepts, the Companion accepts."""
    import base64
    rv = json.loads((paths.TESTS / "vectors" / "ota_release_v1.json").read_text(encoding="utf-8"))
    v1 = json.loads((paths.TESTS / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
    for c in rv["cases"]:
        mb = base64.b64decode(c["manifest_b64"])
        assert R.verify(mb, c["sig"], [v1["test_public_key"]]), c["name"]
        assert R.check_manifest(json.loads(mb)) == c["refused"], c["name"]


def test_build_manifest_refuses_what_a_companion_would_refuse():
    import base64
    v1 = json.loads((paths.TESTS / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
    image = base64.b64decode(v1["image_b64"])
    bad = {"version": "4.1.0", "assets": {"macos": {"name": "../x.dmg", "size": 1, "sha256": "0" * 64}}}
    with pytest.raises(R.ReleaseError, match="unusable companion entry"):
        R.build_manifest("4.1.0", image, "4.0.4", "3.3.12", "5.5.5", "## v4.1.0 - 2026-11-01\n\nx\n", companion=bad)


# ---- the release gate (tools/ota_release.py release) ---------------------------------------
def _gate_inputs(tmp_path, image_extra=b"", idf="5.5.5"):
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    pub = R.public_bytes(key)
    image = b"\xe9" + b"\0" * 64 + b"hid_fi_v4.1.0\0" + pub + image_extra + bytes(range(256)) * 8
    table = bytes([0xAA, 0x50]) + bytes(30) + b"\xff" * (0xC00 - 32)
    m = R.build_manifest("4.1.0", image, "4.0.4", "3.3.12", idf, "## v4.1.0 - 2026-11-01\n\nx\n", table_bytes=table)
    hist = {"releases": {"4.0.4": {"core": "3.3.12", "idf": "5.5.5"}}}
    fmts = {"idf": {"5.5.5": "0xfe", "5.5.6": "0xfe", "9.9.9": "0xfd"}}
    return key, pub, image, table, m, hist, fmts


def test_the_gate_passes_a_proper_release(tmp_path):
    _, pub, image, _, m, hist, fmts = _gate_inputs(tmp_path)
    assert R.release_gate(m, image, [pub.hex()], VEC["test_public_key"], hist, fmts) == []


def test_the_gate_refuses_an_image_that_trusts_the_test_key(tmp_path):
    _, pub, image, _, m, hist, fmts = _gate_inputs(tmp_path, image_extra=VEC["test_public_key"].encode())
    got = R.release_gate(m, image, [pub.hex()], VEC["test_public_key"], hist, fmts)
    assert any("TEST key" in r for r in got), got


def test_the_gate_refuses_an_image_without_the_signing_key(tmp_path):
    _, _, image, _, m, hist, fmts = _gate_inputs(tmp_path)
    other = R.public_bytes(__import__("cryptography.hazmat.primitives.asymmetric.ec", fromlist=["ec"])
                           .generate_private_key(__import__("cryptography.hazmat.primitives.asymmetric.ec",
                                                            fromlist=["ec"]).SECP256R1()))
    got = R.release_gate(m, image, [other.hex()], VEC["test_public_key"], hist, fmts)
    assert any("would refuse every later update" in r for r in got), got


@pytest.mark.parametrize("idf, history_idf, says", [
    ("9.9.9", "5.5.5", "changes the NVS format"),
    ("5.6.0", "5.5.5", "has not been read at the source"),
    ("5.5.6", "5.4.0", "has not been read at the source"),
])
def test_the_gate_refuses_an_nvs_format_it_cannot_vouch_for(tmp_path, idf, history_idf, says):
    _, pub, image, _, m, hist, fmts = _gate_inputs(tmp_path, idf=idf)
    hist["releases"]["4.0.4"]["idf"] = history_idf
    got = R.release_gate(m, image, [pub.hex()], VEC["test_public_key"], hist, fmts)
    assert any(says in r for r in got), got


def test_the_gate_refuses_min_from_that_was_never_published(tmp_path):
    _, pub, image, _, m, hist, fmts = _gate_inputs(tmp_path)
    hist["releases"] = {}
    got = R.release_gate(m, image, [pub.hex()], VEC["test_public_key"], hist, fmts)
    assert any("not a published release" in r for r in got), got


def test_the_committed_gate_tables_are_sound():
    hist = json.loads((paths.TESTS.parent / "tools" / "release_history.json").read_text(encoding="utf-8"))
    fmts = json.loads((paths.TESTS.parent / "tools" / "nvs_format.json").read_text(encoding="utf-8"))
    for v, r in hist["releases"].items():
        assert R.VERSION.match(v) and r["idf"] in fmts["idf"], f"{v}: IDF {r['idf']} has no NVS format on record"
        assert hist["cores"][r["core"]] == r["idf"], f"{v}: its core and IDF disagree with the cores table"
    pinned = json.loads((paths.TESTS / "deps.json").read_text(encoding="utf-8"))["firmware"]["esp32:esp32"]
    assert pinned in hist["cores"], f"the pinned core {pinned} has no IDF on record - the release script needs it"
    assert hist["cores"][pinned] in fmts["idf"], "the pinned core's IDF has no NVS format on record"
    assert all(f.startswith("0x") for f in fmts["idf"].values())


def test_release_writes_signed_files_that_verify(tmp_path, monkeypatch):
    key, pub, image, table, *_ = _gate_inputs(tmp_path)
    from cryptography.hazmat.primitives import serialization
    kp = tmp_path / "release.pem"
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    kp.chmod(0o600)                       # the tool refuses a key others can read
    (tmp_path / "img.bin").write_bytes(image)
    (tmp_path / "p.bin").write_bytes(table)
    (tmp_path / "CHANGELOG.md").write_text("## v4.1.0 - 2026-11-01\n\nx\n", encoding="utf-8")
    out = tmp_path / "out"
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"keys": [{"name": "t", "key_id": R.key_id(pub), "public_key": pub.hex()}]}), encoding="utf-8")
    monkeypatch.setattr(R, "RELEASE_KEYS_FILE", keys)
    rc = R.main(["release", "--version", "4.1.0", "--image", str(tmp_path / "img.bin"), "--table", str(tmp_path / "p.bin"),
                 "--min-from", "4.0.4", "--core", "3.3.12", "--idf", "5.5.5", "--key", str(kp),
                 "--out-dir", str(out), "--changelog", str(tmp_path / "CHANGELOG.md")])
    assert rc == 0
    mb = (out / "hidfi-ota.json").read_bytes()
    assert R.verify(mb, json.loads((out / "hidfi-ota.sig").read_text(encoding="utf-8")), [pub.hex()])
    m = json.loads(mb)
    assert m["table"]["size"] == 0xC00 and (out / m["image"]["name"]).read_bytes() == image


def test_release_refuses_a_key_that_is_not_published(tmp_path, capsys):
    key, pub, image, table, *_ = _gate_inputs(tmp_path)
    from cryptography.hazmat.primitives import serialization
    kp = tmp_path / "k.pem"
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    kp.chmod(0o600)
    (tmp_path / "img.bin").write_bytes(image)
    (tmp_path / "p.bin").write_bytes(table)
    rc = R.main(["release", "--version", "4.1.0", "--image", str(tmp_path / "img.bin"), "--table", str(tmp_path / "p.bin"),
                 "--min-from", "4.0.4", "--core", "3.3.12", "--idf", "5.5.5", "--key", str(kp), "--out-dir", str(tmp_path / "o")])
    assert rc == 2 and "release_keys.json" in capsys.readouterr().err
    assert not (tmp_path / "o").exists(), "nothing written"
