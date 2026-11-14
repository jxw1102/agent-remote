"""A "/rewind N" line sent into a Codex Live TUI is the daemon's rewind.

Codex's own TUI has no /rewind: typed into the pane it answers
"Unrecognized command '/rewind'" (2026-11-10). The Live TUI line box on
every client posts {text, keys:["Enter"]} to /tui/keys, so the daemon turns
that one line into the same job the composer's /rewind starts. Harnesses
whose TUI has its own /rewind (Claude, Grok) still get the keystrokes.

Run:  python3 tests/tui_rewind_line_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.server import ApiHandler  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Runner:
    def __init__(self, native):
        self.tui_native_rewind = native
        self.typed = []

    def rewind_session(self, sid, steps):
        return steps, ""

    def capture_tui(self, sid, ansi=False):
        return {"attached": True}

    def send_tui_keys(self, sid, keys=None, text=""):
        self.typed.append((tuple(keys or ()), text))
        return ""


def handler(runner):
    h = object.__new__(ApiHandler)
    h.store = None
    h.bundles = {}
    h.runner = runner
    h.sent = []
    h.continued = []
    h._find_session = lambda sid: (None, None, None)
    h._session_in_scope = lambda s: True
    h._send_json = lambda payload, status=200: h.sent.append((status, payload))
    h._error = lambda status, msg: h.sent.append((status, msg))
    h._handle_continue = lambda sid, body: h.continued.append((sid, body))
    return h


def main():
    print("codex TUI:")
    r = Runner(native=False)
    h = handler(r)
    h._handle_tui_keys("01a1b4c3", {"text": "/rewind 6", "keys": ["Enter"]})
    check("a /rewind line becomes the daemon rewind",
          h.continued == [("01a1b4c3", {"prompt": "/rewind 6"})], h.continued)
    check("and is not typed into the pane", r.typed == [], r.typed)

    h = handler(r)
    h._handle_tui_keys("01a1b4c3", {"text": " /rewind ", "keys": ["Enter"]})
    check("bare /rewind too", h.continued == [("01a1b4c3", {"prompt": "/rewind"})],
          h.continued)

    r = Runner(native=False)
    h = handler(r)
    h._handle_tui_keys("01a1b4c3", {"text": "/rewind 6"})
    check("text without Enter is just typing", h.continued == []
          and r.typed == [((), "/rewind 6")], (h.continued, r.typed))

    r = Runner(native=False)
    h = handler(r)
    h._handle_tui_keys("01a1b4c3", {"text": "why /rewind 6", "keys": ["Enter"]})
    check("a prompt that mentions it is typed", h.continued == []
          and len(r.typed) == 1, (h.continued, r.typed))

    print("a harness with its own /rewind:")
    r = Runner(native=True)
    h = handler(r)
    h._handle_tui_keys("abc", {"text": "/rewind", "keys": ["Enter"]})
    check("keystrokes go to its TUI", h.continued == []
          and r.typed == [(("Enter",), "/rewind")], (h.continued, r.typed))

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("all tui rewind line checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
