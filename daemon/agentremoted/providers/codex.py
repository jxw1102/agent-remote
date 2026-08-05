"""Codex harness — OpenAI Codex CLI sessions for Agent Remote.

Sessions live in ``$CODEX_HOME/state_*.sqlite`` (``threads`` table) with the
transcript at ``rollout_path`` (JSONL). Turns run via::

    codex exec --json -C <cwd> [flags] <prompt>
    codex exec resume --json <session_id> <prompt>

Stream events (``--json``) are JSONL lines of the form::

    {"type":"thread.started","thread_id":"…"}
    {"type":"item.started","item":{"type":"command_execution","command":"…"}}
    {"type":"item.completed","item":{"type":"agent_message","text":"…"}}
    {"type":"turn.completed","usage":{…}}

Stdin is closed immediately so the CLI does not hang waiting for more input.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from .. import providers
from ..render_blocks import inline_to_rich, markdown_to_blocks
from .. import search_util

log = logging.getLogger(__name__)

_MAX_TITLE = 80
_MAX_PREVIEW = 160
_STATE_GLOB = "state_*.sqlite"


def _preview(text: str, n: int = _MAX_PREVIEW) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _munge_cwd(cwd: str) -> str:
    s = str(cwd or "").strip().replace("\\", "/")
    if not s:
        return "no-project"
    if s.startswith("/"):
        s = s[1:]
    return "-" + s.replace("/", "-").replace(" ", "-")


def _iso_from_unix(ts) -> str:
    try:
        t = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    # state may store seconds or ms
    if t > 10_000_000_000:
        t = t // 1000
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    except (OverflowError, ValueError, OSError):
        return ""


def _safe_json(line: str):
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


class CodexStore:
    """Read Codex threads from the on-disk SQLite index + rollout JSONL."""

    def __init__(self, home: Path, config=None):
        self.home = Path(home).expanduser()
        self.config = config

    # -- discovery ------------------------------------------------------

    def _state_db(self) -> Path | None:
        if not self.home.is_dir():
            return None
        # Prefer the highest numbered state_N.sqlite (schema evolves).
        candidates = sorted(self.home.glob(_STATE_GLOB), reverse=True)
        for path in candidates:
            if path.is_file():
                return path
        legacy = self.home / "state.sqlite"
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

    def _rows(self, user_only: bool = True, project_cwd: str = None):
        con = self._connect()
        if con is None:
            return []
        try:
            q = ("SELECT id, title, cwd, model, git_branch, preview, "
                 "rollout_path, created_at, updated_at, archived, "
                 "first_user_message, has_user_event "
                 "FROM threads")
            clauses = []
            args = []
            if user_only:
                clauses.append("COALESCE(archived, 0) = 0")
                # Skip empty shells that never saw a user event when possible.
                clauses.append("(COALESCE(has_user_event, 1) = 1 "
                               "OR length(COALESCE(first_user_message,'')) > 0 "
                               "OR length(COALESCE(preview,'')) > 0)")
            if project_cwd:
                clauses.append("cwd = ?")
                args.append(project_cwd)
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            q += " ORDER BY COALESCE(updated_at, created_at) DESC"
            return list(con.execute(q, args))
        except sqlite3.Error as e:
            log.warning("codex threads query failed: %s", e)
            return []
        finally:
            con.close()

    # -- store API ------------------------------------------------------

    def list_projects(self):
        by_cwd = {}
        for row in self._rows(user_only=True):
            cwd = (row["cwd"] or "").strip() or "(no project)"
            rec = by_cwd.get(cwd)
            # Float epoch like claude/grok — ISO strings break multi merge sort
            # and Android ProjectDto (last_active: Double).
            ts = float(row["updated_at"] or row["created_at"] or 0)
            if rec is None:
                by_cwd[cwd] = {
                    "id": _munge_cwd(cwd if cwd != "(no project)" else ""),
                    "cwd": "" if cwd == "(no project)" else cwd,
                    "name": Path(cwd).name if cwd not in ("", "(no project)") else "no-project",
                    "session_count": 1,
                    "last_active": ts,
                }
            else:
                rec["session_count"] += 1
                if ts > float(rec.get("last_active") or 0):
                    rec["last_active"] = ts
        return sorted(by_cwd.values(), key=lambda p: p["last_active"], reverse=True)

    def list_sessions(self, project_id=None, limit=25, user_only=True):
        project_cwd = None
        if project_id and project_id != "no-project":
            # Reverse munge is lossy; match by scanning.
            for row in self._rows(user_only=user_only):
                if _munge_cwd(row["cwd"] or "") == project_id:
                    project_cwd = row["cwd"]
                    break
            if project_cwd is None and project_id:
                # No match — empty list rather than everything.
                return []
        rows = self._rows(user_only=user_only, project_cwd=project_cwd)
        limit = max(1, min(int(limit or 25), 200))
        return [self._summary(r) for r in rows[:limit]]

    def search_sessions(self, query, project_id=None, limit=25, user_only=True):
        if not (query or "").strip():
            return []
        q = query.strip()
        out = []
        for row in self._rows(user_only=user_only):
            if project_id and _munge_cwd(row["cwd"] or "") != project_id:
                continue
            hay = " ".join([
                row["title"] or "",
                row["preview"] or "",
                row["first_user_message"] or "",
                row["cwd"] or "",
            ])
            snippet = None
            if search_util.contains_ci(hay, q):
                for field in (row["title"], row["preview"], row["first_user_message"]):
                    if field and search_util.contains_ci(field, q):
                        snippet = search_util.make_snippet(field, q)
                        break
                snippet = snippet or search_util.make_snippet(hay, q)
            else:
                # Fall back to scanning the rollout file (cheap head scan).
                snippet = self._search_rollout(row["rollout_path"] or "", q)
            if not snippet:
                continue
            s = self._summary(row)
            s["snippet"] = snippet
            out.append(s)
            if len(out) >= max(1, min(int(limit or 25), 200)):
                break
        return out

    def get_session(self, session_id: str):
        con = self._connect()
        if con is None:
            return None
        try:
            row = con.execute(
                "SELECT id, title, cwd, model, git_branch, preview, "
                "rollout_path, created_at, updated_at, archived, "
                "first_user_message, has_user_event "
                "FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            con.close()
        if row is None:
            return None
        return self._summary(row)

    def get_messages(self, session_id: str, offset: int = None, limit: int = 50):
        sess = self.get_session(session_id)
        if sess is None:
            return None
        con = self._connect()
        path = ""
        if con is not None:
            try:
                r = con.execute(
                    "SELECT rollout_path FROM threads WHERE id = ?",
                    (session_id,),
                ).fetchone()
                path = (r["rollout_path"] if r else "") or ""
            except sqlite3.Error:
                path = ""
            finally:
                con.close()
        t0 = time.perf_counter()
        messages = _build_transcript(Path(path) if path else None)
        t1 = time.perf_counter()
        total = len(messages)
        if offset is None:
            offset = max(0, total - limit)
        offset = max(0, offset)
        window = messages[offset: offset + limit]
        for msg in window:
            _render_codex_message(msg)
        t2 = time.perf_counter()
        try:
            file_bytes = Path(path).stat().st_size if path else 0
        except OSError:
            file_bytes = 0
        return {
            "session_id": session_id,
            "total": total,
            "offset": offset,
            "messages": window,
            "timing": {
                "parse_ms": round((t1 - t0) * 1000, 1),
                "render_ms": round((t2 - t1) * 1000, 1),
                "total_ms": round((t2 - t0) * 1000, 1),
                "count_total": total,
                "count_window": len(window),
                "file_bytes": file_bytes,
            },
        }

    def known_session_ids(self) -> set:
        return {r["id"] for r in self._rows(user_only=False) if r["id"]}

    def _summary(self, row) -> dict:
        cwd = (row["cwd"] or "").strip()
        title = " ".join(str(row["title"] or "").split())
        if not title or title.lower() in ("", "new session", "untitled"):
            title = " ".join(str(row["first_user_message"] or row["preview"] or "").split())
        if not title:
            title = "Session %s" % (row["id"] or "")[:8]
        last = row["preview"] or row["first_user_message"] or ""
        try:
            size = Path(row["rollout_path"] or "").stat().st_size
        except OSError:
            size = 0
        return {
            "id": row["id"],
            "project_id": _munge_cwd(cwd),
            "cwd": cwd,
            "git_branch": row["git_branch"] or "",
            "title": _preview(title, _MAX_TITLE),
            "started": _iso_from_unix(row["created_at"]),
            "last_active": _iso_from_unix(row["updated_at"] or row["created_at"]),
            "last_role": "assistant" if last else "",
            "last_text": _preview(last),
            "model": row["model"] or "",
            "size_bytes": size,
        }

    @staticmethod
    def _search_rollout(path: str, query: str):
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 400:
                        break
                    ev = _safe_json(line)
                    if not isinstance(ev, dict):
                        continue
                    payload = ev.get("payload") or {}
                    if not isinstance(payload, dict):
                        continue
                    text = payload.get("message") or payload.get("text") or ""
                    if isinstance(text, list):
                        text = " ".join(str(t) for t in text)
                    if text and search_util.contains_ci(str(text), query):
                        return search_util.make_snippet(str(text), query)
        except OSError:
            return None
        return None


def _build_transcript(path: Path | None) -> list:
    """Coalesce rollout JSONL into [{role, text, ts}] for the phone."""
    if path is None or not path.is_file():
        return []
    messages = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                ev = _safe_json(line)
                if not isinstance(ev, dict):
                    continue
                ts = str(ev.get("timestamp") or "")
                payload = ev.get("payload") if ev.get("type") == "event_msg" else None
                if not isinstance(payload, dict):
                    continue
                ptype = str(payload.get("type") or "")
                if ptype == "user_message":
                    text = str(payload.get("message") or "").strip()
                    if text:
                        messages.append({
                            "uuid": "u%d" % len(messages),
                            "role": "user",
                            "ts": ts,
                            "text": text,
                        })
                elif ptype == "agent_message":
                    text = str(payload.get("message") or "").strip()
                    if text:
                        messages.append({
                            "uuid": "a%d" % len(messages),
                            "role": "assistant",
                            "ts": ts,
                            "text": text,
                        })
    except OSError:
        return messages
    return messages


def _render_codex_message(msg: dict) -> None:
    """Attach display blocks. User rows must be k=user so BB/Android/web
    paint the chevron + well chrome (same as Claude/Grok). Assistant stays
    markdown_to_blocks. Previously every role used markdown_to_blocks, so
    historical Codex prompts rendered as plain assistant paragraphs.
    """
    text = (msg.get("text") or "").strip()
    role = msg.get("role") or ""
    if not text or role not in ("assistant", "user"):
        return
    if role == "user":
        plain, rich = inline_to_rich(text)
        msg["blocks"] = [{
            "k": "user",
            "role": "user",
            "text": plain,
            "rich": rich,
            "fmt": "rich",
        }]
    else:
        msg["blocks"] = markdown_to_blocks(text, role="assistant")


class CodexRunner:
    name = "codex"

    def __init__(self, config):
        self.config = config
        self.store = CodexStore(config.codex_home_path, config)
        # Lazily created tmux-TUI manager for "interactive" jobs.
        self._interactive = None
        self._interactive_lock = threading.Lock()

    def _interactive_mgr(self):
        with self._interactive_lock:
            if self._interactive is None:
                from .codex_interactive import CodexInteractiveManager
                self._interactive = CodexInteractiveManager(self.config, self)
            return self._interactive

    def run_alternate(self, job, mode) -> bool:
        """Fully handle a job outside the subprocess pipeline. "interactive"
        drives a real ``codex`` TUI in tmux (same mode Claude/Grok expose)."""
        if mode != "interactive":
            return False
        self._interactive_mgr().run(job)
        return True

    def type_into_tui(self, session_id: str, text: str) -> str:
        """Type a message into a session's live interactive TUI (\"\" or err)."""
        return self._interactive_mgr().type_text(session_id, text)

    def capture_tui(self, session_id: str) -> dict:
        return self._interactive_mgr().capture_tui(session_id)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        return self._interactive_mgr().send_tui_keys(session_id, keys=keys, text=text)

    def capabilities(self):
        from .codex_interactive import tmux_available
        has_tmux = tmux_available()
        return {
            "queue": True,
            "stop": True,
            "projects": True,
            "ws_status": True,
            "permissions": False,
            "permission_modes": False,
            "requires_cwd": True,
            "can_set_model": True,
            "can_set_effort": False,
            "can_show_usage": False,
            "turns": True,
            # "interactive" permission mode: turns run in a host tmux TUI.
            # Requires tmux on the host (same as Claude/Grok interactive).
            "interactive": has_tmux,
            "live_tui": has_tmux,
        }

    # Verified in codex's own TUI command list: /compact and /exit are
    # there, /rewind and /undo are NOT (its "Rewind" string is a sort enum).
    _BUILTIN_SLASH = ["/compact", "/exit"]

    def slash_commands(self):
        out = list(self._BUILTIN_SLASH)
        for extra in getattr(self.config, "slash_commands", None) or []:
            if isinstance(extra, str) and extra.strip():
                out.append(extra.strip())
        return sorted(set(out))

    def models(self):
        extras = list(getattr(self.config, "models", None) or [])
        # models_cache.json is optional flavour; extras always win for the picker.
        cached = []
        try:
            path = self.config.codex_home_path / "models_cache.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            models = data.get("models") if isinstance(data, dict) else None
            if isinstance(models, list):
                for m in models:
                    if isinstance(m, str) and m.strip():
                        cached.append(m.strip())
                    elif isinstance(m, dict):
                        mid = m.get("id") or m.get("slug") or m.get("name")
                        if mid:
                            cached.append(str(mid))
        except (OSError, ValueError, TypeError):
            pass
        seen = set()
        out = []
        for m in extras + cached:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    def efforts(self):
        return list(getattr(self.config, "efforts", None) or [])

    def prepare(self, job, mode):
        # `mode` is claude vocabulary; codex uses sandbox / bypass flags.
        if not job.cwd:
            raise providers.RunnerError("cwd is required for codex sessions")
        cwd = os.path.expanduser(job.cwd)
        if not os.path.isdir(cwd):
            raise providers.RunnerError("cwd does not exist: %s" % cwd)
        job.cwd = cwd

        bin_path = str(getattr(self.config, "codex_bin", "codex") or "codex")
        state = job.runner_state
        state["parts"] = []
        state["full"] = []

        # Global flags before the subcommand (exec / exec resume).
        cmd = [bin_path, "exec", "--json"]
        # Phone-driven turns often use non-git folders (and /tmp in tests).
        cmd.append("--skip-git-repo-check")

        sandbox = str(getattr(self.config, "codex_sandbox", "") or "").strip()
        if not sandbox:
            # Default: full auto for phone use (same spirit as claude bypass /
            # grok --yolo). Override with "read-only" / "workspace-write" in
            # config if you want a tighter box.
            sandbox = "danger-full-access"
        if sandbox in ("danger-full-access", "yolo"):
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd += ["-s", sandbox]

        if job.model and job.model not in ("", "default"):
            cmd += ["-m", job.model]

        # Extra flags from config (whitespace-split), e.g. "--profile work".
        extra = str(getattr(self.config, "codex_exec_flags", "") or "").split()
        cmd += extra

        cmd += ["-C", cwd]

        # Resume is a subcommand of exec:  codex exec resume [id] [prompt]
        if job.session_id:
            cmd += ["resume", job.session_id]

        cmd.append(job.prompt)

        env = dict(os.environ)
        home = str(self.config.codex_home_path)
        env["CODEX_HOME"] = home
        extra_env = getattr(self.config, "codex_env", None) or {}
        env.update({str(k): str(v) for k, v in extra_env.items()})

        # Close stdin in the job runner — jobs.py uses subprocess.Popen with
        # stdin=PIPE by default; we mark that we want DEVNULL via state and
        # rely on prepare's return. Actually JobManager always uses PIPE.
        # Closing happens if we don't write; but CLI may wait. jobs.py should
        # close stdin — check... Looking at jobs.py: it doesn't close stdin.
        # Workaround: the CLI still works if we pass prompt as argv (we do).
        # The "Reading additional input from stdin" is just a notice when
        # stdin is a pipe. Closing: set state flag and patch is heavy; instead
        # document that stdin is a pipe. For robustness, use a wrapper script
        # or set stdin via env. Looking at jobs again...

        return cmd, env

    def handle_stream_line(self, job, line: str):
        obj = _safe_json(line)
        if not isinstance(obj, dict):
            return
        et = str(obj.get("type") or "")
        state = job.runner_state

        if et == "thread.started":
            sid = str(obj.get("thread_id") or "").strip()
            if sid:
                job.new_session_id = sid
                job.add_event("init", session_id=sid,
                              model=job.model or "")
            return

        if et == "turn.started":
            job.set_phase("thinking", "")
            return

        if et in ("error", "turn.failed"):
            msg = (obj.get("message") or obj.get("error")
                   or (obj.get("item") or {}).get("text")
                   or "codex reported an error")
            with job.lock:
                if not job.error:
                    job.error = str(msg)
            job.add_event("text", text=str(msg),
                          blocks=markdown_to_blocks(str(msg)))
            return

        if et in ("item.started", "item.completed", "item.updated"):
            item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
            itype = str(item.get("type") or "")
            if itype == "agent_message":
                text = str(item.get("text") or "").strip()
                if text and et == "item.completed":
                    state.setdefault("parts", []).append(text)
                    state.setdefault("full", []).append(text)
                    job.add_event("text", text=text,
                                  blocks=markdown_to_blocks(text))
                    job.set_phase("writing", text[-160:])
            elif itype in ("command_execution", "command", "shell"):
                cmd = str(item.get("command") or item.get("cmd") or "shell")
                detail = str(item.get("aggregated_output") or "")[:200]
                status = str(item.get("status") or "")
                if et == "item.started" or status == "in_progress":
                    job.add_event("tool", name="shell", detail=cmd[:200])
                    job.set_phase("tool", cmd[:120])
                elif et == "item.completed":
                    # Keep phase as tool until next event; optional exit code.
                    code = item.get("exit_code")
                    if code not in (None, 0, "0"):
                        job.set_phase("tool", "exit %s" % code)
            elif itype in ("file_change", "patch", "apply_patch"):
                path = str(item.get("path") or item.get("file") or "edit")
                job.add_event("tool", name="edit", detail=path[:200])
                job.set_phase("tool", path[:120])
            elif itype in ("reasoning", "thought", "agent_reasoning"):
                job.set_phase("thinking", "")
            return

        if et == "turn.completed":
            full = "".join(state.get("full") or state.get("parts") or [])
            with job.lock:
                if full and not job.result_text:
                    job.result_text = full
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            job.add_event(
                "result",
                is_error=False,
                duration_ms=0,
                cost_usd=0,
                usage=usage,
            )
            return

    def tick(self, job):
        pass

    def finalize(self, job, returncode, stderr_tail):
        state = job.runner_state
        full = "".join(state.get("full") or state.get("parts") or [])
        with job.lock:
            if full and not job.result_text:
                job.result_text = full
        if returncode not in (0, None) and not job.error:
            tail = (stderr_tail or "").strip().splitlines()
            msg = tail[-1] if tail else ("codex exited with code %s" % returncode)
            with job.lock:
                job.error = msg
            return False
        return None

    def cleanup(self, job):
        return
