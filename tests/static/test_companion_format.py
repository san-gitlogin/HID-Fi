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


def test_companion_passes_clippy_as_ci_runs_it():
    """`cargo clippy --workspace --all-targets -- -D warnings`, as companion.yml runs it.
    Seen 2026-11-25: two lints in new code passed every local test and failed the
    installer build on three platforms."""
    cargo = shutil.which("cargo")
    if not cargo:
        pytest.skip("cargo not installed")
    r = subprocess.run([cargo, "clippy", "--workspace", "--all-targets", "--", "-D", "warnings"],
                       cwd=paths.REPO / "companion", capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 and "clippy" in r.stderr and "not installed" in r.stderr:
        pytest.skip("clippy not installed")
    errors = [ln for ln in r.stderr.splitlines() if ln.startswith("error")][:10]
    assert r.returncode == 0, f"clippy (as CI runs it) fails in companion/: {errors}"
