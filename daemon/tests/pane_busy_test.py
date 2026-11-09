"""Is the host TUI still working? Read the live bottom of the pane.

The rule this file exists to protect: "working" is whatever the TUI is
drawing right now, and the bottom of a Claude pane is not a fixed number of
lines. Since the multi-agent manager view, the chrome under the input box is
the status bar plus `⏺ main` plus one row per background agent — so the
turn-state line ("Waiting for 3 background agents to finish") is pushed
further up the more agents there are, which is exactly when the session is
most obviously still working. A tail short enough to keep stale scrollback
out is too short to see it; the input box is the anchor instead.

Run:  python3 tests/pane_busy_test.py
"""

import ast
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.providers import claude_interactive as ci  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


RULE = "─" * 120
BOX = [RULE, "❯ ", RULE]


def pane(*blocks):
    """One pane capture: transcript rows, then the box, then the chrome."""
    return "\n".join(blocks)


def status(*bits):
    return "  ⏵⏵ bypass permissions on (shift+tab to cycle)" + "".join(
        " · " + b for b in bits)


def agents(n):
    rows = ["  ⏺ main"]
    for i in range(n):
        rows.append("  ◯ general-purpose  Doing job %d of %d"
                    "                       %dm %ds · ↓ 141.0k tokens"
                    % (i + 1, n, 15 + i, i))
    return "\n".join(rows)


class Pane(ci.InteractiveManager):
    """The manager with the tmux taken out: panes are strings."""

    def __init__(self, text=""):
        self.text = text
        self._tuis = {}
        self._watched = {}
        import threading
        self._lock = threading.Lock()

    def _pane_text(self, name, *, ansi=False):
        return self.text

    def _tmux_alive(self, name):
        return True

    def _pane_dead(self, name):
        return False, ""

    def _save_state(self):
        pass


def busy(text):
    return Pane(text)._pane_busy("t")


print("the reported bug: waiting on background agents")
waiting = pane(
    "⏺ Progress:",
    "  The Mac has 202 GB free. I'll report again when the step passes.",
    "✻ Waiting for 3 background agents to finish",
    *BOX,
    status("↓ to manage"),
    agents(3),
)
check("3 background agents is working", busy(waiting) is True)
check("the wait line is in the window",
      "waiting for 3 background agents" in Pane(waiting)._pane_bottom("t"))

deep = pane(
    "⏺ Progress:",
    "✻ Waiting for 12 background agents to finish",
    *BOX,
    status("↓ to manage"),
    agents(12),
)
check("12 agents is still working", busy(deep) is True,
      "a taller agent list must not push the turn line out")

print("classic turns")
spinner = pane(
    "⏺ Bash(ls -la)",
    "  ⎿  total 0",
    "✻ Catapulting… (35s · ↓ 1.5k tokens)",
    *BOX,
    status("esc to interrupt"),
)
check("spinner with esc is working", busy(spinner) is True)

tip = pane(
    "⏺ Bash(ls -la)",
    "✻ Whirlpooling… (3m 11s · ↓ 4.0k tokens)",
    "  ⎿  Tip: Running multiple Claude sessions? Use /color to tell them apart.",
    *BOX,
    status("esc to interrupt"),
)
check("a tip under the spinner does not hide it", busy(tip) is True)

busy_tree = pane(
    "⏺ Reading the build log",
    "✻ Catapulting… (35s · ↓ 1.5k tokens)",
    *BOX,
    status("esc to interrupt", "↓ to manage"),
    agents(9),
)
check("esc in the status bar under a tall tree", busy(busy_tree) is True,
      "the chrome is always in the window, however tall it grows")

print("idle panes")
idle = pane(
    "⏺ Done. The build finished in 712s.",
    "✻ Baked for 7m 21s · done 6:27 PM",
    *BOX,
    status("1 shell", "↓ to manage"),
)
check("a finished turn is idle", busy(idle) is False)

stale = pane(
    "⏺ Earlier in this session:",
    "✻ Waiting for 1 background agent to finish",
    "  ⎿  Agent finished: wrote the patch",
    "⏺ All three agents are done. Here is the summary.",
    "  The patch is on disk and the tests pass.",
    "  Nothing else is running.",
    *BOX,
    status("↓ to manage"),
)
check("a stale wait line in the scrollback is not working",
      busy(stale) is False,
      "this is the bug the window exists to keep out")

chrome_agent = pane(
    "⏺ Done.",
    *BOX,
    status("← 1 agent"),
)
check("the idle '← 1 agent' chrome is not working",
      busy(chrome_agent) is False)

print("degenerate panes")
crash = "\n".join([
    "node:internal/errors:496",
    "      ErrorCaptureStackTrace(err);",
    "      ^",
    "Error: EMFILE: too many open files",
])
check("a crash trace with no box is not working", busy(crash) is False)
check("an empty pane is not working", busy("") is False)

print("queued messages")
queued = pane(
    "⏺ Working on it.",
    *BOX,
    status("esc to interrupt"),
    "  ↑ 2 queued messages",
)
check("queued chrome is seen", Pane(queued)._pane_queued("t") is True)
check("no queue when the chrome is quiet",
      Pane(idle)._pane_queued("t") is False)

print("the crash watchdog's health check")
# Same defect, worse consequence: _pane_healthy read a fixed 8 rows, and a
# pane that looks crashed for _CRASH_STALL_S is killed and its job failed
# with "claude crashed mid-turn". Seven agents is enough to push ❯ and ⏵⏵
# out of an 8-row window, so a perfectly healthy session was one quiet
# moment away from being shot.
healthy_deep = pane(
    "⏺ Still going.",
    *BOX,
    status("↓ to manage"),
    agents(9),
)
check("a tall agent tree is still a healthy pane",
      Pane(healthy_deep)._pane_healthy("t") is True)
check("a plain idle pane is healthy", Pane(idle)._pane_healthy("t") is True)
check("a crash trace is not healthy", Pane(crash)._pane_healthy("t") is False)
check("an empty pane is not healthy", Pane("")._pane_healthy("t") is False)

print("the synthetic working row clients read")
tui = ci._Tui("cld-test-1", "/tmp")
tui.spawned = True
tui.session_id = "46c2870a-0b2e-4116-9582-3854aad17fe8"
mgr = Pane(waiting)
mgr._tuis["cld-test-1"] = tui
rows = mgr.active_tui_status()
check("the waiting session is reported active", len(rows) == 1, rows)
if rows:
    check("reported as working", rows[0].get("phase") == "working", rows[0])
    check("carries the session id",
          rows[0].get("session_id") == tui.session_id, rows[0])
mgr_idle = Pane(idle)
mgr_idle._tuis["cld-test-1"] = tui
check("an idle TUI is not reported", mgr_idle.active_tui_status() == [])

print("no method may shadow another")
# The bug behind all of the above: _pane_tail was defined twice in one class,
# so the later 6-line diagnostics helper silently replaced the busy-window
# one written to fix exactly this, and the fix never ran. Nothing in Python
# complains, so the test has to.
for mod in ("claude_interactive", "codex_interactive", "grok_interactive",
            "codebuddy_interactive", "copilot_interactive",
            "cursor_interactive"):
    path = os.path.join(os.path.dirname(__file__), "..", "agentremoted",
                        "providers", mod + ".py")
    if not os.path.exists(path):
        continue
    tree = ast.parse(open(path, encoding="utf-8").read())
    dupes = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        seen = {}
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name in seen:
                    dupes.append("%s.%s (lines %d and %d)"
                                 % (node.name, item.name,
                                    seen[item.name], item.lineno))
                seen[item.name] = item.lineno
    check("%s defines each method once" % mod, not dupes, dupes)

print()
if failures:
    print("FAILED: %d" % len(failures))
    for name in failures:
        print("  - %s" % name)
    sys.exit(1)
print("all pane-busy checks passed")
