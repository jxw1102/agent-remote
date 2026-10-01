"""Interactive GitHub Copilot TUI hosted in a detached tmux session.

Reuses the Codex tmux lifecycle and turn loop (paste, Enter, poll, stop with
Escape, restart adoption, fleet cap) and replaces what is Copilot-specific:

  * Launch. A new session is started with ``--session-id <uuid>``, so its id
    is known before the TUI exists; an existing one with ``--resume <uuid>``.
  * The folder-trust dialog. A folder Copilot has not seen opens on "Do you
    trust the files in this folder?"; nobody is at the pane to answer, so the
    daemon picks "Yes" (this session only) once and waits for the composer.
  * Progress and turn end come from ``session-state/<id>/events.jsonl``, the
    same log the store reads. A turn is over when an assistant message with
    no tool requests has been followed by ``assistant.turn_end``.

Interactive is auto-approve (``--allow-all-tools``): a permission panel would
render in a pane nobody can see.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path

from ..config import CONFIG_DIR, ensure_tmux_server
from ..live_tui import TUI_COLS, TUI_ROWS
from ..render_blocks import markdown_to_blocks
from .codex_interactive import (
    CodexInteractiveManager,
    _Tui,
    _POLL_S,
    _READY_SETTLE_S,
    tmux_available,
)
from .copilot import (
    CopilotStore,
    assistant_text,
    copilot_env,
    is_human_user_event,
    safe_json,
    tool_detail,
    tool_requests,
)

log = logging.getLogger(__name__)

_STATE_FILE = CONFIG_DIR / "copilot-tuis.json"
_PREFIX = "cop-"
_OLD_PREFIXES = ("cop-",)
_START_TIMEOUT_S = 90

# Pane chrome observed on copilot 1.0.86.
_TRUST_MARKER = "Do you trust the files in this folder?"
_IDLE_MARKER = "/ commands"          # idle footer: "← open sidebar · / commands · ? help"
_BUSY_MARKERS = ("esc interrupt", "Working")


def pane_trust_dialog(text: str) -> bool:
    return bool(text) and _TRUST_MARKER in text


def pane_busy(text: str) -> bool:
    return bool(text) and any(m in text for m in _BUSY_MARKERS)


def pane_ready(text: str) -> bool:
    """The composer is waiting: the "❯" input row, idle footer, no dialog."""
    if not text or not text.strip() or pane_trust_dialog(text):
        return False
    rows = [r.strip() for r in text.splitlines() if r.strip()]
    has_prompt = any(r.startswith("❯") for r in rows[-8:])
    return has_prompt and (_IDLE_MARKER in text or pane_busy(text))


class CopilotInteractiveManager(CodexInteractiveManager):
    """Own detached tmux panes for Copilot sessions."""

    # -- registry (own file + prefix; the Codex base would read codex's) ----

    def _save_state(self):
        rows = [{"name": t.name, "cwd": t.cwd, "session_id": t.session_id,
                 "last_used": t.last_used, "isolate_root": t.isolate_root,
                 "events_path": t.rollout_path}
                for t in list(self._tuis.values()) if t.spawned]
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = str(_STATE_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            os.replace(tmp, str(_STATE_FILE))
        except OSError:
            pass

    def _adopt_or_reap(self):
        known = {}
        try:
            saved = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = []
        for entry in saved if isinstance(saved, list) else []:
            if isinstance(entry, dict) and str(entry.get("name") or "").startswith(_OLD_PREFIXES):
                known[str(entry["name"])] = entry
        try:
            result = self._tmux("list-sessions", "-F", "#{session_name}", capture=True)
            if result.returncode != 0:
                return
            for name in result.stdout.decode("utf-8", errors="replace").split():
                if not name.startswith(_OLD_PREFIXES):
                    continue
                entry = known.get(name)
                if entry is None:
                    # Never kill — see claude_interactive._adopt_or_reap.
                    log.info("ignoring unknown Copilot TUI %s", name)
                    continue
                tui = _Tui(name, str(entry.get("cwd") or os.path.expanduser("~")),
                           isolate_root=str(entry.get("isolate_root") or ""))
                tui.session_id = str(entry.get("session_id") or "")
                tui.rollout_path = str(entry.get("events_path") or "")
                tui.spawned = True
                try:
                    tui.last_used = float(entry.get("last_used") or 0) or time.time()
                except (TypeError, ValueError):
                    tui.last_used = time.time()
                self._tuis[name] = tui
                log.info("adopted Copilot TUI %s (session %s)", name,
                         tui.session_id[:8] or "unknown")
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._save_state()

    def _tui_name(self, cwd: str) -> str:
        digest = hashlib.sha1(os.path.realpath(cwd).encode("utf-8")).hexdigest()[:8]
        return "%s%s-%s" % (_PREFIX, digest, uuid.uuid4().hex[:6])

    # -- store plumbing -----------------------------------------------------

    def _store_for_tui(self, tui: _Tui) -> CopilotStore:
        if tui.isolate_root:
            return CopilotStore(Path(tui.isolate_root) / ".copilot", self.config)
        return self.runner.store

    def _events_for(self, tui: _Tui) -> str:
        if not tui.session_id:
            return ""
        d = self._store_for_tui(tui).root / tui.session_id
        return str(d / "events.jsonl")

    def _rollout_for(self, session_id: str) -> str:
        for tui in self._tuis.values():
            if tui.session_id == session_id:
                return self._events_for(tui)
        return self.runner.store.events_path(session_id)

    def _try_bind_session(self, tui: _Tui, _launched_at: float) -> bool:
        if not tui.rollout_path:
            tui.rollout_path = self._events_for(tui)
            if tui.rollout_path:
                self._save_state()
        return bool(tui.session_id)

    def _count_user_messages(self, path: str) -> int:
        if not path:
            return -1
        if not Path(path).is_file():
            return 0    # a brand-new session has no log until its first prompt
        n = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"user.message"' in line and is_human_user_event(safe_json(line)):
                        n += 1
        except OSError:
            return -1
        return n

    # -- launch ---------------------------------------------------------------

    def _launch(self, tui: _Tui, resume_sid: str, model: str,
                timeout_s: float = _START_TIMEOUT_S) -> str:
        sid = resume_sid or str(uuid.uuid4())
        exists = (self._store_for_tui(tui).root / sid / "events.jsonl").is_file()
        parts = [str(getattr(self.config, "copilot_bin", "") or "copilot"),
                 "--allow-all-tools"]
        parts += str(getattr(self.config, "copilot_flags", "") or "").split()
        parts += ["--resume", sid] if exists else ["--session-id", sid]
        if model and model not in ("", "default", "auto"):
            parts += ["--model", model]

        env = copilot_env(self.config)
        extra = getattr(self.config, "copilot_env", None) or {}
        launch_cwd = tui.isolate_root or tui.cwd
        cmdline = " ".join(shlex.quote(p) for p in parts)
        if tui.isolate_root:
            from .. import accounts
            env = accounts.isolation_env(env, tui.isolate_root)
            shell_cmd = accounts.isolate_shell_line(
                "cd %s && %s" % (shlex.quote(tui.cwd), cmdline), tui.isolate_root)
        else:
            env_prefix = " ".join(
                "%s=%s" % (k, shlex.quote(str(v))) for k, v in env.items()
                if k in ("PATH", "COPILOT_DISABLE_AUTO_UPDATE") or k in extra)
            shell_cmd = "%s exec %s" % (env_prefix, cmdline)

        ensure_tmux_server(self._tmux_bin)
        try:
            r = self._tmux("new-session", "-d", "-s", tui.name,
                           "-x", str(TUI_COLS), "-y", str(TUI_ROWS),
                           "-c", launch_cwd, shell_cmd)
        except OSError as e:
            return "tmux not available: %s" % e
        except subprocess.TimeoutExpired:
            return "tmux new-session timed out"
        if r.returncode != 0:
            return "tmux failed: %s" % r.stderr.decode("utf-8", errors="replace").strip()
        tui.spawned = True
        tui.session_id = sid

        deadline = time.time() + timeout_s
        answered_trust = False
        while True:
            text = self._pane_text(tui.name)
            if pane_trust_dialog(text) and not answered_trust:
                # "1. Yes" is selected by default: trust for this session only.
                self._tmux("send-keys", "-t", tui.name, "Enter")
                answered_trust = True
            elif pane_ready(text):
                break
            if time.time() > deadline or not self._tmux_alive(tui.name):
                tail = self._pane_tail(tui.name)
                self._kill(tui)
                return ("Copilot TUI did not become ready"
                        + ((" — screen: %s" % tail) if tail else ""))
            time.sleep(_POLL_S)

        tui.rollout_path = self._events_for(tui)
        try:
            tui.rollout_offset = Path(tui.rollout_path).stat().st_size
        except OSError:
            tui.rollout_offset = 0
        time.sleep(_READY_SETTLE_S)
        return ""

    def run(self, job) -> None:
        if not tmux_available():
            self._fail(job, "interactive mode needs tmux (brew install tmux)")
            return
        if not job.cwd:
            self._fail(job, "cwd is required for Copilot sessions")
            return
        cwd = os.path.expanduser(job.cwd)
        if not os.path.isdir(cwd):
            self._fail(job, "cwd does not exist: %s" % cwd)
            return
        job.cwd = cwd
        if not job.session_id:
            # Choose the id now; _launch starts the TUI with --session-id.
            sid = str(uuid.uuid4())
            job.session_id = sid
            with job.lock:
                job.new_session_id = sid
        super().run(job)

    # -- input + progress -----------------------------------------------------

    def _send_prompt(self, tui: _Tui, prompt: str) -> str:
        tui.copilot_local_command = (prompt or "").lstrip().startswith("/")
        return super()._send_prompt(tui, prompt)

    def _confirm_submit(self, tui: _Tui, before: int):
        # Slash commands never append a user row; pressing Enter again would
        # run them twice.
        if getattr(tui, "copilot_local_command", False):
            return
        deadline = time.time() + 6.0
        while time.time() < deadline:
            path = tui.rollout_path or self._events_for(tui)
            if path and self._count_user_messages(path) > before:
                return
            if pane_busy(self._pane_text(tui.name)):
                return
            time.sleep(_POLL_S)
        log.warning("Copilot TUI %s: submit not confirmed", tui.name)

    def _poll_rollout(self, job, tui: _Tui) -> bool:
        path = tui.rollout_path or self._events_for(tui)
        if path:
            tui.rollout_path = path
        if not path or not Path(path).is_file():
            return False
        state = job.runner_state
        try:
            size = Path(path).stat().st_size
        except OSError:
            return False
        if size < tui.rollout_offset:
            tui.rollout_offset = 0
        if size == tui.rollout_offset:
            return bool(state.get("turn_done"))
        try:
            with open(path, "rb") as f:
                f.seek(tui.rollout_offset)
                raw = f.read()
        except OSError:
            return False
        nl = raw.rfind(b"\n")
        if nl < 0:
            return bool(state.get("turn_done"))   # wait for a whole line
        chunk = raw[: nl + 1]
        tui.rollout_offset += len(chunk)

        for line in chunk.decode("utf-8", errors="replace").splitlines():
            ev = safe_json(line)
            if ev is None:
                continue
            et = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            if et == "user.message":
                state["final"] = False
                job.set_phase("thinking", "")
            elif et == "assistant.message":
                text = assistant_text(data)
                reqs = tool_requests(data)
                if text:
                    state.setdefault("parts", []).append(text)
                    state.setdefault("full", []).append(text)
                    job.add_event("text", text=text, blocks=markdown_to_blocks(text))
                    job.set_phase("writing", text[-160:])
                # No tool requests = the model's answer, not a step on the way.
                state["final"] = not reqs
            elif et == "tool.execution_start":
                name = str(data.get("toolName") or "tool")
                detail = tool_detail(name, data.get("arguments"))
                job.add_event("tool", name=name, detail=detail[:280])
                job.set_phase("tool", (detail or name)[:120])
            elif et == "assistant.turn_end" and state.get("final"):
                state["turn_done"] = True
                full = "\n\n".join(state.get("full") or state.get("parts") or [])
                with job.lock:
                    if full and not job.result_text:
                        job.result_text = full
                job.add_event("result", is_error=False,
                              duration_ms=int((time.time() - job.started_at) * 1000),
                              cost_usd=0)
        return bool(state.get("turn_done"))
