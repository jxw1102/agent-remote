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
from .codex import (
    _event_chat_message,
    _event_tool,
    _safe_json as _codex_safe_json,
)

log = logging.getLogger(__name__)

_MAX_TUIS = 6
_START_TIMEOUT_S = 90
_POLL_S = 0.3
_PASTE_SETTLE_S = 0.3
_READY_SETTLE_S = 0.8
_SUBMIT_CONFIRM_S = 4.0
_SUBMIT_RETRIES = 2
# Initial post-submit wait for a brand-new thread id (then the main loop
# keeps probing forever until the turn ends — never fail for missing id).
_DISCOVER_SID_S = 8.0
_LOCAL_QUIET_S = 2.5
# How many recent rollout files to inspect when sqlite lags the disk.
_ROLLOUT_SCAN = 40

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
    # Prefer the shared helper so interactive + store stay in lockstep.
    return _codex_safe_json(line)


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
        """Open the Codex state DB so concurrent WAL writes are visible.

        ``file:…?mode=ro`` can lag or miss WAL commits from the codex
        process; a normal connection + query_only is safer for discovery.
        """
        db = self._state_db()
        if db is None:
            return None
        try:
            con = sqlite3.connect(str(db), timeout=2.0)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA query_only=ON")
            except sqlite3.Error:
                pass
            return con
        except sqlite3.Error as e:
            log.warning("codex state db open failed: %s", e)
            return None

    def _rollout_for(self, session_id: str) -> str:
        if not session_id:
            return ""
        con = self._connect()
        if con is None:
            return self._rollout_path_on_disk(session_id)
        try:
            row = con.execute(
                "SELECT rollout_path FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
            path = (row["rollout_path"] if row else "") or ""
            if path and Path(path).is_file():
                return path
        except sqlite3.Error:
            pass
        finally:
            con.close()
        return self._rollout_path_on_disk(session_id)

    def _rollout_path_on_disk(self, session_id: str) -> str:
        """Find rollout-*<session_id>.jsonl under CODEX_HOME/sessions/."""
        if not session_id:
            return ""
        root = self.config.codex_home_path / "sessions"
        if not root.is_dir():
            return ""
        needle = "-%s.jsonl" % session_id
        try:
            for path in root.rglob("rollout-*.jsonl"):
                name = path.name
                if name.endswith(".bak") or ".rewind" in name:
                    continue
                if name.endswith(needle) or session_id in name:
                    return str(path)
        except OSError:
            pass
        return ""

    @staticmethod
    def _cwd_match(want: str, got: str, raw_got: str = "", raw_want: str = "") -> bool:
        """True if two project paths refer to the same directory (macOS /tmp)."""
        if not want and not got:
            return True
        a = os.path.realpath(os.path.expanduser(want or raw_want or ""))
        b = os.path.realpath(os.path.expanduser(got or raw_got or ""))
        if a and b and (a == b or a.rstrip("/") == b.rstrip("/")):
            return True
        # /var/folders/… vs different resolve of the same project
        if a and b and (a.endswith(b) or b.endswith(a)):
            return True
        ra, rb = (raw_want or "").rstrip("/"), (raw_got or "").rstrip("/")
        return bool(ra and rb and ra == rb)

    def _session_meta_from_rollout(self, path: Path) -> dict:
        """First session_meta payload in a rollout file (cheap head read)."""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 5:
                        break
                    ev = _safe_json(line)
                    if not isinstance(ev, dict) or ev.get("type") != "session_meta":
                        continue
                    payload = ev.get("payload")
                    return payload if isinstance(payload, dict) else {}
        except OSError:
            return {}
        return {}

    def _newest_rollout_for_cwd(self, cwd: str, after_ts: float = 0):
        """(id, path) from on-disk rollouts — often earlier than sqlite index."""
        root = self.config.codex_home_path / "sessions"
        if not root.is_dir():
            return None
        want = os.path.realpath(os.path.expanduser(cwd or ""))
        ranked = []
        try:
            for path in root.rglob("rollout-*.jsonl"):
                name = path.name
                if name.endswith(".bak") or ".rewind" in name:
                    continue
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if after_ts and mtime + 5 < after_ts:
                    continue
                ranked.append((mtime, path))
        except OSError:
            return None
        ranked.sort(key=lambda t: t[0], reverse=True)
        for _, path in ranked[:_ROLLOUT_SCAN]:
            meta = self._session_meta_from_rollout(path)
            sid = str(meta.get("session_id") or meta.get("id") or "").strip()
            if not sid:
                # Filename fallback: rollout-…-<uuid>.jsonl
                stem = path.stem  # rollout-…-uuid
                if "-" in stem:
                    maybe = stem.split("-")
                    # uuid-ish last 5 segments of UUID form
                    for i in range(len(maybe)):
                        cand = "-".join(maybe[i:])
                        if len(cand) >= 32 and cand.count("-") >= 4:
                            sid = cand
                            break
            if not sid:
                continue
            mcwd = str(meta.get("cwd") or "")
            if meta and not self._cwd_match(want, mcwd, mcwd, cwd):
                continue
            # No meta cwd: still accept very new files for this launch window.
            if not meta.get("cwd") and after_ts and path.stat().st_mtime + 5 < after_ts:
                continue
            return sid, str(path)
        return None

    def _newest_thread_for_cwd(self, cwd: str, after_ts: float = 0):
        """Return (id, rollout_path) for the newest thread in cwd, or None."""
        con = self._connect()
        if con is None:
            return None
        try:
            rows = con.execute(
                "SELECT id, cwd, rollout_path, created_at, updated_at "
                "FROM threads ORDER BY COALESCE(updated_at, created_at) DESC "
                "LIMIT 80"
            ).fetchall()
        except sqlite3.Error:
            return None
        finally:
            con.close()
        want = os.path.realpath(os.path.expanduser(cwd or ""))
        for row in rows:
            raw = str(row["cwd"] or "")
            rcwd = os.path.realpath(raw) if raw else ""
            if not self._cwd_match(want, rcwd, raw, cwd):
                continue
            ts = int(row["updated_at"] or row["created_at"] or 0)
            if ts > 10_000_000_000:
                ts = ts // 1000
            if after_ts and ts + 5 < after_ts:
                continue
            return str(row["id"] or ""), str(row["rollout_path"] or "")
        return None

    def _count_user_messages(self, path: str) -> int:
        """Count human user turns in a rollout (legacy + item_completed)."""
        if not path or not Path(path).is_file():
            return -1
        n = 0
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    ev = _safe_json(line)
                    if not isinstance(ev, dict):
                        continue
                    hit = _event_chat_message(ev)
                    if hit and hit[0] == "user":
                        n += 1
        except OSError:
            return -1
        return n

    def _try_bind_session(self, tui: _Tui, launched_at: float) -> bool:
        """One non-blocking attempt to fill tui.session_id / rollout_path.

        Prefers on-disk rollouts (session_meta is the first line) because
        sqlite often lags; falls back to the threads table. Never fails the
        job — just returns False until Codex has written something.
        """
        if tui.session_id and tui.rollout_path and Path(tui.rollout_path).is_file():
            return True
        after = (launched_at - 30) if launched_at else 0
        hit = self._newest_rollout_for_cwd(tui.cwd, after_ts=after)
        if not hit:
            hit = self._newest_thread_for_cwd(tui.cwd, after_ts=after)
        if not hit or not hit[0]:
            # Have id but missing path?
            if tui.session_id and not tui.rollout_path:
                path = self._rollout_for(tui.session_id)
                if path:
                    tui.rollout_path = path
                    self._save_state()
                    return True
            return bool(tui.session_id and tui.rollout_path)
        sid, path = hit[0], hit[1] or ""
        if not path:
            path = self._rollout_for(sid)
        changed = (sid != tui.session_id) or (path and path != tui.rollout_path)
        tui.session_id = sid
        if path:
            tui.rollout_path = path
            if not tui.rollout_offset:
                tui.rollout_offset = 0
        if changed:
            self._save_state()
            log.info("codex TUI %s: bound session %s → %s",
                     tui.name, sid[:12], (path or "")[-48:])
        return bool(tui.session_id)

    def _discover_session(self, tui: _Tui, launched_at: float,
                          timeout_s: float = None) -> bool:
        """Poll until session id appears, or timeout. Does not fail the job."""
        if tui.session_id and tui.rollout_path:
            return True
        limit = _DISCOVER_SID_S if timeout_s is None else float(timeout_s)
        end = time.time() + max(0.0, limit)
        while True:
            if self._try_bind_session(tui, launched_at):
                return True
            if time.time() >= end:
                return bool(tui.session_id)
            time.sleep(_POLL_S)

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

    def capture_tui(self, session_id: str, *, ansi: bool = False) -> dict:
        from ..live_tui import capture_session
        return capture_session(self, session_id, ansi=ansi)

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
            # Tool activity (response_item custom_tool_call, patch/web end, …)
            tool = _event_tool(ev)
            if tool:
                name = tool.get("name") or "tool"
                detail = tool.get("detail") or ""
                job.add_event("tool", name=name, detail=detail)
                job.set_phase("tool", (detail or name)[:120])
                # Keep scanning — same line is never both chat and tool.
                if et != "event_msg":
                    continue

            if et != "event_msg":
                continue
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            ptype = str(payload.get("type") or "")

            # Chat turns: legacy user_message/agent_message OR item_completed.
            chat = _event_chat_message(ev)
            if chat:
                role, text = chat
                if role == "assistant" and text:
                    state.setdefault("parts", []).append(text)
                    state.setdefault("full", []).append(text)
                    job.add_event("text", text=text,
                                  blocks=markdown_to_blocks(text))
                    job.set_phase("writing", text[-160:])
                # user: submit confirm counts these; no phone event needed
                continue

            if ptype == "task_started":
                job.set_phase("thinking", "")
            elif ptype in ("agent_reasoning", "reasoning"):
                job.set_phase("thinking", "")
            elif ptype == "task_complete":
                turn_done = True
                state["turn_done"] = True
                # Newer CLIs put the final answer on last_agent_message when
                # item_completed was missed (e.g. offset after resume).
                last = str(payload.get("last_agent_message") or "").strip()
                if last and not (state.get("full") or state.get("parts")):
                    state.setdefault("parts", []).append(last)
                    state.setdefault("full", []).append(last)
                    job.add_event("text", text=last,
                                  blocks=markdown_to_blocks(last))
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
            job.tui_name = tui.name
            try:
                self._run_turn(job, tui, launched_at, resume=False)
            finally:
                tui.job = None

    def resume(self, job) -> None:
        """Re-attach a mid-turn job after daemon restart (tmux TUI adopted)."""
        if not tmux_available():
            self._fail(job, "interactive mode needs tmux (brew install tmux)")
            return
        sid = (job.new_session_id or job.session_id or "").strip()
        tui = None
        with self._lock:
            if job.tui_name and job.tui_name in self._tuis:
                tui = self._tuis.get(job.tui_name)
            if tui is None and sid:
                for t in self._tuis.values():
                    if t.session_id == sid:
                        tui = t
                        break
        if tui is None or not self._tmux_alive(tui.name):
            cwd = os.path.expanduser(job.cwd or "")
            if not cwd or not os.path.isdir(cwd):
                self._fail(job, "interrupted by daemon restart: cwd missing")
                return
            tui, err = self._ensure_tui(cwd, sid, job.model)
            if err:
                self._fail(job, "interrupted by daemon restart: %s" % err)
                return
        job.tui_name = tui.name
        with tui.lock:
            tui.job = job
            try:
                job.add_event("tool", name="daemon",
                              detail="resumed mid-turn after daemon restart")
                job.set_phase("thinking", "resumed")
                self._run_turn(job, tui, time.time(), resume=True)
            finally:
                tui.job = None

    def close_for_session(self, session_id: str) -> bool:
        """Kill the live TUI hosting this session, if any. Rewind truncates
        the rollout on disk — a running CLI would neither see the edit nor
        survive under it — so the TUI dies first and the next turn resumes
        the rewound session. Refuses mid-turn."""
        sid = (session_id or "").strip()
        if not sid:
            return False
        with self._lock:
            victim = None
            for t in self._tuis.values():
                if t.session_id == sid:
                    victim = t
                    break
            if victim is None:
                return False
            if victim.job is not None:
                from . import RunnerError
                raise RunnerError("finish or stop the running turn first")
            try:
                self._tmux("kill-session", "-t", victim.name)
            except (OSError, subprocess.TimeoutExpired):
                pass
            del self._tuis[victim.name]
            self._save_state()
        return True

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

    def _run_turn(self, job, tui: _Tui, launched_at: float, resume: bool = False):
        state = job.runner_state
        state.clear()
        state.update({"parts": [], "full": [], "turn_done": False})
        with job.lock:
            job.status = "running"
            if tui.session_id:
                job.new_session_id = tui.session_id or job.new_session_id
            job.tui_name = tui.name
        if not resume:
            job.add_event("init", session_id=tui.session_id or "",
                          model=job.model or "interactive")

        # Resume: start tailing after existing bytes so old messages don't replay.
        if tui.session_id and not tui.rollout_path:
            tui.rollout_path = self._rollout_for(tui.session_id)
        if tui.rollout_path:
            try:
                tui.rollout_offset = Path(tui.rollout_path).stat().st_size
            except OSError:
                tui.rollout_offset = 0

        if not resume:
            before = self._count_user_messages(
                tui.rollout_path or self._rollout_for(tui.session_id))
            err = self._send_prompt(tui, job.prompt)
            if err:
                self._fail(job, err)
                return
        else:
            log.info("codex TUI %s: resume watch for job %s", tui.name, job.id)
            before = -1

        # New sessions: brief wait for id (disk/sqlite), then keep going.
        # Never fail for a missing id — the main loop rebinds when Codex writes.
        bound_announced = bool(tui.session_id)
        if not resume and not tui.session_id:
            job.set_phase("thinking", "starting session")
            self._discover_session(tui, launched_at, timeout_s=_DISCOVER_SID_S)
            if tui.session_id:
                bound_announced = True
                with job.lock:
                    job.new_session_id = tui.session_id
                job.add_event("init", session_id=tui.session_id,
                              model=job.model or "interactive")
            else:
                log.info("codex TUI %s: session id not ready yet — "
                         "continuing; will bind when rollout appears", tui.name)

        if not resume:
            if before >= 0:
                self._confirm_submit(tui, before)
            elif tui.rollout_path:
                self._confirm_submit(tui, 0)
            else:
                # Brand-new: no path yet — nudge Enter while discovery races.
                self._confirm_submit(tui, 0)

        prompt = (job.prompt or "").strip()
        local = prompt.startswith("/")
        timeout_s = float(getattr(self.config, "turn_timeout", 0) or 0)
        if resume and timeout_s > 0:
            deadline = time.time() + timeout_s
        else:
            deadline = job.started_at + timeout_s if timeout_s > 0 else None
        quiet_since = time.time()
        interrupted = False
        tui.typed_ahead = 0
        last_off = tui.rollout_offset
        last_bind_try = 0.0

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
                # TUI may die after the turn already landed on disk (common
                # when submit-confirm spammed Enter against the new event
                # format). Prefer finishing from the rollout over hard-fail.
                self._try_bind_session(tui, launched_at)
                self._poll_rollout(job, tui)
                if state.get("turn_done") or state.get("full") or state.get("parts"):
                    text = "".join(state.get("full") or state.get("parts") or [])
                    with job.lock:
                        job.new_session_id = tui.session_id or job.new_session_id
                    self._done(job, text)
                    return
                self._fail(job, "codex TUI exited mid-turn")
                break

            # Non-blocking rebind every poll until we have id+path.
            now = time.time()
            if (not tui.session_id or not tui.rollout_path) and now - last_bind_try >= _POLL_S:
                last_bind_try = now
                if self._try_bind_session(tui, launched_at) and tui.session_id:
                    with job.lock:
                        job.new_session_id = tui.session_id
                    if not bound_announced:
                        bound_announced = True
                        job.add_event("init", session_id=tui.session_id,
                                      model=job.model or "interactive")
                        job.set_phase("thinking", "")
                    if tui.rollout_path and not last_off:
                        # Start reading from the beginning of a brand-new rollout.
                        tui.rollout_offset = 0
                        last_off = 0

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
                # Last chance to bind before finishing.
                self._try_bind_session(tui, launched_at)
                text = "".join(state.get("full") or state.get("parts") or [])
                with job.lock:
                    job.new_session_id = tui.session_id or job.new_session_id
                self._done(job, text)
                return

            if local and time.time() - quiet_since > _LOCAL_QUIET_S:
                # Slash commands may not emit task_complete.
                self._try_bind_session(tui, launched_at)
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
            self._try_bind_session(tui, launched_at)
            text = "".join(state.get("full") or state.get("parts") or [])
            with job.lock:
                if job.status == "stopped":
                    job.result_text = text
                    job.new_session_id = tui.session_id or job.new_session_id
            return
        # Fall-through if TUI died after partial result.
        if state.get("full") and job.status == "running":
            self._try_bind_session(tui, launched_at)
            with job.lock:
                job.new_session_id = tui.session_id or job.new_session_id
            self._done(job, "".join(state["full"]))
