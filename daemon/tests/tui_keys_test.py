"""Live TUI key vocabulary: modifier-composed names → tmux send-keys tokens.

Run:  python3 tests/tui_keys_test.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.live_tui import (  # noqa: E402
    TEXT_SETTLE_S, TUI_COLS, TUI_ROWS, frame_payload, map_key, map_keys,
    send_to_session,
)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def eq(name, got, want):
    check(name, got == want, "got %r want %r" % (got, want))


print("live TUI keys")

# --- the vocabulary every shipped client already sends (must not regress) ---
for name, want in [
    ("Escape", "Escape"), ("esc", "Escape"), ("Enter", "Enter"),
    ("Backspace", "BSpace"), ("Tab", "Tab"), ("Delete", "DC"),
    ("Up", "Up"), ("Down", "Down"), ("Left", "Left"), ("Right", "Right"),
    ("Home", "Home"), ("End", "End"), ("PageUp", "PPage"),
    ("PageDown", "NPage"), ("Ctrl+C", "C-c"), ("c-d", "C-d"),
]:
    eq("legacy %s" % name, map_key(name), want)

# --- new: every modifier combination a keyboard can produce ---
eq("shift+tab is BTab", map_key("Shift+Tab"), "BTab")
eq("S-Tab is BTab", map_key("S-Tab"), "BTab")
eq("bare BTab", map_key("BTab"), "BTab")
eq("ctrl+o", map_key("Ctrl+O"), "C-o")
eq("ctrl+o lower", map_key("ctrl+o"), "C-o")
eq("alt letter", map_key("Alt+b"), "M-b")
eq("option is alt", map_key("Option+b"), "M-b")
eq("ctrl+alt letter", map_key("Ctrl+Alt+k"), "C-M-k")
eq("shift arrow", map_key("Shift+Up"), "S-Up")
eq("ctrl arrow", map_key("Ctrl+Left"), "C-Left")
eq("function key", map_key("F5"), "F5")
eq("modified function key", map_key("Ctrl+F12"), "C-F12")
eq("space", map_key("Space"), "Space")
eq("ctrl+space", map_key("Ctrl+Space"), "C-Space")
eq("insert", map_key("Insert"), "IC")
# Shift lives in the character on a terminal, never as a modifier.
eq("shift+letter is the capital", map_key("Shift+g"), "G")
eq("bare capital survives", map_key("G"), "G")
# What `claude /terminal-setup` binds shift-enter to: ESC CR.
eq("shift+enter is ESC CR", map_key("Shift+Enter"), "M-Enter")
eq("alt+enter is ESC CR", map_key("Alt+Enter"), "M-Enter")
# Punctuation must never be read as modifier syntax.
eq("bare plus", map_key("+"), "+")
eq("bare dash", map_key("-"), "-")
eq("ctrl+plus", map_key("Ctrl++"), "C-+")
eq("ctrl+underscore", map_key("Ctrl+_"), "C-_")

check("unknown name dropped", map_key("Hyper+Frobnicate") is None)
check("empty dropped", map_key("") is None)
eq("list drops unknowns", map_keys(["Shift+Tab", "nope", "G"]), ["BTab", "G"])
eq("non-list is empty", map_keys("Tab"), [])

# --- geometry reaches the client ---
eq("attached frame reports cols", frame_payload("s", "hi", True)["cols"], TUI_COLS)
eq("detached frame has no cols", frame_payload("s", "", False)["cols"], 0)
check("pane is not absurdly wide", 80 <= TUI_COLS <= 140, TUI_COLS)
check("pane keeps its height", TUI_ROWS >= 40, TUI_ROWS)

# --- a line from a client's text field must actually be sent --------------
# {text, keys:["Enter"]} in one request. Codex reads a CR that arrives in the
# same burst as the text as part of a paste: it put a newline in the composer
# and the line was never submitted, so the human had to press Enter again.

class _FakeTmux:
    """Records what reached tmux, and when."""

    def __init__(self, **attrs):
        self.calls = []
        for k, v in attrs.items():
            setattr(self, k, v)
        tui = type("T", (), {"session_id": "s1", "name": "pane-1"})()
        self._tuis = {"pane-1": tui}
        self._lock = None

    def _tmux_alive(self, name):
        return True

    def _tmux(self, *args, input_bytes=None):
        self.calls.append((time.monotonic(), args))
        return type("R", (), {"returncode": 0, "stderr": b""})()

    def gap(self):
        """Seconds between the literal text and the key after it."""
        if len(self.calls) < 2:
            return 0.0
        return self.calls[1][0] - self.calls[0][0]


m = _FakeTmux()
eq("line send reports no error", send_to_session(m, "s1", keys=["Enter"], text="hi"), "")
eq("text goes first", m.calls[0][1][:2], ("send-keys", "-l"))
eq("text is the literal", m.calls[0][1][-1], "hi")
eq("then the key", m.calls[1][1][-1], "Enter")
check("key waits out the paste burst", m.gap() >= TEXT_SETTLE_S * 0.9,
      "%.3fs gap, want >= %.3fs" % (m.gap(), TEXT_SETTLE_S))

m = _FakeTmux()
send_to_session(m, "s1", text="typing")
eq("typing is one call", len(m.calls), 1)

m = _FakeTmux()
send_to_session(m, "s1", keys=["Up", "Enter"])
eq("keys alone are not delayed", len(m.calls), 2)
check("no gap without text", m.gap() < 0.1, "%.3fs" % m.gap())

m = _FakeTmux(tui_text_settle_s=0)
send_to_session(m, "s1", keys=["Enter"], text="hi")
check("a manager can opt out", m.gap() < 0.1, "%.3fs" % m.gap())

check("the gap is big enough to be worth having", TEXT_SETTLE_S >= 0.1, TEXT_SETTLE_S)

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("all ok")
