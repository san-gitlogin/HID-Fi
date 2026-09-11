"""
ESP32-S3-N16R8 — Phase 3: USB HID Unlock Client (Serial + WiFi)

Sends commands to the ESP32-S3 over serial (primary) or WiFi HTTP (fallback).
The ESP32 then types the password as USB HID keystrokes.

Usage:
    python unlock_client.py                          # auto-detect, run tests
    python unlock_client.py COM6                     # specify port
    python unlock_client.py COM6 --unlock            # interactive unlock (prompts for password)
    python unlock_client.py COM6 --unlock --password "pw"  # scripted unlock (no prompt)
    python unlock_client.py COM6 --unlock --cad      # use Ctrl+Alt+Del before password
    python unlock_client.py COM6 --lock              # lock the PC (Win+L)
    python unlock_client.py COM6 --lock-unlock --password "pw"  # lock, wait, then unlock
    python unlock_client.py COM6 --test-hid          # type 'HID_OK' to verify HID works
    python unlock_client.py COM6 --type "hello"      # type arbitrary text
    python unlock_client.py COM6 --press ENTER       # press a key
    python unlock_client.py COM6 --press CTRL+ALT+DELETE
    python unlock_client.py COM6 -i                  # interactive mode

    WiFi mode (no serial needed):
    python unlock_client.py --wifi 192.168.1.50      # use WiFi API
    python unlock_client.py --wifi 192.168.1.50 --unlock --password "pw"
    python unlock_client.py --wifi 192.168.1.50 --lock
    python unlock_client.py --wifi 192.168.1.50 -i   # interactive over WiFi

    WiFi setup (via serial):
    python unlock_client.py COM6 --wifi-setup --ssid "MyNetwork" --wifi-password "MyPass"

Requirements:
    pip install pyserial requests
"""

import serial
import serial.tools.list_ports
import json
import sys
import time
import getpass

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


BAUD_RATE = 115200
TIMEOUT = 5  # seconds per command (unlock takes longer)
BOOT_WAIT = 4  # ESP32 needs extra time for USB HID enumeration
LOCK_SETTLE = 3  # seconds to wait after Win+L for lock screen to settle
WIFI_TIMEOUT = 5  # HTTP request timeout


def send(transport, cmd_dict, timeout=None):
    """Unified send: routes to serial or WiFi based on transport tuple."""
    ser, esp_ip = transport
    if esp_ip:
        return send_wifi_command(esp_ip, cmd_dict, timeout=timeout or WIFI_TIMEOUT)
    elif ser:
        return send_command(ser, cmd_dict, timeout=timeout)
    else:
        print("ERROR: No transport available")
        return None


def find_esp32_port():
    """Auto-detect the ESP32-S3 CH343 COM port."""
    ports = serial.tools.list_ports.comports()
    
    candidates = []
    for p in ports:
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").lower()
        if any(kw in desc for kw in ["ch343", "ch340", "usb-serial", "usb serial"]):
            candidates.append(p)
        elif "1a86" in hwid:  # WCH vendor ID
            candidates.append(p)
    
    if not candidates:
        print("Available COM ports:")
        for p in ports:
            print(f"  {p.device}: {p.description} [{p.hwid}]")
        return None
    
    if len(candidates) == 1:
        return candidates[0].device
    
    print("Multiple CH343/CH340 ports found:")
    for i, p in enumerate(candidates):
        print(f"  [{i}] {p.device}: {p.description}")
    
    choice = input("Select port number: ").strip()
    try:
        return candidates[int(choice)].device
    except (ValueError, IndexError):
        return candidates[0].device


def send_command(ser, cmd_dict, timeout=None):
    """Send a JSON command over serial and read the JSON response."""
    timeout = timeout or TIMEOUT
    
    # Drain stale data
    while ser.in_waiting:
        ser.readline()
    
    line = json.dumps(cmd_dict) + "\n"
    ser.write(line.encode("utf-8"))
    ser.flush()
    
    # For unlock, we might get multiple JSON lines (started + done)
    responses = []
    start = time.time()
    
    while time.time() - start < timeout:
        if ser.in_waiting:
            raw = ser.readline().decode("utf-8", errors="replace").strip()
            if raw:
                try:
                    resp = json.loads(raw)
                    responses.append(resp)
                    # If we got a final response (not a "started" message), return it
                    reply = resp.get("reply", "")
                    if reply and "_started" not in reply:
                        return resp
                except json.JSONDecodeError:
                    print(f"  [log] {raw}")
        else:
            time.sleep(0.05)
    
    # Return last response if any
    return responses[-1] if responses else None


def send_wifi_command(esp_ip, cmd_dict, timeout=None):
    """Send a JSON command over WiFi HTTP and return the JSON response."""
    if not HAS_REQUESTS:
        print("ERROR: 'requests' module not installed. Run: pip install requests")
        return None
    
    timeout = timeout or WIFI_TIMEOUT
    url = f"http://{esp_ip}/api/command"
    
    try:
        resp = requests.post(url, json=cmd_dict, timeout=timeout)
        return resp.json()
    except requests.exceptions.ConnectionError:
        print(f"  ERROR: Cannot connect to ESP32 at {esp_ip}")
        return None
    except requests.exceptions.Timeout:
        print(f"  ERROR: Request timed out ({timeout}s)")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def wifi_ping(esp_ip):
    """Quick health check over WiFi."""
    if not HAS_REQUESTS:
        return False
    try:
        resp = requests.get(f"http://{esp_ip}/api/ping", timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def wifi_status(esp_ip):
    """Get device status over WiFi."""
    if not HAS_REQUESTS:
        return None
    try:
        resp = requests.get(f"http://{esp_ip}/api/status", timeout=3)
        return resp.json()
    except Exception:
        return None


def drain_boot_messages(ser):
    """Read and display boot messages. Wait for boot JSON event or timeout.
    
    CH343 auto-reset circuit causes ESP32 to reboot on port open regardless of DTR.
    We must wait for the full boot sequence (~3s: ROM → setup → HID init → WiFi AP → boot JSON).
    The boot JSON event (containing "event":"boot") signals setup() is complete.
    """
    time.sleep(0.3)  # brief pause for first ROM bytes to arrive
    
    lines_read = 0
    boot_json_seen = False
    deadline = time.time() + 6.0  # max 6s for full boot (HID enum takes 2s)
    
    while time.time() < deadline:
        if ser.in_waiting:
            raw = ser.readline().decode("utf-8", errors="replace").strip()
            if raw:
                lines_read += 1
                try:
                    msg = json.loads(raw)
                    print(f"  [boot] {json.dumps(msg, indent=2)}")
                    if msg.get("event") == "boot":
                        boot_json_seen = True
                        break  # boot complete — ESP32 is ready
                except json.JSONDecodeError:
                    print(f"  [boot] {raw}")
        else:
            if boot_json_seen:
                break
            # If we've read ROM messages but nothing for 1s, ESP32 may be in setup()
            time.sleep(0.05)
    
    if lines_read == 0:
        print("  ESP32 already running (no boot messages)")
    elif boot_json_seen:
        print(f"  ({lines_read} boot messages, boot event received — ESP32 ready)")
    else:
        print(f"  ({lines_read} boot messages, no boot event — ESP32 may still be starting)")


def run_tests(transport):
    """Run basic connectivity tests. transport is (ser, None) or (None, ip)."""
    ser, esp_ip = transport
    
    tests = [
        ("PING", {"cmd": "ping"}),
        ("STATUS", {"cmd": "status"}),
        ("ECHO", {"cmd": "echo", "data": "Phase 3 test"}),
    ]
    
    mode = f"WiFi ({esp_ip})" if esp_ip else "Serial"
    print("\n" + "=" * 60)
    print(f"Running Phase 3 connectivity tests [{mode}]")
    print("=" * 60)
    
    passed = 0
    for name, cmd in tests:
        print(f"\n--- {name} ---")
        print(f"  Sending: {json.dumps(cmd)}")
        resp = send(transport, cmd)
        if resp:
            print(f"  Response: {json.dumps(resp, indent=2)}")
            if resp.get("status") in ("ok", "error"):
                passed += 1
                # Check HID ready
                if "hid_ready" in resp:
                    hid = resp["hid_ready"]
                    print(f"  HID Ready: {'YES' if hid else 'NO — check USB-OTG pads and USB cable'}")
                if "pc_state" in resp:
                    print(f"  PC State: {resp['pc_state']}")
                print(f"  PASS")
            else:
                print(f"  FAIL")
        else:
            print(f"  FAIL — no response")
    
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{len(tests)} passed")
    print(f"{'=' * 60}")
    return passed == len(tests)


def do_unlock(transport, use_cad=False, password=None):
    """Unlock the PC. Returns True on success or already_unlocked, False on failure."""
    print("\n" + "=" * 60)
    print("PC UNLOCK via USB HID")
    print("=" * 60)
    
    if use_cad:
        print("Mode: Ctrl+Alt+Delete → password → Enter")
    else:
        print("Mode: Space (wake) → password → Enter")
    
    if not password:
        password = getpass.getpass("Enter PC password: ")
    
    if not password:
        print("No password entered. Aborting.")
        return False
    
    cmd = {
        "cmd": "unlock",
        "password": password,
        "ctrl_alt_del": use_cad
    }
    
    print("\nSending unlock command...")
    resp = send(transport, cmd, timeout=10)
    
    if not resp:
        print("ERROR: No response — check connection")
        return False
    
    print(f"Response: {json.dumps(resp, indent=2)}")
    reply = resp.get("reply", "")
    pc_state = resp.get("pc_state", "unknown")
    
    if reply == "unlock_done":
        print(f"\nUnlock sequence completed successfully! [pc_state={pc_state}]")
        return True
    elif reply == "already_unlocked":
        print(f"\nPC is already unlocked — no action needed. [pc_state={pc_state}]")
        return True
    else:
        status = resp.get("status", "unknown")
        message = resp.get("message", reply or "unknown error")
        print(f"\nUnlock FAILED: {message} [status={status}, pc_state={pc_state}]")
        return False


def do_lock(transport):
    """Lock the PC with dedicated lock command (Win+L with explicit timing)."""
    print("\n" + "=" * 60)
    print("LOCKING PC (Win+L)")
    print("=" * 60)
    resp = send(transport, {"cmd": "lock"})
    
    if not resp:
        print("ERROR: No response \u2014 check connection")
        return False
    
    print(f"Response: {json.dumps(resp, indent=2)}")
    reply = resp.get("reply", "")
    pc_state = resp.get("pc_state", "unknown")
    
    if reply == "locked":
        print(f"PC locked successfully. [pc_state={pc_state}]")
        return True
    elif reply == "already_locked":
        print(f"PC is already locked \u2014 no action needed. [pc_state={pc_state}]")
        return True
    else:
        status = resp.get("status", "unknown")
        message = resp.get("message", reply or "unknown error")
        print(f"Lock FAILED: {message} [status={status}, pc_state={pc_state}]")
        return False


def do_lock_unlock(transport, use_cad=False, password=None):
    """Lock the PC, wait, then unlock it."""
    print("\n" + "=" * 60)
    print("LOCK → UNLOCK TEST")
    print("=" * 60)

    # Step 1: Lock
    print("\n[1/2] Locking PC with Win+L...")
    resp = send(transport, {"cmd": "lock", "force": True})
    already_locked = False
    if resp:
        reply = resp.get("reply", "")
        pc_state = resp.get("pc_state", "unknown")
        print(f"  Response: {reply} [pc_state={pc_state}]")
        if reply == "already_locked":
            print("  PC was already locked — skipping settle wait.")
            already_locked = True
        elif reply == "locked":
            print("  Lock command sent successfully.")
        else:
            message = resp.get("message", "unknown")
            print(f"  WARNING: Unexpected response: {message}")
    else:
        print("  WARNING: No response to lock command")

    # Step 2: Wait for lock screen (skip if already locked)
    if not already_locked:
        print(f"\n[...] Waiting {LOCK_SETTLE}s for lock screen to settle...")
        time.sleep(LOCK_SETTLE)
    else:
        print("\n[...] Skipping settle wait (already locked)")
        time.sleep(0.5)  # brief pause

    # Step 3: Unlock
    print("\n[2/2] Unlocking PC...")
    result = do_unlock(transport, use_cad=use_cad, password=password)
    
    if result:
        print("\nLock → Unlock test PASSED")
    else:
        print("\nLock → Unlock test FAILED")
    return result


def do_type(transport, text):
    """Type arbitrary text via HID."""
    resp = send(transport, {"cmd": "type", "text": text})
    if resp:
        print(f"Response: {json.dumps(resp, indent=2)}")
    else:
        print("No response")


def do_press(transport, keys):
    """Press a key combination via HID."""
    resp = send(transport, {"cmd": "press", "keys": keys})
    if resp:
        print(f"Response: {json.dumps(resp, indent=2)}")
    else:
        print("No response")


def interactive_mode(transport):
    """Interactive REPL."""
    ser, esp_ip = transport
    mode = f"WiFi ({esp_ip})" if esp_ip else "Serial"
    
    print("\n" + "=" * 60)
    print(f"Interactive mode [{mode}] — commands:")
    print("  ping                    → ping ESP32")
    print("  status                  → device status")
    print("  type <text>             → type text via HID")
    print("  press <KEY>             → press key (e.g., ENTER, CTRL+ALT+DELETE)")
    print("  lock                    → lock PC (Win+L)")
    print("  unlock                  → unlock PC (prompts for password)")
    print("  unlock --cad            → unlock with Ctrl+Alt+Del")
    print("  test_hid                → type HID_OK to verify HID")
    print("  --- mouse / wiggle / led ---")
    print("  wiggle [amp] [ms] [sec] → interval wiggle (amp px, every ms, for sec)")
    print("  wiggle single [amp]     → single net-zero jiggle")
    print("  wiggle timed [amp] [ms] [sec] → wiggle for N seconds then auto-stop")
    print("  stop                    → stop a running wiggle")
    print("  move <dx> <dy>          → move mouse (relative)")
    print("  click [left|right|middle] → mouse click")
    print("  scroll <amount>         → scroll wheel (+down/-up)")
    print("  led <color|off>         → set LED (red/green/blue/.../off)")
    print("  led <r> <g> <b>         → set LED by RGB (0-255)")
    print("  led_blink [color]       → blink LED 3x")
    print("  led_status              → LED status")
    print("  --- other ---")
    print("  wifi_status             → WiFi connection info")
    print("  echo <text>             → echo test")
    print("  quit                    → exit")
    print("=" * 60)
    
    while True:
        try:
            raw_input = input("\nesp32> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        
        if not raw_input:
            continue
        
        if raw_input.lower() in ("quit", "exit", "q"):
            break
        
        resp = None
        if raw_input == "ping":
            resp = send(transport, {"cmd": "ping"})
        elif raw_input == "status":
            resp = send(transport, {"cmd": "status"})
        elif raw_input == "lock":
            do_lock(transport)
            continue
        elif raw_input == "test_hid":
            resp = send(transport, {"cmd": "test_hid"})
        elif raw_input == "wifi_status":
            resp = send(transport, {"cmd": "wifi_status"})
        elif raw_input.startswith("type "):
            text = raw_input[5:]
            resp = send(transport, {"cmd": "type", "text": text})
        elif raw_input.startswith("press "):
            keys = raw_input[6:].strip().upper()
            resp = send(transport, {"cmd": "press", "keys": keys})
        elif raw_input.startswith("unlock"):
            use_cad = "--cad" in raw_input
            do_unlock(transport, use_cad=use_cad)
            continue
        elif raw_input.startswith("echo "):
            data = raw_input[5:]
            resp = send(transport, {"cmd": "echo", "data": data})
        elif raw_input == "stop" or raw_input == "wiggle_stop":
            resp = send(transport, {"cmd": "wiggle_stop"})
        elif raw_input.startswith("wiggle"):
            parts = raw_input.split()
            # wiggle                      → interval, defaults
            # wiggle single [amp]         → single
            # wiggle [amp] [ms] [sec]     → interval (sec ignored) or timed if 'timed'
            # wiggle timed [amp] [ms] [sec]
            mode = "interval"
            args = parts[1:]
            if args and args[0] in ("single", "interval", "timed"):
                mode = args[0]
                args = args[1:]
            cmd = {"cmd": "wiggle", "mode": mode}
            if len(args) >= 1:
                cmd["amplitude"] = int(args[0])
            if len(args) >= 2:
                cmd["interval_ms"] = int(args[1])
            if len(args) >= 3:
                cmd["duration_s"] = int(args[2])
            resp = send(transport, cmd)
        elif raw_input.startswith("move "):
            parts = raw_input.split()
            dx = int(parts[1]) if len(parts) > 1 else 0
            dy = int(parts[2]) if len(parts) > 2 else 0
            resp = send(transport, {"cmd": "mouse_move", "dx": dx, "dy": dy})
        elif raw_input.startswith("click"):
            parts = raw_input.split()
            button = parts[1] if len(parts) > 1 else "left"
            resp = send(transport, {"cmd": "mouse_click", "button": button})
        elif raw_input.startswith("scroll "):
            amount = int(raw_input.split()[1])
            resp = send(transport, {"cmd": "mouse_scroll", "amount": amount})
        elif raw_input == "led_status":
            resp = send(transport, {"cmd": "led_status"})
        elif raw_input.startswith("led_blink"):
            parts = raw_input.split()
            cmd = {"cmd": "led_blink", "times": 3}
            if len(parts) > 1:
                cmd["color"] = parts[1]
            resp = send(transport, cmd)
        elif raw_input.startswith("led"):
            parts = raw_input.split()
            if len(parts) == 4 and all(p.lstrip("-").isdigit() for p in parts[1:]):
                resp = send(transport, {"cmd": "led_set", "r": int(parts[1]), "g": int(parts[2]), "b": int(parts[3])})
            elif len(parts) >= 2:
                resp = send(transport, {"cmd": "led_set", "color": parts[1]})
            else:
                print("Usage: led <color|off>  OR  led <r> <g> <b>")
                continue
        else:
            # Try raw JSON
            try:
                cmd = json.loads(raw_input)
                resp = send(transport, cmd)
            except json.JSONDecodeError:
                print("Unknown command. Type 'quit' to exit or send valid JSON.")
                continue
        
        if resp:
            print(f"  ← {json.dumps(resp, indent=2)}")
        elif resp is not None:
            print("  ← (no response)")


def main():
    port = None
    esp_ip = None
    do_unlock_flag = "--unlock" in sys.argv
    do_lock_flag = "--lock" in sys.argv and "--lock-unlock" not in sys.argv
    do_lock_unlock_flag = "--lock-unlock" in sys.argv
    use_cad = "--cad" in sys.argv
    interactive = "-i" in sys.argv or "--interactive" in sys.argv
    test_hid = "--test-hid" in sys.argv
    wifi_setup = "--wifi-setup" in sys.argv
    type_text = None
    press_keys = None
    password = None
    wifi_ssid = None
    wifi_password = None
    
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg.startswith("COM") or arg.startswith("/dev/"):
            port = arg
        elif arg == "--wifi" and i + 1 < len(sys.argv):
            esp_ip = sys.argv[i + 1]
        elif arg == "--type" and i + 1 < len(sys.argv):
            type_text = sys.argv[i + 1]
        elif arg == "--press" and i + 1 < len(sys.argv):
            press_keys = sys.argv[i + 1]
        elif arg == "--password" and i + 1 < len(sys.argv):
            password = sys.argv[i + 1]
        elif arg == "--ssid" and i + 1 < len(sys.argv):
            wifi_ssid = sys.argv[i + 1]
        elif arg == "--wifi-password" and i + 1 < len(sys.argv):
            wifi_password = sys.argv[i + 1]
    
    # ======== WiFi-only mode ========
    if esp_ip and not port:
        if not HAS_REQUESTS:
            print("ERROR: WiFi mode requires 'requests'. Run: pip install requests")
            sys.exit(1)
        
        print(f"Connecting to ESP32 via WiFi at {esp_ip}...")
        if not wifi_ping(esp_ip):
            print(f"ERROR: ESP32 not reachable at {esp_ip}")
            print("Check: Is WiFi configured on ESP32? Is the IP correct?")
            sys.exit(1)
        
        status = wifi_status(esp_ip)
        if status:
            print(f"Connected! Firmware: {status.get('firmware', '?')}, HID: {status.get('hid_ready', '?')}")
        
        transport = (None, esp_ip)
        
        if test_hid:
            resp = send(transport, {"cmd": "test_hid"})
            if resp:
                print(f"Response: {json.dumps(resp, indent=2)}")
        elif do_lock_unlock_flag:
            do_lock_unlock(transport, use_cad=use_cad, password=password)
        elif do_lock_flag:
            do_lock(transport)
        elif do_unlock_flag:
            do_unlock(transport, use_cad=use_cad, password=password)
        elif type_text:
            do_type(transport, type_text)
        elif press_keys:
            do_press(transport, press_keys)
        elif interactive:
            interactive_mode(transport)
        else:
            run_tests(transport)
        
        print("Done.")
        return
    
    # ======== Serial mode (primary) ========
    if not port:
        port = find_esp32_port()
        if not port:
            print("\nERROR: No ESP32-S3 COM port found.")
            print("Check: Device Manager → Ports → CH343")
            print("Tip: Use --wifi <ip> to connect over WiFi instead.")
            sys.exit(1)
    
    print(f"Connecting to {port} at {BAUD_RATE} baud...")
    
    try:
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = BAUD_RATE
        ser.timeout = TIMEOUT
        ser.dtr = False   # prevent DTR toggle from resetting ESP32
        ser.rts = False   # prevent RTS toggle from resetting ESP32
        ser.open()
    except serial.SerialException as e:
        print(f"ERROR: Cannot open {port}: {e}")
        sys.exit(1)
    
    print(f"Connected to {port}")
    drain_boot_messages(ser)
    
    transport = (ser, None)
    
    # WiFi setup command (serial only — configures the ESP32's WiFi)
    if wifi_setup:
        if not wifi_ssid:
            wifi_ssid = input("WiFi SSID: ").strip()
        if not wifi_password:
            wifi_password = getpass.getpass("WiFi password: ")
        
        print(f"\nConfiguring WiFi: {wifi_ssid}")
        resp = send_command(ser, {
            "cmd": "wifi_configure",
            "ssid": wifi_ssid,
            "password": wifi_password
        }, timeout=20)
        if resp:
            print(f"Response: {json.dumps(resp, indent=2)}")
            if resp.get("connected"):
                print(f"\nWiFi configured! ESP32 IP: {resp.get('ip')}")
                print(f"You can now use: python unlock_client.py --wifi {resp.get('ip')} --unlock")
        else:
            print("No response")
        ser.close()
        return
    
    # Dispatch
    if test_hid:
        print("\n" + "=" * 60)
        print("HID TEST — open a text editor and watch for 'HID_OK'")
        print("=" * 60)
        resp = send_command(ser, {"cmd": "test_hid"})
        if resp:
            print(f"Response: {json.dumps(resp, indent=2)}")
        else:
            print("No response — HID may not be working")
    elif do_lock_unlock_flag:
        do_lock_unlock(transport, use_cad=use_cad, password=password)
    elif do_lock_flag:
        do_lock(transport)
    elif do_unlock_flag:
        do_unlock(transport, use_cad=use_cad, password=password)
    elif type_text:
        do_type(transport, type_text)
    elif press_keys:
        do_press(transport, press_keys)
    elif interactive:
        interactive_mode(transport)
    else:
        # Default: run tests, then offer interactive
        run_tests(transport)
        print("\nSwitch to interactive mode? (y/n): ", end="")
        if input().strip().lower() == "y":
            interactive_mode(transport)
    
    ser.close()
    print("Done.")


if __name__ == "__main__":
    main()
