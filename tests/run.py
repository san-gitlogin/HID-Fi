#!/usr/bin/env python3
"""HID-Fi test runner - the one command.

    python tests/run.py               everything that needs no board: static, unit, ui, build, hostsim
    python tests/run.py --board       + the serial suites against the board on COM
    python tests/run.py --e2e         + real typing/mouse into a focus-guarded test page on THIS computer
    python tests/run.py --perf        + performance and soak (add --board/--e2e for the hardware ones)
    python tests/run.py --all         every tier this machine can run
    python tests/run.py --tier unit   only some tiers (comma separated)
    python tests/run.py -k unicode    only tests whose name matches
    python tests/run.py doctor        check the environment, run nothing
    python tests/run.py --ci          what CI runs: no-hardware tiers, strict dependency pins

Anything after `--` goes straight to pytest. See docs/TESTING.md.
"""
import argparse
import os
import platform
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

TIERS = ["static", "unit", "ui", "build", "hostsim", "board", "e2e", "linux", "perf"]
DEFAULT_TIERS = ["static", "unit", "ui", "build", "hostsim"]


def _have_xdist():
    import importlib.util            # find, not import: importing it here stops pytest rewriting it
    return importlib.util.find_spec("xdist") is not None


def tier_of(keywords):
    for t in ("perf", "linux", "e2e", "board", "hostsim", "build", "ui", "unit", "static", "manual"):
        if t in keywords:
            return t
    return "other"


class Collector:
    def __init__(self):
        self.rows = []
        self.tiers = {}

    def pytest_collection_modifyitems(self, items):
        for item in items:   # tier from real markers, not keywords (those include parameter ids)
            self.tiers[item.nodeid] = tier_of({m.name for m in item.iter_markers()})

    def pytest_collectreport(self, report):
        # A test file that cannot even load (a typo, a missing import) runs none
        # of its tests: name it in the table and the FAILED list, or the verdict
        # says FAILED with nothing to point at.
        if report.failed:
            msg = (report.longreprtext or "").strip().splitlines()
            self.rows.append({"id": report.nodeid or "collection", "tier": "other", "outcome": "error",
                              "secs": 0, "reason": "could not load: " + " ".join((msg[-1] if msg else "").split())[:220]})

    def pytest_runtest_logreport(self, report):
        if report.when == "call" or (report.when == "setup" and report.outcome != "passed") \
                or (report.when == "teardown" and report.outcome == "failed"):
            reason = ""
            if report.skipped and isinstance(report.longrepr, tuple):
                reason = str(report.longrepr[2]).replace("Skipped: ", "")
            elif report.failed:
                crash = getattr(report.longrepr, "reprcrash", None)
                msg = crash.message if crash else (report.longreprtext or "").strip().splitlines()[-1:] and report.longreprtext.strip().splitlines()[-1]
                reason = " ".join(str(msg).split())[:240]
            outcome = "error" if (report.failed and report.when != "call") else report.outcome
            if hasattr(report, "wasxfail"):          # a tagged known issue, still failing as expected
                outcome, reason = ("known" if report.skipped else "fixed?"), report.wasxfail[:240]
            tier = dict(report.user_properties).get("tier") or self.tiers.get(report.nodeid, "other")
            self.rows.append({"id": report.nodeid, "tier": tier, "outcome": outcome,
                              "secs": report.duration, "reason": reason})


def install_hooks():
    """Point git at tests/hooks: static+unit before a commit, every no-board tier
    (with the dashboard coverage contract) before a push."""
    import subprocess
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, cwd=HERE)
    if top.returncode:
        print("  not a git checkout - nothing to install")
        return 1
    subprocess.run(["git", "config", "core.hooksPath", "tests/hooks"], cwd=top.stdout.strip(), check=True)
    print("  git hooks on: pre-commit runs static+unit, pre-push runs every no-board tier.\n"
          "  Undo with: git config --unset core.hooksPath")
    return 0


def doctor():
    from support import deps
    from support.board import find_ports
    print(f"\nHID-Fi test doctor - {platform.system()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}\n")
    print(f"  {'dependency':14s} {'wanted':12s} {'installed':14s} verdict")
    worst = 0
    for name, want, have, verdict in deps.check():
        mark = {"ok": "ok", "differs": "DIFFERS", "old": "TOO OLD", "missing": "MISSING"}[verdict]
        worst = max(worst, 0 if verdict == "ok" else 1)
        print(f"  {name:14s} {want:12s} {str(have):14s} {mark}")
    import shutil
    cxx = shutil.which("clang++") or shutil.which("g++")
    print(f"\n  C++ compiler   {cxx or 'MISSING - unit tier will be skipped'}")
    ports = find_ports()
    print(f"  board on COM   {', '.join(ports) if ports else 'none found - board/e2e tiers will be skipped'}")
    print("\n  What each tier needs: docs/TESTING.md#tiers")
    if worst:
        print("  Versions that differ from tests/deps.json are a different product until the suite passes on them:")
        print("  docs/TESTING.md#upgrading-a-dependency\n")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["run", "doctor", "install-hooks"], default="run")
    ap.add_argument("--tier", help=f"comma separated subset of {','.join(TIERS)}")
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--e2e", action="store_true")
    ap.add_argument("--perf", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ci", action="store_true")
    ap.add_argument("--port")
    ap.add_argument("--sink", choices=["local", "remote"])
    ap.add_argument("--allow-input-source-switch", action="store_true")
    ap.add_argument("--update-golden", action="store_true")
    ap.add_argument("--soak-minutes", type=float)
    ap.add_argument("--board-url", help="ws://<board-ip>:81 for the WiFi tests (or HIDFI_BOARD_URL)")
    ap.add_argument("-k", dest="keyword")
    ap.add_argument("-x", dest="exitfirst", action="store_true", help="stop at the first failure")
    ap.add_argument("-v", dest="verbose", action="store_true")
    ap.add_argument("--serial", action="store_true", help="one process, even when pytest-xdist is installed")
    args, passthrough = ap.parse_known_args()
    passthrough = [a for a in passthrough if a != "--"]

    if args.command == "doctor":
        return doctor()
    if args.command == "install-hooks":
        return install_hooks()

    if args.all:
        args.board = args.e2e = args.perf = True
    tiers = args.tier.split(",") if args.tier else list(DEFAULT_TIERS)
    if not args.tier:
        if args.board: tiers += ["board"]
        if args.e2e: tiers += ["board", "e2e", "linux"]
        if args.perf: tiers += ["perf"]
    tiers = list(dict.fromkeys(tiers))
    unknown = set(tiers) - set(TIERS)
    if unknown:
        ap.error(f"unknown tier(s) {sorted(unknown)}; choose from {TIERS}")
    if "board" in tiers or "e2e" in tiers or "linux" in tiers:
        args.board = True
    if "e2e" in tiers or "linux" in tiers:
        args.e2e = True

    pyargs = [str(HERE), "-m", " or ".join(tiers), "-p", "no:cacheprovider",
              f"--junitxml={HERE / '.reports' / 'junit.xml'}", "-q" if not args.verbose else "-v"]
    for flag in ("board", "e2e", "perf", "update_golden", "allow_input_source_switch"):
        if getattr(args, flag):
            pyargs.append("--" + flag.replace("_", "-"))
    if args.ci:
        pyargs.append("--strict-deps")
    if args.port: pyargs += ["--port", args.port]
    if args.sink: pyargs += ["--sink", args.sink]
    if args.soak_minutes: pyargs += ["--soak-minutes", str(args.soak_minutes)]
    if args.board_url: pyargs += ["--board-url", args.board_url]
    if args.keyword: pyargs += ["-k", args.keyword]
    if args.exitfirst: pyargs.append("-x")
    # Board-free tiers run in parallel when pytest-xdist is installed. Anything
    # that touches the board or the computer never does: one board, one desktop.
    hardware = {"board", "e2e", "linux", "perf"} & set(tiers)
    parallel = not hardware and not args.serial and _have_xdist()
    timed_args = None
    if parallel:
        # HIDFI_WORKERS caps it on a small VM or sandbox, where one Chromium per
        # CPU runs out of memory and timer-driven scenarios starve.
        # Speed measurements (marker "timing") never share the machine with the
        # parallel workers: they measured the load, not the code. They run after,
        # alone, into the same table.
        timed_args = list(pyargs)
        timed_args[2] = f"({pyargs[2]}) and timing"
        timed_args[timed_args.index(f"--junitxml={HERE / '.reports' / 'junit.xml'}")] = \
            f"--junitxml={HERE / '.reports' / 'junit_timing.xml'}"
        pyargs[2] = f"({pyargs[2]}) and not timing"
        pyargs += ["-n", os.environ.get("HIDFI_WORKERS") or "auto", "--dist", "loadgroup"]
    pyargs += passthrough
    if timed_args is not None:
        timed_args += passthrough

    print(f"\nHID-Fi tests - tiers: {', '.join(tiers)}"
          + ("" if args.board else "   (board tiers off: add --board / --e2e / --perf)") + "\n")
    import pytest
    col = Collector()
    t0 = time.time()
    code = pytest.main(pyargs, plugins=[col])
    if timed_args is not None:
        print("\n-- timing tests, alone on a quiet machine --")
        timed = pytest.main(timed_args, plugins=[col])
        if timed not in (0, 5):                 # 5: none selected for these tiers
            code = timed if code in (0, 5) else code
    report(col.rows, tiers, time.time() - t0, code)
    if code == 5:            # nothing collected/selected
        print("  Nothing ran for these tiers on this machine.")
        return 0
    return int(code)


def report(rows, tiers, secs, code=0):
    by = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by[r["tier"]][r["outcome"]] += 1
    W = 64
    print("\n" + "=" * W)
    print(f"  {'tier':8s} {'passed':>7s} {'failed':>7s} {'error':>6s} {'skipped':>8s} {'known':>6s}")
    for t in TIERS + ["manual", "other"]:
        if t in by:
            d = by[t]
            print(f"  {t:8s} {d['passed']:7d} {d['failed']:7d} {d['error']:6d} {d['skipped']:8d} {d['known']:6d}")
    fails = [r for r in rows if r["outcome"] in ("failed", "error")]
    skips = defaultdict(list)
    for r in rows:
        if r["outcome"] == "skipped":
            skips[r["reason"]].append(r["id"])
    known = [r for r in rows if r["outcome"] == "known"]
    if known:
        print("\n  KNOWN ISSUES (tagged @known_issue, docs/TESTING.md#known-issues)")
        for r in known:
            print(f"   ~ {r['id'].split('::')[-1][:70]}")
    if fails:
        print("\n  FAILED")
        for r in fails:
            print(f"   x {r['id']}\n     {r['reason']}")
    if skips:
        print("\n  SKIPPED (and why)")
        for reason, ids in sorted(skips.items(), key=lambda kv: -len(kv[1])):
            print(f"   - {len(ids):3d} x {reason}")
    # A session-level check (the dashboard coverage contract) can fail with every
    # test green, so pytest's own exit code decides too.
    verdict = "FAILED" if fails or code not in (0, 5) else "PASSED"
    if code not in (0, 5) and not fails:
        print("\n  FAILED outside any single test - see the coverage contract lines above")
    print("\n  " + verdict + f" in {secs:.0f}s - details: tests/.reports/junit.xml, tests/.reports/summary.md")
    print("=" * W + "\n")
    rep = HERE / ".reports"
    rep.mkdir(exist_ok=True)
    lines = [f"# HID-Fi test summary - {verdict}", "",
             f"{platform.system()} {platform.release()}, Python {platform.python_version()}, tiers: {', '.join(tiers)}, {secs:.0f}s", "",
             "| tier | passed | failed | error | skipped |", "|---|---|---|---|---|"]
    for t in TIERS + ["manual", "other"]:
        if t in by:
            d = by[t]
            lines.append(f"| {t} | {d['passed']} | {d['failed']} | {d['error']} | {d['skipped']} |")
    if fails:
        lines += ["", "## Failed", ""] + [f"- `{r['id']}` - {r['reason']}" for r in fails]
    if skips:
        lines += ["", "## Skipped", ""] + [f"- {len(v)} x {k}" for k, v in skips.items()]
    (rep / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
