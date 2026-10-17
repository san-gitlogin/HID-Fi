#!/usr/bin/env python3
"""Record the firmware's keystrokes for the hostsim tier, to replay on another machine.

    python tests/hostsim/record.py > cases.json      # needs the ESP32 core + a C++ compiler
    HIDFI_HOSTSIM_CASES=cases.json python tests/run.py --tier hostsim   # on the Linux box
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support import corpus                                   # noqa: E402
from unit.test_typing import build_bench, _dump               # noqa: E402

MODES = ["linux_off", "linux_on", "linux_caps"]
exe, _ = build_bench()
labels = [k for k in corpus.CORPUS if k not in corpus.HOST_UNTYPEABLE]
traces = _dump(exe, [(l, corpus.CORPUS[l].encode(), m) for m in MODES for l in labels])
json.dump([{"id": f"{d['mode']}/{d['label']}", "ev": d["ev"], "caps": d["mode"] == "linux_caps"} for d in traces], sys.stdout)
