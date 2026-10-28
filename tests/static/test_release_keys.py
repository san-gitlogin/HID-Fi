"""The release keys: one list (tools/release_keys.json), embedded the same in the
firmware and the Companion. A key in one and not the other is a board that
refuses a release the Companion accepts, or the reverse."""
import json
import re
import sys

import pytest

from support import paths

pytestmark = pytest.mark.static
ROOT = paths.TESTS.parent
sys.path.insert(0, str(ROOT / "tools"))
import ota_release as R  # noqa: E402

KEYS = json.loads((ROOT / "tools" / "release_keys.json").read_text(encoding="utf-8"))["keys"]


def _arrays(text, start):
    body = text[text.index(start) + len(start):]
    body = body[:min(i for i in (body.find("\n];"), body.find("\n};")) if i >= 0)]
    blocks = [bytes(int(h, 16) for h in re.findall(r"0x([0-9a-f]{2})", b)) for b in re.split(r"//[^\n]*\n", body)]
    return [b.hex() for b in blocks if b]


def test_firmware_embeds_exactly_the_published_keys():
    got = _arrays((ROOT / "hid_fi" / "ota.h").read_text(encoding="utf-8"), "OTA_RELEASE_KEYS[] = {")
    assert got == [k["public_key"] for k in KEYS]


def test_companion_embeds_exactly_the_published_keys():
    got = _arrays((ROOT / "companion" / "crates" / "hidfi-core" / "src" / "ota.rs").read_text(encoding="utf-8"), "RELEASE_KEYS: &[[u8; 65]] = &[")
    assert got == [k["public_key"] for k in KEYS]


def test_each_key_is_a_real_point_with_its_id_and_never_the_test_key():
    test = json.loads((paths.TESTS / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))["test_public_key"]
    assert len(KEYS) >= 2, "a primary and an offline backup"
    for k in KEYS:
        R.load_public(k["public_key"])
        assert R.key_id(bytes.fromhex(k["public_key"])) == k["key_id"]
        assert k["public_key"] != test


def test_no_private_key_in_the_repository():
    """Every tracked file; the test vectors' keys are TEST keys, public on purpose."""
    import subprocess
    marker = b"PRIVATE " + b"KEY-----"
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.split(b"\0")
    hits = [f.decode() for f in files if f and not f.startswith(b"tests/vectors/")
            and (ROOT / f.decode()).is_file() and marker in (ROOT / f.decode()).read_bytes()]
    assert hits == [], hits
