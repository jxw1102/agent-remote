"""Interactive CodeBuddy Code TUI in a detached tmux session.

Same turn loop as Claude (paste, transcript tail, Stop hook, Live TUI
capture). What is CodeBuddy-specific:

  * Launch ``codebuddy --permission-mode bypassPermissions --plugin-dir
    <agentremote-bridge>`` (``--resume``, ``--model``, ``--effort``).
    ``CODEBUDDY_CONFIG_DIR`` points at the store the daemon reads, and
    ``CODEBUDDY_CODE_DISABLE_AGENT_VIEW=1`` keeps a stray Left arrow from
    parking the conversation where Enter does not submit.
  * The folder-trust dialog ("Do you trust the files in this folder?")
    has nobody at the pane. The daemon confirms option 1, this folder
    only. Escape on that dialog exits, so it is never sent.
  * The login picker is not answered. A phone cannot complete Chinese
    site / international site / enterprise / iOA sign-in, and pressing
    Enter would open a browser. The turn fails with a sign-in message.

Sessions stay the Claude-shaped JSONL under ``<codebuddy home>/projects``.
The bridge plugin is the same tree Claude loads; CodeBuddy accepts its
``.claude-plugin`` manifest and the ``.codebuddy-plugin`` copy.
"""

from __future__ import annotations

import os
import re
import shutil

from .claude_interactive import InteractiveManager
from .codebuddy import codebuddy_home

_TRUST_MARKER = "Do you trust the files in this folder?"
# CodeBuddy's busy chrome, which differs from Claude's.
#
# Claude's TUI prints "· esc to interrupt" in the status bar while a
# turn runs; CodeBuddy prints no such hint — its footer stays
# "⏵⏵ bypass permissions on (shift+Tab to cycle)" either way. The
# only running-turn marker is the spinner line right above the input
# box, e.g.
#
#   ✺ Waking… (1454s · running REPL · ⚒ 3.5k tokens · Task running for a while)
#
# The "(NNNs · …" parenthetical is that line's elapsed-timer block:
# it only exists while a turn is in flight, and it is re-rendered in
# place (never appended), so a stale copy cannot linger in the tail.
_CBC_BUSY_RE = re.compile(r"\(\d+s\s*·.*\)\s*$")
_LOGIN_MARKERS = (
    "Select login method",
    "Enter to login",
    "Browser didn't open",
    "Paste code here",
)


def tmux_available() -> bool:
    return bool(shutil.which("tmux") or os.path.exists("/opt/homebrew/bin/tmux"))


class CodebuddyInteractiveManager(InteractiveManager):
    """Detached tmux panes for CodeBuddy sessions. Names are ``cbc-*``."""

    prefix = "cbc-"
    old_prefixes = ("cbc-",)
    state_name = "codebuddy-tuis.json"
    binary_attr = "codebuddy_bin"
    default_binary = "codebuddy"
    disable_view_env = "CODEBUDDY_CODE_DISABLE_AGENT_VIEW"
    product = "codebuddy"

    def _pane_busy(self, name: str) -> bool:
        """CodeBuddy's own running-turn chrome.

        The base class looks for Claude's "esc to interrupt" hint, which
        CodeBuddy never prints — so it reported every CodeBuddy TUI as
        idle. Two things broke as a result: Stop did not send the
        interrupt key (the job went "stopped" while the host TUI kept
        working), and active_tui_status never reported the session as
        busy, so the phone's "working" pulse stayed off.

        CodeBuddy's marker is the elapsed-timer parenthetical on the
        spinner line above the input box, e.g.
        "✺ Waking… (1454s · running REPL · ⚒ 3.5k tokens)". It is
        re-rendered in place, so it cannot go stale in the tail.

        The lines are read from the pane directly rather than from
        _pane_tail: that helper joins rows with " | ", which would
        smear several pane lines into one and defeat the match.
        """
        rows = self._pane_rows(name)
        for line in rows:
            if _CBC_BUSY_RE.search(line):
                return True
            # CodeBuddy's multi-agent wait: "Waiting for N teammate(s)…".
            if "waiting for" in line.lower() and "teammate" in line.lower():
                return True
        # The base class also knows Claude's multi-agent chrome; keep it so
        # nothing that used to count as busy stops counting.
        return super()._pane_busy(name)

    def _pane_queued(self, name: str) -> bool:
        """CodeBuddy's queued-message chrome.

        Claude says "queued message"; CodeBuddy instead prints
        "Messages to be submitted after next tool call" above the
        queued line.
        """
        for line in self._pane_rows(name):
            if "messages to be submitted" in line.lower():
                return True
        return False

    def _pane_rows(self, name: str, n: int = 20) -> list:
        """Last non-blank pane lines, one per element."""
        rows = [l.rstrip() for l in self._pane_text(name).splitlines()
                if l.strip()]
        return rows[-n:]

    def _project_dir(self, cwd: str, isolate_root: str = "") -> str:
        iso = (isolate_root or "").strip()
        if iso:
            cfg = os.path.join(iso, ".codebuddy")
        else:
            cfg = str(codebuddy_home(self.config))
        real = os.path.realpath(cwd or iso or os.path.expanduser("~"))
        slug = re.sub(r"[^A-Za-z0-9-]", "-", real)
        return os.path.join(cfg, "projects", slug)

    def _extra_exports(self, tui) -> list:
        # Guest isolation already exports CODEBUDDY_CONFIG_DIR into the jail.
        if tui.isolate_root:
            return []
        out = [("CODEBUDDY_CONFIG_DIR", str(codebuddy_home(self.config)))]
        extra = getattr(self.config, "codebuddy_env", None) or {}
        for key, value in extra.items():
            if key and value is not None:
                out.append((str(key), str(value)))
        return out

    def _cli_parts(self, tui, resume_sid: str, model: str, effort: str = "") -> list:
        parts = super()._cli_parts(tui, resume_sid, model, effort)
        flags = str(getattr(self.config, "codebuddy_flags", "") or "").split()
        if flags:
            parts = [parts[0]] + flags + parts[1:]
        level = (effort or "").strip()
        if level and level != "default":
            parts += ["--effort", level]
        return parts

    def _blocked_reason(self, text: str) -> str:
        if text and any(marker in text for marker in _LOGIN_MARKERS):
            return (
                "CodeBuddy is not signed in. Run `codebuddy` in a terminal "
                "and sign in (Chinese site, international site, enterprise, "
                "or iOA), or set CODEBUDDY_API_KEY"
            )
        return ""

    def _startup_key(self, text: str, nudge_i: int) -> str:
        if text and _TRUST_MARKER in text:
            # Option 1 is "Trust folder only". Escape is "No, exit".
            return "Enter" if nudge_i < 3 else ""
        if text and any(marker in text for marker in _LOGIN_MARKERS):
            return ""
        return super()._startup_key(text, nudge_i)

    # Notification kinds that block the pane on a human. CodeBuddy also
    # fires auth_success ("auth_success: <nickname>") and agent_completed,
    # which are progress, not a wait — surfacing them made the status strip
    # read "waiting · auth_success: …" mid-turn.
    _NOTIFY_WAIT_KINDS = ("permission_prompt", "idle_prompt",
                          "elicitation_dialog", "agent_needs_input")

    def _notification_message(self, payload: dict) -> str:
        kind = str(payload.get("notification_type") or "").strip()
        if kind and kind not in self._NOTIFY_WAIT_KINDS:
            return ""
        text = str(payload.get("message") or "").strip()
        if text.lower().startswith(("auth_success", "agent_completed")):
            return ""
        return super()._notification_message(payload)

    def _note_todos(self, job, sid: str) -> None:
        return None

    def _on_task_tool(self, job, sid: str, tool_input) -> bool:
        return False
