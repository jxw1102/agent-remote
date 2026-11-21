"""Live TUI frame stream: frames are pushed when the pane changes, a key
wakes the stream, and one capture is one tmux call.

Uses a real tmux pane (running `cat`) on a private socket; skipped when tmux
is not installed.

Run:  python3 tests/tui_stream_test.py
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted import live_tui  # noqa: E402
from agentremoted.live_tui import (  # noqa: E402
    capture_session, send_to_session, serve_stream,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("live TUI stream")

TMUX = shutil.which("tmux")
if not TMUX:
    print("  (tmux not installed — skipped)")
    sys.exit(0)

SOCK = "artest-%d" % os.getpid()
PANE = "stream-test"


class Mgr:
    """The slice of an interactive manager that live_tui uses."""

    def __init__(self):
        self.calls = 0
        self._lock = threading.Lock()
        self._tuis = {}

    def attach(self):
        tui = type("T", (), {"session_id": "s1", "name": PANE, "job": None})()
        self._tuis = {PANE: tui}

    def _tmux(self, *args, capture=False, input_bytes=None):
        self.calls += 1
        return subprocess.run(
            [TMUX, "-L", SOCK] + list(args), input=input_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=15)

    def _tmux_alive(self, name):
        return self._tmux("has-session", "-t", name).returncode == 0

    def _pane_text(self, name, *, ansi=False):
        args = ["capture-pane", "-p"] + (["-e"] if ansi else []) + ["-t", name]
        return self._tmux(*args, capture=True).stdout.decode("utf-8", "replace")


class Handler:
    """Collects what the stream writes, with the time it was written."""

    def __init__(self):
        self.frames = []
        self.keepalives = 0
        self.closed = False
        self.wfile = self

    def send_response(self, code): self.code = code
    def send_header(self, k, v): pass
    def end_headers(self): pass
    def flush(self): pass

    def write(self, data):
        if self.closed:
            raise BrokenPipeError()
        text = data.decode("utf-8")
        if text.startswith("data: "):
            self.frames.append((time.monotonic(), json.loads(text[6:])))
        else:
            self.keepalives += 1

    def wait_for(self, pred, timeout=3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for at, frame in list(self.frames):
                if pred(frame):
                    return at
            time.sleep(0.005)
        return None


m = Mgr()
m._tmux("new-session", "-d", "-s", PANE, "-x", "100", "-y", "30", "cat")
try:
    time.sleep(0.3)

    # --- no TUI yet: the stream says so, then picks the pane up by itself ---
    h = Handler()
    t = threading.Thread(target=serve_stream,
                         args=(h, lambda: capture_session(m, "s1", ansi=True)),
                         kwargs={"session_id": "s1"},
                         daemon=True)
    t.start()
    check("first frame arrives at once",
          h.wait_for(lambda f: True, 0.5) is not None)
    check("it reports no TUI", h.frames[0][1]["attached"] is False,
          h.frames[0][1])
    m.attach()
    at = h.wait_for(lambda f: f["attached"], 2.0)
    check("a TUI that appears is picked up without reconnecting",
          at is not None)

    # --- one look is one tmux call ---
    before = m.calls
    capture_session(m, "s1")
    check("a frame costs one tmux call", m.calls - before == 1,
          m.calls - before)

    # --- a still pane sends nothing ---
    time.sleep(0.6)
    n = len(h.frames)
    time.sleep(0.8)
    check("a still pane sends no frames", len(h.frames) == n,
          "%d new" % (len(h.frames) - n))

    # --- typing is echoed quickly, even from the idle rate ---
    old_fast = live_tui.STREAM_FAST_FOR_S, live_tui.STREAM_WARM_FOR_S
    live_tui.STREAM_FAST_FOR_S = live_tui.STREAM_WARM_FOR_S = 0.0   # idle now
    time.sleep(0.5)
    sent = time.monotonic()
    err = send_to_session(m, "s1", text="hello-stream")
    check("text is accepted", err == "", err)
    at = h.wait_for(lambda f: "hello-stream" in f.get("text", ""), 2.0)
    check("the echo is pushed", at is not None)
    if at is not None:
        lag = at - sent
        check("within 250 ms of the key, not the idle interval", lag < 0.25,
              "%.0f ms" % (lag * 1000))
        print("      echo lag %.0f ms" % (lag * 1000))
    live_tui.STREAM_FAST_FOR_S, live_tui.STREAM_WARM_FOR_S = old_fast

    # --- keys keep their order ---
    for ch in "abcdef":
        send_to_session(m, "s1", text=ch)
    send_to_session(m, "s1", keys=["BSpace"])
    at = h.wait_for(lambda f: "hello-streamabcde" in f.get("text", "")
                    and "abcdef" not in f.get("text", ""), 2.0)
    check("typed text and a key arrive in order", at is not None,
          h.frames[-1][1].get("text", "")[-60:])

    # --- a dead pane: reported, and a key says so without a pre-check ---
    before = m.calls
    send_to_session(m, "s1", keys=["Left"])
    check("a key is one tmux call", m.calls - before == 1, m.calls - before)
    m._tmux("kill-session", "-t", PANE)
    at = h.wait_for(lambda f: f.get("error") == "the host TUI has exited", 3.0)
    check("the stream reports the TUI exiting", at is not None)
    check("a key to a dead pane is refused",
          send_to_session(m, "s1", keys=["Enter"]) == "the host TUI has exited")

    # --- the stream ends when the client is gone ---
    h.closed = True
    m._tuis = {}          # change the frame so the stream has to write
    t.join(3.0)
    check("the stream thread ends when the client leaves", not t.is_alive())
finally:
    subprocess.run([TMUX, "-L", SOCK, "kill-server"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# --- how often the pane is looked at ---
from agentremoted import live_tui as _lt  # noqa: E402

check("fast only just after a key into this pane",
      _lt._stream_interval(True, 0.5, 0.5) == _lt.STREAM_FAST_S)
check("a pane that redraws by itself is watched at the warm rate",
      _lt._stream_interval(True, 60.0, 0.1) == _lt.STREAM_WARM_S)
check("a still pane at the idle rate",
      _lt._stream_interval(True, 60.0, 60.0) == _lt.STREAM_IDLE_S)
before_a, before_b = _lt._wake_gens.get("a", 0), _lt._wake_gens.get("b", 0)
_lt.poke("a")
check("a key wakes its own session's streams only",
      _lt._wake_gens.get("a", 0) == before_a + 1 and _lt._wake_gens.get("b", 0) == before_b)

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("all ok")
