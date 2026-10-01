"""Live TUI key vocabulary: modifier-composed names → tmux send-keys tokens.

Run:  python3 tests/tui_keys_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.live_tui import (  # noqa: E402
    TUI_COLS, TUI_ROWS, frame_payload, map_key, map_keys,
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

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("all ok")
