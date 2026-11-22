"""The one-time Bypass Permissions consent screen is reported, not keyed past.

Run:  python3 tests/bypass_warning_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.providers import claude_interactive as ci  # noqa: E402

failed = 0


def check(name, ok):
    global failed
    print("  [%s] %s" % ("ok" if ok else "FAIL", name))
    failed += 0 if ok else 1


WARNING = """
  WARNING: Claude Code running in Bypass Permissions mode
  In Bypass Permissions mode, Claude Code will not ask for your approval before running potentially dangerous
  commands.
  By proceeding, you accept all responsibility for actions taken while running in Bypass Permissions mode.
  https://code.claude.com/docs/en/security
  ❯ No, exit
    Yes, I accept
  Enter to confirm · Esc to cancel
"""
TRUST = """
 Quick safety check: Is this a project you created or one you trust?
 ❯ No, exit
   Yes, I trust this folder
 Enter to confirm · Esc to cancel
"""
READY = "❯ \n  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"

m = ci.InteractiveManager.__new__(ci.InteractiveManager)
check("the consent screen blocks with instructions",
      "Yes, I accept" in m._blocked_reason(WARNING)
      and "--dangerously-skip-permissions" in m._blocked_reason(WARNING))
check("no key is sent to the consent screen", m._startup_key(WARNING, 0) == "")
check("the consent screen is not a ready pane", not m._pane_is_ready(WARNING))
check("the folder-trust dialog is still answered", m._startup_key(TRUST, 0) == "Down")
check("the folder-trust dialog does not block", m._blocked_reason(TRUST) == "")
check("a ready pane does not block",
      m._blocked_reason(READY) == "" and m._pane_is_ready(READY))

print("FAILED: %d" % failed if failed else "all passed")
sys.exit(1 if failed else 0)
