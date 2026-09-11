"""What the board knows about itself: MAC addresses, permanent storage, and the
clients on its access point. Also checks that ap_configure rejects bad input.

Read-only and safe to run at any time. The ap_configure cases are all rejection
paths, which return before the radio is touched - so the access point you are
connected to is never restarted. Nothing here changes a password, because the
board deliberately will not reveal the current one and the change could not be
undone.

    python tests/test_inventory.py
"""
import json, sys, time, serial

PORT = "COM13"
ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK ] {label}" + (f": {detail}" if detail else ""))
    else:
        fail += 1
        print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))


def send(sp, obj, timeout=4.0):
    sp.reset_input_buffer()
    sp.write((json.dumps(obj) + "\n").encode())
    sp.flush()
    end = time.time() + timeout
    while time.time() < end:
        line = sp.readline().decode("utf-8", "replace").strip()
        if not line or not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "status" in d or "reply" in d:
            return d
    return None


with serial.Serial(PORT, 115200, timeout=1) as sp:
    time.sleep(2.0)
    # Reopening the CH343 straight after another suite closed it can leave the
    # first command unanswered, which failed this whole file for no real reason.
    st = None
    for _ in range(5):
        st = send(sp, {"cmd": "status"})
        if st:
            break
        time.sleep(1.0)

    print("\n-- MAC addresses in status --")
    check("status responded", st is not None)
    if st:
        mac, apmac = st.get("mac", ""), st.get("ap_mac", "")
        check("mac present and well formed", len(mac.split(":")) == 6, mac)
        check("ap_mac present and well formed", len(apmac.split(":")) == 6, apmac)
        check("the two MACs differ", mac != apmac, "AP uses its own address")

    print("\n-- storage_info --")
    si = send(sp, {"cmd": "storage_info"})
    check("storage_info answered", si is not None and si.get("status") == "ok")
    if si:
        nvs = si.get("nvs", {})
        used, total = nvs.get("used_entries"), nvs.get("total_entries")
        check("entry counts returned", isinstance(used, int) and isinstance(total, int),
              f"{used} of {total}")
        check("used does not exceed total", (used or 0) <= (total or 0))
        check("percent is sane", 0 <= nvs.get("percent_used", -1) <= 100,
              f"{nvs.get('percent_used')}%")
        cfg, wf = si.get("hid_cfg", {}), si.get("wifi_cfg", {})
        check("hid_cfg summarised", "pointer_mode" in cfg, cfg.get("pointer_mode"))
        check("wifi_cfg summarised", "ap_ssid" in wf, wf.get("ap_ssid"))
        # The important one: secrets must never leave the board.
        blob = json.dumps(si).lower()
        leaked = [k for k in ("sta_pass", "ap_pass", "password", "token", "pin")
                  if f'"{k}"' in blob and "_set" not in blob.split(f'"{k}"')[1][:8]]
        check("no secret values in the payload", not leaked, str(leaked) if leaked else "clean")
        check("passwords reported as booleans only",
              isinstance(wf.get("ap_pass_set"), bool) and isinstance(wf.get("sta_pass_set"), bool),
              f"ap_pass_set={wf.get('ap_pass_set')} sta_pass_set={wf.get('sta_pass_set')}")

    print("\n-- clients --")
    cl = send(sp, {"cmd": "clients"})
    check("clients answered", cl is not None and cl.get("status") == "ok")
    if cl:
        arr = cl.get("clients")
        check("clients is a list", isinstance(arr, list), f"{len(arr or [])} entries")
        check("count matches the list", cl.get("count") == len(arr or []))
        check("attached count reported", isinstance(cl.get("attached"), int),
              str(cl.get("attached")))
        check("explains the missing names", bool(cl.get("note")))
        for c in (arr or []):
            check("  entry has a MAC", len(c.get("mac", "").split(":")) == 6, c.get("mac"))
            check("  entry has ip and dashboard keys", "ip" in c and "dashboard" in c,
                  f"{c.get('ip')} dashboard={c.get('dashboard')}")

    # Only the rejection paths are exercised. Each of these returns before the
    # radio is touched, so the access point the user is on is never disturbed -
    # and a password change could not be undone anyway, since the board will not
    # tell us the current one.
    print("\n-- ap_configure rejects bad input (AP untouched) --")
    before = send(sp, {"cmd": "status"})
    r = send(sp, {"cmd": "ap_configure", "ssid": "test", "password": "short"})
    check("short password refused", r and r.get("status") == "error", r and r.get("message"))
    check("the refusal now points at open:true",
          r and "open:true" in (r.get("message") or ""), r and r.get("message"))

    r = send(sp, {"cmd": "ap_configure", "ssid": ""})
    check("empty SSID refused", r and r.get("status") == "error", r and r.get("message"))

    r = send(sp, {"cmd": "ap_configure", "ssid": "x" * 33})
    check("33-byte SSID refused", r and r.get("status") == "error", r and r.get("message"))

    time.sleep(1.0)
    after = send(sp, {"cmd": "status"})
    check("the access point was never restarted",
          before and after and before.get("ap_ssid") == after.get("ap_ssid"),
          f"still {after.get('ap_ssid') if after else '?'}")
    check("uptime kept climbing (no reboot)",
          before and after and after.get("uptime_sec", 0) >= before.get("uptime_sec", 0),
          f"{before.get('uptime_sec') if before else '?'} -> {after.get('uptime_sec') if after else '?'}")

print("\n" + "=" * 60)
print(f"  {ok}/{ok + fail} passed")
print("=" * 60)
sys.exit(1 if fail else 0)
