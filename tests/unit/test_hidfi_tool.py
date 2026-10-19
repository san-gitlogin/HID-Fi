"""tools/hidfi.py - the command line and the MCP server an AI agent uses.

Run against the board model through an in-memory link, so what the agent is
told is what the board would say.
"""
import io
import json
import sys

import pytest

from support import paths
from support.board_sim import BoardSim

pytestmark = pytest.mark.unit

sys.path.insert(0, str(paths.TESTS.parent / "tools"))
import hidfi  # noqa: E402


class SimLink:
    def __init__(self, os_="windows", **kw):
        self.sim = BoardSim(host_os=os_, **kw)
        self.sent = []

    def cmd(self, body, timeout=20.0):
        self.sent.append(body)
        return self.sim.handle(dict(body), "serial")[0]


@pytest.mark.parametrize("argv,body", [
    (["do", "copy"], {"cmd": "do", "a": "copy"}),
    (["open", "System", "Settings"], {"cmd": "open", "app": "System Settings"}),
    (["run", "calc"], {"cmd": "open", "run": "calc"}),
    (["type", "hi", "there"], {"cmd": "type", "text": "hi there"}),
    (["key", "CTRL+SHIFT+T"], {"cmd": "press", "keys": "CTRL+SHIFT+T"}),
    (["steps", "do copy", "wait 100"], {"cmd": "steps", "s": ["do copy", "wait 100"]}),
    (["actions"], {"cmd": "actions"}),
    ([], None), (["do"], None), (["fly"], None),
])
def test_the_command_line_says_one_thing_per_verb(argv, body):
    assert hidfi.body_for(argv) == body


def test_replies_come_back_in_a_few_words():
    link = SimLink("mac")
    assert hidfi.short(link.cmd({"cmd": "do", "a": "copy"})) == "ok copy = GUI+C (mac)"
    assert hidfi.short(link.cmd({"cmd": "do", "a": "nope"})).startswith("refused: Not an action")
    assert hidfi.short(link.cmd({"cmd": "steps", "s": ["do copy", "dance"]})) == "refused: unknown step (step 1)"


def test_the_pin_comes_from_the_environment_only(monkeypatch):
    monkeypatch.setenv("HIDFI_PIN", "4826")
    seen = {}

    class R:
        def __init__(self, req):
            seen["body"] = json.loads(req.data)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return b'{"status":"ok","reply":"done"}'

    monkeypatch.setattr(hidfi.urllib.request, "urlopen", lambda req, timeout: R(req))
    hidfi.Board().cmd({"cmd": "do", "a": "copy"})
    assert seen["body"] == {"cmd": "do", "a": "copy", "token": "4826"}


def _mcp(link, *msgs):
    out = io.StringIO()
    hidfi.serve_mcp(link, io.StringIO("\n".join(json.dumps(m) for m in msgs) + "\n"), out)
    return [json.loads(l) for l in out.getvalue().splitlines()]


def test_an_agent_connects_lists_the_tools_and_does_something():
    link = SimLink("linux")
    init, lst, call = _mcp(link,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "hidfi_do", "arguments": {"a": "lock"}}})
    assert init["result"]["capabilities"] == {"tools": {}}
    tools = {t["name"]: t for t in lst["result"]["tools"]}
    assert set(tools) == {"hidfi_do", "hidfi_open", "hidfi_type", "hidfi_steps", "hidfi_key"}
    enum = tools["hidfi_do"]["inputSchema"]["properties"]["a"]["enum"]
    assert "lock" in enum and "clipboard_history" not in enum, "the choices are what works on THIS computer"
    assert call["result"] == {"content": [{"type": "text", "text": "ok lock = GUI+L (linux)"}], "isError": False}


def test_the_tools_cost_little():
    size = len(json.dumps(hidfi.tools(BoardSim(host_os="windows").handle({"cmd": "actions"})[0]["a"].split())))
    assert size < 3500, f"the tool list is {size} bytes - it is paid for on every turn of an agent's conversation"


def test_a_refusal_is_an_error_the_agent_can_read():
    (r,) = _mcp(SimLink("mac"), {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "hidfi_steps", "arguments": {"s": ["run calc"]}}})
    assert r["result"]["isError"] is True and "macOS" in r["result"]["content"][0]["text"]


def test_an_unreachable_board_is_said_plainly():
    class Down:
        def cmd(self, body, timeout=20.0):
            raise OSError("no route to host")
    (lst, call) = _mcp(Down(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "hidfi_open", "arguments": {"app": "Notes"}}})
    assert lst["result"]["tools"], "the tools are still offered"
    assert call["result"]["isError"] and "could not reach" in call["result"]["content"][0]["text"]


def test_unknown_methods_and_bad_json():
    out = io.StringIO()
    hidfi.serve_mcp(SimLink(), io.StringIO('not json\n{"jsonrpc":"2.0","id":9,"method":"resources/list"}\n'), out)
    a, b = [json.loads(l) for l in out.getvalue().splitlines()]
    assert a["error"]["code"] == -32700 and b["error"]["code"] == -32601
