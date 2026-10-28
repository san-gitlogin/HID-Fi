#!/usr/bin/env python3
"""Pinned versus latest, and every published advisory against what HID-Fi pins.

    python tools/dep_watch.py           # the report
    python tools/dep_watch.py --json    # the same, for a machine

Exit codes: 0 everything was checked and no advisory reaches a pinned version
without an acknowledgement; 1 an advisory covers a pinned version and is not
acknowledged in tools/dep_watch_ack.json; 2 something could not be checked
(network, a core that is not installed) - never read 2 as a pass.

Why it exists (AGENTS.md, "Outside facts and dependencies"): every release up
to v4.0.4 shipped core 3.3.1, which has a published, remotely reachable
WebServer crash (GHSA-8cmm-3887-r32j). Nobody had looked.

Sources, all primary: GitHub release feeds and repository security advisories
for the firmware chain (the core, ESP-IDF, esptool, mbedTLS, ArduinoJson,
WebSockets); OSV.dev for the Companion's crates (Cargo.lock, every package) and
the Python test tools; crates.io and PyPI for their latest versions. ESP-IDF,
mbedTLS and esptool are not typed in anywhere: they are read from the installed
copy of the pinned core, so the answer is about what is actually compiled in.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEPS = ROOT / "tests" / "deps.json"
ACK = Path(__file__).resolve().parent / "dep_watch_ack.json"
CARGO_LOCK = ROOT / "companion" / "Cargo.lock"
PY_TOOLS = ["pytest", "pytest-bdd", "pytest-xdist", "pyserial", "playwright"]

# name in deps.json -> GitHub repository
FIRMWARE_REPOS = {
    "esp32:esp32": "espressif/arduino-esp32",
    "ArduinoJson": "bblanchon/ArduinoJson",
    "WebSockets": "Links2004/arduinoWebSockets",
}
DERIVED_REPOS = {
    "ESP-IDF": "espressif/esp-idf",
    "mbedTLS": "Mbed-TLS/mbedtls",
    "esptool": "espressif/esptool",
}


class CannotCheck(Exception):
    """A source could not be read. Reported, and the run exits 2."""


# ---------------------------------------------------------------- versions --

def parse_version(text):
    """'v5.5.1' / '3.3.12' / 'mbedtls-3.6.4' / '4.0.0-alpha1' -> comparable tuple.

    A pre-release sorts before its release. Returns None for anything that is
    not a version (a branch name, a commit)."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-.]?((?:alpha|beta|rc|dev|pre)[.\-]?\d*))?",
                  str(text), re.I)
    if not m:
        return None
    nums = tuple(int(x) if x is not None else 0 for x in m.group(1, 2, 3, 4))
    pre = m.group(5)
    if pre:
        kind = re.match(r"[a-z]+", pre.lower()).group(0)
        rank = {"dev": 0, "pre": 1, "alpha": 1, "beta": 2, "rc": 3}.get(kind, 0)
        n = int(re.sub(r"\D", "", pre) or 0)
        return nums + (0, rank, n)
    return nums + (1, 0, 0)


def _clause(clause, version):
    """One clause of an advisory's range against a parsed version.

    '<= 3.3.7', '>=1.2', '= 2.0' compare. A bare version - ESP-IDF lists the
    last affected release of each branch that way ('v5.5.4') - means 'that
    branch, up to and including it'."""
    clause = clause.strip()
    m = re.match(r"(<=|>=|<|>|==|=)?\s*(.+)", clause)
    op, ver = m.group(1), parse_version(m.group(2))
    if ver is None:
        return False
    if op is None:
        return version[:2] == ver[:2] and version <= ver
    return {"<": version < ver, "<=": version <= ver, ">": version > ver,
            ">=": version >= ver, "=": version == ver, "==": version == ver}[op]


def in_range(range_text, version_text):
    """Does a GitHub advisory's free-text range cover this version?

    Repository advisories write ranges by hand. A comma joins two bounds of one
    range ('>= 1.0, < 2.0') - read as AND - or lists several branches
    ('<= 3.3.11, 4.0.0-alpha1', '<= 3.3.0-alpha1, <= 3.2.0') - read as OR. It is
    AND only when both a lower and an upper bound are present; otherwise OR,
    which errs towards flagging: a false alarm costs an acknowledgement, a
    missed advisory costs a user."""
    version = parse_version(version_text)
    if version is None or not range_text:
        return False
    parts = [p for p in re.split(r"[,;]", range_text) if p.strip()]
    lower = any(re.match(r"\s*>", p) for p in parts)
    upper = any(re.match(r"\s*<", p) for p in parts)
    if lower and upper:
        return all(_clause(p, version) for p in parts)
    return any(_clause(p, version) for p in parts)


# ----------------------------------------------------------------- sources --

def _get_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}))
    req.add_header("User-Agent", "hidfi-dep-watch")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise CannotCheck(f"{url}: {e}") from None


def github(path):
    """GitHub REST, through `gh` when it is installed and signed in (5000
    requests an hour), else anonymously (60), with GITHUB_TOKEN if set."""
    if shutil.which("gh"):
        p = subprocess.run(["gh", "api", path], capture_output=True, text=True, timeout=60)
        if p.returncode == 0:
            return json.loads(p.stdout)
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    return _get_json("https://api.github.com/" + path.lstrip("/"), headers=headers)


class Net:
    """Everything that reaches the internet, in one place so tests replace it."""

    def releases(self, repo):
        rel = github(f"repos/{repo}/releases?per_page=50")
        return [r["tag_name"] for r in rel if not r.get("draft") and not r.get("prerelease")]

    def advisories(self, repo):
        out = []
        for a in github(f"repos/{repo}/security-advisories?per_page=100&state=published"):
            vulns = a.get("vulnerabilities") or [{}]
            out.append({
                "id": a.get("ghsa_id"), "severity": a.get("severity") or "unknown",
                "summary": (a.get("summary") or "").strip(),
                "published": (a.get("published_at") or "")[:10],
                "ranges": [v.get("vulnerable_version_range") or "" for v in vulns],
                "patched": [v.get("patched_versions") or "" for v in vulns],
            })
        return out

    def osv(self, ecosystem, packages):
        """packages: [(name, version)] -> {(name, version): [ids]}"""
        found = {}
        for i in range(0, len(packages), 500):
            chunk = packages[i:i + 500]
            body = json.dumps({"queries": [{"package": {"name": n, "ecosystem": ecosystem}, "version": v}
                                           for n, v in chunk]}).encode()
            res = _get_json("https://api.osv.dev/v1/querybatch", data=body)
            for (n, v), r in zip(chunk, res.get("results", [])):
                ids = [x["id"] for x in r.get("vulns", [])]
                if ids:
                    found[(n, v)] = ids
        return found

    def osv_detail(self, vid):
        return _get_json(f"https://api.osv.dev/v1/vulns/{vid}")

    def crate_latest(self, name):
        d = _get_json(f"https://crates.io/api/v1/crates/{name}")
        return d["crate"].get("max_stable_version") or d["crate"].get("max_version")

    def pypi_latest(self, name):
        return _get_json(f"https://pypi.org/pypi/{name}/json")["info"]["version"]


# ------------------------------------------------------------ what we pin --

def arduino_data_dir():
    for d in (os.environ.get("ARDUINO_DATA_DIR"), Path.home() / "Library" / "Arduino15",
              Path.home() / ".arduino15", Path(os.environ.get("LOCALAPPDATA", "")) / "Arduino15"):
        if d and Path(d).is_dir():
            return Path(d)
    return None


def derived_from_core(core_version, data_dir):
    """What the pinned core actually compiles in: ESP-IDF, mbedTLS, esptool.

    Read from that core's installed.json (its tool versions) and its
    esp32-arduino-libs (sdkconfig, mbedTLS header). Missing -> CannotCheck."""
    if data_dir is None:
        raise CannotCheck("no Arduino data folder - install the pinned core to check ESP-IDF, mbedTLS, esptool")
    plat = data_dir / "packages" / "esp32" / "hardware" / "esp32" / core_version
    inst = plat / "installed.json"
    if not inst.is_file():
        raise CannotCheck(f"core {core_version} is not installed ({plat})")
    tools = {}
    for pkg in json.loads(inst.read_text()).get("packages", []):
        for p in pkg.get("platforms") or []:
            if p.get("version") == core_version:
                tools = {t["name"]: t["version"] for t in p.get("toolsDependencies") or []}
    libs = data_dir / "packages" / "esp32" / "tools" / "esp32-arduino-libs" / tools.get("esp32-arduino-libs", "?")
    sdk = libs / "esp32s3" / "sdkconfig"
    if not sdk.is_file():
        raise CannotCheck(f"esp32-arduino-libs for core {core_version} not found ({libs})")
    m = re.search(r'CONFIG_IDF_INIT_VERSION="([^"]+)"', sdk.read_text())
    idf = m.group(1) if m else None
    mbed = None
    for h in (libs / "esp32s3" / "include").rglob("build_info.h"):
        mm = re.search(r'#define MBEDTLS_VERSION_STRING\s+"([^"]+)"', h.read_text(errors="replace"))
        if mm:
            mbed = mm.group(1)
            break
    out = {"ESP-IDF": idf, "mbedTLS": mbed, "esptool": tools.get("esptool_py")}
    missing = [k for k, v in out.items() if not v]
    if missing:
        raise CannotCheck(f"could not read {', '.join(missing)} from core {core_version}")
    return out


def cargo_lock_packages(lock=CARGO_LOCK):
    """Every crates.io package in the Companion's lock file: (name, version)."""
    if not lock.is_file():
        return []
    pkgs, cur = [], {}
    for line in lock.read_text().splitlines() + ["[[package]]"]:
        if line.strip() == "[[package]]":
            if cur.get("source", "").startswith("registry+"):
                pkgs.append((cur["name"], cur["version"]))
            cur = {}
            continue
        m = re.match(r'(\w+) = "(.*)"', line)
        if m:
            cur[m.group(1)] = m.group(2)
    return sorted(set(pkgs))


def direct_crates():
    """The Companion's direct crates.io dependencies (what we chose, not what came along)."""
    names = set()
    for toml in (ROOT / "companion").glob("crates/*/Cargo.toml"):
        section = None
        for line in toml.read_text().splitlines():
            s = re.match(r"\[(.+)\]", line.strip())
            if s:
                section = s.group(1)
                continue
            if section and section.endswith("dependencies"):
                m = re.match(r"([A-Za-z0-9_\-]+)\s*=", line)
                if m and "path" not in line:
                    names.add(m.group(1))
    return sorted(names)


def installed_python(name):
    try:
        from importlib import metadata
        return metadata.version(name)
    except Exception:
        return None


# ------------------------------------------------------------------- run --

def latest_of(tags, same_branch_as=None):
    vs = [(parse_version(t), t) for t in tags]
    vs = [(v, t) for v, t in vs if v and v[4] == 1]       # releases only, no pre-releases
    if same_branch_as:
        b = parse_version(same_branch_as)
        vs = [(v, t) for v, t in vs if b and v[:2] == b[:2]]
    return max(vs)[1] if vs else None


def run(net=None, deps_path=DEPS, ack_path=ACK, data_dir="auto", lock=CARGO_LOCK, today=None):
    net = net or Net()
    today = today or datetime.date.today().isoformat()
    deps = json.loads(Path(deps_path).read_text())
    acks = json.loads(Path(ack_path).read_text()) if Path(ack_path).is_file() else {}
    acks = {k: v for k, v in acks.items() if not k.startswith("_")}
    report = {"date": today, "rows": [], "advisories": [], "cannot_check": []}

    pins = {}
    for name, repo in FIRMWARE_REPOS.items():
        if name in deps.get("firmware", {}):
            pins[name] = (repo, deps["firmware"][name])
    core = deps.get("firmware", {}).get("esp32:esp32")
    try:
        derived = derived_from_core(core, arduino_data_dir() if data_dir == "auto" else data_dir)
        for name, ver in derived.items():
            pins[name] = (DERIVED_REPOS[name], ver)
    except CannotCheck as e:
        report["cannot_check"].append(str(e))

    for name, (repo, ver) in pins.items():
        row = {"part": name, "kind": "firmware", "pinned": ver, "latest": None, "latest_branch": None}
        try:
            tags = net.releases(repo)
            row["latest"] = latest_of(tags)
            row["latest_branch"] = latest_of(tags, same_branch_as=ver)
        except CannotCheck as e:
            report["cannot_check"].append(f"{name} releases: {e}")
        report["rows"].append(row)
        try:
            for a in net.advisories(repo):
                hit = [r for r in a["ranges"] if in_range(r, ver)]
                if hit:
                    report["advisories"].append(dict(a, part=name, pinned=ver,
                                                     ack=acks.get(a["id"])))
        except CannotCheck as e:
            report["cannot_check"].append(f"{name} advisories: {e}")

    crates = cargo_lock_packages(lock)
    locked = dict(crates)
    for name in direct_crates():
        row = {"part": name, "kind": "crate", "pinned": locked.get(name), "latest": None, "latest_branch": None}
        try:
            row["latest"] = net.crate_latest(name)
        except CannotCheck as e:
            report["cannot_check"].append(f"crate {name}: {e}")
        report["rows"].append(row)

    pys = [(n, installed_python(n)) for n in PY_TOOLS]
    for n, v in pys:
        row = {"part": n, "kind": "test tool", "pinned": v or "not installed", "latest": None, "latest_branch": None}
        try:
            row["latest"] = net.pypi_latest(n)
        except CannotCheck as e:
            report["cannot_check"].append(f"PyPI {n}: {e}")
        report["rows"].append(row)

    for eco, pk in (("crates.io", crates), ("PyPI", [(n, v) for n, v in pys if v])):
        if not pk:
            continue
        try:
            for (n, v), ids in net.osv(eco, pk).items():
                for vid in ids:
                    try:
                        d = net.osv_detail(vid)
                    except CannotCheck:
                        d = {}
                    alias = next((x for x in d.get("aliases", []) if x.startswith("GHSA-")), None)
                    fixed = sorted({ev["fixed"] for af in d.get("affected", [])
                                    if (af.get("package") or {}).get("name") == n
                                    for rg in af.get("ranges", []) for ev in rg.get("events", []) if "fixed" in ev})
                    report["advisories"].append({
                        "id": vid, "severity": (d.get("database_specific") or {}).get("severity", "unknown"),
                        "summary": (d.get("summary") or d.get("details") or "")[:160],
                        "published": (d.get("published") or "")[:10], "ranges": [], "patched": fixed,
                        "part": n, "pinned": v, "ack": acks.get(vid) or (acks.get(alias) if alias else None)})
        except CannotCheck as e:
            report["cannot_check"].append(f"OSV {eco}: {e}")

    open_ = [a for a in report["advisories"] if not a["ack"]]
    report["open"] = len(open_)
    report["exit"] = 1 if open_ else (2 if report["cannot_check"] else 0)
    return report


def show(report):
    w = max([len(r["part"]) for r in report["rows"]] + [10])
    print(f"HID-Fi dependency watch - {report['date']}\n")
    print(f"{'part':<{w}}  {'kind':<9}  {'pinned':<16} {'latest':<16} same branch")
    for r in report["rows"]:
        latest = r["latest"] or "?"
        mark = "" if (r["latest"] is None or parse_version(r["latest"]) == parse_version(r["pinned"] or "")) else "  <- newer"
        print(f"{r['part']:<{w}}  {r['kind']:<9}  {str(r['pinned']):<16} {latest:<16} {r['latest_branch'] or ''}{mark}")
    print()
    if report["advisories"]:
        print("Advisories covering a pinned version:")
        for a in sorted(report["advisories"], key=lambda a: (a["ack"] is not None, a["part"], a["id"])):
            state = "acknowledged: " + a["ack"]["reason"] if a["ack"] else "OPEN - reaches HID-Fi until shown otherwise"
            patched = "; ".join(p for p in a["patched"] if p) or "?"
            print(f"  [{a['severity']}] {a['id']} {a['part']} {a['pinned']} (fixed: {patched})\n"
                  f"      {a['summary'][:110]}\n      {state}")
    else:
        print("No published advisory covers a pinned version.")
    for c in report["cannot_check"]:
        print(f"\nCOULD NOT CHECK: {c}")
    verdict = {0: "OK - everything checked, nothing open",
               1: f"{report['open']} open advisory(ies) - a security update comes first (AGENTS.md)",
               2: "INCOMPLETE - some sources could not be checked; this is not a pass"}[report["exit"]]
    print(f"\n{verdict}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    args = ap.parse_args(argv)
    report = run()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        show(report)
    return report["exit"]


if __name__ == "__main__":
    sys.exit(main())
