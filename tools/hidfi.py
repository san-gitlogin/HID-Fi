"""Drive a HID-Fi board from a script or an AI agent, in as few words as possible.

    python3 tools/hidfi.py do copy
    python3 tools/hidfi.py open Calculator
    python3 tools/hidfi.py run https://example.com        (Windows, Linux)
    python3 tools/hidfi.py type "Hello"
    python3 tools/hidfi.py steps "open Notepad" "wait 1500" "type Hi" "do save"
    python3 tools/hidfi.py actions                        (what works on this computer)
    python3 tools/hidfi.py status
    python3 tools/hidfi.py mcp                            (an MCP server on stdin/stdout)

Where the board is:
    HIDFI_HOST=192.168.4.1   its own WiFi (the default), or its home-network address
    HIDFI_PORT=/dev/cu.usb...   use the USB COM cable instead (needs pyserial)
    HIDFI_PIN=1234           the access PIN, if one is set. Read from the
                             environment only: never pass it on a command line,
                             where other users and shell history can see it.

The board picks the keys for the computer it is plugged into, so an agent says
"copy" and never has to know whether that is Cmd+C or Ctrl+C. No dependencies
beyond the standard library (pyserial only for HIDFI_PORT).

Exit codes: 0 done | 1 the board refused (its message is printed) | 2 usage |
3 could not reach the board.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"


# ---- talking to the board ---------------------------------------------------------------------
class Board:
    def __init__(self):
        self.host = os.environ.get("HIDFI_HOST", "192.168.4.1")
        self.port = os.environ.get("HIDFI_PORT", "")
        self.pin = os.environ.get("HIDFI_PIN", "")
        self.ser = None

    def cmd(self, body, timeout=20.0):
        if self.port:
            return self._serial(body, timeout)
        if self.pin:
            body = dict(body, token=self.pin)
        req = urllib.request.Request(f"http://{self.host}/api/command", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")

    def _serial(self, body, timeout):
        if self.ser is None:
            import serial
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = self.port, 115200, 0.3
            s.dtr = s.rts = False                  # before open: they drive the board's reset
            s.open()
            time.sleep(float(os.environ.get("HIDFI_SETTLE", "1.5")))
            s.reset_input_buffer()
            self.ser = s
        self.ser.write((json.dumps(body) + "\n").encode())
        end = time.time() + timeout
        while time.time() < end:
            line = self.ser.readline().decode(errors="ignore").strip()
            if line.startswith("{"):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if "status" in d and not d.get("event"):
                    return d
        raise TimeoutError(f"no answer to {body.get('cmd')}")


def short(r):
    """A reply in the fewest tokens that still say what happened."""
    if r.get("status") != "ok":
        return f"refused: {r.get('message') or r.get('reply')}" + (f" (step {r['at']})" if "at" in r else "")
    rep = r.get("reply")
    if rep == "done":
        return f"ok {r.get('a')} = {r.get('keys')} ({r.get('os')})"
    if rep == "opened":
        return f"ok opened via {r.get('via')} ({r.get('os')})"
    if rep == "steps_done":
        return f"ok {r.get('done')} steps ({r.get('os')})"
    if rep == "typed":
        return f"ok typed {r.get('length')} bytes"
    return "ok " + json.dumps({k: v for k, v in r.items() if k not in ("status",)}, separators=(",", ":"))


# ---- the command line ---------------------------------------------------------------------------
def body_for(argv):
    if not argv:
        return None
    verb, rest = argv[0], argv[1:]
    if verb == "do" and len(rest) == 1:
        return {"cmd": "do", "a": rest[0]}
    if verb == "open" and rest:
        return {"cmd": "open", "app": " ".join(rest)}
    if verb == "run" and rest:
        return {"cmd": "open", "run": " ".join(rest)}
    if verb == "type" and rest:
        return {"cmd": "type", "text": " ".join(rest)}
    if verb == "key" and len(rest) == 1:
        return {"cmd": "press", "keys": rest[0]}
    if verb == "steps" and rest:
        return {"cmd": "steps", "s": rest}
    if verb == "actions":
        return {"cmd": "actions"}
    if verb == "status":
        return {"cmd": "status"}
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["mcp"]:
        return serve_mcp(Board())
    body = body_for(argv)
    if body is None:
        print(__doc__.strip().split("\n\n")[1])
        return 2
    try:
        r = Board().cmd(body)
    except (OSError, urllib.error.URLError, TimeoutError) as e:
        print(f"could not reach the board: {e}")
        return 3
    if body["cmd"] == "actions" and r.get("status") == "ok":
        print(r["a"])
        print("also: " + r.get("also", ""))
    elif body["cmd"] == "status":
        print(json.dumps(r, indent=1))
    else:
        print(short(r))
    return 0 if r.get("status") == "ok" else 1


# ---- MCP: the same five verbs as tools, for an AI agent -----------------------------------------
# Small on purpose: every tool description is paid for on every turn of the
# agent's conversation. The action list becomes an enum, so a model chooses from
# what the board says works on this computer instead of guessing key combos.
def tools(actions):
    s = lambda **p: {"type": "object", "properties": p, "required": list(p), "additionalProperties": False}
    return [
        {"name": "hidfi_do", "description": "Do one thing on the computer HID-Fi is plugged into. "
                                            "The board presses the right keys for its OS.",
         "inputSchema": s(a={"type": "string", "enum": actions} if actions else {"type": "string"})},
        {"name": "hidfi_open", "description": "Open an app by the name the OS's search shows "
                                              "(e.g. Calculator, Terminal, System Settings).",
         "inputSchema": s(app={"type": "string"})},
        {"name": "hidfi_type", "description": "Type text where the cursor is.",
         "inputSchema": s(text={"type": "string"})},
        {"name": "hidfi_steps", "description": "Run up to 32 steps in one call. Each: do NAME | open APP | "
                                               "run LINE | type TEXT | key COMBO | wait MS | click left.",
         "inputSchema": s(s={"type": "array", "items": {"type": "string"}, "maxItems": 32})},
        {"name": "hidfi_key", "description": "Press a raw key combo, e.g. CTRL+SHIFT+T, F5, ENTER. "
                                             "Prefer hidfi_do.",
         "inputSchema": s(keys={"type": "string"})},
    ]


def call(board, name, args):
    body = {"hidfi_do": lambda: {"cmd": "do", "a": args.get("a", "")},
            "hidfi_open": lambda: {"cmd": "open", "app": args.get("app", "")},
            "hidfi_type": lambda: {"cmd": "type", "text": args.get("text", "")},
            "hidfi_steps": lambda: {"cmd": "steps", "s": args.get("s", [])},
            "hidfi_key": lambda: {"cmd": "press", "keys": args.get("keys", "")}}.get(name)
    if body is None:
        return f"unknown tool {name}", True
    try:
        r = board.cmd(body())
    except (OSError, urllib.error.URLError, TimeoutError) as e:
        return f"could not reach the board: {e}", True
    return short(r), r.get("status") != "ok"


def serve_mcp(board, inp=None, out=None):
    """MCP over stdio: one JSON-RPC message per line."""
    inp, out = inp or sys.stdin, out or sys.stdout
    actions = []

    def send(msg):
        out.write(json.dumps(msg, separators=(",", ":")) + "\n")
        out.flush()

    for line in inp:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        mid, method = m.get("id"), m.get("method")
        if mid is None:                       # a notification: nothing to answer
            continue
        if method == "initialize":
            res = {"protocolVersion": m.get("params", {}).get("protocolVersion", PROTOCOL),
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "hidfi", "version": "3.10"}}
        elif method == "tools/list":
            if not actions:
                try:
                    r = board.cmd({"cmd": "actions"})
                    actions = r.get("a", "").split() if r.get("status") == "ok" else []
                except (OSError, urllib.error.URLError, TimeoutError):
                    actions = []              # the tool still works; the model just is not given the list
            res = {"tools": tools(actions)}
        elif method == "tools/call":
            p = m.get("params", {})
            text, err = call(board, p.get("name"), p.get("arguments") or {})
            res = {"content": [{"type": "text", "text": text}], "isError": err}
        elif method == "ping":
            res = {}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no method {method}"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": res})
    return 0


if __name__ == "__main__":
    sys.exit(main())
