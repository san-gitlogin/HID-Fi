"""The board's manifest check (hid_fi/ota_verify.h), on this computer, against
the mbedTLS release the pinned core compiles in.

Same vectors as tools/ota_release.py and the Companion: the three must agree.
The mbedTLS source is fetched once (checksum checked) into tests/.build - the
version and checksum come from companion/vectors/check_board.sh, which a unit
test already holds to the pinned core.
"""
import base64
import hashlib
import json
import re
import subprocess
import tarfile
import urllib.request

import pytest

from support import firmware, paths

pytestmark = pytest.mark.unit

ROOT = paths.TESTS.parent
VEC = json.loads((paths.TESTS / "vectors" / "ota_v1.json").read_text(encoding="utf-8"))
MBED_SRC = ["ecp", "ecp_curves", "ecp_curves_new", "bignum", "bignum_core", "bignum_mod", "bignum_mod_raw",
            "constant_time", "platform_util", "platform", "sha256", "sha1", "sha512", "sha3", "md5",
            "ripemd160", "md", "ecdsa", "asn1parse", "asn1write", "hmac_drbg"]


def mbedtls():
    script = (ROOT / "companion" / "vectors" / "check_board.sh").read_text(encoding="utf-8")
    ver = re.search(r"^ver=(\S+)$", script, re.M).group(1)
    sha = re.search(r"^sha=([0-9a-f]{64})$", script, re.M).group(1)
    work = paths.CACHE / f"mbedtls-{ver}"
    src = work / f"mbedtls-{ver}"
    if not (src / "library").is_dir():
        work.mkdir(parents=True, exist_ok=True)
        tar = work / "src.tar.bz2"
        url = f"https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-{ver}/mbedtls-{ver}.tar.bz2"
        try:
            urllib.request.urlretrieve(url, tar)
        except OSError as e:
            pytest.skip(f"mbedTLS {ver} source not cached and not downloadable: {e}")
        got = hashlib.sha256(tar.read_bytes()).hexdigest()
        if got != sha:
            tar.unlink()
            raise AssertionError(f"mbedTLS {ver} archive checksum {got} != {sha}")
        with tarfile.open(tar) as t:
            t.extractall(work, filter="data")
    return ver, src


@pytest.fixture(scope="module")
def bench():
    ver, src = mbedtls()
    cc = firmware.host_cxx()
    if not cc:
        pytest.skip("no C++ compiler")
    objs = paths.CACHE / f"mbedtls-{ver}" / "o"
    objs.mkdir(exist_ok=True)
    ccompiler = "clang" if "clang" in cc else "gcc"
    for f in MBED_SRC:
        o = objs / f"{f}.o"
        if not o.exists():
            subprocess.run([ccompiler, "-O1", "-w", f"-I{src / 'include'}", f"-I{src / 'library'}", "-c",
                            str(src / "library" / f"{f}.c"), "-o", str(o)], check=True)
    stub = objs / "psa_stub.o"
    if not stub.exists():
        subprocess.run([ccompiler, "-O1", "-w", "-c", str(ROOT / "companion" / "vectors" / "psa_stub.c"), "-o", str(stub)],
                       check=True)
    bench_cpp = paths.TESTS / "unit" / "firmware" / "ota_verify_bench.cpp"
    h = hashlib.sha256((ROOT / "hid_fi" / "ota_verify.h").read_bytes() + bench_cpp.read_bytes() + ver.encode())
    exe = paths.CACHE / f"ota_verify_bench-{h.hexdigest()[:12]}"      # rebuilt whenever the firmware's check changes
    if exe.exists():
        return exe
    for old in paths.CACHE.glob("ota_verify_bench*"):
        old.unlink(missing_ok=True)
    r = subprocess.run([cc, "-std=c++17", "-O1", f"-I{src / 'include'}", f"-I{ROOT / 'hid_fi'}",
                        str(bench_cpp), *map(str, sorted(objs.glob("*.o"))),
                        "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


def run(exe, keys, cases):
    lines = [f"{c['sig'].get('key_id') or '-'} {c['sig'].get('sig') or '-'} "
             f"{base64.b64decode(c['manifest_b64']).hex() or '-'}" for c in cases]
    r = subprocess.run([str(exe), *keys], input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return [x == "1" for x in r.stdout.split()]


def test_the_board_gives_the_shared_answers(bench):
    got = run(bench, [VEC["test_public_key"]], VEC["cases"])
    want = [c["valid"] for c in VEC["cases"]]
    wrong = [c["name"] for c, g, w in zip(VEC["cases"], got, want) if g != w]
    assert not wrong, f"the firmware disagrees with the vectors on: {wrong}"


def test_the_key_id_chooses_among_trusted_keys(bench):
    assert run(bench, [VEC["untrusted_public_key"], VEC["test_public_key"]], VEC["cases"][:1]) == [True]
    assert run(bench, [VEC["untrusted_public_key"]], VEC["cases"][:1]) == [False]
    assert run(bench, [], VEC["cases"][:1]) == [False]


def test_the_firmware_uses_this_check():
    src = (ROOT / "hid_fi" / "ota_verify.h").read_text(encoding="utf-8")
    assert "mbedtls_ecdsa_verify" in src and "otaKeyId(keys[i].pub" in src, \
        "the key id must be computed from the trusted key, never taken from a table"
