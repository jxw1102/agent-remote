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

    def _note_todos(self, job, sid: str) -> None:
        return None

    def _on_task_tool(self, job, sid: str, tool_input) -> bool:
        return False
