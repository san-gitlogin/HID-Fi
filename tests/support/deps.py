"""What is installed versus what tests/deps.json says HID-Fi is verified against."""
import importlib.metadata as md
import json
import re
import shutil
import subprocess
import sys

from support.paths import DEPS


def _ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3]) or (0,)


def manifest():
    return json.loads(DEPS.read_text())


def _run(*cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def installed():
    got = {}
    got["python"] = ".".join(map(str, sys.version_info[:3]))
    for pkg in ("pytest", "pytest-bdd", "pytest-xdist", "pyserial", "playwright", "cryptography"):
        try:
            got[pkg] = md.version(pkg)
        except md.PackageNotFoundError:
            got[pkg] = None
    node = shutil.which("node") and _run("node", "--version")
    got["node"] = node.strip().lstrip("v") if node else None
    cli = shutil.which("arduino-cli") and _run("arduino-cli", "version", "--format", "json")
    got["arduino-cli"] = json.loads(cli).get("VersionString") if cli else None
    if cli:
        cores = json.loads(_run("arduino-cli", "core", "list", "--format", "json") or "{}")
        for p in cores.get("platforms", []):
            got[p.get("id")] = p.get("installed_version")
        libs = json.loads(_run("arduino-cli", "lib", "list", "--format", "json") or "{}")
        for l in libs.get("installed_libraries", []):
            got[l["library"]["name"]] = l["library"]["version"]
    return got


def check():
    """Rows of (name, wanted, installed, verdict). verdict: ok | differs | old | missing."""
    m, got = manifest(), installed()
    rows = []
    for name, want in m["firmware"].items():
        have = got.get(name)
        rows.append((name, f"=={want}", have, "missing" if not have else "ok" if have == want else "differs"))
    for name, want in m["tools"].items():
        have = got.get(name)
        rows.append((name, f">={want}", have,
                     "missing" if not have else "ok" if _ver_tuple(have) >= _ver_tuple(want) else "old"))
    return rows
