"""Cursor's ask-question panel -> phone question payload.

Cursor Agent's interactive TUI asks with a panel that appears nowhere in the
stream-json or the transcript:

    Question 1 of 1
    1. Should the project use tabs or spaces for indentation?
      › [ ] Tabs
        [ ] Spaces (2)
        [ ] Other: (type to answer)
    ↑/↓ option · ←/→ question · Space select · Enter next/submit · Esc to skip

Headless (--print) auto-rejects the question, so the pane is the only place
a phone-driven answer can come from.

Run:  python3 tests/cursor_question_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.providers.cursor_interactive import (  # noqa: E402
    CursorInteractiveManager)

PANEL = """
 │ Question 1 of 1                                                                                                    │
 │                                                                                                                    │
 │ 1. Should the project use tabs or spaces for indentation?                                                          │
 │                                                                                                                    │
 │   › [ ] Tabs                                                                                                       │
 │     [ ] Spaces (2)                                                                                                 │
 │     [ ] Spaces (4)                                                                                                 │
 │     [ ] Other: (type to answer)                                                                                    │
 │                                                                                                                    │
 │ ↑/↓ option · ←/→ question · Space select · Enter next/submit · Esc to skip                                         │
 └────────────────────────────────────┘
"""

MULTI = PANEL.replace("Question 1 of 1", "Question 2 of 3").replace(
    "1. Should the project use tabs or spaces for indentation?",
    "2. Pick a license")
IDLE = "→ Add a follow-up\nGLM 5.3 High · 15.4% \u00b7 3 files edited\n"
ANSWERED = PANEL.split("\u2191/↓")[0]  # footer gone: panel closed

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


panel_of = CursorInteractiveManager._question_panel
p = panel_of(PANEL)
check("panel parsed", bool(p))
check("prompt text", p.get("question") ==
      "Should the project use tabs or spaces for indentation?", p.get("question"))
check("options without Other",
      [o["label"] for o in p.get("options") or []]
      == ["Tabs", "Spaces (2)", "Spaces (4)"],
      [o["label"] for o in p.get("options") or []])
check("single select", p.get("multi_select") is False)
check("header", p.get("header") == "Question")
check("multi-question title", panel_of(MULTI).get("question") == "Pick a license")
check("idle pane ignored", panel_of(IDLE) == {})
check("answered/closed pane ignored", panel_of(ANSWERED) == {})
check("cursor index",
      CursorInteractiveManager._question_cursor(
          PANEL, ["Tabs", "Spaces (2)", "Spaces (4)"]) == 0)
moved = PANEL.replace("› [ ] Tabs", "  [ ] Tabs").replace(
    "  [ ] Spaces (2)", "› [ ] Spaces (2)")
check("cursor index after move",
      CursorInteractiveManager._question_cursor(
          moved, ["Tabs", "Spaces (2)", "Spaces (4)"]) == 1)

print("\n%s" % ("FAILED (%d)" % len(failures) if failures
                 else "all cursor question checks passed"))
raise SystemExit(1 if failures else 0)
