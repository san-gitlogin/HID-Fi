"""The Companion's Rust is formatted the way CI checks it (`cargo fmt --check`
in .github/workflows/companion.yml). Seen: unformatted code reached CI and failed
every platform's build there - this catches it locally first."""
import shutil
import subprocess

import pytest

from support import paths

pytestmark = pytest.mark.static


def test_companion_rust_is_formatted():
    cargo = shutil.which("cargo")
    if not cargo:
        pytest.skip("cargo not installed")
    r = subprocess.run([cargo, "fmt", "--all", "--check"], cwd=paths.REPO / "companion", capture_output=True, text=True, timeout=300)
    if r.returncode != 0 and "rustfmt" in r.stderr and "not installed" in r.stderr:
        pytest.skip("rustfmt not installed")
    files = sorted({ln.split(" at line")[0].replace("Diff in ", "") for ln in r.stdout.splitlines() if ln.startswith("Diff in ")})
    assert r.returncode == 0, f"run `cargo fmt --all` in companion/: {files[:10]}"
