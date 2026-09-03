"""Pane permission dialogs -> phone question payloads.

Interactive mode runs the TUI with --permission-mode bypassPermissions, which
was assumed to remove permission prompts entirely. It does not: the static
Bash guards (cd-compound-read/write/redirect, and commands whose arguments
cannot be analysed statically) still open a dialog that blocks the pane, and
nobody is sitting at the pane. _permission_panel lifts those onto the same
question channel AskUserQuestion uses.

The two cases that matter are the negative ones. Acting on a dialog that has
already been answered types keys into a live prompt, and mistaking an
AskUserQuestion panel for a permission dialog makes two handlers race for the
same keystrokes.

Run:  python3 tests/permission_panel_test.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SRC = os.path.join(os.path.dirname(__file__), "..", "agentremoted",
                   "providers", "claude_interactive.py")


def _load():
    """Grab the parser without importing the daemon package (needs tmux)."""
    src = open(SRC, encoding="utf-8").read()
    ns = {"re": re}
    exec(src[src.index("_ANSI_RE"):src.index("def _claude_splash")], ns)
    exec("class T:\n" + src[src.index("    @staticmethod\n    def _permission_panel"):
                            src.index("    def _perm_open")], ns)
    return ns["T"]._permission_panel


DIALOG = """\u256d\u2500\u2500\u2500\u2500
\u2502 Bash command
\u2502
\u2502   cd /repo/bb10-whatsapp; grep -n "kBotServer" engine/src/protocol/native_wa_client.cpp
\u2502   Find every bot check to update
\u2502
\u2502 grep on 'engine/src/protocol/native_wa_client.cpp' after a cd would search a
\u2502 directory that cannot be determined here, and a Read() deny rule is configured.
\u2502
\u2502 Do you want to proceed?
\u2502 \u276f 1. Yes
\u2502   2. No
\u2570\u2500 Esc to cancel \u00b7 Tab to amend
"""

THREE_WAY = """\u2502 Do you want to proceed?
\u2502 \u276f 1. Yes
\u2502   2. Yes, and don't ask again for grep commands in /Users/xiaowen.ji/Developer/cloud-projects
\u2502   3. No, and tell Claude what to do differently (esc)
\u2570\u2500 Esc to cancel
"""

EDIT = """\u2502 Do you want to make this edit to jobs.py?
\u2502 \u276f 1. Yes
\u2502   2. Yes, allow all edits during this session
\u2502   3. No (esc)
\u2570\u2500 Esc to cancel
"""

# Answered: the tool output now sits below the dialog, which is still visible.
ANSWERED = DIALOG + "\n     42:  if (kBotServer == jid) {\n\u276f\n"

# AskUserQuestion: also numbered, but it has its own handler and its own
# footer marker. Two handlers driving one panel double-type the picks.
ASK_PANEL = """\u2502 Which approach?
\u2502 \u276f 1. Rewrite
\u2502   2. Patch
\u2570\u2500 \u2191\u2193 to navigate \u00b7 Esc to cancel
"""

IDLE = "\u276f \n  ? for shortcuts\n"
PROSE = "\u2502 I will ask: Do you want to proceed?\n\u2502 and then continue\n\u276f\n"


def main() -> int:
    panel = _load()
    cases = [
        ("open bash dialog", DIALOG, True),
        ("three-way dialog", THREE_WAY, True),
        ("edit dialog", EDIT, True),
        ("answered, output below", ANSWERED, False),
        ("AskUserQuestion panel", ASK_PANEL, False),
        ("idle pane", IDLE, False),
        ("prose that mentions the phrase", PROSE, False),
    ]
    bad = 0
    for name, pane, want in cases:
        got = bool(panel(pane))
        if got != want:
            bad += 1
            print("FAIL %s: detected=%s want=%s" % (name, got, want))
        else:
            print("ok   %s" % name)

    p = panel(DIALOG)
    checks = [
        ("header", p["header"] == "Permission"),
        ("single select", p["multi_select"] is False),
        ("both options", [o["label"] for o in p["options"]] == ["Yes", "No"]),
        ("question ends on the ask", p["question"].splitlines()[-1]
         == "Do you want to proceed?"),
        ("command forwarded", "kBotServer" in p["question"]),
        ("reason forwarded", "cannot be determined" in p["question"]),
    ]
    for name, good in checks:
        if not good:
            bad += 1
            print("FAIL payload: %s" % name)
        else:
            print("ok   payload: %s" % name)

    # A long option label is truncated for the phone but kept in full in the
    # description, because that is the text the user needs to judge it.
    long_opt = panel(THREE_WAY)["options"][1]
    if len(long_opt["label"]) > 60 or "don't ask again" not in long_opt["description"]:
        bad += 1
        print("FAIL payload: long option keeps its full text in description")
    else:
        print("ok   payload: long option truncated with full description")

    print("\n%s" % ("FAILED (%d)" % bad if bad else "all permission panel checks passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
