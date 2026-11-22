"""Watch how hot a HID-Fi board runs, for hours, with nobody at the keyboard.

    python3 tools/thermal_watch.py                    find the board, log every 30 s for 8 hours
    python3 tools/thermal_watch.py --hours 24 --every 60
    python3 tools/thermal_watch.py --note "fan on"    a note kept in the log (optional, never needed)
    python3 tools/thermal_watch.py report [FILE ...]  what the logs say: stretches, peaks, events

Where the board is: HIDFI_HOST (its address or name.local); otherwise the board mDNS
finds (tools/hidfi.py find), the one whose name matches HIDFI_MAC if several answer.
Status needs no PIN. With HIDFI_PIN in the environment (never on a command line) it
also reads the board's own four-hour log (`temp_log`) when it starts and after the
board was out of reach, so time nobody was watching is filled in. A refused PIN is
never tried again: the board locks out after repeated wrong ones.

One JSON line per sample goes to ~/HID-Fi-work/thermal/<board>-<start>.jsonl
(HIDFI_THERMAL_DIR to change): the temperatures, uptime, and what the board says it
is doing (radio awake, access point on, how many clients, power mode). No network
names, no addresses, nothing a person typed.

Nobody has to say whether the board is in its shell, or keep saying so: a person
takes it out and puts it back whenever they like. The report finds those moments
itself. A shift of the steady temperature that nothing the board reports explains
(a restart, the radio, the access point, clients, the power mode, a firmware change)
is called "likely put in a shell or covered" (up) or "likely taken out or uncovered"
(down) - always a guess, and labelled as one.

Exit codes: 0 done | 2 usage | 3 the board could not be found.
"""
import argparse
import datetime
import glob
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hidfi  # noqa: E402

LOAD_KEYS = ("radio_awake", "ap_running", "ap_clients", "ws_clients", "power_mode", "firmware")
KEEP_KEYS = ("uptime_sec", "temp_c", "temp_peak_c", "reset_reason", "free_heap", "wifi_rssi") + LOAD_KEYS
SHIFT_C = 2.5            # a change of the steady temperature this big is an event...
SMOOTH_S = 300           # ...measured on 5-minute means...
LOOKBACK_S = 900         # ...against the mean 15 minutes earlier
EXPLAIN_S = (1200, 300)  # a load change from 20 min before to 5 min after a shift explains it


# ---- what a sample says the board is doing -------------------------------------------------------
def signature(s):
    """The load the board reports, or None for a point from its own log (no load known)."""
    if s.get("source") == "board_log":
        return None
    return (bool(s.get("radio_awake")), bool(s.get("ap_running")), (s.get("ap_clients") or 0) > 0,
            (s.get("ws_clients") or 0) > 0, s.get("power_mode"), s.get("firmware"))


def describe_load(sig):
    if sig is None:
        return "unknown (from the board's own log)"
    radio, ap, apc, ws, mode, fw = sig
    return (f"radio {'awake' if radio else 'asleep'} · access point {'on' if ap else 'off'}"
            f"{' with clients' if apc else ''} · {'dashboard open' if ws else 'no dashboard'} · {mode} · {fw}")


def load_change(a, b):
    """What changed between two signatures, in words, or '' if nothing (or unknown)."""
    if a is None or b is None or a == b:
        return ""
    names = ("the radio " + ("woke" if b[0] else "went to sleep"), "the access point " + ("came on" if b[1] else "went off"),
             "a phone " + ("joined" if b[2] else "left") + " the access point", "a dashboard " + ("opened" if b[3] else "closed"),
             f"power mode {b[4]}", f"firmware {b[5]}")
    return ", ".join(n for n, x, y in zip(names, a, b) if x != y)


# ---- analysis (pure: tests/unit/test_thermal_watch.py) -------------------------------------------
def clean(samples):
    """Samples with a temperature, oldest first, one per timestamp."""
    seen, out = set(), []
    for s in sorted((s for s in samples if s.get("temp_c") is not None and s.get("t") is not None), key=lambda s: s["t"]):
        if s["t"] in seen:
            continue
        seen.add(s["t"])
        out.append(s)
    return out


def events(samples):
    """Restarts, load changes and steady-temperature shifts, oldest first.

    Each event: {"t", "kind": restart|load|shift|gap, "what", ...}. A shift carries
    "delta" and either "because" (the restart or load change that explains it) or
    "guess" (nothing the board reported explains it)."""
    s = clean(samples)
    out = []
    for a, b in zip(s, s[1:]):
        if a.get("source") != "board_log" and b.get("source") != "board_log" \
                and b.get("uptime_sec") is not None and a.get("uptime_sec") is not None and b["uptime_sec"] < a["uptime_sec"]:
            out.append({"t": b["t"], "kind": "restart", "what": f"the board restarted ({b.get('reset_reason', 'unknown')})"})
        ch = load_change(signature(a), signature(b))
        if ch:
            out.append({"t": b["t"], "kind": "load", "what": ch})
    # shifts: a 5-minute mean against the 5-minute mean 15 minutes before it
    ts = [x["t"] for x in s]
    temps = [x["temp_c"] for x in s]

    def mean_before(t, width=SMOOTH_S):
        vals = [v for tt, v in zip(ts, temps) if t - width < tt <= t]
        return statistics.fmean(vals) if vals else None

    shifts, cur = [], None
    for t in ts:
        now, then = mean_before(t), mean_before(t - LOOKBACK_S)
        if now is None or then is None or not any(t - LOOKBACK_S - SMOOTH_S < tt <= t - LOOKBACK_S for tt in ts):
            cur = None
            continue
        d = now - then
        if abs(d) >= SHIFT_C:
            if cur and (d > 0) == (cur["delta"] > 0) and t - cur["end"] <= 2 * SMOOTH_S:
                cur["end"] = t
                if abs(d) > abs(cur["delta"]):
                    cur["delta"] = d
            else:
                cur = {"start": t - LOOKBACK_S, "end": t, "delta": d}
                shifts.append(cur)
        elif cur and t - cur["end"] > 2 * SMOOTH_S:
            cur = None
    # merge shifts the same way that overlap (one slow rise read in two windows)
    merged = []
    for sh in shifts:
        if merged and (sh["delta"] > 0) == (merged[-1]["delta"] > 0) and sh["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], sh["end"])
            if abs(sh["delta"]) > abs(merged[-1]["delta"]):
                merged[-1]["delta"] = sh["delta"]
        else:
            merged.append(dict(sh))
    causes = [e for e in out if e["kind"] in ("restart", "load")]
    for sh in merged:
        lo, hi = sh["start"] - EXPLAIN_S[0], sh["start"] + LOOKBACK_S + EXPLAIN_S[1]
        why = [c for c in causes if lo <= c["t"] <= hi]
        ev = {"t": sh["start"], "kind": "shift", "delta": round(sh["delta"], 1), "until": sh["end"]}
        if why:
            ev["because"] = "; ".join(c["what"] for c in why)
            ev["what"] = f"{'+' if sh['delta'] > 0 else ''}{sh['delta']:.1f} °C, after: {ev['because']}"
        else:
            ev["guess"] = "likely put in a shell or covered" if sh["delta"] > 0 else "likely taken out or uncovered"
            ev["what"] = f"{'+' if sh['delta'] > 0 else ''}{sh['delta']:.1f} °C with no change the board reports: {ev['guess']}"
        out.append(ev)
    return sorted(out, key=lambda e: e["t"])


def summary(samples):
    s = clean(samples)
    if not s:
        return {"samples": 0}
    temps = [x["temp_c"] for x in s]
    peak = max([x.get("temp_peak_c") or x["temp_c"] for x in s] + temps)
    return {"samples": len(s), "from": s[0]["t"], "to": s[-1]["t"], "hours": round((s[-1]["t"] - s[0]["t"]) / 3600, 2),
            "min_c": min(temps), "mean_c": round(statistics.fmean(temps), 1), "max_c": max(temps), "peak_c": peak,
            "last_c": temps[-1]}


def backfill(log_reply, read_at):
    """Points from the board's temp_log reply, timed on this computer's clock."""
    pts, period = log_reply.get("tenths") or [], log_reply.get("period_s", 60)
    end = read_at - (log_reply.get("uptime_sec", 0) - log_reply.get("last_s", 0))
    out = []
    for i, v in enumerate(pts):
        if v is None:
            continue
        mid = end - (len(pts) - i) * period + period / 2
        out.append({"t": round(mid, 1), "temp_c": v / 10, "source": "board_log"})
    return out


# ---- talking to the board ---------------------------------------------------------------------------
def board_name(mac):
    return "esp-hid-" + mac.replace(":", "").lower()[-6:] + ".local"


def locate():
    host = os.environ.get("HIDFI_HOST", "")
    if host:
        return host
    want = os.environ.get("HIDFI_MAC", "")
    found = hidfi.find()
    if want:
        found = {h: ip for h, ip in found.items() if h.rstrip(".") == board_name(want)}
    if len(found) == 1:
        h, ip = next(iter(found.items()))
        return ip or h.rstrip(".")
    return ""


def get_status(host, timeout=6.0):
    with urllib.request.urlopen(f"http://{host}/api/status", timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def read_board_log(host, state):
    """temp_log through the PIN, once per need; a refused PIN switches it off for good."""
    if not os.environ.get("HIDFI_PIN") or state.get("pin_refused"):
        return None
    b = hidfi.Board()
    b.host = host
    try:
        r = b.cmd({"cmd": "temp_log"}, timeout=10)
    except (OSError, ValueError, TimeoutError, urllib.error.URLError):
        return None
    if r.get("reply") != "temp_log":
        if r.get("reply") in ("auth_required", "auth_failed", "locked_out") or "PIN" in str(r.get("message", "")):
            state["pin_refused"] = True
        return None
    return r


def watch(hours, every, note, out_dir):
    host = locate()
    if not host:
        print("No board found. Set HIDFI_HOST to its address (Settings > Network on the dashboard).", file=sys.stderr)
        return 3
    st = get_status(host)
    mac = st.get("mac", "board").replace(":", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{mac}-{datetime.datetime.now():%Y%m%d-%H%M}.jsonl"
    state, last_ok, last_up, shown = {}, None, None, set()
    started = time.time()
    end = started + hours * 3600
    print(f"Watching {board_name(mac)} every {every} s for {hours} h -> {path}")
    with path.open("a", encoding="utf-8") as f:
        def write(rec):
            f.write(json.dumps(rec) + "\n")
            f.flush()
        if note:
            write({"t": round(time.time(), 1), "note": note})
        samples = []
        while time.time() < end:
            t = time.time()
            try:
                st = get_status(host)
            except (OSError, ValueError, urllib.error.URLError) as e:
                write({"t": round(t, 1), "unreachable": type(e).__name__})
                print(f"{datetime.datetime.now():%H:%M:%S}  out of reach ({type(e).__name__}); looking again")
                new = locate()
                host = new or host
                time.sleep(min(every, 30))
                continue
            gap = last_ok is None or t - last_ok > 3 * every
            restarted = last_up is not None and st.get("uptime_sec", 0) < last_up
            if gap or restarted:
                log = read_board_log(host, state)
                if log:
                    for p in backfill(log, t):
                        if last_ok is None or p["t"] > last_ok:
                            write(p)
                            samples.append(p)
            rec = {"t": round(t, 1), **{k: st.get(k) for k in KEEP_KEYS}}
            write(rec)
            samples.append(rec)
            last_ok, last_up = t, st.get("uptime_sec")
            tc = st.get("temp_c")
            print(f"{datetime.datetime.now():%H:%M:%S}  {'-' if tc is None else f'{tc:.1f}'} °C"
                  f" (peak {st.get('temp_peak_c')})  {describe_load(signature(rec))}")
            recent = [x for x in samples if x["t"] > t - 7200]          # the last two hours are enough to see a shift
            for e in events(recent):
                key = (e["kind"], e.get("what", "")[:40], round(e["t"] / 600))
                if key not in shown and e["t"] > started - LOOKBACK_S:
                    shown.add(key)
                    print(f"          {e['kind']}: {e['what']}")
            time.sleep(max(1.0, every - (time.time() - t)))
    return 0


def load_files(files):
    samples, notes = [], []
    for fn in files:
        for line in Path(fn).read_text(encoding="utf-8").splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            (notes if "note" in d else samples).append(d)
    return samples, notes


def report(files):
    if not files:
        d = Path(os.environ.get("HIDFI_THERMAL_DIR", Path.home() / "HID-Fi-work" / "thermal"))
        files = sorted(glob.glob(str(d / "*.jsonl")))[-1:]
    if not files:
        print("No logs yet: run tools/thermal_watch.py first.", file=sys.stderr)
        return 2
    samples, notes = load_files(files)
    sm = summary(samples)
    if not sm["samples"]:
        print("No temperatures in these logs (firmware before temp_c?).")
        return 0
    when = lambda t: datetime.datetime.fromtimestamp(t).strftime("%d %b %H:%M")
    print(f"{sm['samples']} samples, {when(sm['from'])} to {when(sm['to'])} ({sm['hours']} h)")
    print(f"chip {sm['min_c']}-{sm['max_c']} °C, mean {sm['mean_c']}, peak since start {sm['peak_c']}, last {sm['last_c']}")
    for n in notes:
        print(f"  {when(n['t'])}  note: {n['note']}")
    for e in events(samples):
        print(f"  {when(e['t'])}  {e['kind']:<7} {e['what']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="watch", choices=["watch", "report"])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--every", type=float, default=30.0)
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    if a.action == "report":
        return report(a.files)
    if a.hours <= 0 or a.every < 2:
        print("--hours must be above 0 and --every at least 2 s (the board reads its sensor every 2 s)", file=sys.stderr)
        return 2
    out = Path(os.environ.get("HIDFI_THERMAL_DIR", Path.home() / "HID-Fi-work" / "thermal"))
    try:
        return watch(a.hours, a.every, a.note, out)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
