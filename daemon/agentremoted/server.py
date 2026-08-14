"""HTTP API served to the BB10 app.

Three clients share this API: the BlackBerry and Android apps, and the
standalone browser client in ../web (one page, wherever it is hosted, talking
to every daemon at once). That last one is why CORS is enabled on every
response — see _cors_headers. No daemon serves a UI itself.

Design constraints from the client side (BlackBerry 10, Qt 4.8 / Cascades):
  - plain HTTP + JSON, no SSE — the app polls (plus one optional WebSocket)
  - auth via `X-Auth-Token` header (also accepts `Authorization: Bearer`
    and `?token=` for quick tests)
  - small, flat JSON payloads; never emit NaN/Infinity (Qt4 JSON chokes)

The API is identical for every provider; /api/ping carries the provider
name and capability flags so one client binary can gate features at
runtime.

Endpoints (all under /api, all JSON):
  GET  /api/ping                                  liveness + provider + caps + auth
  GET  /api/usage                                 subscription usage buckets
  GET  /api/projects                              projects, most recent first
  GET  /api/sessions?project=<id>&limit=<n>&all=1  session summaries (all=1:
                                                  include agent-spawned and
                                                  contentless sessions too)
  GET  /api/sessions/search?q=<text>&project=&limit=&all=1  full-text search
  GET  /api/sessions/search?…&stream=1                      NDJSON progressive hits
  GET  /api/sessions/<id>                         one session's summary
  GET  /api/sessions/<id>/messages?offset=&limit= transcript window (default: tail)
  POST /api/sessions/<id>/continue {prompt, permission_mode?}
  POST /api/sessions/new {cwd, prompt, permission_mode?}
  GET  /api/focus                                 focus rows only (session
                                                  summaries the human enrolled,
                                                  each tagged focus_state)
  POST /api/focus/<key>/done                      take a row off the list
  POST /api/focus/<key>/restore                   undo done (7-day window)
  POST /api/focus/<key>/seen                      read cursor (styles a finished
                                                  turn lit vs dim; not a state)
  POST /api/sessions/<id>/title {title}           rename ("" clears override)
  POST /api/sessions/<id>/title/regenerate        re-derive the title via Haiku
  GET  /api/jobs                                  running/recent jobs
  GET  /ws/status                                 WebSocket: active-job status pushes
  GET  /sse/status                                SSE: active-job status pushes
  GET  /api/jobs/<id>?since=<seq>                 job status + new events
  POST /api/jobs/<id>/input {prompt}              type into an interactive TUI
  POST /api/jobs/<id>/queue {prompt}              queue a prompt behind the job
  POST /api/jobs/<id>/queue/<qid>/cancel          drop one queued prompt
  POST /api/jobs/<id>/stop
  POST /api/jobs/<id>/permission {request_id, allow}   answer a prompt
  POST /api/jobs/<id>/question {request_id, answers|cancel}  answer AskUserQuestion
  POST /api/shell {command, cwd?}                      run a shell command
  POST /api/attachments?name=<filename>                raw file body -> {path}
  GET  /api/drop                                       host→phone drop listing
                                                       (files and folders;
                                                       type=file|dir, dirs also
                                                       carry entries/partial)
  GET  /api/drop/<name>                                download one drop entry
                                                       (a folder arrives zipped
                                                       as <name>.zip)
  POST /api/drop/<name>/delete                         remove one drop file
                                                       or folder (recursive;
                                                       still confined to drop)
  POST /internal/permission {job_id, nonce, ...}       (helper MCP tool only)
  POST /internal/hook?secret= {hook payload}           (interactive TUI hooks)
"""

import hmac
import json
import logging
import math
import os
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import tempfile
import time
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from . import __version__
from . import accounts
from . import focus as focus_store
from . import ssestream
from . import wstream

log = logging.getLogger(__name__)

_SESSION_MSGS = re.compile(r"^/api/sessions/([^/]+)/messages$")
# Process view: full text behind one truncated step (<record uuid>:<block ix>).
_SESSION_STEP = re.compile(r"^/api/sessions/([^/]+)/steps/([^/]+)$")
_SESSION_CONT = re.compile(r"^/api/sessions/([^/]+)/continue$")
_SESSION_TUI = re.compile(r"^/api/sessions/([^/]+)/tui$")
_SESSION_TUI_KEYS = re.compile(r"^/api/sessions/([^/]+)/tui/keys$")
_SESSION_ONE = re.compile(r"^/api/sessions/([^/]+)$")
_JOB_ONE = re.compile(r"^/api/jobs/([^/]+)$")
_JOB_STOP = re.compile(r"^/api/jobs/([^/]+)/stop$")
_JOB_INPUT = re.compile(r"^/api/jobs/([^/]+)/input$")
_JOB_QUEUE = re.compile(r"^/api/jobs/([^/]+)/queue$")
_JOB_QCANCEL = re.compile(r"^/api/jobs/([^/]+)/queue/([^/]+)/cancel$")
_JOB_PERM = re.compile(r"^/api/jobs/([^/]+)/permission$")
_JOB_QUESTION = re.compile(r"^/api/jobs/([^/]+)/question$")
_DROP_FILE = re.compile(r"^/api/drop/([^/]+)$")
_DROP_DELETE = re.compile(r"^/api/drop/([^/]+)/delete$")
_SESSION_TITLE = re.compile(r"^/api/sessions/([^/]+)/title$")
_SESSION_RETITLE = re.compile(r"^/api/sessions/([^/]+)/title/regenerate$")
_FOCUS_DONE = re.compile(r"^/api/focus/([^/]+)/done$")
_FOCUS_RESTORE = re.compile(r"^/api/focus/([^/]+)/restore$")
_FOCUS_SEEN = re.compile(r"^/api/focus/([^/]+)/seen$")


MAX_BODY = 256 * 1024


# Names that live in a default macOS ~/Public but are not agent staging.
# "Drop Box" is the classic incoming-share folder (sticky permissions); showing
# it in Inbox only confuses, and recursive delete would be the wrong tool.
_DROP_LIST_SKIP = frozenset({"Drop Box"})


def _safe_drop_name(raw: str) -> str:
    """Basename-only, no traversal; empty if the name is unusable.

    Callers pass a URL path segment — unquote first so "hello%20drop.txt"
    resolves to the real filename on disk.
    """
    name = os.path.basename(unquote((raw or "").strip()))
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return ""
    # Reject null bytes and control chars; keep the rest so non-ASCII
    # filenames the agent dropped still download.
    if any(ord(ch) < 32 for ch in name):
        return ""
    return name


def _sanitize(obj):
    """Strip values Qt4's JSON parser cannot digest (NaN/Infinity — grok
    usage payloads carry them) so one bad float can't break a whole load."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    return str(obj)


class ApiHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "agentremoted/" + __version__

    # Injected by make_server():
    store = None
    jobs = None
    runner = None
    config = None
    token = ""
    # Multi-provider: OrderedDict name → ProviderBundle. Empty/None = single.
    bundles = None
    # Focus-list membership + title overrides, shared across every provider
    # and every client of this daemon (focus.Focus).
    focus = None
    # Set per-request by _authorize(); never share data across principals.
    principal = None

    # -- plumbing --------------------------------------------------------

    def _bind_path(self):
        """Strip /{provider} prefix when multi-mounted; bind store/jobs/runner.

        Returns (path, query, bundle_or_None). path is the remainder used by
        the rest of the router (same shape as today's single-provider paths).
        """
        url = urlparse(self.path)
        query = parse_qs(url.query)
        path = url.path.rstrip("/") or "/"
        bundles = self.bundles or {}
        if not bundles:
            return path, query, None
        # Longest-name first so a future "claude-x" does not steal "claude".
        for name in sorted(bundles.keys(), key=len, reverse=True):
            prefix = "/" + name
            if path == prefix or path.startswith(prefix + "/"):
                rest = path[len(prefix):] or "/"
                if not rest.startswith("/"):
                    rest = "/" + rest
                b = bundles[name]
                self.store = b.store
                self.jobs = b.jobs
                self.runner = b.runner
                # Principal may already be set on keep-alive reuse of handler
                # class attrs — prefer guest store when applicable.
                if getattr(self, "principal", None) is not None:
                    self._rebind_store_for_principal()
                return rest, query, b
        return path, query, None

    def _jobs_for_id(self, job_id):
        """Find the JobManager that owns job_id (multi or single)."""
        if self.jobs and self.jobs.get(job_id):
            return self.jobs
        for b in (self.bundles or {}).values():
            if b.jobs.get(job_id):
                return b.jobs
        return self.jobs

    def _bind_bundle(self, name):
        """Set store/jobs/runner from a named multi-provider bundle."""
        b = (self.bundles or {}).get(name)
        if b is None:
            return None
        self.store = self._store_for_provider(name, b)
        self.jobs = b.jobs
        self.runner = b.runner
        return b

    def _find_session(self, session_id):
        """(provider_name, bundle, session_dict) or (None, None, None)."""
        if self.store is not None and (not self.bundles or len(self.bundles) <= 1):
            name = self.runner.name if self.runner else ""
            store = self._store_for_provider(name) if name else self.store
            s = store.get_session(session_id) if store is not None else None
            if s is not None:
                return name, None, s
        for name, b in (self.bundles or {}).items():
            store = self._store_for_provider(name, b)
            s = store.get_session(session_id) if store is not None else None
            if s is not None:
                return name, b, s
        return None, None, None

    def _find_job(self, job_id):
        """(provider_name, bundle, job) or (None, None, None)."""
        if self.jobs is not None and (not self.bundles or len(self.bundles) <= 1):
            j = self.jobs.get(job_id)
            if j is not None:
                name = self.runner.name if self.runner else ""
                return name, None, j
        for name, b in (self.bundles or {}).items():
            j = b.jobs.get(job_id)
            if j is not None:
                return name, b, j
        return None, None, None

    def _merged_active_status(self):
        """Active jobs across every harness, each tagged with provider.

        Also merges busy interactive host TUIs that no longer have a running
        job (e.g. turn_timeout fired while multi-agent work continued). Without
        those rows clients drop the session from the "working" list even though
        the tmux agent is still mid-turn.
        """
        out = []
        seen_sessions = set()  # provider/session_id already covered by a job
        for name, b in (self.bundles or {}).items():
            for row in b.jobs.active_status():
                row = dict(row)
                row["provider"] = name
                out.append(row)
                for sid in (row.get("session_id"), row.get("new_session_id")):
                    if sid:
                        seen_sessions.add("%s/%s" % (name, sid))
            # Busy live TUIs without a running job.
            runner = getattr(b, "runner", None)
            mgr_fn = getattr(runner, "_interactive_mgr", None) if runner else None
            if not callable(mgr_fn):
                continue
            try:
                mgr = mgr_fn()
            except Exception:
                continue
            status_fn = getattr(mgr, "active_tui_status", None)
            if not callable(status_fn):
                continue
            try:
                extra = status_fn() or []
            except Exception as e:
                log.debug("active_tui_status %s: %s", name, e)
                continue
            for row in extra:
                row = dict(row)
                row["provider"] = name
                sid = row.get("new_session_id") or row.get("session_id") or ""
                key = "%s/%s" % (name, sid) if sid else ""
                if key and key in seen_sessions:
                    continue
                if key:
                    seen_sessions.add(key)
                out.append(row)
        # Single-provider path (no multi bundles): same merge for self.jobs.
        if not self.bundles and self.jobs is not None:
            for row in self.jobs.active_status():
                out.append(dict(row))
                for sid in (row.get("session_id"), row.get("new_session_id")):
                    if sid:
                        seen_sessions.add(sid)
            runner = self.runner
            mgr_fn = getattr(runner, "_interactive_mgr", None) if runner else None
            if callable(mgr_fn):
                try:
                    extra = (mgr_fn().active_tui_status() or [])
                except Exception:
                    extra = []
                for row in extra:
                    sid = row.get("new_session_id") or row.get("session_id") or ""
                    if sid and sid in seen_sessions:
                        continue
                    out.append(dict(row))
        out.sort(key=lambda s: s.get("job_id") or "")
        return out

    @staticmethod
    def _activity_sort_key(row):
        """Normalize last_active/started across providers for sort.

        Claude/Grok project lists use float mtimes; Codex (and most session
        rows) use ISO-8601 strings. Mixing them in list.sort blows up with
        TypeError: float vs str — which is what Test connection hit on
        GET /api/projects in multi mode.
        """
        val = row.get("last_active")
        if val is None or val == "":
            val = row.get("started")
        if val is None or val == "":
            return 0.0
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val).strip()
        if not s:
            return 0.0
        # Unix epoch as string
        try:
            return float(s)
        except (TypeError, ValueError):
            pass
        # ISO-8601 (with or without trailing Z)
        try:
            from datetime import datetime, timezone
            iso = s.replace("Z", "+00:00") if s.endswith("Z") else s
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            return 0.0

    @staticmethod
    def _auth_summary(auth_by: dict) -> dict:
        """Worst-status rollup across harness auth_health snapshots."""
        if not auth_by:
            return {
                "cli": "",
                "cli_on_path": False,
                "mode": "none",
                "status": "unknown",
                "detail": "no providers",
                "by_provider": {},
            }
        rank = {
            "missing": 0,
            "expired": 1,
            "warning": 2,
            "unknown": 3,
            "ok": 4,
        }
        worst_name = None
        worst_score = 99
        for name, ah in auth_by.items():
            st = str((ah or {}).get("status") or "unknown")
            score = rank.get(st, 3)
            if score < worst_score:
                worst_score = score
                worst_name = name
        worst = dict(auth_by.get(worst_name) or {})
        # Compact multi-line summary for Test connection UIs.
        parts = []
        for name in sorted(auth_by.keys()):
            ah = auth_by[name] or {}
            parts.append("%s: %s" % (name, ah.get("status") or "unknown"))
        worst["by_provider"] = auth_by
        worst["detail"] = "; ".join(parts) + (
            " — " + str(worst.get("detail") or "") if worst.get("detail") else ""
        )
        return worst

    def _merged_sessions(self, project, limit, user_only):
        """Merge harness lists so every provider stays visible.

        Global top-N by recency starves Codex when Claude/Grok are busier
        (limit=6–12 showed codex:0). Round-robin by harness (each queue
        already newest-first) keeps a mixed feed. Running jobs pin first.
        """
        bundles = list((self.bundles or {}).items())
        if not bundles:
            return []
        limit = max(1, min(int(limit or 25), 200))
        fetch = max(limit, 40)
        # name -> list of sessions (newest first within harness)
        queues = []
        for name, b in bundles:
            try:
                store = self._store_for_provider(name, b)
                rows = list(store.list_sessions(
                    project, fetch, user_only=user_only) or [])
            except Exception:
                log.exception("list_sessions failed for %s", name)
                rows = []
            tagged = []
            for s in rows:
                s = dict(s)
                s["provider"] = name
                tagged.append(s)
            # Within-harness newest first
            tagged.sort(key=self._activity_sort_key, reverse=True)
            queues.append([name, tagged, 0])  # name, rows, cursor

        selected = []
        # Dedupe by session id (not just provider+id). Running-job rows carry
        # provider; store rows used to omit it, so (grok, id) + ("", id) both
        # passed and the same session appeared twice in the list.
        seen_ids = set()

        run_by_id, pending = self._split_running(self._running_job_sessions())

        def _add(s):
            sid = (s.get("id") or "").strip()
            if not sid or sid in seen_ids:
                return False
            # A live job lends its liveness to the session's own row; it never
            # replaces the row (that renamed sessions after the prompt).
            if sid in run_by_id:
                s = self._apply_running(s, run_by_id.pop(sid))
            seen_ids.add(sid)
            selected.append(s)
            return True

        # Sessions that have no transcript yet (new Codex before sqlite has a
        # thread): the prompt is the only name they have.
        for s in pending:
            _add(s)

        # Round-robin: one from each harness per wave (Codex never buried).
        while len(selected) < limit:
            progressed = False
            for i, (name, rows, cur) in enumerate(queues):
                if len(selected) >= limit:
                    break
                while cur < len(rows):
                    s = rows[cur]
                    cur += 1
                    queues[i][2] = cur
                    if _add(s):
                        progressed = True
                        break
            if not progressed:
                break

        # Running sessions no harness page reached still deserve a row.
        for sid, r in list(run_by_id.items()):
            if sid not in seen_ids:
                seen_ids.add(sid)
                selected.append(r)

        return selected[:limit]

    def _running_job_sessions(self):
        """Synthetic session rows for active jobs (multi + single)."""
        import time as _time
        now = _time.time()
        out = []
        if self.bundles:
            items = list(self.bundles.items())
        else:
            name = ""
            if getattr(self, "runner", None) is not None:
                name = str(getattr(self.runner, "name", "") or "")
            items = [(name, type("B", (), {"jobs": getattr(self, "jobs", None)})())]

        for name, b in items:
            jobs_mgr = getattr(b, "jobs", None)
            if jobs_mgr is None:
                continue
            try:
                job_list = jobs_mgr.list_jobs()
            except Exception:
                continue
            for j in job_list or []:
                if not isinstance(j, dict):
                    continue
                st = str(j.get("status") or "")
                if st not in ("running", "starting"):
                    continue
                # Never surface another account's in-flight job as a session.
                if self.principal is not None and not accounts.job_owned_by(
                        j, self.principal):
                    continue
                sid = (j.get("new_session_id") or j.get("session_id") or "").strip()
                jid = str(j.get("id") or "")
                prompt = " ".join(str(j.get("prompt") or "").split())
                title = (prompt[:80] if prompt else "Running…")
                rid = sid or ("job:%s" % jid)
                prov = j.get("provider") or name or ""
                # ISO last_active so web clients that Date.parse() don't
                # sort running rows to 0 (float seconds used to bury them).
                try:
                    from datetime import datetime, timezone
                    last_iso = datetime.fromtimestamp(
                        now, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                except (OverflowError, ValueError, OSError):
                    last_iso = now
                out.append({
                    "id": rid,
                    "project_id": "",
                    "cwd": j.get("cwd") or "",
                    "git_branch": "",
                    "title": title,
                    "started": "",
                    "last_active": last_iso,
                    "last_role": "user",
                    "last_text": prompt[:200],
                    "model": j.get("model") or "",
                    "size_bytes": 0,
                    "provider": prov,
                    "job_id": jid,
                    "running": True,
                    "account": j.get("account") or "",
                    "isolate_root": j.get("isolate_root") or "",
                })
        return out

    def _apply_running(self, row: dict, run: dict) -> dict:
        """Overlay liveness onto a real session row.

        A running job used to be *prepended* as its own row titled with the
        prompt, and it won the dedupe — so sending "continue" to a well-named
        session renamed it "continue" until the turn ended. Identity belongs to
        the session; only the liveness flags come from the job.
        """
        row = dict(row)
        row["running"] = True
        row["job_id"] = run.get("job_id") or row.get("job_id") or ""
        # In-flight turns still sort to the top.
        if run.get("last_active"):
            row["last_active"] = run["last_active"]
        if not str(row.get("title") or "").strip():
            row["title"] = run.get("title") or ""
        return row

    def _split_running(self, rows):
        """(by-session-id, no-session-yet) split of the synthetic job rows."""
        by_id, pending = {}, []
        for r in rows or []:
            sid = str(r.get("id") or "").strip()
            if not sid:
                continue
            if sid.startswith(focus_store.JOB_KEY_PREFIX):
                # No session id yet: the prompt IS the only name it has.
                pending.append(r)
            else:
                by_id[sid] = r
        return by_id, pending

    def _sessions_with_running(self, sessions, limit):
        """Prepend active jobs onto a single-provider session list."""
        limit = max(1, min(int(limit or 25), 200))
        seen_ids = set()
        out = []
        run_by_id, pending = self._split_running(self._running_job_sessions())
        for s in pending:
            sid = (s.get("id") or "").strip()
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                out.append(s)
        for s in sessions or []:
            s = dict(s)
            sid = (s.get("id") or "").strip()
            if sid and sid in run_by_id:
                s = self._apply_running(s, run_by_id.pop(sid))
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                out.append(s)
            if len(out) >= limit:
                break
        # Running sessions the store page did not reach still deserve a row.
        for sid, r in run_by_id.items():
            if sid not in seen_ids:
                seen_ids.add(sid)
                out.append(r)
        out.sort(key=self._activity_sort_key, reverse=True)
        return out[:limit]

    def _merged_projects(self):
        rows = []
        for name, b in (self.bundles or {}).items():
            store = self._store_for_provider(name, b)
            for p in store.list_projects():
                p = dict(p)
                p["provider"] = name
                # Disambiguate project ids across harnesses.
                p["id"] = "%s:%s" % (name, p.get("id") or "")
                # Normalize last_active to a float epoch so clients (Android
                # ProjectDto) never see mixed ISO strings + floats.
                p["last_active"] = self._activity_sort_key(p)
                rows.append(p)
        rows.sort(key=lambda r: float(r.get("last_active") or 0), reverse=True)
        return rows

    def _merged_search(self, q, project, limit, user_only):
        """Search every harness in parallel — sequential 3× was the multi lag."""
        import concurrent.futures

        bundles = list((self.bundles or {}).items())
        if not bundles:
            return []

        def _one(item):
            name, b = item
            store = self._store_for_provider(name, b)
            search_fn = getattr(store, "search_sessions", None)
            if search_fn is None:
                return []
            out = []
            try:
                for s in search_fn(q, project, limit, user_only=user_only):
                    s = dict(s)
                    s["provider"] = name
                    out.append(s)
            except Exception:
                log.exception("search failed for provider %s", name)
            return out

        rows = []
        # Small pool: one worker per harness (usually 1–3).
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(bundles))) as pool:
            for part in pool.map(_one, bundles):
                rows.extend(part)
        rows.sort(key=self._activity_sort_key, reverse=True)
        return rows[:limit]

    def _stream_search(self, q, project, limit, user_only, multi=False):
        """NDJSON progressive search: one JSON object per line.

        Lines:
          {"type":"hit","session":{...}}
          {"type":"done","query":"...","count":N}
        Web clients paint each hit as it arrives; phones keep the batch API.
        """
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # Streaming: do not set Content-Length; close when finished.
        self.send_header("Connection", "close")
        self.close_connection = True
        self._cors_headers()
        self.end_headers()

        count = 0
        # Over-fetch when scoped so filtering still fills `limit`.
        fetch = limit if (
            self.principal is None or self.principal.is_main
        ) else min(max(limit * 4, 50), 100)
        try:
            if multi:
                for row in self._iter_merged_search(
                        q, project, fetch, user_only):
                    if not self._session_in_scope(row):
                        continue
                    self._write_ndjson({"type": "hit", "session": row})
                    count += 1
                    if count >= limit:
                        break
            else:
                it_fn = getattr(self.store, "iter_search_sessions", None)
                if it_fn is None:
                    search_fn = getattr(self.store, "search_sessions", None)
                    rows = (search_fn(q, project, fetch, user_only=user_only)
                            if search_fn else [])
                    for row in rows:
                        if not self._session_in_scope(row):
                            continue
                        self._write_ndjson({"type": "hit", "session": row})
                        count += 1
                        if count >= limit:
                            break
                else:
                    for row in it_fn(q, project, fetch, user_only=user_only):
                        if not self._session_in_scope(row):
                            continue
                        self._write_ndjson({"type": "hit", "session": row})
                        count += 1
                        if count >= limit:
                            break
            self._write_ndjson({"type": "done", "query": q, "count": count})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("stream search failed")
            try:
                self._write_ndjson({"type": "error", "error": "search failed"})
            except Exception:
                pass

    def _write_ndjson(self, obj):
        line = self._json_bytes(obj) + b"\n"
        self.wfile.write(line)
        try:
            self.wfile.flush()
        except Exception:
            pass

    def _iter_merged_search(self, q, project, limit, user_only):
        """Yield multi-provider hits as each harness produces them (parallel)."""
        import concurrent.futures
        import queue
        import threading

        bundles = list((self.bundles or {}).items())
        if not bundles:
            return
        q_out = queue.Queue()
        sentinel = object()

        def _worker(name, b):
            try:
                store = self._store_for_provider(name, b)
                it_fn = getattr(store, "iter_search_sessions", None)
                if it_fn is not None:
                    for s in it_fn(q, project, limit, user_only=user_only):
                        s = dict(s)
                        s["provider"] = name
                        q_out.put(s)
                else:
                    search_fn = getattr(store, "search_sessions", None)
                    if search_fn is None:
                        return
                    for s in search_fn(q, project, limit, user_only=user_only):
                        s = dict(s)
                        s["provider"] = name
                        q_out.put(s)
            except Exception:
                log.exception("stream search failed for provider %s", name)
            finally:
                q_out.put(sentinel)

        threads = []
        for name, b in bundles:
            t = threading.Thread(target=_worker, args=(name, b), daemon=True)
            t.start()
            threads.append(t)

        done = 0
        yielded = 0
        while done < len(threads) and yielded < limit:
            item = q_out.get()
            if item is sentinel:
                done += 1
                continue
            yield item
            yielded += 1

        # Drain remaining sentinels so workers can exit cleanly.
        while done < len(threads):
            item = q_out.get()
            if item is sentinel:
                done += 1
        for t in threads:
            t.join(timeout=0.1)

    def log_message(self, fmt, *args):
        log.info("%s %s", self._caller_ip(), fmt % args)

    def _caller_ip(self):
        """The socket peer, except when it's the loopback of a local
        tunnel/reverse proxy — then the proxy's X-Forwarded-For / X-Real-IP
        holds the real caller (first hop of the XFF chain)."""
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            headers = getattr(self, "headers", None)
            if headers is not None:
                fwd = (headers.get("X-Forwarded-For", "")
                       or headers.get("X-Real-IP", "")).split(",")[0].strip()
                if fwd:
                    return "%s (via %s)" % (fwd, ip)
        return ip

    @staticmethod
    def _json_bytes(obj):
        try:
            body = json.dumps(obj, allow_nan=False)
        except ValueError:
            body = json.dumps(_sanitize(obj), allow_nan=False)
        # Lone surrogates from transcript files degrade to '?' instead of 500.
        return body.encode("utf-8", errors="replace")

    def _cors_headers(self):
        """Let the browser UI talk to every daemon, not just its own host.

        The web client is one page fanning out to several daemons, so all but
        one of its requests are cross-origin. Allowing any origin is safe
        *because auth is a header token, never a cookie*: a hostile page can
        issue the request but cannot read the token out of another origin's
        localStorage, and without the header the daemon answers 401. For the
        same reason Allow-Credentials is deliberately NOT sent.
        """
        origin = str(getattr(self, "cors_origin", "*") or "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Vary", "Origin")

    def _send_json(self, obj, status=200, close=False):
        self._send_json_bytes(self._json_bytes(obj), status=status, close=close)

    def _send_json_bytes(self, body, status=200, close=False):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        if close:
            # Error paths may leave an unread request body; reusing the
            # connection would desync HTTP keep-alive, so drop it.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message):
        self._send_json({"error": message}, status=status, close=True)

    def _extract_token(self, query) -> str:
        supplied = self.headers.get("X-Auth-Token", "")
        if not supplied:
            bearer = self.headers.get("Authorization", "")
            if bearer.startswith("Bearer "):
                supplied = bearer[len("Bearer "):].strip()
        if not supplied:
            supplied = self.headers.get("X-Grok-Token", "")  # legacy client
        if not supplied:
            supplied = (query.get("token") or [""])[0]
        return (supplied or "").strip()

    def _authorize(self, query) -> bool:
        """Resolve the caller to a Principal. Main and each guest are isolated.

        On success sets ``self.principal``; on failure clears it and returns
        False. Never return account A's jobs/sessions/drops to account B.
        """
        supplied = self._extract_token(query)
        principal = accounts.resolve_principal(supplied, self.token)
        self.principal = principal
        if principal is not None:
            self._rebind_store_for_principal()
        return principal is not None

    def _authorized(self, query) -> bool:
        # Back-compat alias used throughout this module.
        return self._authorize(query)

    def _make_guest_store(self, provider_name: str, guest_root: str):
        """Store rooted under the guest's harness home (sessions live there).

        Sandboxed agents set GROK_HOME / CLAUDE_CONFIG_DIR / CODEX_HOME to
        ``<guest_root>/.{grok,claude,codex}``, so transcripts are not under
        the main account's host home. The HTTP layer must read the same tree.
        """
        from pathlib import Path
        root = Path(guest_root)
        name = (provider_name or "").strip().lower()
        try:
            if name == "grok":
                from .providers.grok import GrokStore
                return GrokStore(root / ".grok")
            if name == "claude":
                from .providers.claude import ClaudeStore
                return ClaudeStore(root / ".claude" / "projects", self.config)
            if name == "codex":
                from .providers.codex import CodexStore
                return CodexStore(root / ".codex", self.config)
        except Exception:
            log.exception("guest store for %s failed", name)
        return None

    def _store_for_provider(self, name: str, bundle=None):
        """Store for *name*, guest-scoped when the caller is a guest."""
        p = self.principal
        if p is not None and p.is_guest and p.root:
            gs = self._make_guest_store(name, p.root)
            if gs is not None:
                return gs
        if bundle is not None:
            return bundle.store
        if self.store is not None and (
                not self.bundles or len(self.bundles) <= 1
                or (self.runner and getattr(self.runner, "name", "") == name)):
            return self.store
        b = (self.bundles or {}).get(name)
        return b.store if b is not None else self.store

    def _rebind_store_for_principal(self):
        """After auth, point self.store at the guest harness home if needed."""
        p = self.principal
        if p is None or not p.is_guest or not p.root:
            return
        name = ""
        if self.runner is not None:
            name = str(getattr(self.runner, "name", "") or "")
        elif self.bundles and len(self.bundles) == 1:
            name = next(iter(self.bundles))
        if not name:
            return
        gs = self._make_guest_store(name, p.root)
        if gs is not None:
            self.store = gs

    def _require_job(self, job_id):
        """Return Job if it exists *and* belongs to self.principal, else None.

        Unknown ids and cross-account ids both look like 404 so one account
        cannot probe another's job ids.
        """
        job = None
        mgr = self._jobs_for_id(job_id)
        if mgr is not None:
            job = mgr.get(job_id)
        if job is None:
            # Multi: search all bundles.
            _name, _b, job = self._find_job(job_id)
        if job is None:
            return None
        if not accounts.job_in_scope(job, self.principal):
            return None
        return job

    def _session_in_scope(self, session) -> bool:
        if not isinstance(session, dict):
            return False
        return accounts.record_in_scope(session, self.principal)

    def _filter_rows(self, rows) -> list:
        return accounts.filter_records(rows, self.principal)

    # ---- focus list --------------------------------------------------
    #
    # Focus is a filter over the one session list, not a second view: rows keep
    # their shape and gain two fields — `focus` (is this a card) and
    # `focus_state` (the tag to draw). Membership is stored; the tag is
    # derived on every request from live job state, so it can never go stale.

    def _focus_live_state(self) -> dict:
        """key -> (running, pending) for every in-flight turn.

        Keyed by both session id and `job:<id>` so a card enrolled before its
        session existed still resolves.
        """
        try:
            rows = self._active_status_scoped() or []
        except Exception:
            log.exception("focus: active status failed")
            return {}
        out = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            # `asking` is set exactly when AskUserQuestion is on screen, so it
            # is the same "stop and answer" fact as the pending flags. The
            # `planning` phase is NOT included: it is the agent's own todo list
            # (claude.py emit_claude_todos), not a request for a human.
            pending = bool(row.get("pending_permission")
                           or row.get("pending_question")
                           or str(row.get("phase") or "") == "asking")
            for field in ("new_session_id", "session_id"):
                key = str(row.get(field) or "").strip()
                if key:
                    out[key] = (True, pending)
            jid = str(row.get("job_id") or "").strip()
            if jid:
                out[focus_store.JOB_KEY_PREFIX + jid] = (True, pending)
        return out

    def _focus_job_briefs(self) -> list:
        """Every job this daemon still remembers, finished ones included."""
        out = []
        managers = []
        if self.jobs is not None:
            managers.append(self.jobs)
        for bundle in (self.bundles or {}).values():
            if bundle.jobs not in managers:
                managers.append(bundle.jobs)
        for mgr in managers:
            try:
                out.extend(mgr.list_jobs() or [])
            except Exception:
                log.exception("focus: list_jobs failed")
        return out

    def _focus_failed_keys(self, briefs) -> set:
        """Session keys whose MOST RECENT remembered job ended in error.

        Only the latest job counts — an old failure followed by a good turn is
        not a failed session. `stopped` is excluded: you pressing Stop is a
        decision, not a breakage.

        Limitation worth knowing: this reads the in-memory job list, so a
        failure is forgotten once the job is evicted or the daemon restarts,
        and the row falls back to "turn finished".
        """
        latest = {}   # key -> (started_at, status)
        for job in briefs or []:
            if not isinstance(job, dict):
                continue
            status = str(job.get("status") or "")
            started = 0.0
            try:
                started = float(job.get("started_at") or 0.0)
            except (TypeError, ValueError):
                started = 0.0
            for field in ("new_session_id", "session_id"):
                key = str(job.get(field) or "").strip()
                if not key:
                    continue
                prev = latest.get(key)
                if prev is None or started >= prev[0]:
                    latest[key] = (started, status)
        return {k for k, (_ts, st) in latest.items() if st == "error"}

    def _focus_migrate_keys(self) -> None:
        """Follow cards as a turn learns its real session id.

        Driven by the job list rather than the session rows, because only
        synthetic running-job rows carry `job_id` — once a turn finishes, its
        transcript row has no link back to the job, and a card still keyed
        `job:<id>` would be orphaned with nothing to match it.
        """
        b = self.focus
        if b is None:
            return
        for job in self._focus_job_briefs():
            if not isinstance(job, dict):
                continue
            jid = str(job.get("id") or "").strip()
            old = str(job.get("session_id") or "").strip()
            new = str(job.get("new_session_id") or "").strip()
            target = new or old
            if not target:
                continue
            if jid:
                b.rekey(focus_store.JOB_KEY_PREFIX + jid, target)
            # Providers that mint a fresh id on resume (grok) leave the card
            # sitting on a session that will never be written to again.
            if new and old and new != old:
                b.rekey(old, new)

    def _decorate_focus(self, rows, live=None) -> list:
        """Stamp focus membership + state tag + title override onto rows."""
        b = self.focus
        if b is None:
            return list(rows or [])
        self._focus_migrate_keys()
        if live is None:
            live = self._focus_live_state()
        failed = self._focus_failed_keys(self._focus_job_briefs())
        active = b.active_keys()
        out = []
        for row in rows or []:
            if not isinstance(row, dict):
                out.append(row)
                continue
            row = dict(row)
            key = str(row.get("id") or "").strip()
            jid = str(row.get("job_id") or "").strip()
            title = b.title(key)
            if title:
                row["title"] = title
                entry = b.title_entry(key) or {}
                row["title_manual"] = bool(entry.get("manual"))
            member = key in active
            row["focus"] = member
            if not member:
                out.append(row)
                continue
            running, pending = live.get(key, (None, False))
            if running is None and jid:
                running, pending = live.get(
                    focus_store.JOB_KEY_PREFIX + jid, (None, False))
            if running is None:
                # No live job for this row; the synthetic running-job rows
                # still carry the flag, so trust it before calling it idle.
                running = bool(row.get("running"))
            row["focus_state"] = focus_store.state_for(
                running=bool(running),
                pending=bool(pending),
                failed=key in failed,
            )
            # Cosmetic companion to the state, not a state of its own: a
            # finished turn you have not opened is drawn lit, one you have is
            # drawn dim. Clients advance the cursor via /api/focus/<key>/seen.
            row["focus_unread"] = self._activity_sort_key(row) > b.seen_at(key)
            out.append(row)
        return out

    def _focus_key_for_job(self, job, session_id: str = "") -> str:
        """Focus key for a job: its session id once known, else the job id."""
        key = str(session_id or "").strip()
        if key:
            return key
        for attr in ("new_session_id", "session_id"):
            key = str(getattr(job, attr, "") or "").strip()
            if key:
                return key
        return focus_store.JOB_KEY_PREFIX + str(getattr(job, "id", "") or "")

    def _focus_enroll_key(self, key: str, *, provider: str = "",
                          cwd: str = "", job_id: str = "") -> None:
        """Put a card on the focus list for a human-driven turn.

        Only ever reached from POST handlers past the auth gate. Work the agent
        starts for itself arrives on /internal/* and never gets here, which is
        what keeps subagents and hook traffic off the list.
        """
        b = self.focus
        if b is None or not key:
            return
        try:
            b.enroll(key, provider=provider, cwd=cwd, job_id=job_id)
        except Exception:
            # Never fail the turn the human asked for over bookkeeping.
            log.exception("focus: enroll failed")

    def _focus_enroll(self, job, session_id: str = "") -> None:
        if job is None:
            self._focus_enroll_key(str(session_id or "").strip())
            return
        provider = str(getattr(job, "provider", "") or "")
        if not provider and self.runner is not None:
            provider = str(getattr(self.runner, "name", "") or "")
        self._focus_enroll_key(
            self._focus_key_for_job(job, session_id),
            provider=provider,
            cwd=str(getattr(job, "cwd", "") or ""),
            job_id=str(getattr(job, "id", "") or ""),
        )

    def _all_session_rows(self, limit: int = 200) -> list:
        """Every session row this principal can see, provider-tagged."""
        limit = max(1, min(int(limit or 200), 200))
        if self.bundles and len(self.bundles) > 1:
            rows = self._merged_sessions(None, limit, True)
        elif self.store is not None:
            rows = self._sessions_with_running(
                self.store.list_sessions(None, limit, user_only=True), limit)
        else:
            rows = []
        return self._filter_rows(rows)

    def _handle_focus_list(self):
        """The kanban rows: same session summaries, membership-filtered.

        Served as its own endpoint so thin clients (the pager) get the list
        without fetching and filtering the whole session list themselves.
        """
        b = self.focus
        if b is None:
            self._send_json({"sessions": [], "counts": {}, "total": 0})
            return
        rows = self._decorate_focus(self._all_session_rows(200))
        cards = [r for r in rows if isinstance(r, dict) and r.get("focus")]
        # Most urgent first, then most recently active — the pager shows only
        # the first row or two, so the ordering is the whole UI there.
        weight = {name: i for i, name in enumerate(focus_store.STATES)}
        cards.sort(key=lambda r: (
            weight.get(r.get("focus_state"), len(weight)),
            -self._activity_sort_key(r),
        ))
        counts = {name: 0 for name in focus_store.STATES}
        for row in cards:
            name = row.get("focus_state")
            if name in counts:
                counts[name] += 1
        self._send_json({
            "sessions": cards,
            "counts": counts,
            "total": len(cards),
            "states": list(focus_store.STATES),
            "labels": dict(focus_store.STATE_LABELS),
        })

    def _focus_key_from_path(self, raw: str) -> str:
        return unquote(str(raw or "")).strip()

    def _route_focus_post(self, path, body) -> bool:
        """Handle a focus/title POST. True when this request was answered."""
        # Longest pattern first: /title/regenerate also matches /title's regex
        # prefix only by accident of ordering, so be explicit about it.
        m = _SESSION_RETITLE.match(path)
        if m:
            self._handle_session_retitle(m.group(1))
            return True
        m = _SESSION_TITLE.match(path)
        if m:
            self._handle_session_title(m.group(1), body)
            return True
        for pattern, action in ((_FOCUS_DONE, "done"),
                                (_FOCUS_RESTORE, "restore"),
                                (_FOCUS_SEEN, "seen")):
            m = pattern.match(path)
            if m:
                self._handle_focus_action(m.group(1), action)
                return True
        return False

    def _handle_focus_action(self, raw_key: str, action: str):
        b = self.focus
        if b is None:
            self._error(503, "focus list unavailable")
            return
        key = self._focus_key_from_path(raw_key)
        if not key:
            self._error(400, "session key required")
            return
        if action == "done":
            changed = b.mark_done(key)
        elif action == "restore":
            changed = b.restore(key)
        else:
            changed = b.mark_seen(key)
        # Idempotent by design: marking done twice, or seen when already seen,
        # is a no-op the client should not have to special-case.
        self._send_json({"ok": True, "changed": bool(changed), "key": key,
                         "focus": b.is_member(key)})

    def _handle_session_title(self, raw_id: str, body):
        """Rename a session (or clear the override with an empty title)."""
        b = self.focus
        if b is None:
            self._error(503, "focus list unavailable")
            return
        if not isinstance(body, dict) or "title" not in body:
            self._error(400, "body must be JSON with a 'title'")
            return
        key = self._focus_key_from_path(raw_id)
        if not key:
            self._error(400, "session id required")
            return
        raw = body.get("title")
        if not isinstance(raw, str):
            self._error(400, "'title' must be a string")
            return
        title = b.set_title(key, raw, manual=True)
        self._send_json({"ok": True, "id": key, "title": title,
                         "manual": bool(title)})

    def _handle_session_retitle(self, raw_id: str):
        """Re-derive a title from the transcript, discarding any rename.

        Synchronous: it is a button press, and the model call is one short
        Haiku turn. Falls back to the provider's own title on any failure so
        the button can never blank a session's name.
        """
        b = self.focus
        if b is None:
            self._error(503, "focus list unavailable")
            return
        key = self._focus_key_from_path(raw_id)
        if not key:
            self._error(400, "session id required")
            return
        name, bundle, session = self._find_session(key)
        if session is None or not self._session_in_scope(session):
            self._error(404, "session not found")
            return
        store = bundle.store if bundle is not None else self.store
        text = self._retitle_source_text(store, key, session)
        if not text:
            self._error(422, "nothing to summarise yet")
            return
        # Named by the harness that owns it: Grok titles a Grok session, Codex a
        # Codex one. Runs inline because this is a button press — Grok/Codex go
        # through their CLI, so allow it the same budget their one-shot needs.
        runner = bundle.runner if bundle is not None else self.runner
        titler = getattr(runner, "title_for", None)
        title = ""
        if callable(titler):
            try:
                title = titler(text) or ""
            except Exception:
                log.exception("retitle failed for %s", key)
                title = ""
        if not title:
            # No usable login, or the harness declined. Say so plainly —
            # silently keeping the old title looks like a broken button.
            self._error(503, "%s could not name this session"
                             % (str(getattr(runner, "name", "") or "the harness")))
            return
        b.set_title(key, title, manual=False)
        self._send_json({"ok": True, "id": key, "title": title,
                         "manual": False})

    def _retitle_source_text(self, store, session_id: str, session: dict) -> str:
        """Text to summarise: the opening ask plus the latest exchange.

        Both ends matter — the first message says what the project *is*, the
        last says where it got to, and a title drawn from only one of them is
        wrong half the time.
        """
        parts = []
        title = str((session or {}).get("title") or "").strip()
        if title:
            parts.append(title)
        cwd = str((session or {}).get("cwd") or "").strip()
        if cwd:
            parts.append("Working directory: " + cwd)
        try:
            window = store.get_messages(session_id, offset=0, limit=4) or {}
            head = window.get("messages") or []
        except Exception:
            head = []
        try:
            total = int((window or {}).get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        tail = []
        if total > 8:
            try:
                win = store.get_messages(
                    session_id, offset=max(0, total - 4), limit=4) or {}
                tail = win.get("messages") or []
            except Exception:
                tail = []
        for group in (head, tail):
            for msg in group:
                if not isinstance(msg, dict):
                    continue
                text = " ".join(str(msg.get("text") or "").split())
                if text:
                    parts.append("%s: %s" % (msg.get("role") or "?", text[:600]))
        return "\n".join(parts).strip()

    def _provider_allowed(self, name: str) -> bool:
        p = self.principal
        if p is None:
            return True
        return p.allows_provider(name)

    def _allowed_bundle_items(self):
        """(name, bundle) pairs visible to the current principal."""
        items = list((self.bundles or {}).items())
        if self.principal is None or self.principal.is_main:
            return items
        return [(n, b) for n, b in items if self.principal.allows_provider(n)]

    def _deny_if_provider_blocked(self, name: str = "") -> bool:
        """If guest is not allowed this harness, send 403 and return True."""
        p = self.principal
        if p is None or p.is_main:
            return False
        prov = (name or "").strip().lower()
        if not prov and self.runner is not None:
            prov = str(getattr(self.runner, "name", "") or "").strip().lower()
        if prov and not p.allows_provider(prov):
            self._error(403, "provider not allowed")
            return True
        return False

    def _scoped_drop_path(self):
        p = self.principal
        if p is None:
            return self.config.drop_path
        return p.drop_path()

    def _scoped_upload_path(self):
        p = self.principal
        if p is None:
            return self.config.upload_path
        return p.upload_path()

    def _start_job_for_principal(self, **kwargs):
        """start_job with account + isolate_root stamped from self.principal."""
        p = self.principal or accounts.main_principal()
        kwargs.setdefault("account", p.account)
        kwargs.setdefault("isolate_root", p.isolate_root)
        # Guests must not run without a real confinement backend (bwrap /
        # sandbox-exec / chroot). Soft cd-only is not isolation.
        if p.is_guest and p.isolate_root and not accounts.isolation_ready(
                p.isolate_root):
            raise RuntimeError(accounts.isolation_required_hint())
        job = self.jobs.start_job(**kwargs)
        # Every caller of this method is a client POST past the auth gate —
        # /api/sessions/new or /api/sessions/<id>/continue — so starting a turn
        # here is by definition the human picking up a project.
        self._focus_enroll(job, str(kwargs.get("session_id") or ""))
        return job

    def _active_status_scoped(self):
        """Active jobs (+ busy TUIs) visible to the current principal only."""
        return self._filter_rows(self._merged_active_status())

    def _merged_active_status_scoped(self):
        return self._active_status_scoped()

    def _read_body(self):
        """Read and parse the request body.

        Always called once per POST before routing so the body is drained
        even by handlers that ignore it — an unread body desyncs HTTP
        keep-alive (the phone's QNAM reuses connections).
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            self.close_connection = True
            return None
        if length <= 0:
            return None
        if length > MAX_BODY:
            # Don't drain megabytes; drop the connection after responding.
            self.close_connection = True
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
            return None

    @staticmethod
    def _int_param(query, name, default):
        try:
            return int((query.get(name) or [default])[0])
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _flag(query, name):
        """Boolean query param: 1/true/yes/on. (parse_qs drops blank values,
        so a bare "?name" never reaches here.)"""
        values = query.get(name)
        if not values:
            return False
        return str(values[0]).strip().lower() in ("1", "true", "yes", "on")

    # -- routing ---------------------------------------------------------

    def do_GET(self):
        self._dispatch(self._route_get)

    def do_POST(self):
        self._dispatch(self._route_post)

    def do_OPTIONS(self):
        """CORS preflight.

        The browser sends this before any request carrying X-Auth-Token or a
        JSON body. Without an answer here every cross-daemon call from the web
        UI fails before it is ever made.

        Also answers Chrome's Private Network Access preflight: a public
        HTTPS page (e.g. Azure Static Web Apps) talking to a loopback or
        LAN daemon must get Access-Control-Allow-Private-Network or the
        browser blocks the call and the UI reports a generic CORS error.
        """
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "X-Auth-Token, Authorization, Content-Type, X-Grok-Token")
        self.send_header("Access-Control-Max-Age", "600")
        # Always allow — only the browser decides whether the target is
        # private; we never know our public-vs-private address space here.
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, route):
        """Never let a handler bug close the connection without a JSON error."""
        try:
            route()
        except Exception:
            log.exception("unhandled error handling %s %s", self.command, self.path)
            try:
                self._error(500, "internal server error")
            except OSError:
                pass  # client already gone

    def _route_get(self):
        path, query, bundle = self._bind_path()
        multi = bool(self.bundles) and len(self.bundles) > 1

        if path == "/api/ping":
            # Unauthenticated on purpose: lets the app discover the daemon
            # and verify connectivity before the user has entered the token.
            if multi and bundle is None:
                # Catalogue for one-profile clients: harness list + caps so
                # the app can paint a picker without a second fan-out.
                # With a guest token, only allowed harnesses are advertised.
                authorized = self._authorized(query)
                bundle_items = (
                    self._allowed_bundle_items() if authorized
                    else list((self.bundles or {}).items())
                )
                provider_names = [n for n, _ in bundle_items]
                payload = {
                    "ok": True,
                    "app": "agentremoted",
                    "version": __version__,
                    "host": platform.node(),
                    "multi": True,
                    "providers": provider_names,
                    "paths": {n: "/" + n for n in provider_names},
                }
                details = {}
                auth_by = {}
                for name, b in bundle_items:
                    details[name] = {
                        "caps": b.runner.capabilities(),
                    }
                    auth_fn = getattr(b.runner, "auth_health", None)
                    if callable(auth_fn):
                        try:
                            ah = auth_fn() or {}
                        except Exception:
                            log.exception("auth_health failed for %s", name)
                            ah = {
                                "cli": name,
                                "cli_on_path": False,
                                "mode": "unknown",
                                "status": "unknown",
                                "detail": "auth check failed",
                            }
                        details[name]["auth"] = ah
                        auth_by[name] = ah
                if authorized:
                    for name, b in bundle_items:
                        details[name]["slash_commands"] = b.runner.slash_commands()
                        models = getattr(b.runner, "models", None)
                        details[name]["models"] = models() if models else []
                        efforts = getattr(b.runner, "efforts", None)
                        details[name]["efforts"] = efforts() if efforts else []
                    try:
                        drop = self._scoped_drop_path()
                        drop.mkdir(parents=True, exist_ok=True)
                        payload["drop_path"] = str(drop)
                    except OSError:
                        payload["drop_path"] = str(self._scoped_drop_path())
                payload["provider_details"] = details
                # Aggregate auth: worst status among harnesses (missing > expired
                # > warning > unknown > ok) so a multi host shows one summary.
                payload["auth"] = self._auth_summary(auth_by)
                # Default harness for UIs that need a primary accent.
                payload["provider"] = provider_names[0] if provider_names else ""
                # Root caps are a union so one-profile clients know what any
                # harness can do without reading provider_details first.
                #
                # Unioned GENERICALLY on purpose: this used to list each key by
                # hand, and "rewind" was never added when that shipped — so
                # every client that reads root caps concluded the daemon could
                # not rewind, and refused before asking. A new cap must not be
                # able to go missing here again.
                union = {
                    "multi": True,
                    "requires_cwd": True,
                    "queue": True,
                    "stop": True,
                    "projects": True,
                    "ws_status": True,
                }
                for _n, b in bundle_items:
                    for key, val in (b.runner.capabilities() or {}).items():
                        if isinstance(val, bool):
                            union[key] = union.get(key, False) or val
                        elif key not in union:
                            union[key] = val
                payload["caps"] = union
                # Focus list support: clients gate the mode toggle,
                # rename, and regenerate on this flag.
                payload["focus"] = self.focus is not None
                payload["focus_states"] = list(focus_store.STATES)
                self._send_json(payload)
                return
            if self.runner is None:
                self._error(404, "unknown provider path")
                return
            # Guest token on a disallowed harness path: still answer ping
            # (connectivity) but mark provider; gated API routes return 403.
            payload = {
                "ok": True,
                "app": "agentremoted",
                "version": __version__,
                "host": platform.node(),
                "provider": self.runner.name,
                "caps": self.runner.capabilities(),
            }
            auth_fn = getattr(self.runner, "auth_health", None)
            if callable(auth_fn):
                try:
                    payload["auth"] = auth_fn() or {}
                except Exception:
                    log.exception("auth_health failed for %s", self.runner.name)
                    payload["auth"] = {
                        "cli": getattr(self.runner, "name", ""),
                        "cli_on_path": False,
                        "mode": "unknown",
                        "status": "unknown",
                        "detail": "auth check failed",
                    }
            # Command names can hint at internal workflows — only share the
            # slash-command / model lists with an authenticated caller.
            if self._authorized(query):
                payload["slash_commands"] = self.runner.slash_commands()
                models = getattr(self.runner, "models", None)
                payload["models"] = models() if models else []
                efforts = getattr(self.runner, "efforts", None)
                payload["efforts"] = efforts() if efforts else []
                # Absolute path the agent should copy host→phone files into.
                try:
                    drop = self._scoped_drop_path()
                    drop.mkdir(parents=True, exist_ok=True)
                    payload["drop_path"] = str(drop)
                except OSError:
                    payload["drop_path"] = str(self._scoped_drop_path())
            # Focus list support: clients gate the mode toggle,
            # rename, and regenerate on this flag.
            payload["focus"] = self.focus is not None
            payload["focus_states"] = list(focus_store.STATES)
            self._send_json(payload)
            return

        if not self._authorized(query):
            self._error(401, "missing or invalid token")
            return

        # Focus state spans providers, so it is answered before the harness
        # split — one implementation for the multi root and prefixed paths.
        if path == "/api/focus":
            self._handle_focus_list()
            return

        # Multi root: one profile talks to the catalogue host; we merge
        # sessions/jobs across harnesses and tag each row with provider.
        if multi and bundle is None:
            self._route_get_multi(path, query)
            return

        # Prefixed harness path (or single-provider process): guest allow-list.
        if self._deny_if_provider_blocked():
            return

        if path == "/ws/status" and wstream.is_upgrade(self.headers):
            # Hijacks the connection until the client leaves; nothing may
            # be written through the normal HTTP path afterwards.
            self.close_connection = True
            wstream.serve_status(self, None, active_fn=self._active_status_scoped)
            return

        if path == "/sse/status":
            self.close_connection = True
            ssestream.serve_status(self, None, active_fn=self._active_status_scoped)
            return

        if path == "/api/usage":
            usage_fn = getattr(self.runner, "usage", None)
            if usage_fn is None:
                self._send_json({"ok": False, "error": "not supported",
                                "provider": getattr(self.runner, "name", "") or "",
                                "account": "", "account_id": ""})
            else:
                data = usage_fn() or {}
                if isinstance(data, dict) and not data.get("provider"):
                    data = dict(data)
                    data["provider"] = getattr(self.runner, "name", "") or ""
                self._send_json(data)
            return

        if path == "/api/projects":
            self._send_json({
                "projects": self._filter_rows(self.store.list_projects()),
            })
            return

        if path == "/api/sessions":
            project = (query.get("project") or [None])[0]
            limit = min(max(self._int_param(query, "limit", 25), 1), 200)
            # Over-fetch then filter so guests still fill the limit.
            fetch = limit if (
                self.principal is None or self.principal.is_main
            ) else min(max(limit * 4, 50), 200)
            sessions = self.store.list_sessions(
                project, fetch, user_only=not self._flag(query, "all"))
            sessions = self._filter_rows(
                self._sessions_with_running(sessions, fetch))
            self._send_json({
                "sessions": self._decorate_focus(sessions[:limit]),
            })
            return

        if path == "/api/sessions/search":
            # Full-text over titles + human-visible message text. The phone
            # highlights `q` in title/snippet client-side (brand accent).
            # stream=1 → NDJSON progressive hits (web); default stays JSON.
            from . import search_util
            q = search_util.normalize_query((query.get("q") or [""])[0])
            if not q:
                self._send_json({"query": "", "results": []})
                return
            project = (query.get("project") or [None])[0]
            limit = min(max(self._int_param(query, "limit", 25), 1), 100)
            user_only = not self._flag(query, "all")
            if self._flag(query, "stream"):
                self._stream_search(q, project, limit, user_only, multi=False)
                return
            search_fn = getattr(self.store, "search_sessions", None)
            if search_fn is None:
                self._error(501, "search not supported")
                return
            fetch = limit if (
                self.principal is None or self.principal.is_main
            ) else min(max(limit * 4, 50), 100)
            results = search_fn(q, project, fetch, user_only=user_only)
            results = self._decorate_focus(self._filter_rows(results)[:limit])
            self._send_json({"query": q, "results": results})
            return

        m = _SESSION_MSGS.match(path)
        if m:
            self._send_session_messages(m.group(1), query)
            return

        m = _SESSION_STEP.match(path)
        if m:
            self._send_session_step(m.group(1), m.group(2))
            return

        m = _SESSION_TUI.match(path)
        if m:
            self._handle_tui_capture(m.group(1), query)
            return

        m = _SESSION_ONE.match(path)
        if m:
            session = self.store.get_session(m.group(1))
            if session is None or not self._session_in_scope(session):
                self._error(404, "session not found")
            else:
                self._send_json(self._decorate_focus([session])[0])
            return

        if path == "/api/jobs":
            self._send_json({"jobs": self._filter_rows(self.jobs.list_jobs())})
            return

        m = _JOB_ONE.match(path)
        if m:
            job = self._require_job(m.group(1))
            if job is None:
                self._error(404, "job not found")
            else:
                since = max(self._int_param(query, "since", 0), 0)
                self._send_json(job.snapshot(since))
            return

        if path == "/api/drop":
            self._handle_drop_list()
            return

        m = _DROP_FILE.match(path)
        if m:
            self._handle_drop_download(m.group(1))
            return

        self._error(404, "not found")

    def _route_get_multi(self, path, query):
        """Root routes for multi-provider (one client profile)."""
        if path == "/ws/status" and wstream.is_upgrade(self.headers):
            self.close_connection = True
            wstream.serve_status(
                self, None, active_fn=self._merged_active_status_scoped)
            return
        if path == "/sse/status":
            self.close_connection = True
            ssestream.serve_status(
                self, None, active_fn=self._merged_active_status_scoped)
            return
        if path == "/api/usage":
            # One profile, every harness: return per-provider sections plus a
            # flat buckets list (titles tagged "Claude · …") for older clients
            # that only render buckets. Guests only see allowed harnesses.
            sections = []
            flat = []
            for name, b in self._allowed_bundle_items():
                label = str(name or "").strip().capitalize() or "Agent"
                usage_fn = getattr(b.runner, "usage", None)
                if usage_fn is None:
                    sections.append({
                        "provider": name,
                        "account": "",
                        "account_id": "",
                        "ok": False,
                        "error": "not supported",
                        "buckets": [],
                    })
                    continue
                try:
                    data = usage_fn() or {}
                except Exception as e:
                    log.exception("usage probe failed for %s", name)
                    sections.append({
                        "provider": name,
                        "account": "",
                        "account_id": "",
                        "ok": False,
                        "error": str(e) or "usage failed",
                        "buckets": [],
                    })
                    continue
                if not isinstance(data, dict):
                    sections.append({
                        "provider": name,
                        "account": "",
                        "account_id": "",
                        "ok": False,
                        "error": "invalid usage response",
                        "buckets": [],
                    })
                    continue
                account = str(data.get("account") or "").strip()
                account_id = str(data.get("account_id") or account).strip()
                if data.get("ok") is False:
                    log.warning("usage %s: %s", name, data.get("error") or "not available")
                    sections.append({
                        "provider": name,
                        "account": account,
                        "account_id": account_id,
                        "ok": False,
                        "error": data.get("error") or "not available",
                        "buckets": [],
                    })
                    continue
                buckets = []
                for raw in (data.get("buckets") or []):
                    if not isinstance(raw, dict):
                        continue
                    bucket = dict(raw)
                    bucket["provider"] = name
                    bucket["account"] = account
                    bucket["account_id"] = account_id
                    title = str(bucket.get("title") or "").strip()
                    # Prefix once so single-list UIs still show which harness.
                    if title and not title.lower().startswith(label.lower() + " "):
                        bucket["title"] = "%s · %s" % (label, title)
                    elif not title:
                        bucket["title"] = label
                    buckets.append(bucket)
                    flat.append(dict(bucket))
                # Stale cache after Anthropic 429: still ok=true with bars + note.
                note = ""
                if data.get("stale") or data.get("error"):
                    note = str(data.get("error") or "cached")
                    log.info("usage %s: serving %s snapshot (%s)",
                             name,
                             "stale" if data.get("stale") else "cached",
                             note[:80])
                sections.append({
                    "provider": name,
                    "account": account,
                    "account_id": account_id,
                    "ok": True,
                    "error": note,
                    "buckets": buckets,
                    "cached": bool(data.get("cached") or data.get("stale")),
                    "stale": bool(data.get("stale")),
                })
            any_ok = any(s.get("ok") for s in sections)
            self._send_json({
                "ok": any_ok,
                "multi": True,
                "sections": sections,
                "buckets": flat,
                "error": "" if any_ok else "no usage data",
            })
            return
        if path == "/api/projects":
            self._send_json({
                "projects": self._filter_rows(self._merged_projects()),
            })
            return
        if path == "/api/sessions":
            project = (query.get("project") or [None])[0]
            # project ids are "provider:id" when merged; strip for store.
            provider_filter = None
            if project and ":" in str(project):
                provider_filter, project = str(project).split(":", 1)
            limit = min(max(self._int_param(query, "limit", 25), 1), 200)
            user_only = not self._flag(query, "all")
            fetch = limit if (
                self.principal is None or self.principal.is_main
            ) else min(max(limit * 4, 50), 200)
            if provider_filter and provider_filter in (self.bundles or {}):
                b = self.bundles[provider_filter]
                store = self._store_for_provider(provider_filter, b)
                sessions = [dict(s, provider=provider_filter)
                            for s in store.list_sessions(
                                project, fetch, user_only=user_only)]
            else:
                sessions = self._merged_sessions(project, fetch, user_only)
            sessions = self._filter_rows(sessions)[:limit]
            self._send_json({"sessions": self._decorate_focus(sessions)})
            return
        if path == "/api/sessions/search":
            from . import search_util
            q = search_util.normalize_query((query.get("q") or [""])[0])
            if not q:
                self._send_json({"query": "", "results": []})
                return
            project = (query.get("project") or [None])[0]
            limit = min(max(self._int_param(query, "limit", 25), 1), 100)
            user_only = not self._flag(query, "all")
            if self._flag(query, "stream"):
                self._stream_search(q, project, limit, user_only, multi=True)
                return
            fetch = limit if (
                self.principal is None or self.principal.is_main
            ) else min(max(limit * 4, 50), 100)
            results = self._merged_search(
                q, project, fetch, user_only=user_only)
            results = self._decorate_focus(self._filter_rows(results)[:limit])
            self._send_json({"query": q, "results": results})
            return
        m = _SESSION_MSGS.match(path)
        if m:
            name, b, session = self._find_session(m.group(1))
            if b is None or not self._session_in_scope(session or {}):
                self._error(404, "session not found")
                return
            self._bind_bundle(name)
            self._send_session_messages(m.group(1), query)
            return
        m = _SESSION_STEP.match(path)
        if m:
            name, b, session = self._find_session(m.group(1))
            if b is None or not self._session_in_scope(session or {}):
                self._error(404, "session not found")
                return
            self._bind_bundle(name)
            self._send_session_step(m.group(1), m.group(2))
            return
        m = _SESSION_TUI.match(path)
        if m:
            name, b, session = self._find_session(m.group(1))
            if b is None:
                # TUI may exist for a brand-new session before store indexes it.
                self._handle_tui_capture(m.group(1), query)
                return
            if not self._session_in_scope(session or {}):
                self._error(404, "session not found")
                return
            self._bind_bundle(name)
            self._handle_tui_capture(m.group(1), query)
            return
        m = _SESSION_ONE.match(path)
        if m:
            name, b, session = self._find_session(m.group(1))
            if session is None or not self._session_in_scope(session):
                self._error(404, "session not found")
                return
            session = dict(session)
            session["provider"] = name
            self._send_json(self._decorate_focus([session])[0])
            return
        if path == "/api/jobs":
            jobs = []
            for name, b in (self.bundles or {}).items():
                for j in b.jobs.list_jobs():
                    j = dict(j)
                    j["provider"] = name
                    jobs.append(j)
            self._send_json({"jobs": self._filter_rows(jobs)})
            return
        m = _JOB_ONE.match(path)
        if m:
            name, b, job = self._find_job(m.group(1))
            if job is None or not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            since = max(self._int_param(query, "since", 0), 0)
            snap = job.snapshot(since)
            if isinstance(snap, dict):
                snap = dict(snap)
                snap["provider"] = name
            self._send_json(snap)
            return
        if path == "/api/drop":
            self._handle_drop_list()
            return
        m = _DROP_FILE.match(path)
        if m:
            self._handle_drop_download(m.group(1))
            return
        self._error(404, "not found")

    def _send_session_step(self, session_id, ref):
        """Full text behind a truncated step, fetched only when expanded —
        this is what keeps a 200KB tool result out of the window fetch."""
        session = None
        if self.store is not None:
            session = self.store.get_session(session_id)
        if session is None:
            _n, _b, session = self._find_session(session_id)
        if session is None or not self._session_in_scope(session):
            self._error(404, "session not found")
            return
        getter = getattr(self.store, "get_step", None)
        if not callable(getter):
            self._error(404, "not supported by this harness")
            return
        result = getter(session_id, unquote(ref))
        if result is None:
            self._error(404, "step not found")
            return
        self._send_json(result)

    def _send_session_messages(self, session_id, query):
        session = None
        if self.store is not None:
            session = self.store.get_session(session_id)
        if session is None:
            _n, _b, session = self._find_session(session_id)
        if session is None or not self._session_in_scope(session):
            self._error(404, "session not found")
            return
        offset = self._int_param(query, "offset", None)
        limit = min(max(self._int_param(query, "limit", 50), 1), 500)
        # Opt-in process view. Without it the response is byte-identical to
        # what every client got before steps existed, which is what keeps
        # BlackBerry / Android / ESP32 out of this feature entirely.
        want_steps = (query.get("detail", [""])[0] or "").lower() == "steps"
        if want_steps and getattr(self.store, "supports_steps", False):
            result = self.store.get_messages(session_id, offset, limit,
                                             steps=True)
        else:
            result = self.store.get_messages(session_id, offset, limit)
        if result is None:
            self._error(404, "session not found")
            return
        t = result.get("timing")
        s0 = time.perf_counter()
        body = self._json_bytes(result)
        if t is not None:
            t["serialize_ms"] = round((time.perf_counter() - s0) * 1000, 1)
            t["body_bytes"] = len(body)
            log.info(
                "messages %s: parse=%.0fms render=%.0fms serialize=%.0fms "
                "total=%d window=%d file=%dKB body=%dKB",
                session_id[:8], t.get("parse_ms", 0), t.get("render_ms", 0),
                t.get("serialize_ms", 0), t.get("count_total", 0),
                t.get("count_window", 0), t.get("file_bytes", 0) // 1024,
                len(body) // 1024)
            body = self._json_bytes(result)
        self._send_json_bytes(body)

    def _route_post(self):
        path, query, bundle = self._bind_path()
        multi = bool(self.bundles) and len(self.bundles) > 1

        # Attachments carry a raw (possibly binary, several-MB) body — they
        # must not go through the JSON body reader and its 256 KB cap.
        if path == "/api/attachments":
            if multi and bundle is None:
                # Shared upload dir — any harness can use it.
                pass
            if not self._authorized(query):
                self._error(401, "missing or invalid token")
                return
            self._handle_attachment(query)
            return

        body = self._read_body()

        # The permission bridge is called by the helper MCP tool, which holds
        # the job's per-run nonce instead of the app token. Handle it before
        # the token gate. It long-polls until the phone answers.
        # Always unprefixed so MCP env stays simple in multi mode.
        if path == "/internal/permission":
            self._handle_internal_permission(body)
            return

        # Hook posts from daemon-spawned interactive TUIs (SessionStart /
        # Stop), authenticated by the persistent hook secret in the URL.
        if path == "/internal/hook":
            secret = (query.get("secret") or [""])[0]
            tui_name = (query.get("tui") or [""])[0]
            payload = body if isinstance(body, dict) else {}
            runners = []
            if self.runner is not None:
                runners.append(self.runner)
            for b in (self.bundles or {}).values():
                if b.runner not in runners:
                    runners.append(b.runner)
            for runner in runners:
                handler = getattr(runner, "on_hook", None)
                if handler is not None and handler(payload, secret, tui_name):
                    self._send_json({"ok": True})
                    return
            self._error(403, "bad hook")
            return

        if not self._authorized(query):
            self._error(401, "missing or invalid token")
            return

        # Focus + title writes span providers, so they are answered before the
        # harness split — one implementation for multi root and prefixed paths.
        if self._route_focus_post(path, body):
            return

        # Multi root: resolve harness from body.provider or session/job id.
        if multi and bundle is None:
            self._route_post_multi(path, query, body)
            return

        # Prefixed harness path (or single-provider process): guest allow-list.
        if self._deny_if_provider_blocked():
            return

        if path == "/api/clientlog":
            self._handle_client_log(body)
            return

        if path == "/api/shell":
            self._handle_shell(body)
            return

        m = _JOB_PERM.match(path)
        if m:
            self._handle_permission_answer(m.group(1), body)
            return

        m = _JOB_QUESTION.match(path)
        if m:
            self._handle_question_answer(m.group(1), body)
            return

        m = _SESSION_CONT.match(path)
        if m:
            self._handle_continue(m.group(1), body)
            return

        m = _SESSION_TUI_KEYS.match(path)
        if m:
            self._handle_tui_keys(m.group(1), body)
            return

        if path == "/api/sessions/new":
            self._handle_new_session(body)
            return

        m = _JOB_INPUT.match(path)
        if m:
            self._handle_job_input(m.group(1), body)
            return

        m = _JOB_QUEUE.match(path)
        if m:
            self._handle_queue(m.group(1), body)
            return

        m = _JOB_QCANCEL.match(path)
        if m:
            self._handle_queue_cancel(m.group(1), m.group(2))
            return

        m = _JOB_STOP.match(path)
        if m:
            job = self._require_job(m.group(1))
            if job is None:
                self._error(404, "job not found")
            elif self.jobs.stop(m.group(1)):
                self._send_json({"ok": True})
            else:
                self._error(404, "job not found")
            return

        m = _DROP_DELETE.match(path)
        if m:
            self._handle_drop_delete(m.group(1))
            return

        self._error(404, "not found")

    def _route_post_multi(self, path, query, body):
        """Root POST routes when one profile owns every harness."""
        if path == "/api/clientlog":
            self._handle_client_log(body)
            return
        if path == "/api/shell":
            # Resolve store via session_id so cwd lookup still works.
            sid = ""
            if isinstance(body, dict):
                sid = body.get("session_id") or body.get("sessionId") or ""
            if sid:
                name, b, session = self._find_session(str(sid))
                if b is not None and self._session_in_scope(session or {}):
                    self._bind_bundle(name)
                elif b is not None:
                    self._error(404, "session not found")
                    return
            elif self.bundles:
                # Fall back to first harness for store-less shell.
                self._bind_bundle(next(iter(self.bundles)))
            self._handle_shell(body)
            return
        if path == "/api/sessions/new":
            if not isinstance(body, dict):
                self._error(400, "body must be JSON")
                return
            provider = str(body.get("provider") or "").strip().lower()
            allowed = [n for n, _ in self._allowed_bundle_items()]
            if not provider:
                self._error(400, "provider is required "
                            "(one of: %s)" % ", ".join(allowed or self.bundles.keys()))
                return
            if provider not in self.bundles:
                self._error(400, "unknown provider %r" % provider)
                return
            if not self._provider_allowed(provider):
                self._error(403, "provider not allowed")
                return
            self._bind_bundle(provider)
            self._handle_new_session(body)
            return
        m = _SESSION_CONT.match(path)
        if m:
            name, b, session = self._find_session(m.group(1))
            if b is None or not self._session_in_scope(session or {}):
                self._error(404, "session not found")
                return
            if name and not self._provider_allowed(name):
                self._error(403, "provider not allowed")
                return
            self._bind_bundle(name)
            self._handle_continue(m.group(1), body)
            return
        m = _SESSION_TUI_KEYS.match(path)
        if m:
            name, b, session = self._find_session(m.group(1))
            if b is not None:
                if not self._session_in_scope(session or {}):
                    self._error(404, "session not found")
                    return
                self._bind_bundle(name)
            self._handle_tui_keys(m.group(1), body)
            return
        m = _JOB_PERM.match(path)
        if m:
            name, b, job = self._find_job(m.group(1))
            if b is None or not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_permission_answer(m.group(1), body)
            return
        m = _JOB_QUESTION.match(path)
        if m:
            name, b, job = self._find_job(m.group(1))
            if b is None or not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_question_answer(m.group(1), body)
            return
        m = _JOB_INPUT.match(path)
        if m:
            jid = m.group(1)
            name, b, job = self._find_job(jid)
            if b is None:
                # Synthetic tui-* status rows (busy host TUI, no JobManager
                # job) — still accept input by resolving the session id.
                name, b, _sid = self._resolve_synthetic_tui_job(jid)
                if b is None:
                    self._error(404, "job not found")
                    return
            elif not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_job_input(jid, body)
            return
        m = _JOB_QUEUE.match(path)
        if m:
            name, b, job = self._find_job(m.group(1))
            if b is None or not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_queue(m.group(1), body)
            return
        m = _JOB_QCANCEL.match(path)
        if m:
            name, b, job = self._find_job(m.group(1))
            if b is None or not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_queue_cancel(m.group(1), m.group(2))
            return
        m = _JOB_STOP.match(path)
        if m:
            name, b, job = self._find_job(m.group(1))
            if b is None or not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            if b.jobs.stop(m.group(1)):
                self._send_json({"ok": True})
            else:
                self._error(404, "job not found")
            return
        m = _DROP_DELETE.match(path)
        if m:
            self._handle_drop_delete(m.group(1))
            return
        self._error(404, "not found")

    # -- handlers ----------------------------------------------------------

    def _handle_client_log(self, body):
        """Append a client-side line (e.g. transcript-load timing) to
        ~/.agentremoted/client-timing.log so it can be analyzed off-device."""
        if not isinstance(body, dict):
            self._send_json({"ok": False}, status=400)
            return
        line = str(body.get("line", "")).replace("\n", " ").replace("\r", " ")
        if not line:
            self._send_json({"ok": False}, status=400)
            return
        tag = str(body.get("app", ""))[:40]
        try:
            path = self.config.log_path
            path.parent.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s [%s %s] %s\n" % (stamp, tag, self.address_string(), line))
        except OSError as e:
            log.warning("client log write failed: %s", e)
            self._send_json({"ok": False}, status=500)
            return
        self._send_json({"ok": True})

    def _handle_shell(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("command"), str) \
                or not body["command"].strip():
            self._error(400, "body must be JSON with a non-empty 'command'")
            return
        cmd = body["command"]
        # Prefer an explicit cwd from the phone; otherwise resolve from the
        # open session id so we never fall back to the daemon's own PWD
        # (launchd WorkingDirectory is often the agentremoted source tree).
        cwd = body.get("cwd") or None
        if isinstance(cwd, str):
            cwd = cwd.strip() or None
        else:
            cwd = None
        if not cwd:
            sid = body.get("session_id") or body.get("sessionId") or ""
            if isinstance(sid, str) and sid.strip():
                session = self.store.get_session(sid.strip()) if self.store else None
                if session is None:
                    _n, _b, session = self._find_session(sid.strip())
                if session and self._session_in_scope(session):
                    raw = session.get("cwd") or ""
                    if isinstance(raw, str) and raw.strip():
                        cwd = raw.strip()
                elif session is not None:
                    self._error(404, "session not found")
                    return
        p = self.principal or accounts.main_principal()
        cwd_resolved, cerr = accounts.confine_cwd(cwd or "", p)
        if cerr:
            self._error(403, cerr)
            return
        cwd = cwd_resolved or None
        if cwd is not None and not os.path.isdir(cwd):
            self._error(400, "cwd not found: %s" % cwd)
            return
        run_kw = {
            "shell": True,
            "capture_output": True,
            "timeout": 30,
            "cwd": cwd,
        }
        if p.is_guest and p.isolate_root:
            if not accounts.isolation_ready(p.isolate_root):
                self._error(503, accounts.isolation_required_hint())
                return
            iso = accounts.isolation_popen_kwargs(p.isolate_root)
            if iso.get("cwd"):
                run_kw["cwd"] = iso["cwd"]
            if iso.get("preexec_fn"):
                run_kw["preexec_fn"] = iso["preexec_fn"]
            run_kw["env"] = accounts.isolation_env(None, p.isolate_root)
            cmd = accounts.wrap_shell_command(cmd, p.isolate_root)
        try:
            result = subprocess.run(cmd, **run_kw)
            output = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            if stderr:
                output = output + stderr if output else stderr
            self._send_json({
                "ok": True,
                "output": output,
                "exit_code": result.returncode,
                "cwd": cwd or "",
            })
        except subprocess.TimeoutExpired:
            self._error(504, "command timed out (30s)")
        except OSError as e:
            self._error(500, "exec failed: %s" % e)

    def _handle_continue(self, session_id, body):
        if not isinstance(body, dict) or not isinstance(body.get("prompt"), str) \
                or not body["prompt"].strip():
            self._error(400, "body must be JSON with a non-empty 'prompt'")
            return
        session = self.store.get_session(session_id) if self.store else None
        if session is None:
            _n, _b, session = self._find_session(session_id)
        if session is None or not self._session_in_scope(session):
            self._error(404, "session not found")
            return
        p = self.principal or accounts.main_principal()
        cwd, cerr = accounts.confine_cwd(session.get("cwd", "") or "", p)
        if cerr:
            self._error(403, cerr)
            return
        try:
            job = self._start_job_for_principal(
                prompt=body["prompt"],
                cwd=cwd or session.get("cwd", ""),
                session_id=session_id,
                permission_mode=body.get("permission_mode", ""),
                model=str(body.get("model", "") or ""),
                effort=str(body.get("effort", "") or ""),
            )
        except RuntimeError as e:
            self._error(503, str(e))
            return
        # 200 (not 202): some mobile HTTP stacks / proxies mishandle 202
        # bodies; clients only need the job_id JSON either way.
        self._send_json({"job_id": job.id}, status=200)

    def _handle_new_session(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("prompt"), str) \
                or not body["prompt"].strip():
            self._error(400, "body must be JSON with a non-empty 'prompt'")
            return
        cwd = body.get("cwd", "")
        if not isinstance(cwd, str):
            cwd = ""
        p = self.principal or accounts.main_principal()
        cwd, cerr = accounts.confine_cwd(cwd, p)
        if cerr:
            self._error(403, cerr)
            return
        # claude requires a project dir; grok falls back to its workspace.
        if not cwd and self.runner.capabilities().get("requires_cwd", True):
            if p.is_guest:
                cwd = p.root
            else:
                self._error(400, "'cwd' is required for a new session")
                return
        try:
            job = self._start_job_for_principal(
                prompt=body["prompt"],
                cwd=cwd,
                permission_mode=body.get("permission_mode", ""),
                model=str(body.get("model", "") or ""),
                effort=str(body.get("effort", "") or ""),
            )
        except RuntimeError as e:
            self._error(503, str(e))
            return
        self._send_json({"job_id": job.id}, status=200)

    def _resolve_synthetic_tui_job(self, job_id):
        """Map synthetic status id `tui-<sidprefix>` → (provider, bundle, session_id).

        These ids are advertised by interactive managers' active_tui_status()
        so clients can pulse "working" after a real job timed out. They are
        not JobManager jobs — clients that POST /input against them used to
        get a hard 404 and drop the prompt.
        """
        jid = (job_id or "").strip()
        if not jid.startswith("tui-"):
            return None, None, None
        # Multi-harness: walk each provider's interactive manager.
        for name, b in (self.bundles or {}).items():
            runner = getattr(b, "runner", None)
            mgr_fn = getattr(runner, "_interactive_mgr", None) if runner else None
            if not callable(mgr_fn):
                continue
            try:
                mgr = mgr_fn()
                rows = (mgr.active_tui_status() or []) if hasattr(mgr, "active_tui_status") else []
            except Exception:
                continue
            for row in rows:
                if (row.get("job_id") or "") == jid:
                    sid = (row.get("new_session_id") or row.get("session_id") or "").strip()
                    if sid:
                        return name, b, sid
        # Single-provider.
        if not self.bundles and self.runner is not None:
            mgr_fn = getattr(self.runner, "_interactive_mgr", None)
            if callable(mgr_fn):
                try:
                    mgr = mgr_fn()
                    rows = (mgr.active_tui_status() or []) if hasattr(mgr, "active_tui_status") else []
                except Exception:
                    rows = []
                for row in rows:
                    if (row.get("job_id") or "") == jid:
                        sid = (row.get("new_session_id") or row.get("session_id") or "").strip()
                        if sid:
                            return None, None, sid  # caller uses self.runner
        return None, None, None

    def _handle_job_input(self, job_id, body):
        """Type a message into an interactive job's TUI (no daemon queue)."""
        if not isinstance(body, dict) or not isinstance(body.get("prompt"), str) \
                or not body["prompt"].strip():
            self._error(400, "body must be JSON with a non-empty 'prompt'")
            return
        prompt = body["prompt"]
        reason = None

        def _accepted(job_obj=None, session_id=""):
            # Typing into a live session is the human picking that project up,
            # exactly like starting a turn — so it enrols too.
            self._focus_enroll(job_obj, session_id)
            self._send_json({"ok": True}, status=202)

        # Ownership: never type into another account's real job.
        name, b, job = self._find_job(job_id)
        if job is not None:
            if not accounts.job_in_scope(job, self.principal):
                self._error(404, "job not found")
                return
            mgr = b.jobs if b is not None else self.jobs
            if mgr is not None:
                reason = mgr.type_into_tui(job_id, prompt)
                if not reason:
                    _accepted(job)
                    return
        elif self.jobs is not None and self.jobs.get(job_id) is not None:
            j = self.jobs.get(job_id)
            if not accounts.job_in_scope(j, self.principal):
                self._error(404, "job not found")
                return
            reason = self.jobs.type_into_tui(job_id, prompt)
            if not reason:
                _accepted(j)
                return
        # Real job missing / not interactive — try synthetic tui-* → session.
        if str(job_id).startswith("tui-") or (reason and "not running" in reason):
            _name, _b, sid = self._resolve_synthetic_tui_job(job_id)
            if not sid:
                # Also accept exact synthetic id match from *scoped* status.
                for row in self._active_status_scoped():
                    if (row.get("job_id") or "") == job_id:
                        sid = (row.get("new_session_id") or
                               row.get("session_id") or "").strip()
                        break
            if sid:
                # Session must be in scope so guest A cannot drive guest B's TUI.
                sess = None
                if self.store is not None:
                    sess = self.store.get_session(sid)
                if sess is None:
                    _n, _bb, sess = self._find_session(sid)
                if sess is not None and not self._session_in_scope(sess):
                    self._error(404, "job not found")
                    return
                runner = self._tui_runner_for_session(sid) or self.runner
                typer = getattr(runner, "type_into_tui", None) if runner else None
                if callable(typer):
                    reason = typer(sid, prompt) or ""
                    if not reason:
                        _accepted(session_id=sid)
                        return
                keys_fn = getattr(runner, "send_tui_keys", None) if runner else None
                if callable(keys_fn):
                    # Fallback: type the line + Enter into the host pane.
                    text = prompt if prompt.endswith("\n") else (prompt + "\n")
                    reason = keys_fn(sid, text=text) or ""
                    if not reason:
                        _accepted(session_id=sid)
                        return
        if reason:
            # 404 for unknown synthetic; 409 for real job that can't accept.
            if str(job_id).startswith("tui-") and "not running" in (reason or ""):
                self._error(404, "job not found")
            else:
                self._error(409, reason)
            return
        self._error(404, "job not found")

    def _tui_runner_for_session(self, session_id: str):
        """Pick the runner that owns a live TUI for session_id (multi or single)."""
        sid = (session_id or "").strip()
        if not sid:
            return None
        # Prefer the already-bound runner (path prefix or find_session).
        if self.runner is not None and hasattr(self.runner, "capture_tui"):
            frame = self.runner.capture_tui(sid)
            if frame.get("attached"):
                return self.runner
        # Multi: probe every harness.
        for b in (self.bundles or {}).values():
            r = b.runner
            if not hasattr(r, "capture_tui"):
                continue
            frame = r.capture_tui(sid)
            if frame.get("attached"):
                return r
        return self.runner if hasattr(self.runner or object(), "capture_tui") else None

    def _handle_tui_capture(self, session_id: str, query=None):
        """GET /api/sessions/<id>/tui — live host TUI pane text.

        Default text is plain (no SGR, simplified chrome) for BB and simple
        clients. Colour clients pass ``?ansi=1`` (or true/yes/on).
        """
        from .live_tui import frame_payload
        # Never stream another account's pane.
        sess = None
        if self.store is not None:
            sess = self.store.get_session(session_id)
        if sess is None:
            _n, _b, sess = self._find_session(session_id)
        if sess is not None and not self._session_in_scope(sess):
            self._error(404, "session not found")
            return
        want_ansi = self._flag(query or {}, "ansi")
        last = None
        # Multi: try every harness so an attached TUI is found regardless of
        # path binding; keep the last unattached frame for a useful error.
        if self.bundles:
            for b in self.bundles.values():
                r = b.runner
                if not hasattr(r, "capture_tui"):
                    continue
                frame = r.capture_tui(session_id, ansi=want_ansi)
                last = frame
                if frame.get("attached"):
                    self._send_json(frame)
                    return
            if last is not None:
                self._send_json(last)
                return
        if self.runner is not None and hasattr(self.runner, "capture_tui"):
            self._send_json(self.runner.capture_tui(session_id, ansi=want_ansi))
            return
        self._send_json(frame_payload(
            session_id, "", False,
            error="live TUI not available on this daemon",
            ansi=want_ansi,
        ))

    def _handle_tui_keys(self, session_id, body):
        """POST /api/sessions/<id>/tui/keys {keys?:[], text?:str}."""
        if not isinstance(body, dict):
            self._error(400, "body must be JSON")
            return
        keys = body.get("keys")
        text = body.get("text") or ""
        if not isinstance(text, str):
            text = str(text)
        if keys is not None and not isinstance(keys, list):
            self._error(400, "'keys' must be a list of key names")
            return
        if (not keys) and not text:
            self._error(400, "provide 'keys' and/or 'text'")
            return
        sess = None
        if self.store is not None:
            sess = self.store.get_session(session_id)
        if sess is None:
            _n, _b, sess = self._find_session(session_id)
        if sess is not None and not self._session_in_scope(sess):
            self._error(404, "session not found")
            return
        runner = self._tui_runner_for_session(session_id)
        if runner is None or not hasattr(runner, "send_tui_keys"):
            # Multi probe
            if self.bundles:
                for b in self.bundles.values():
                    r = b.runner
                    if hasattr(r, "send_tui_keys"):
                        err = r.send_tui_keys(session_id, keys=keys, text=text)
                        if not err or err != "no interactive TUI for this session":
                            if err:
                                self._error(409, err)
                            else:
                                self._send_json({"ok": True})
                            return
            self._error(409, "no interactive TUI for this session")
            return
        err = runner.send_tui_keys(session_id, keys=keys, text=text)
        if err:
            # Multi: try other harnesses if this one has no pane.
            if err == "no interactive TUI for this session" and self.bundles:
                for b in self.bundles.values():
                    r = b.runner
                    if r is runner or not hasattr(r, "send_tui_keys"):
                        continue
                    err2 = r.send_tui_keys(session_id, keys=keys, text=text)
                    if not err2:
                        self._send_json({"ok": True})
                        return
                    if err2 != "no interactive TUI for this session":
                        self._error(409, err2)
                        return
            self._error(409, err)
            return
        self._send_json({"ok": True})

    def _handle_queue(self, job_id, body):
        if not isinstance(body, dict) or not isinstance(body.get("prompt"), str) \
                or not body["prompt"].strip():
            self._error(400, "body must be JSON with a non-empty 'prompt'")
            return
        job = self._require_job(job_id)
        if job is None:
            self._error(404, "job not found")
            return
        queued, reason = self.jobs.enqueue(job_id, body["prompt"])
        if queued is None:
            self._error(409, reason)
            return
        # Queuing a prompt is a commitment to that project just as much as
        # sending one right now.
        self._focus_enroll(job)
        self._send_json({"queued": queued}, status=202)

    def _handle_queue_cancel(self, job_id, qid):
        if self._require_job(job_id) is None:
            self._error(404, "job not found")
            return
        result = self.jobs.cancel_queued(job_id, qid)
        if result is None:
            self._error(404, "no such queued prompt")
            return
        queued, prompt = result
        self._send_json({"ok": True, "queued": queued, "prompt": prompt})

    def _handle_permission_answer(self, job_id, body):
        if not isinstance(body, dict) or not isinstance(body.get("request_id"), str):
            self._error(400, "body must be JSON with 'request_id' and 'allow'")
            return
        if self._require_job(job_id) is None:
            self._error(404, "job not found")
            return
        ok = self.jobs.resolve_permission(
            job_id, body["request_id"], bool(body.get("allow")),
            str(body.get("message", "")))
        if ok:
            self._send_json({"ok": True})
        else:
            self._error(404, "no matching pending permission")

    def _handle_question_answer(self, job_id, body):
        """Answer AskUserQuestion: 'answers' is one list of chosen option
        labels per question (single-select questions carry exactly one), with
        optional 'notes' — free text per question for options that take one.
        A missing/false 'answers' with cancel=true Escapes the panel."""
        if not isinstance(body, dict) or not isinstance(body.get("request_id"), str):
            self._error(400, "body must be JSON with 'request_id' and 'answers'")
            return
        if self._require_job(job_id) is None:
            self._error(404, "job not found")
            return
        answers = None
        if not body.get("cancel"):
            raw = body.get("answers")
            if not isinstance(raw, list):
                self._error(400, "'answers' must be a list of label lists")
                return
            answers = []
            for entry in raw:
                if isinstance(entry, str):
                    entry = [entry]
                if not isinstance(entry, list):
                    self._error(400, "'answers' must be a list of label lists")
                    return
                answers.append([str(x) for x in entry])
        notes = None
        raw_notes = body.get("notes")
        if isinstance(raw_notes, str):
            notes = [raw_notes]
        elif isinstance(raw_notes, list):
            notes = [str(x or "") for x in raw_notes]
        ok = self.jobs.resolve_question(job_id, body["request_id"], answers,
                                        notes)
        if ok:
            self._send_json({"ok": True})
        else:
            self._error(404, "no matching pending question")

    def _handle_attachment(self, query):
        """Store a phone upload where the agent CLI can read it by path.

        Prefers Content-Length (Android/OkHttp always set it after 2.4.7).
        Without a length, read with a short socket timeout so keep-alive
        connections cannot hang the worker forever.
        """
        import socket
        name = os.path.basename((query.get("name") or ["file"])[0]).strip()
        name = "".join(ch if (ch.isalnum() or ch in "-_.") else "_"
                       for ch in name) or "file"
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        max_bytes = int(getattr(self.config, "max_upload_mb", 16) or 16) * 1024 * 1024
        if length > max_bytes:
            self.close_connection = True
            self._error(413, "attachment too large (max %d MB)"
                        % (max_bytes // (1024 * 1024)))
            return
        upload_dir = self._scoped_upload_path()
        dest = None
        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            dest = upload_dir / ("%s-%s" % (uuid.uuid4().hex[:8], name))
            written = 0
            with open(dest, "wb") as f:
                if length > 0:
                    remaining = length
                    while remaining > 0:
                        chunk = self.rfile.read(min(remaining, 256 * 1024))
                        if not chunk:
                            break
                        f.write(chunk)
                        remaining -= len(chunk)
                        written += len(chunk)
                    if remaining > 0:
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                        self._error(
                            400,
                            "upload truncated (%d of %d bytes) — network dropped mid-transfer"
                            % (written, length),
                        )
                        return
                else:
                    # No Content-Length: drain with a timeout so HTTP keep-alive
                    # does not block forever waiting for a body that never ends.
                    sock = getattr(self, "connection", None)
                    prev_to = None
                    if sock is not None:
                        try:
                            prev_to = sock.gettimeout()
                            sock.settimeout(30.0)
                        except (OSError, AttributeError):
                            prev_to = None
                    try:
                        while written <= max_bytes:
                            try:
                                chunk = self.rfile.read(256 * 1024)
                            except socket.timeout:
                                break
                            if not chunk:
                                break
                            f.write(chunk)
                            written += len(chunk)
                    finally:
                        if sock is not None and prev_to is not None:
                            try:
                                sock.settimeout(prev_to)
                            except (OSError, AttributeError):
                                pass
                    if written == 0:
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                        self._error(
                            400,
                            "empty upload (missing Content-Length and no body)",
                        )
                        return
                    if written > max_bytes:
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                        self.close_connection = True
                        self._error(413, "attachment too large (max %d MB)"
                                    % (max_bytes // (1024 * 1024)))
                        return
        except OSError as e:
            if dest is not None:
                try:
                    dest.unlink()
                except OSError:
                    pass
            self._error(500, "could not store attachment: %s" % e)
            return
        self._send_json({"ok": True, "path": str(dest), "size": written},
                        status=201)

    def _resolve_drop_entry(self, raw_name, *, dirs_ok=True):
        """Return (Path, None) or (None, error) for a drop entry.

        Resolving before the containment check is what keeps a symlink in the
        drop folder from serving files outside it — the resolved target has to
        land back inside.
        """
        name = _safe_drop_name(raw_name)
        if not name:
            return None, "invalid filename"
        try:
            drop_dir = self._scoped_drop_path().resolve()
        except OSError as e:
            return None, "drop dir unavailable: %s" % e
        try:
            drop_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return None, "drop dir unavailable: %s" % e
        candidate = (drop_dir / name).resolve()
        # Stay inside the drop folder even if name somehow escaped basename.
        try:
            candidate.relative_to(drop_dir)
        except ValueError:
            return None, "invalid filename"
        if candidate.is_dir():
            if not dirs_ok:
                return None, "that is a folder"
            return candidate, None
        if not candidate.is_file():
            return None, "file not found"
        return candidate, None

    def _resolve_drop_file(self, raw_name):
        """Files only — kept for callers that must not touch directories."""
        return self._resolve_drop_entry(raw_name, dirs_ok=False)

    @staticmethod
    def _dir_stats(root, cap=20000):
        """(bytes, file_count, truncated) for a staged folder.

        Bounded: a listing must not stall on a huge tree, so the walk gives up
        after `cap` files and says so instead of lying with a partial total.
        """
        total = files = 0
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for fn in filenames:
                    if fn.startswith("."):
                        continue
                    files += 1
                    if files > cap:
                        return total, cap, True
                    try:
                        total += os.path.getsize(os.path.join(dirpath, fn))
                    except OSError:
                        pass
        except OSError:
            pass
        return total, files, False

    def _zip_drop_dir(self, folder, drop_dir, max_bytes):
        """Zip a staged folder to a temp file. Returns (path, None) or
        (None, error). Caller always deletes the path it gets back.

        The archive is built OUTSIDE the drop folder on purpose: writing it
        inside would make it show up in the next listing (and, on a second
        download, zip the previous zip).
        """
        fd, tmp = tempfile.mkstemp(prefix="agentremoted-drop-", suffix=".zip")
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED,
                                 allowZip64=True) as zf:
                # Top-level entry named after the folder, so unzipping yields
                # one folder rather than spraying files into Downloads.
                for dirpath, dirnames, filenames in os.walk(folder):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    for fn in filenames:
                        if fn.startswith("."):
                            continue
                        full = os.path.join(dirpath, fn)
                        real = os.path.realpath(full)
                        # Same containment rule as _resolve_drop_entry: a
                        # symlink may not carry files out of the drop folder.
                        if os.path.commonpath([real, str(drop_dir)]) != str(drop_dir):
                            continue
                        if not os.path.isfile(real):
                            continue
                        rel = os.path.relpath(full, folder)
                        try:
                            zf.write(real, os.path.join(folder.name, rel))
                        except OSError:
                            continue
                        if os.path.getsize(tmp) > max_bytes:
                            return None, ("folder too large once zipped "
                                          "(max %d MB)"
                                          % (max_bytes // (1024 * 1024)))
                if not zf.namelist():
                    zf.writestr(folder.name + "/", b"")
        except (OSError, zipfile.BadZipFile, RuntimeError) as e:
            return None, "zip failed: %s" % e
        return tmp, None

    def _handle_drop_list(self):
        """List files the agent (or user) put in the host→phone drop folder."""
        drop_dir = self._scoped_drop_path()
        try:
            drop_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._error(500, "drop dir unavailable: %s" % e)
            return
        files = []
        try:
            for entry in sorted(drop_dir.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("."):
                    continue
                # macOS ~/Public/Drop Box and similar — not agent staging.
                if entry.name in _DROP_LIST_SKIP:
                    continue
                is_dir = entry.is_dir()
                if not is_dir and not entry.is_file():
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                row = {
                    "name": entry.name,
                    "size": int(st.st_size),
                    "mtime": int(st.st_mtime),
                    # "file" is stated explicitly so a client never has to
                    # infer it; older clients ignore the key and keep working.
                    "type": "dir" if is_dir else "file",
                }
                if is_dir:
                    total, count, partial = self._dir_stats(entry)
                    # size = what the folder weighs, so the row reads like a
                    # file row; the zip that downloads it will be smaller.
                    row["size"] = int(total)
                    row["entries"] = int(count)
                    row["partial"] = bool(partial)
                files.append(row)
        except OSError as e:
            self._error(500, "could not list drop: %s" % e)
            return
        self._send_json({
            "path": str(drop_dir.resolve()),
            "files": files,
        })

    def _handle_drop_download(self, raw_name):
        """Stream one drop entry as raw bytes to the phone.

        A folder is zipped to a temp file first (outside the drop folder) and
        the archive is deleted as soon as it is on the wire, so staging a
        folder never leaves a second copy on the host.
        """
        path, err = self._resolve_drop_entry(raw_name)
        if path is None:
            status = 404 if err == "file not found" else 400
            self._error(status, err)
            return
        max_bytes = int(getattr(self.config, "max_drop_mb", 64) or 64) * 1024 * 1024
        tmp_zip = None
        out_name = path.name
        if path.is_dir():
            try:
                drop_dir = self._scoped_drop_path().resolve()
            except OSError as e:
                self._error(500, "drop dir unavailable: %s" % e)
                return
            tmp_zip, zerr = self._zip_drop_dir(path, drop_dir, max_bytes)
            if tmp_zip is None:
                self._error(413 if "too large" in (zerr or "") else 500, zerr)
                return
            out_name = path.name + ".zip"
        try:
            read_from = tmp_zip if tmp_zip else str(path)
            try:
                size = os.path.getsize(read_from)
            except OSError as e:
                self._error(500, "stat failed: %s" % e)
                return
            if size > max_bytes:
                self._error(413, "file too large (max %d MB)"
                            % (max_bytes // (1024 * 1024)))
                return
            try:
                # Read fully: BB10's QNAM is happier with Content-Length + one
                # write than chunked transfer of an unknown length.
                with open(read_from, "rb") as fh:
                    data = fh.read()
            except OSError as e:
                self._error(500, "read failed: %s" % e)
                return
        finally:
            if tmp_zip:
                try:
                    os.unlink(tmp_zip)
                except OSError:
                    pass
        # ASCII-safe Content-Disposition filename; the real name is in the
        # URL path the client already knows.
        safe_ascii = "".join(
            ch if (ch.isalnum() or ch in "-_.") else "_" for ch in out_name
        ) or "file"
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % safe_ascii)
        self.send_header("Cache-Control", "no-store")
        # out_name, not path.name: a folder arrives as <folder>.zip and the
        # client saves it under the name it actually got.
        self.send_header("X-Drop-Name", out_name)
        self.send_header("X-Drop-Size", str(len(data)))
        # Browser client downloads these cross-origin.
        self.send_header("Access-Control-Expose-Headers", "X-Drop-Name, X-Drop-Size")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _handle_drop_delete(self, raw_name):
        """Remove one staged file or folder from the drop dir.

        Folders are deleted recursively, but only after the same resolve +
        containment checks as download (symlink out of drop → refused). The
        drop root itself and macOS "Drop Box" are never removed.
        """
        name = _safe_drop_name(raw_name)
        if name in _DROP_LIST_SKIP:
            self._error(400, "cannot delete protected folder %r" % name)
            return
        path, err = self._resolve_drop_entry(raw_name, dirs_ok=True)
        if path is None:
            status = 404 if err == "file not found" else 400
            self._error(status, err)
            return
        try:
            drop_root = self._scoped_drop_path().resolve()
        except OSError as e:
            self._error(500, "drop dir unavailable: %s" % e)
            return
        # Never rmtree the drop directory itself (empty name, ".", etc.).
        try:
            if path.resolve() == drop_root:
                self._error(400, "cannot delete the drop folder itself")
                return
        except OSError as e:
            self._error(500, "delete failed: %s" % e)
            return
        was_dir = path.is_dir()
        try:
            if was_dir:
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as e:
            self._error(500, "delete failed: %s" % e)
            return
        self._send_json({"ok": True, "name": path.name,
                         "type": "dir" if was_dir else "file"})

    def _handle_internal_permission(self, body):
        if not isinstance(body, dict):
            self._send_json({"allow": False, "message": "bad request"}, close=True)
            return
        job_id = body.get("job_id", "")
        nonce = body.get("nonce", "")
        tool_name = body.get("tool_name", "")
        tool_input = body.get("input", {})
        managers = []
        if self.jobs is not None:
            managers.append(self.jobs)
        for b in (self.bundles or {}).values():
            if b.jobs not in managers:
                managers.append(b.jobs)
        # Prefer the manager that already knows this job id.
        for jm in managers:
            if jm.get(job_id):
                decision = jm.request_permission(job_id, nonce, tool_name, tool_input)
                self._send_json(decision, close=True)
                return
        # Fall through: first manager returns the standard deny for unknown job.
        jm = managers[0] if managers else None
        if jm is None:
            self._send_json({"allow": False, "message": "no jobs"}, close=True)
            return
        decision = jm.request_permission(job_id, nonce, tool_name, tool_input)
        self._send_json(decision, close=True)


def make_server(config, token, bundles) -> ThreadingHTTPServer:
    """bundles: OrderedDict name → ProviderBundle (at least one)."""
    bundles = bundles or {}
    names = list(bundles.keys())
    multi = len(names) > 1
    # Single-provider: bind store/jobs/runner on the class so root paths work.
    # Multi: class attrs stay None until _bind_path picks a prefix.
    first = bundles[names[0]] if names else None
    handler = type("BoundApiHandler", (ApiHandler,), {
        "store": None if multi else (first.store if first else None),
        "jobs": None if multi else (first.jobs if first else None),
        "runner": None if multi else (first.runner if first else None),
        "config": config,
        "token": token,
        "bundles": bundles,
        # One focus list per daemon, shared by every harness and every client.
        "focus": focus_store.Focus(),
    })
    server = ThreadingHTTPServer((config.bind, int(config.port)), handler)
    cert = str(getattr(config, "tls_cert", "") or "")
    key = str(getattr(config, "tls_key", "") or "")
    if cert and key:
        # Internet-facing VPS behind Cloudflare Full-SSL: terminate TLS here
        # (self-signed is fine; the phone only ever sees Cloudflare's cert).
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(certfile=cert, keyfile=key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
    return server
