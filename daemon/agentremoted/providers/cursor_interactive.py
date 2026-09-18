"""Interactive Cursor Agent TUI hosted in a detached tmux session.

Cursor's interactive CLI writes the same agent-transcript JSONL consumed by
CursorStore, including a ``turn_ended`` row.  This manager reuses the mature
Codex tmux lifecycle/input loop while replacing launch, persistence and
journal parsing with Cursor-specific behavior.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path

from ..config import CONFIG_DIR, ensure_tmux_server
from ..render_blocks import markdown_to_blocks
from .codex_interactive import (
    CodexInteractiveManager,
    _Tui,
    _POLL_S,
    _READY_SETTLE_S,
    tmux_available,
)
from .cursor import (
    CursorStore,
    _assistant_text,
    _human_user_text,
    _is_session_id,
    _safe_json,
    _steps_of,
)

log = logging.getLogger(__name__)

_STATE_FILE = CONFIG_DIR / "cursor-tuis.json"
_PREFIX = "cur-"
_OLD_PREFIXES = ("cur-",)
_START_TIMEOUT_S = 90

_READY_MARKERS = (
    "Cursor Agent",
    "Run Everything",
    "Plan, search, build anything",
)


def _pane_ready(text: str) -> bool:
    """Cursor's input row starts with a right arrow near the pane bottom."""
    if not text or not text.strip():
        return False
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    for row in rows[-8:]:
        if row.startswith("→"):
            return True
    return all(marker in text for marker in ("Cursor Agent", "Run Everything"))


class CursorInteractiveManager(CodexInteractiveManager):
    """Own detached tmux panes for Cursor sessions."""

    def _save_state(self):
        rows = [{
            "name": tui.name,
            "cwd": tui.cwd,
            "session_id": tui.session_id,
            "last_used": tui.last_used,
            "isolate_root": tui.isolate_root,
            "transcript_path": tui.rollout_path,
        } for tui in list(self._tuis.values()) if tui.spawned]
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
            if (isinstance(entry, dict)
                    and str(entry.get("name") or "").startswith(_OLD_PREFIXES)):
                known[str(entry["name"])] = entry
        try:
            result = self._tmux(
                "list-sessions", "-F", "#{session_name}", capture=True)
            if result.returncode != 0:
                return
            names = result.stdout.decode("utf-8", errors="replace").split()
            for name in names:
                if not name.startswith(_OLD_PREFIXES):
                    continue
                entry = known.get(name)
                if entry is None:
                    log.info("ignoring unknown Cursor TUI %s", name)
                    continue
                tui = _Tui(
                    name,
                    str(entry.get("cwd") or os.path.expanduser("~")),
                    isolate_root=str(entry.get("isolate_root") or ""),
                )
                tui.session_id = str(entry.get("session_id") or "")
                tui.rollout_path = str(entry.get("transcript_path") or "")
                tui.spawned = True
                try:
                    tui.last_used = float(entry.get("last_used") or 0)
                except (TypeError, ValueError):
                    tui.last_used = time.time()
                self._tuis[name] = tui
                log.info("adopted Cursor TUI %s (session %s)",
                         name, tui.session_id[:8] or "unknown")
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._save_state()

    def _tui_name(self, cwd: str) -> str:
        import hashlib
        digest = hashlib.sha1(
            os.path.realpath(cwd).encode("utf-8")).hexdigest()[:8]
        return "%s%s-%s" % (_PREFIX, digest, uuid.uuid4().hex[:6])

    def _cursor_bin(self) -> str:
        return str(getattr(self.config, "cursor_bin", "cursor-agent")
                   or "cursor-agent")

    def _env(self) -> dict:
        env = dict(os.environ)
        env["PATH"] = ":".join([
            str(Path.home() / ".local" / "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            env.get("PATH", ""),
        ])
        extra = getattr(self.config, "cursor_env", None) or {}
        env.update({str(key): str(value) for key, value in extra.items()})
        return env

    def _create_chat(self, cwd: str, isolate_root: str = "") -> tuple[str, str]:
        env = self._env()
        cmd = [self._cursor_bin(), "create-chat"]
        run_cwd = cwd
        kwargs = {}
        if isolate_root:
            from .. import accounts
            env = accounts.isolation_env(env, isolate_root)
            run_cwd = isolate_root
            shell = accounts.isolate_shell_line(
                "cd %s && %s" % (
                    shlex.quote(cwd),
                    " ".join(shlex.quote(part) for part in cmd)),
                isolate_root,
            )
            cmd = ["/bin/sh", "-lc", shell]
        try:
            result = subprocess.run(
                cmd, cwd=run_cwd, env=env, capture_output=True,
                text=True, timeout=30, **kwargs)
        except (OSError, subprocess.SubprocessError) as e:
            return "", "could not create Cursor chat: %s" % e
        sid = (result.stdout or "").strip().splitlines()
        sid = sid[-1].strip() if sid else ""
        if result.returncode != 0 or not _is_session_id(sid):
            detail = (result.stderr or result.stdout or "no session id").strip()
            return "", "could not create Cursor chat: %s" % detail[-240:]
        return sid, ""

    def _store_for_tui(self, tui: _Tui) -> CursorStore:
        if tui.isolate_root:
            key = os.path.realpath(os.path.expanduser(tui.isolate_root))
            store = self.runner._guest_stores.get(key)
            if store is None:
                store = CursorStore(Path(key) / ".cursor", self.config)
                self.runner._guest_stores[key] = store
            return store
        return self.runner.store

    def _transcript_for(self, tui: _Tui) -> str:
        if not tui.session_id:
            return ""
        path = self._store_for_tui(tui)._transcript_path(
            tui.session_id, tui.cwd)
        return str(path) if path is not None else ""

    def _launch(self, tui: _Tui, resume_sid: str, model: str,
                timeout_s: float = _START_TIMEOUT_S) -> str:
        if not resume_sid:
            return "Cursor interactive launch requires a session id"
        parts = [self._cursor_bin(), "--resume", resume_sid]
        flags = str(getattr(
            self.config, "cursor_tui_flags", "") or "").split()
        parts += flags or ["--force", "--trust", "--approve-mcps"]
        if "--approve-mcps" not in parts:
            parts.append("--approve-mcps")
        if not tui.isolate_root:
            parts += ["--workspace", tui.cwd]
        if model and model not in ("", "default", "auto"):
            parts += ["--model", model]

        env = self._env()
        launch_cwd = tui.isolate_root or tui.cwd
        extra = getattr(self.config, "cursor_env", None) or {}
        if tui.isolate_root:
            from .. import accounts
            env = accounts.isolation_env(env, tui.isolate_root)
            shell_cmd = accounts.isolate_shell_line(
                " ".join(shlex.quote(part) for part in parts),
                tui.isolate_root,
            )
        else:
            env_prefix = " ".join(
                "%s=%s" % (key, shlex.quote(str(value)))
                for key, value in env.items()
                if key == "PATH" or key in extra
            )
            shell_cmd = "%s exec %s" % (
                env_prefix,
                " ".join(shlex.quote(part) for part in parts),
            )

        ensure_tmux_server(self._tmux_bin)
        try:
            result = self._tmux(
                "new-session", "-d", "-s", tui.name,
                "-x", "220", "-y", "50", "-c", launch_cwd, shell_cmd)
        except OSError as e:
            return "tmux not available: %s" % e
        except subprocess.TimeoutExpired:
            return "tmux new-session timed out"
        if result.returncode != 0:
            return "tmux failed: %s" % result.stderr.decode(
                "utf-8", errors="replace").strip()
        tui.spawned = True
        tui.session_id = resume_sid

        deadline = time.time() + timeout_s
        while not _pane_ready(self._pane_text(tui.name)):
            if time.time() > deadline or not self._tmux_alive(tui.name):
                tail = self._pane_tail(tui.name)
                self._kill(tui)
                return ("Cursor TUI did not become ready"
                        + ((" — screen: %s" % tail) if tail else ""))
            time.sleep(_POLL_S)

        tui.rollout_path = self._transcript_for(tui)
        if tui.rollout_path:
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
            self._fail(job, "cwd is required for Cursor sessions")
            return
        cwd = os.path.expanduser(job.cwd)
        if not os.path.isdir(cwd):
            self._fail(job, "cwd does not exist: %s" % cwd)
            return
        job.cwd = cwd
        if not job.session_id:
            sid, err = self._create_chat(
                cwd, str(getattr(job, "isolate_root", "") or ""))
            if err:
                self._fail(job, err)
                return
            job.session_id = sid
            with job.lock:
                job.new_session_id = sid
        super().run(job)

    def resume(self, job) -> None:
        """Reattach after daemon restart without dropping guest isolation."""
        if not tmux_available():
            self._fail(job, "interactive mode needs tmux (brew install tmux)")
            return
        sid = (job.new_session_id or job.session_id or "").strip()
        tui = None
        with self._lock:
            if job.tui_name and job.tui_name in self._tuis:
                tui = self._tuis.get(job.tui_name)
            if tui is None and sid:
                for candidate in self._tuis.values():
                    if candidate.session_id == sid:
                        tui = candidate
                        break
        if tui is None or not self._tmux_alive(tui.name):
            cwd = os.path.expanduser(job.cwd or "")
            if not cwd or not os.path.isdir(cwd):
                self._fail(job, "interrupted by daemon restart: cwd missing")
                return
            tui, err = self._ensure_tui(
                cwd, sid, job.model,
                isolate_root=str(getattr(job, "isolate_root", "") or ""))
            if err:
                self._fail(job, "interrupted by daemon restart: %s" % err)
                return
        job.tui_name = tui.name
        with tui.lock:
            tui.job = job
            try:
                job.add_event(
                    "tool", name="daemon",
                    detail="resumed mid-turn after daemon restart")
                job.set_phase("thinking", "resumed")
                self._run_turn(job, tui, time.time(), resume=True)
            finally:
                tui.job = None

    def _try_bind_session(self, tui: _Tui, _launched_at: float) -> bool:
        if not tui.rollout_path:
            tui.rollout_path = self._transcript_for(tui)
            if tui.rollout_path:
                self._save_state()
        return bool(tui.session_id)

    def _rollout_for(self, session_id: str) -> str:
        for tui in self._tuis.values():
            if tui.session_id == session_id:
                return self._transcript_for(tui)
        return ""

    def _count_user_messages(self, path: str) -> int:
        if not path or not Path(path).is_file():
            return -1
        count = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    obj = _safe_json(line)
                    if (isinstance(obj, dict)
                            and (obj.get("role") == "user"
                                 or obj.get("type") == "user")
                            and _human_user_text(obj)):
                        count += 1
        except OSError:
            return -1
        return count

    def _send_prompt(self, tui: _Tui, prompt: str) -> str:
        tui.cursor_local_command = (prompt or "").lstrip().startswith("/")
        return super()._send_prompt(tui, prompt)

    def _confirm_submit(self, tui: _Tui, before: int):
        # Slash commands are consumed by the TUI and never append a user row.
        # Retrying Enter would execute commands such as /fork more than once.
        if getattr(tui, "cursor_local_command", False):
            return
        # Cursor paints "Working" before its transcript writer appends the
        # user row. Treat that screen as acknowledgement; otherwise the
        # inherited journal-only check can press Enter twice on every turn.
        deadline = time.time() + 6.0
        while time.time() < deadline:
            path = tui.rollout_path or self._transcript_for(tui)
            if path and self._count_user_messages(path) > before:
                return
            if "Working" in self._pane_text(tui.name):
                return
            time.sleep(_POLL_S)
        log.warning("Cursor TUI %s: submit not confirmed", tui.name)

    def _poll_rollout(self, job, tui: _Tui) -> bool:
        path = tui.rollout_path or self._transcript_for(tui)
        if path:
            tui.rollout_path = path
        if not path or not Path(path).is_file():
            return False
        try:
            size = Path(path).stat().st_size
        except OSError:
            return False
        if size < tui.rollout_offset:
            tui.rollout_offset = 0
        if size == tui.rollout_offset:
            return bool(job.runner_state.get("turn_done"))
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(tui.rollout_offset)
                chunk = f.read()
                tui.rollout_offset = f.tell()
        except OSError:
            return False

        state = job.runner_state
        for line in chunk.splitlines():
            obj = _safe_json(line)
            if not isinstance(obj, dict):
                continue
            kind = str(obj.get("type") or obj.get("role") or "")
            if kind == "assistant":
                text = _assistant_text(obj)
                if text:
                    state.setdefault("parts", []).append(text)
                    state.setdefault("full", []).append(text)
                    job.add_event(
                        "text", text=text, blocks=markdown_to_blocks(text))
                    job.set_phase("writing", text[-160:])
                for step in _steps_of(obj, {}, 0):
                    if step.get("kind") == "tool_use":
                        name = step.get("name") or "tool"
                        detail = step.get("detail") or step.get("preview") or ""
                        job.add_event("tool", name=name, detail=detail[:280])
                        job.set_phase("tool", (detail or name)[:120])
                continue
            if kind == "user":
                job.set_phase("thinking", "")
                continue
            if kind == "turn_ended":
                status = str(obj.get("status") or "success").lower()
                state["turn_done"] = True
                full = "".join(state.get("full") or state.get("parts") or [])
                with job.lock:
                    if full and not job.result_text:
                        job.result_text = full
                    if status not in ("success", "done", "completed"):
                        job.error = str(obj.get("error") or status)
                job.add_event(
                    "result",
                    is_error=status not in ("success", "done", "completed"),
                    duration_ms=int((time.time() - job.started_at) * 1000),
                    cost_usd=0,
                )
        return bool(state.get("turn_done"))
