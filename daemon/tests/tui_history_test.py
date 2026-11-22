"""Live TUI scrollback: the lines that scrolled off the top of the pane.

Uses a real tmux pane on a private socket; skipped when tmux is missing.

Run:  python3 tests/tui_history_test.py
"""

import os
import shutil
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.live_tui import capture_session, history_session  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("live TUI scrollback")
TMUX = shutil.which("tmux")
if not TMUX:
    print("  (tmux not installed — skipped)")
    sys.exit(0)

SOCK = "arhist-%d" % os.getpid()
PANE = "hist-test"


class Mgr:
    def __init__(self):
        self._lock = threading.Lock()
        tui = type("T", (), {"session_id": "s1", "name": PANE, "job": None})()
        self._tuis = {PANE: tui}

    def _tmux(self, *args, capture=False, input_bytes=None):
        return subprocess.run([TMUX, "-L", SOCK] + list(args), input=input_bytes,
                              stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                              stderr=subprocess.PIPE, timeout=15)

    def _tmux_alive(self, name):
        return self._tmux("has-session", "-t", name).returncode == 0

    def _pane_text(self, name, *, ansi=False):
        out = self._tmux("capture-pane", "-p", "-t", name, capture=True)
        return out.stdout.decode()


mgr = Mgr()
try:
    # 300 numbered lines in a 40-row pane: about 260 scroll into history.
    subprocess.run([TMUX, "-L", SOCK, "new-session", "-d", "-s", PANE, "-x", "80", "-y", "40",
                    "sh", "-c", "seq 1 300; sleep 4; seq 301 320; sleep 30"], check=True)
    time.sleep(0.8)
    screen = capture_session(mgr, "s1")["text"].strip().splitlines()
    top = int(screen[0])
    h = history_session(mgr, "s1", lines=50)
    rows = h["text"].splitlines()
    check("attached", h["attached"], h)
    check("50 lines asked, 50 given", h["lines"] == 50 and len(rows) == 50, h["lines"])
    check("they end just above the screen", rows and int(rows[-1]) == top - 1,
          (rows[-1:] if rows else None, top))
    check("in order, oldest first", [int(x) for x in rows] == list(range(top - 50, top)))
    check("more is older", h["more"] is True and h["history"] == top - 1, h)
    allh = history_session(mgr, "s1", lines=5000)
    check("asking for more than there is gives all of it",
          allh["lines"] == top - 1 and allh["more"] is False, (allh["lines"], top))
    check("starting at line 1", allh["text"].splitlines()[0] == "1")
    # Paging: the next page, older, joins the first with no gap or overlap.
    p2 = history_session(mgr, "s1", lines=50, before=h["next"], at=h["history"])
    rows2 = p2["text"].splitlines()
    check("page 2 is the 50 before page 1",
          [int(x) for x in rows2] == list(range(top - 100, top - 50)), rows2[:2])
    check("page 2 says where page 3 starts", p2["before"] == 50 and p2["next"] == 100, p2)
    last = history_session(mgr, "s1", lines=5000, before=p2["next"], at=p2["history"])
    lrows = last["text"].splitlines()
    check("the last page ends the history",
          last["more"] is False and lrows[0] == "1" and int(lrows[-1]) == top - 101,
          (last["more"], lrows[:1], lrows[-1:]))
    past = history_session(mgr, "s1", lines=50, before=100000)
    check("a page past the start is empty", past["lines"] == 0 and past["more"] is False, past)
    # Lines that scroll in while the reader pages do not shift the next page.
    end = time.time() + 10
    while time.time() < end:
        if history_session(mgr, "s1", lines=1)["history"] > h["history"]:
            break
        time.sleep(0.2)
    grown = history_session(mgr, "s1", lines=1)["history"] - h["history"]
    p2b = history_session(mgr, "s1", lines=50, before=h["next"], at=h["history"])
    check("20 more lines scrolled in", grown == 20, grown)
    check("page 2 is the same after they did",
          [int(x) for x in p2b["text"].splitlines()] == list(range(top - 100, top - 50))
          and p2b["before"] == 50 + grown, p2b["before"])
    gone = history_session(mgr, "nope", lines=10)
    check("no TUI: not attached", gone["attached"] is False and gone["text"] == "")
finally:
    subprocess.run([TMUX, "-L", SOCK, "kill-server"], stderr=subprocess.DEVNULL)

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all ok")
