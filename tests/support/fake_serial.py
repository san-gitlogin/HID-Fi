"""A simulated board on a real serial device (a pseudo-terminal), for testing the
tools that talk to a board over its COM cable - the recovery tool, the flashers'
read-back - without a board and without risking anyone's settings.

    with FakeSerialBoard(BoardSim(pin="1234")) as fb:
        run_tool("--port", fb.path)        # the tool opens fb.path like any COM port
        assert fb.sim.pin == ...

Each line written to it is one JSON command, answered by the BoardSim exactly as
the firmware answers serial (transport "serial": no PIN gate, USB-only commands
allowed). `drop` names commands to treat as unknown, to imitate older firmware.
"""
import json
import os
import pty
import select
import threading
import tty


class FakeSerialBoard:
    def __init__(self, sim, drop=()):
        self.sim, self.drop = sim, set(drop)
        self.master, self.slave = pty.openpty()
        tty.setraw(self.slave)
        self.path = os.ttyname(self.slave)
        self.seen = []
        self._stop = False
        self._t = threading.Thread(target=self._serve, daemon=True)
        self._t.start()

    def _serve(self):
        buf = b""
        while not self._stop:
            # Poll, never block: closing a descriptor another thread is blocked
            # reading never returns on macOS.
            try:
                ready, _, _ = select.select([self.master], [], [], 0.05)
                if not ready:
                    continue
                chunk = os.read(self.master, 4096)
            except (OSError, ValueError):
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    body = json.loads(line)
                except ValueError:
                    self._send({"status": "error", "message": "Invalid JSON"})
                    continue
                self.seen.append(body)
                if body.get("cmd") in self.drop:
                    self._send({"status": "error", "message": "Unknown command", "cmd": body.get("cmd")})
                    continue
                for r in self.sim.handle(body, "serial"):
                    self._send(r)

    def _send(self, obj):
        os.write(self.master, (json.dumps(obj) + "\n").encode())

    def close(self):
        self._stop = True
        self._t.join(timeout=1)
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
