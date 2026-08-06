"""Interactive mode for Codex: drive a real `codex` TUI inside tmux.

Why this exists: headless ``codex exec`` covers one-shot turns, but the
product also has a full interactive CLI (default ``codex`` / ``codex resume``).
Phone clients already expose an "interactive" execution mode for Claude and
Grok; this wires Codex to the same path.

Design (patterned on grok_interactive, simpler):

  * One detached tmux session per Codex thread id.
  * New: ``codex -C <cwd> --dangerously-bypass-approvals-and-sandbox``
  * Resume: ``codex resume <id> -C <cwd> --dangerously-bypass-approvals-and-sandbox``
  * Prompts are bracketed-pasted + Enter (same as grok).
  * Progress and turn-end come from the on-disk rollout JSONL
    (``event_msg/task_complete``, ``agent_message``, …) — no hooks plugin.
  * Session id for a brand-new TUI is discovered from the state sqlite after
    the first accepted user message (or from the newest thread for that cwd).

Interactive is auto-approve by definition (YOLO / bypass flags): approval
panels would render in a pane nobody can see.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

from ..config import CONFIG_DIR
from ..render_blocks import markdown_to_blocks

log = logging.getLogger(__name__)

_MAX_TUIS = 6
_START_TIMEOUT_S = 90
_POLL_S = 0.3
_PASTE_SETTLE_S = 0.3
_READY_SETTLE_S = 0.8
_SUBMIT_CONFIRM_S = 4.0
_SUBMIT_RETRIES = 2
_DISCOVER_SID_S = 30.0
_LOCAL_QUIET_S = 2.5

_STATE_FILE = CONFIG_DIR / "codex-tuis.json"
_PREFIX = "cdx-"
_OLD_PREFIXES = ("cdx-",)

_TMUX_FALLBACK = "/opt/homebrew/bin/tmux"

# Ready markers observed on codex-cli 0.146 (startup banner + prompt).
_READY_MARKERS = (
    "OpenAI Codex",
    "›",          # prompt chevron (U+203A)
    "YOLO mode",
    "permissions:",
)


def tmux_available() -> bool:
    return bool(shutil.which("tmux")) or os.path.exists(_TMUX_FALLBACK)


def _pane_ready(text: str) -> bool:
    if not text or not text.strip():
        return False
    # Prefer the input chevron near the bottom (resume can echo old › lines).
    rows = [l.strip() for l in text.splitlines() if l.strip()]
    for row in rows[-6:]:
        if row.startswith("›"):
            return True
    return any(m in text for m in _READY_MARKERS)


def _safe_json(line: str):
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


class _Tui:
    """One detached tmux session hosting one Codex TUI."""

    def __init__(self, name: str, cwd: str):
        self.name = name
        self.cwd = cwd
        self.session_id = ""
        self.spawned = False
        self.last_used = time.time()
        self.job = None
        self.typed_ahead = 0
        self.lock = threading.Lock()
        self.rollout_path = ""
        self.rollout_offset = 0


class CodexInteractiveManager:
    """Owns tmux Codex TUIs and runs interactive jobs against them."""

    def __init__(self, config, runner):
        self.config = config
        self.runner = runner  # CodexRunner (store for sqlite/rollout)
        self._tuis = {}
        self._lock = threading.Lock()
        self._adopt_or_reap()

    # -- registry ----------------------------------------------------------

    def _save_state(self):
        rows = [{"name": t.name, "cwd": t.cwd, "session_id": t.session_id,
                 "last_used": t.last_used, "rollout_path": t.rollout_path}
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
            if isinstance(entry, dict) and str(entry.get("name", "")).startswith(_OLD_PREFIXES):
                known[str(entry["name"])] = entry
        try:
            r = self._tmux("list-sessions", "-F", "#{session_name}", capture=True)
            if r.returncode != 0:
                return
            for name in r.stdout.decode("utf-8", errors="replace").split():
                if not name.startswith(_OLD_PREFIXES):
                    continue
                entry = known.get(name)
                if entry is None:
                    log.info("reaping unknown codex TUI %s", name)
                    self._tmux("kill-session", "-t", name)
                    continue
                tui = _Tui(name, str(entry.get("cwd", "")) or os.path.expanduser("~"))
                tui.session_id = str(entry.get("session_id", ""))
                tui.rollout_path = str(entry.get("rollout_path", ""))
                tui.spawned = True
                try:
                    tui.last_used = float(entry.get("last_used") or 0) or time.time()
                except (TypeError, ValueError):
                    tui.last_used = time.time()
                self._tuis[name] = tui
                log.info("adopted codex TUI %s (session %s)", name,
                         tui.session_id[:8] or "unknown")
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._save_state()

    # -- tmux helpers ------------------------------------------------------

    @property
    def _tmux_bin(self):
        return shutil.which("tmux") or _TMUX_FALLBACK

    def _tmux(self, *args, capture=False, input_bytes=None):
        return subprocess.run(
            [self._tmux_bin] + list(args), input=input_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=15)

    def _tmux_alive(self, name: str) -> bool:
        try:
            return self._tmux("has-session", "-t", name).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _pane_text(self, name: str, *, ansi: bool = False) -> str:
        # -e keeps SGR colour sequences for Live TUI; -J joins wrapped lines.
        args = ["capture-pane", "-p", "-J"]
        if ansi:
            args.append("-e")
        args.extend(["-t", name])
        try:
            out = self._tmux(*args, capture=True)
            return out.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _pane_tail(self, name: str, lines: int = 6) -> str:
        rows = [l.rstrip() for l in self._pane_text(name).splitlines() if l.strip()]
        return " | ".join(rows[-lines:])

    def _kill(self, tui: _Tui):
        try:
            self._tmux("kill-session", "-t", tui.name)
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -- TUI lifecycle -----------------------------------------------------

    def _tui_name(self, cwd: str) -> str:
        h = hashlib.sha1(os.path.realpath(cwd).encode("utf-8")).hexdigest()[:8]
        return "%s%s-%s" % (_PREFIX, h, uuid.uuid4().hex[:6])

    def _codex_bin(self) -> str:
        return str(getattr(self.config, "codex_bin", "codex") or "codex")

    def _launch(self, tui: _Tui, resume_sid: str, model: str,
                timeout_s: float = _START_TIMEOUT_S) -> str:
        """Start the TUI in a fresh tmux session. Returns \"\" or an error."""
        bin_path = self._codex_bin()
        # YOLO-style auto mode: cannot combine with -a <policy>.
        parts = [bin_path]
        if resume_sid:
            parts += ["resume", resume_sid]
        parts += [
            "-C", tui.cwd,
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if model and model not in ("", "default"):
            parts += ["-m", model]

        env = dict(os.environ)
        home = str(self.config.codex_home_path)
        env["CODEX_HOME"] = home
        # Ensure codex is findable when launched via bare name.
        path_extra = [
            str(Path.home() / ".local" / "bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
        ]
        env["PATH"] = ":".join(path_extra + [env.get("PATH", "")])
        extra_env = getattr(self.config, "codex_env", None) or {}
        env.update({str(k): str(v) for k, v in extra_env.items()})

        # Use env= on shell_cmd so tmux child sees CODEX_HOME/PATH.
        env_prefix = " ".join(
            "%s=%s" % (k, shlex.quote(str(v)))
            for k, v in env.items()
            if k in ("CODEX_HOME", "PATH") or k in extra_env
        )
        shell_cmd = "%s exec %s" % (
            env_prefix,
            " ".join(shlex.quote(p) for p in parts),
        )
        tui.session_id = resume_sid or ""
        try:
            r = self._tmux("new-session", "-d", "-s", tui.name,
                           "-x", "220", "-y", "50", "-c", tui.cwd, shell_cmd)
        except OSError as e:
            return "tmux not available: %s" % e
        except subprocess.TimeoutExpired:
            return "tmux new-session timed out"
        if r.returncode != 0:
            return "tmux failed: %s" % r.stderr.decode(
                "utf-8", errors="replace").strip()
        tui.spawned = True

        deadline = time.time() + timeout_s
        while not _pane_ready(self._pane_text(tui.name)):
            if time.time() > deadline or not self._tmux_alive(tui.name):
                tail = self._pane_tail(tui.name)
                self._kill(tui)
                return ("codex TUI did not become ready" +
                        (" — screen: %s" % tail if tail else ""))
            time.sleep(_POLL_S)

        if resume_sid:
            tui.session_id = resume_sid
            path = self._rollout_for(resume_sid)
            if path:
                tui.rollout_path = path
                try:
                    tui.rollout_offset = Path(path).stat().st_size
                except OSError:
                    tui.rollout_offset = 0
        time.sleep(_READY_SETTLE_S)
        return ""

    def _ensure_tui(self, cwd: str, session_id: str, model: str):
        with self._lock:
            dead = [n for n, t in self._tuis.items()
                    if t.spawned and not self._tmux_alive(n)]
            for name in dead:
                del self._tuis[name]
            if dead:
                self._save_state()
            if session_id:
                for t in self._tuis.values():
                    if t.session_id == session_id:
                        t.last_used = time.time()
                        return t, ""
            while len(self._tuis) >= _MAX_TUIS:
                idle = [t for t in self._tuis.values() if t.job is None]
                if not idle:
                    break
                victim = min(idle, key=lambda t: t.last_used)
                log.info("evicting idle codex TUI %s (cap %d)",
                         victim.name, _MAX_TUIS)
                self._kill(victim)
                del self._tuis[victim.name]
                self._save_state()
            tui = _Tui(self._tui_name(cwd), cwd)
            self._tuis[tui.name] = tui
        err = self._launch(tui, session_id, model)
        if err:
            with self._lock:
                self._tuis.pop(tui.name, None)
            self._save_state()
            return None, err
        self._save_state()
        return tui, ""

    # -- sqlite / rollout discovery ----------------------------------------

    def _state_db(self) -> Path | None:
        home = self.config.codex_home_path
        if not home.is_dir():
            return None
        candidates = sorted(home.glob("state_*.sqlite"), reverse=True)
        for path in candidates:
            if path.is_file():
                return path
        legacy = home / "state.sqlite"
        return legacy if legacy.is_file() else None

    def _connect(self):
        db = self._state_db()
        if db is None:
            return None
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            return con
        except sqlite3.Error as e:
            log.warning("codex state db open failed: %s", e)
            return None

    def _rollout_for(self, session_id: str) -> str:
        if not session_id:
            return ""
        con = self._connect()
        if con is None:
            return ""
        try:
            row = con.execute(
                "SELECT rollout_path FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
            return (row["rollout_path"] if row else "") or ""
        except sqlite3.Error:
            return ""
        finally:
            con.close()

    def _newest_thread_for_cwd(self, cwd: str, after_ts: float = 0):
        """Return (id, rollout_path) for the newest thread in cwd, or None."""
        con = self._connect()
        if con is None:
            return None
        try:
            rows = con.execute(
                "SELECT id, cwd, rollout_path, created_at, updated_at "
                "FROM threads ORDER BY COALESCE(updated_at, created_at) DESC "
                "LIMIT 40"
            ).fetchall()
        except sqlite3.Error:
            return None
        finally:
            con.close()
        want = os.path.realpath(os.path.expanduser(cwd or ""))
        for row in rows:
            rcwd = os.path.realpath(str(row["cwd"] or ""))
            if rcwd != want and str(row["cwd"] or "").rstrip("/") != cwd.rstrip("/"):
                # macOS /tmp vs /private/tmp
                if not (want.endswith(str(row["cwd"] or "")) or
                        rcwd.endswith(cwd.rstrip("/"))):
                    continue
            ts = int(row["updated_at"] or row["created_at"] or 0)
            if ts > 10_000_000_000:
                ts = ts // 1000
            if after_ts and ts + 2 < after_ts:
                continue
            return str(row["id"] or ""), str(row["rollout_path"] or "")
        return None

    def _count_user_messages(self, path: str) -> int:
        if not path or not Path(path).is_file():
            return -1
        n = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    ev = _safe_json(line)
                    if not isinstance(ev, dict):
                        continue
                    payload = ev.get("payload") if ev.get("type") == "event_msg" else None
                    if isinstance(payload, dict) and payload.get("type") == "user_message":
                        n += 1
        except OSError:
            return -1
        return n

    def _discover_session(self, tui: _Tui, launched_at: float) -> bool:
        """Fill tui.session_id / rollout_path after a new session starts."""
        if tui.session_id and tui.rollout_path:
            return True
        end = time.time() + _DISCOVER_SID_S
        while time.time() < end:
            hit = self._newest_thread_for_cwd(tui.cwd, after_ts=launched_at - 5)
            if hit and hit[0]:
                tui.session_id = hit[0]
                tui.rollout_path = hit[1] or self._rollout_for(hit[0])
                if tui.rollout_path:
                    try:
                        # Tail only new bytes if we already had an offset.
                        if not tui.rollout_offset:
                            tui.rollout_offset = 0
                    except Exception:
                        pass
                self._save_state()
                return True
            time.sleep(_POLL_S)
        return bool(tui.session_id)

    # -- input -------------------------------------------------------------

    def _send_prompt(self, tui: _Tui, prompt: str) -> str:
        buf = "cdx-%s" % uuid.uuid4().hex[:8]
        try:
            for step in (
                ("load-buffer", "-b", buf, "-"),
                ("paste-buffer", "-p", "-d", "-b", buf, "-t", tui.name),
            ):
                r = self._tmux(*step, input_bytes=prompt.encode("utf-8")
                               if step[0] == "load-buffer" else None)
                if r.returncode != 0:
                    return "tmux %s failed: %s" % (
                        step[0], r.stderr.decode("utf-8", errors="replace").strip())
            time.sleep(_PASTE_SETTLE_S)
            r = self._tmux("send-keys", "-t", tui.name, "Enter")
            if r.returncode != 0:
                return "tmux send-keys failed: %s" % (
                    r.stderr.decode("utf-8", errors="replace").strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            return "tmux input failed: %s" % e
        return ""

    def _confirm_submit(self, tui: _Tui, before: int):
        if before < 0:
            return
        for attempt in range(_SUBMIT_RETRIES + 1):
            end = time.time() + _SUBMIT_CONFIRM_S
            while time.time() < end:
                path = tui.rollout_path or self._rollout_for(tui.session_id)
                if path:
                    tui.rollout_path = path
                    n = self._count_user_messages(path)
                    if n > before:
                        return
                time.sleep(_POLL_S)
            if attempt >= _SUBMIT_RETRIES:
                break
            log.warning("TUI %s: no submit after %.0fs, pressing Enter again",
                        tui.name, _SUBMIT_CONFIRM_S)
            try:
                self._tmux("send-keys", "-t", tui.name, "Enter")
            except (OSError, subprocess.TimeoutExpired):
                return
        log.warning("TUI %s: submit unconfirmed", tui.name)

    def type_text(self, session_id: str, text: str) -> str:
        if not text.strip():
            return "empty message"
        with self._lock:
            tui = None
            for t in self._tuis.values():
                if session_id and t.session_id == session_id:
                    tui = t
                    break
        if tui is None:
            return "no interactive TUI for this session"
        if not self._tmux_alive(tui.name):
            return "the codex TUI has exited"
        tui.typed_ahead += 1
        path = tui.rollout_path or self._rollout_for(session_id)
        before = self._count_user_messages(path) if path else -1
        err = self._send_prompt(tui, text)
        if err:
            tui.typed_ahead = max(0, tui.typed_ahead - 1)
            return err
        if before >= 0:
            threading.Thread(target=self._confirm_submit, args=(tui, before),
                             daemon=True).start()
        return ""

    def capture_tui(self, session_id: str) -> dict:
        from ..live_tui import capture_session
        return capture_session(self, session_id)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        from ..live_tui import send_to_session
        return send_to_session(self, session_id, keys=keys, text=text)

    # -- rollout tail → job events -----------------------------------------

    def _poll_rollout(self, job, tui: _Tui) -> bool:
        """Read new rollout bytes. Returns True if task_complete seen."""
        path = tui.rollout_path or self._rollout_for(tui.session_id)
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
            tui.rollout_offset = 0  # truncated / rotated
        if size == tui.rollout_offset:
            return bool(state.get("turn_done"))
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(tui.rollout_offset)
                chunk = f.read()
                tui.rollout_offset = f.tell()
        except OSError:
            return False
        turn_done = False
        for line in chunk.splitlines():
            ev = _safe_json(line)
            if not isinstance(ev, dict):
                continue
            et = str(ev.get("type") or "")
            if et == "session_meta":
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                sid = str(payload.get("session_id") or payload.get("id") or "").strip()
                if sid and not tui.session_id:
                    tui.session_id = sid
                    with job.lock:
                        job.new_session_id = sid
                continue
            if et != "event_msg":
                # response_item / tool-ish payloads — optional phase only
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                itype = str(payload.get("type") or "")
                if itype in ("function_call", "custom_tool_call", "tool_call"):
                    name = str(payload.get("name") or payload.get("tool") or "tool")
                    job.set_phase("tool", name[:120])
                continue
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            ptype = str(payload.get("type") or "")
            if ptype == "task_started":
                job.set_phase("thinking", "")
            elif ptype == "user_message":
                # Confirm submit landed; no event needed for the phone
                # (messages load from store).
                pass
            elif ptype == "agent_message":
                text = str(payload.get("message") or payload.get("text") or "").strip()
                if text:
                    state.setdefault("parts", []).append(text)
                    state.setdefault("full", []).append(text)
                    job.add_event("text", text=text,
                                  blocks=markdown_to_blocks(text))
                    job.set_phase("writing", text[-160:])
            elif ptype in ("agent_reasoning", "reasoning"):
                job.set_phase("thinking", "")
            elif ptype in ("command_execution", "exec_command"):
                cmd = str(payload.get("command") or payload.get("cmd") or "shell")
                job.add_event("tool", name="shell", detail=cmd[:200])
                job.set_phase("tool", cmd[:120])
            elif ptype == "task_complete":
                turn_done = True
                state["turn_done"] = True
                full = "".join(state.get("full") or state.get("parts") or [])
                with job.lock:
                    if full and not job.result_text:
                        job.result_text = full
                job.add_event(
                    "result",
                    is_error=False,
                    duration_ms=int((time.time() - job.started_at) * 1000),
                    cost_usd=0,
                )
        return turn_done or bool(state.get("turn_done"))

    # -- turn execution ----------------------------------------------------

    def run(self, job) -> None:
        if not tmux_available():
            self._fail(job, "interactive mode needs tmux (brew install tmux)")
            return
        if not job.cwd:
            self._fail(job, "cwd is required for codex sessions")
            return
        cwd = os.path.expanduser(job.cwd)
        if not os.path.isdir(cwd):
            self._fail(job, "cwd does not exist: %s" % cwd)
            return
        job.cwd = cwd
        launched_at = time.time()
        tui, err = self._ensure_tui(cwd, job.session_id, job.model)
        if err:
            self._fail(job, err)
            return
        with tui.lock:
            tui.job = job
            try:
                self._run_turn(job, tui, launched_at)
            finally:
                tui.job = None

    def _fail(self, job, message: str):
        with job.lock:
            if job.status != "stopped":
                job.status = "error"
                job.error = message

    def _done(self, job, text: str):
        with job.lock:
            if job.status != "stopped":
                job.result_text = text
                job.status = "done"
        if not any(e.get("kind") == "result" for e in list(job.events)):
            job.add_event("result", is_error=False,
                          duration_ms=int((time.time() - job.started_at) * 1000),
                          cost_usd=0)

    def _run_turn(self, job, tui: _Tui, launched_at: float):
        state = job.runner_state
        state.clear()
        state.update({"parts": [], "full": [], "turn_done": False})
        with job.lock:
            job.status = "running"
            if tui.session_id:
                job.new_session_id = tui.session_id
        job.add_event("init", session_id=tui.session_id or "",
                      model=job.model or "interactive")

        # Resume: start tailing after existing bytes so old messages don't replay.
        if tui.session_id and not tui.rollout_path:
            tui.rollout_path = self._rollout_for(tui.session_id)
        if tui.rollout_path and tui.session_id == job.session_id:
            try:
                tui.rollout_offset = Path(tui.rollout_path).stat().st_size
            except OSError:
                tui.rollout_offset = 0

        before = self._count_user_messages(
            tui.rollout_path or self._rollout_for(tui.session_id))
        err = self._send_prompt(tui, job.prompt)
        if err:
            self._fail(job, err)
            return

        # New sessions: discover id after submit.
        if not tui.session_id:
            job.set_phase("thinking", "starting session")
            if not self._discover_session(tui, launched_at):
                # Still try to stream from pane quiet-timeout later.
                log.warning("codex TUI %s: session id not discovered yet", tui.name)
            else:
                with job.lock:
                    job.new_session_id = tui.session_id
                job.add_event("init", session_id=tui.session_id,
                              model=job.model or "interactive")
                if tui.rollout_path:
                    # Include the user message we just wrote — offset 0 is fine
                    # for a brand-new file; for an existing one keep current.
                    pass

        if before >= 0:
            self._confirm_submit(tui, before)
        elif tui.rollout_path:
            self._confirm_submit(tui, 0)

        prompt = (job.prompt or "").strip()
        local = prompt.startswith("/")
        timeout_s = float(getattr(self.config, "turn_timeout", 0) or 0)
        deadline = job.started_at + timeout_s if timeout_s > 0 else None
        quiet_since = time.time()
        interrupted = False
        tui.typed_ahead = 0
        last_off = tui.rollout_offset

        while True:
            time.sleep(_POLL_S)
            if job.status == "stopped":
                interrupted = True
                try:
                    self._tmux("send-keys", "-t", tui.name, "Escape")
                except (OSError, subprocess.TimeoutExpired):
                    pass
                break
            if not self._tmux_alive(tui.name):
                if not state.get("turn_done"):
                    self._fail(job, "codex TUI exited mid-turn")
                break
            if not tui.session_id:
                self._discover_session(tui, launched_at)
                if tui.session_id:
                    with job.lock:
                        job.new_session_id = tui.session_id

            done = self._poll_rollout(job, tui)
            if tui.rollout_offset != last_off:
                quiet_since = time.time()
                last_off = tui.rollout_offset

            if done or state.get("turn_done"):
                if tui.typed_ahead > 0:
                    # Mid-turn typed message — keep watching for its reply.
                    tui.typed_ahead = 0
                    state["turn_done"] = False
                    quiet_since = time.time()
                    continue
                text = "".join(state.get("full") or state.get("parts") or [])
                with job.lock:
                    job.new_session_id = tui.session_id or job.new_session_id
                self._done(job, text)
                return

            if local and time.time() - quiet_since > _LOCAL_QUIET_S:
                # Slash commands may not emit task_complete.
                text = "".join(state.get("full") or []) or self._pane_tail(tui.name, 12)
                with job.lock:
                    job.new_session_id = tui.session_id or job.new_session_id
                self._done(job, text)
                return

            if deadline and time.time() > deadline:
                try:
                    self._tmux("send-keys", "-t", tui.name, "Escape")
                except (OSError, subprocess.TimeoutExpired):
                    pass
                self._fail(job, "turn timed out after %ss" % int(timeout_s))
                return

        if interrupted:
            text = "".join(state.get("full") or state.get("parts") or [])
            with job.lock:
                if job.status == "stopped":
                    job.result_text = text
            return
        # Fall-through if TUI died after partial result.
        if state.get("full") and job.status == "running":
            self._done(job, "".join(state["full"]))
