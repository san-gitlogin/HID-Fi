"""Every socket the Companion clones is kept out of child processes.

On Windows, Rust's try_clone() hands back an INHERITABLE socket (measured on
Windows 11: an accepted connection's clone and a UDP socket's clone; the
originals are not). Any program the app starts - the browser, clip, the new
version during "Install and restart" - got a copy: it held the connection open
after the app closed it, and reset it when killed. hidfi_stream::
keep_to_this_process clears that; this rule makes sure no try_clone() in the
Companion's code goes without it."""
import re

import pytest

from support import paths

pytestmark = pytest.mark.static


def test_every_try_clone_is_kept_to_this_process():
    bad = []
    for rs in (paths.REPO / "companion" / "crates").rglob("*.rs"):
        if "target" in rs.parts or rs.name == "tests.rs" or "/tests/" in rs.as_posix():
            continue
        lines = rs.read_text(encoding="utf-8").splitlines()
        code = "\n".join(lines)
        m = re.search(r"#\[cfg\((?:all\()?test\b", code)   # test modules (cfg(test), cfg(all(test, ..)))
        cut = m.start() if m else -1
        for i, line in enumerate(lines):
            if cut >= 0 and sum(len(l) + 1 for l in lines[:i]) >= cut:
                break
            if "try_clone()" in line and "// not a socket" not in line:
                near = "\n".join(lines[i:i + 4])
                if "keep_to_this_process" not in near:
                    bad.append(f"{rs.relative_to(paths.REPO)}:{i + 1}: {line.strip()}")
    assert not bad, "a cloned socket goes to child processes on Windows:\n  " + "\n  ".join(bad)
