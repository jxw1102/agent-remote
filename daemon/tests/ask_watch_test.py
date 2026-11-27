"""The question watcher reads only the panel that is on screen.

Covers what the screen parse keeps and cuts (the preview box beside the
options, but not a table in the question or a dash in a label) and when the
hook's copy of a question may stand in for the panel read off the pane.

Run:  python3 tests/ask_watch_test.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
HOME = tempfile.mkdtemp(prefix="agentremoted-askwatch-")
os.environ["HOME"] = HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.providers.claude_interactive import InteractiveManager  # noqa: E402

FAILED = []


def check(name, ok, got=None):
    print("  [%s] %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        FAILED.append(name)
        if got is not None:
            print("        got:", got)


FOOTER = "Enter to select · ↑/↓ to navigate · Esc to cancel"

print("screen parse:")
side = """ Pick a layout.

❯ 1. Uniform icon circles         ┌──────────────┐
  2. Labelled pills               │ ( ▶ ) ( ✓ )  │
  3. Primary + secondary          └──────────────┘

""" + FOOTER + "\n"
p = InteractiveManager._ask_panel(side)
check("the box beside the options is not in the labels",
      [o["label"] for o in p.get("options", [])]
      == ["Uniform icon circles", "Labelled pills", "Primary + secondary"], p)

table = """ Which column wins? Today:
 col   │ a │ b │
❯ 1. Column a                     ┌──────────────┐
  2. Column b                     └──────────────┘

""" + FOOTER + "\n"
p = InteractiveManager._ask_panel(table)
check("a table in the question survives beside a preview box",
      "a │ b" in p.get("question", ""), p)
check("and the labels still lose the box",
      [o["label"] for o in p.get("options", [])] == ["Column a", "Column b"], p)

dash = """ How fast?
❯ 1. Fast  ─ recommended
  2. Slow

""" + FOOTER + "\n"
p = InteractiveManager._ask_panel(dash)
check("a dash in a label is kept",
      [o["label"] for o in p.get("options", [])][:1] == ["Fast  ─ recommended"], p)

print("hook payload against the screen:")
hook = [{"question": "Pick a layout.", "header": "Layout", "options": [
    {"label": "Uniform icon circles"}, {"label": "Labelled pills"},
    {"label": "Primary + secondary"}]}]
screen = InteractiveManager._ask_panel(side)
check("the hook's question matches its own panel",
      InteractiveManager._hook_matches(hook, screen))
other = InteractiveManager._ask_panel(dash)
check("an older hook question does not stand in for a new panel",
      not InteractiveManager._hook_matches(hook, other))
long_label = "x" * 80
hook_long = [{"question": "q", "options": [{"label": long_label}, {"label": "Short"}]}]
cut = {"options": [{"label": long_label[:57] + "..."}, {"label": "Short"}]}
check("a label cut at 60 on screen still matches",
      InteractiveManager._hook_matches(hook_long, cut))
check("no screen options: no match", not InteractiveManager._hook_matches(hook, {}))

print()
if FAILED:
    print("FAILED: " + ", ".join(FAILED))
    sys.exit(1)
print("all ok")
