#!/usr/bin/env python3
"""Claude's folder-trust dialog: the startup nudge must pick "trust", never
"No, exit" (Claude 2.1.3xx lists the exit option first)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentremoted.providers.claude_interactive import _trust_dialog_key  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


EXIT_FIRST = """ Accessing workspace:
 /home/u/projects/x
 Quick safety check: Is this a project you created or one you trust?
 ❯ No, exit
   Yes, I trust this folder
 Enter to confirm · Esc to cancel"""
TRUST_SELECTED = EXIT_FIRST.replace("❯ No, exit", "  No, exit").replace(
    "  Yes, I trust", "❯ Yes, I trust")
OLD_ORDER = """ Do you trust the files in this folder?
 ❯ 1. Yes, I trust this folder
   2. No, exit"""

check("cursor on 'No, exit' moves down", _trust_dialog_key(EXIT_FIRST) == "Down",
      _trust_dialog_key(EXIT_FIRST))
check("cursor on trust confirms", _trust_dialog_key(TRUST_SELECTED) == "Enter")
check("old order (trust first) confirms", _trust_dialog_key(OLD_ORDER) == "Enter")
check("no dialog, no key", _trust_dialog_key("> hello\n ⏵⏵ bypass") == "")
check("empty pane, no key", _trust_dialog_key("") == "")

print("FAIL" if failures else "OK")
sys.exit(1 if failures else 0)
