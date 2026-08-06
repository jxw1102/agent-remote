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
  GET  /api/ping                                  liveness + provider + caps
  GET  /api/usage                                 subscription usage buckets
  GET  /api/projects                              projects, most recent first
  GET  /api/sessions?project=<id>&limit=<n>&all=1  session summaries (all=1:
                                                  include agent-spawned and
                                                  contentless sessions too)
  GET  /api/sessions/search?q=<text>&project=&limit=&all=1  full-text search
  GET  /api/sessions/<id>                         one session's summary
  GET  /api/sessions/<id>/messages?offset=&limit= transcript window (default: tail)
  POST /api/sessions/<id>/continue {prompt, permission_mode?}
  POST /api/sessions/new {cwd, prompt, permission_mode?}
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
  GET  /api/drop                                       host→phone drop folder listing
  GET  /api/drop/<name>                                download one drop file (raw)
  POST /api/drop/<name>/delete                         remove one drop file
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
import ssl
import subprocess
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from . import __version__
from . import ssestream
from . import wstream

log = logging.getLogger(__name__)

_SESSION_MSGS = re.compile(r"^/api/sessions/([^/]+)/messages$")
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


MAX_BODY = 256 * 1024


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
        self.store = b.store
        self.jobs = b.jobs
        self.runner = b.runner
        return b

    def _find_session(self, session_id):
        """(provider_name, bundle, session_dict) or (None, None, None)."""
        if self.store is not None and (not self.bundles or len(self.bundles) <= 1):
            s = self.store.get_session(session_id)
            if s is not None:
                name = self.runner.name if self.runner else ""
                return name, None, s
        for name, b in (self.bundles or {}).items():
            s = b.store.get_session(session_id)
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
        """Active jobs across every harness, each tagged with provider."""
        out = []
        for name, b in (self.bundles or {}).items():
            for row in b.jobs.active_status():
                row = dict(row)
                row["provider"] = name
                out.append(row)
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

    def _merged_sessions(self, project, limit, user_only):
        rows = []
        for name, b in (self.bundles or {}).items():
            for s in b.store.list_sessions(project, limit, user_only=user_only):
                s = dict(s)
                s["provider"] = name
                rows.append(s)
        # Newest first across harnesses (mixed float mtime / ISO timestamps).
        rows.sort(key=self._activity_sort_key, reverse=True)
        return rows[:limit]

    def _merged_projects(self):
        rows = []
        for name, b in (self.bundles or {}).items():
            for p in b.store.list_projects():
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
        rows = []
        for name, b in (self.bundles or {}).items():
            search_fn = getattr(b.store, "search_sessions", None)
            if search_fn is None:
                continue
            for s in search_fn(q, project, limit, user_only=user_only):
                s = dict(s)
                s["provider"] = name
                rows.append(s)
        rows.sort(key=self._activity_sort_key, reverse=True)
        return rows[:limit]

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

    def _authorized(self, query) -> bool:
        supplied = self.headers.get("X-Auth-Token", "")
        if not supplied:
            bearer = self.headers.get("Authorization", "")
            if bearer.startswith("Bearer "):
                supplied = bearer[len("Bearer "):].strip()
        if not supplied:
            supplied = self.headers.get("X-Grok-Token", "")  # legacy client
        if not supplied:
            supplied = (query.get("token") or [""])[0]
        return bool(supplied) and hmac.compare_digest(supplied, self.token)

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
                payload = {
                    "ok": True,
                    "app": "agentremoted",
                    "version": __version__,
                    "host": platform.node(),
                    "multi": True,
                    "providers": list(self.bundles.keys()),
                    "paths": {n: "/" + n for n in self.bundles},
                }
                details = {}
                for name, b in self.bundles.items():
                    details[name] = {
                        "caps": b.runner.capabilities(),
                    }
                if self._authorized(query):
                    for name, b in self.bundles.items():
                        details[name]["slash_commands"] = b.runner.slash_commands()
                        models = getattr(b.runner, "models", None)
                        details[name]["models"] = models() if models else []
                        efforts = getattr(b.runner, "efforts", None)
                        details[name]["efforts"] = efforts() if efforts else []
                    try:
                        drop = self.config.drop_path
                        drop.mkdir(parents=True, exist_ok=True)
                        payload["drop_path"] = str(drop)
                    except OSError:
                        payload["drop_path"] = str(self.config.drop_path)
                payload["provider_details"] = details
                # Default harness for UIs that need a primary accent.
                payload["provider"] = list(self.bundles.keys())[0]
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
                for b in self.bundles.values():
                    for key, val in (b.runner.capabilities() or {}).items():
                        if isinstance(val, bool):
                            union[key] = union.get(key, False) or val
                        elif key not in union:
                            union[key] = val
                payload["caps"] = union
                self._send_json(payload)
                return
            if self.runner is None:
                self._error(404, "unknown provider path")
                return
            payload = {
                "ok": True,
                "app": "agentremoted",
                "version": __version__,
                "host": platform.node(),
                "provider": self.runner.name,
                "caps": self.runner.capabilities(),
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
                    drop = self.config.drop_path
                    drop.mkdir(parents=True, exist_ok=True)
                    payload["drop_path"] = str(drop)
                except OSError:
                    payload["drop_path"] = str(self.config.drop_path)
            self._send_json(payload)
            return

        if not self._authorized(query):
            self._error(401, "missing or invalid token")
            return

        # Multi root: one profile talks to the catalogue host; we merge
        # sessions/jobs across harnesses and tag each row with provider.
        if multi and bundle is None:
            self._route_get_multi(path, query)
            return

        if path == "/ws/status" and wstream.is_upgrade(self.headers):
            # Hijacks the connection until the client leaves; nothing may
            # be written through the normal HTTP path afterwards.
            self.close_connection = True
            wstream.serve_status(self, self.jobs)
            return

        if path == "/sse/status":
            self.close_connection = True
            ssestream.serve_status(self, self.jobs)
            return

        if path == "/api/usage":
            usage_fn = getattr(self.runner, "usage", None)
            if usage_fn is None:
                self._send_json({"ok": False, "error": "not supported"})
            else:
                self._send_json(usage_fn())
            return

        if path == "/api/projects":
            self._send_json({"projects": self.store.list_projects()})
            return

        if path == "/api/sessions":
            project = (query.get("project") or [None])[0]
            limit = min(max(self._int_param(query, "limit", 25), 1), 200)
            self._send_json({"sessions": self.store.list_sessions(
                project, limit, user_only=not self._flag(query, "all"))})
            return

        if path == "/api/sessions/search":
            # Full-text over titles + human-visible message text. The phone
            # highlights `q` in title/snippet client-side (brand accent).
            from . import search_util
            q = search_util.normalize_query((query.get("q") or [""])[0])
            if not q:
                self._send_json({"query": "", "results": []})
                return
            project = (query.get("project") or [None])[0]
            limit = min(max(self._int_param(query, "limit", 25), 1), 100)
            search_fn = getattr(self.store, "search_sessions", None)
            if search_fn is None:
                self._error(501, "search not supported")
                return
            results = search_fn(q, project, limit,
                                user_only=not self._flag(query, "all"))
            self._send_json({"query": q, "results": results})
            return

        m = _SESSION_MSGS.match(path)
        if m:
            self._send_session_messages(m.group(1), query)
            return

        m = _SESSION_TUI.match(path)
        if m:
            self._handle_tui_capture(m.group(1), query)
            return

        m = _SESSION_ONE.match(path)
        if m:
            session = self.store.get_session(m.group(1))
            if session is None:
                self._error(404, "session not found")
            else:
                self._send_json(session)
            return

        if path == "/api/jobs":
            self._send_json({"jobs": self.jobs.list_jobs()})
            return

        m = _JOB_ONE.match(path)
        if m:
            job = self.jobs.get(m.group(1))
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
            wstream.serve_status(self, None, active_fn=self._merged_active_status)
            return
        if path == "/sse/status":
            self.close_connection = True
            ssestream.serve_status(self, None, active_fn=self._merged_active_status)
            return
        if path == "/api/usage":
            # One profile, every harness: return per-provider sections plus a
            # flat buckets list (titles tagged "Claude · …") for older clients
            # that only render buckets.
            sections = []
            flat = []
            for name, b in (self.bundles or {}).items():
                label = str(name or "").strip().capitalize() or "Agent"
                usage_fn = getattr(b.runner, "usage", None)
                if usage_fn is None:
                    sections.append({
                        "provider": name,
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
                        "ok": False,
                        "error": str(e) or "usage failed",
                        "buckets": [],
                    })
                    continue
                if not isinstance(data, dict):
                    sections.append({
                        "provider": name,
                        "ok": False,
                        "error": "invalid usage response",
                        "buckets": [],
                    })
                    continue
                if data.get("ok") is False:
                    log.warning("usage %s: %s", name, data.get("error") or "not available")
                    sections.append({
                        "provider": name,
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
            self._send_json({"projects": self._merged_projects()})
            return
        if path == "/api/sessions":
            project = (query.get("project") or [None])[0]
            # project ids are "provider:id" when merged; strip for store.
            provider_filter = None
            if project and ":" in str(project):
                provider_filter, project = str(project).split(":", 1)
            limit = min(max(self._int_param(query, "limit", 25), 1), 200)
            user_only = not self._flag(query, "all")
            if provider_filter and provider_filter in (self.bundles or {}):
                b = self.bundles[provider_filter]
                sessions = [dict(s, provider=provider_filter)
                            for s in b.store.list_sessions(
                                project, limit, user_only=user_only)]
            else:
                sessions = self._merged_sessions(project, limit, user_only)
            self._send_json({"sessions": sessions})
            return
        if path == "/api/sessions/search":
            from . import search_util
            q = search_util.normalize_query((query.get("q") or [""])[0])
            if not q:
                self._send_json({"query": "", "results": []})
                return
            project = (query.get("project") or [None])[0]
            limit = min(max(self._int_param(query, "limit", 25), 1), 100)
            results = self._merged_search(
                q, project, limit, user_only=not self._flag(query, "all"))
            self._send_json({"query": q, "results": results})
            return
        m = _SESSION_MSGS.match(path)
        if m:
            name, b, _ = self._find_session(m.group(1))
            if b is None:
                self._error(404, "session not found")
                return
            self._bind_bundle(name)
            self._send_session_messages(m.group(1), query)
            return
        m = _SESSION_TUI.match(path)
        if m:
            name, b, _ = self._find_session(m.group(1))
            if b is None:
                # TUI may exist for a brand-new session before store indexes it.
                # Still try every harness.
                self._handle_tui_capture(m.group(1), query)
                return
            self._bind_bundle(name)
            self._handle_tui_capture(m.group(1), query)
            return
        m = _SESSION_ONE.match(path)
        if m:
            name, b, session = self._find_session(m.group(1))
            if session is None:
                self._error(404, "session not found")
                return
            session = dict(session)
            session["provider"] = name
            self._send_json(session)
            return
        if path == "/api/jobs":
            jobs = []
            for name, b in (self.bundles or {}).items():
                for j in b.jobs.list_jobs():
                    j = dict(j)
                    j["provider"] = name
                    jobs.append(j)
            self._send_json({"jobs": jobs})
            return
        m = _JOB_ONE.match(path)
        if m:
            name, b, job = self._find_job(m.group(1))
            if job is None:
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

    def _send_session_messages(self, session_id, query):
        offset = self._int_param(query, "offset", None)
        limit = min(max(self._int_param(query, "limit", 50), 1), 500)
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

        # Multi root: resolve harness from body.provider or session/job id.
        if multi and bundle is None:
            self._route_post_multi(path, query, body)
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
            if self.jobs.stop(m.group(1)):
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
                name, b, _ = self._find_session(str(sid))
                if b is not None:
                    self._bind_bundle(name)
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
            if not provider:
                self._error(400, "provider is required "
                            "(one of: %s)" % ", ".join(self.bundles.keys()))
                return
            if provider not in self.bundles:
                self._error(400, "unknown provider %r" % provider)
                return
            self._bind_bundle(provider)
            self._handle_new_session(body)
            return
        m = _SESSION_CONT.match(path)
        if m:
            name, b, _ = self._find_session(m.group(1))
            if b is None:
                self._error(404, "session not found")
                return
            self._bind_bundle(name)
            self._handle_continue(m.group(1), body)
            return
        m = _SESSION_TUI_KEYS.match(path)
        if m:
            name, b, _ = self._find_session(m.group(1))
            if b is not None:
                self._bind_bundle(name)
            self._handle_tui_keys(m.group(1), body)
            return
        m = _JOB_PERM.match(path)
        if m:
            name, b, _ = self._find_job(m.group(1))
            if b is None:
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_permission_answer(m.group(1), body)
            return
        m = _JOB_QUESTION.match(path)
        if m:
            name, b, _ = self._find_job(m.group(1))
            if b is None:
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_question_answer(m.group(1), body)
            return
        m = _JOB_INPUT.match(path)
        if m:
            name, b, _ = self._find_job(m.group(1))
            if b is None:
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_job_input(m.group(1), body)
            return
        m = _JOB_QUEUE.match(path)
        if m:
            name, b, _ = self._find_job(m.group(1))
            if b is None:
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_queue(m.group(1), body)
            return
        m = _JOB_QCANCEL.match(path)
        if m:
            name, b, _ = self._find_job(m.group(1))
            if b is None:
                self._error(404, "job not found")
                return
            self._bind_bundle(name)
            self._handle_queue_cancel(m.group(1), m.group(2))
            return
        m = _JOB_STOP.match(path)
        if m:
            name, b, _ = self._find_job(m.group(1))
            if b is None:
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
            cwd = os.path.expanduser(cwd.strip()) or None
        else:
            cwd = None
        if not cwd:
            sid = body.get("session_id") or body.get("sessionId") or ""
            if isinstance(sid, str) and sid.strip():
                session = self.store.get_session(sid.strip())
                if session:
                    raw = session.get("cwd") or ""
                    if isinstance(raw, str) and raw.strip():
                        cwd = os.path.expanduser(raw.strip()) or None
        if cwd is not None and not os.path.isdir(cwd):
            self._error(400, "cwd not found: %s" % cwd)
            return
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, timeout=30,
                cwd=cwd)
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
        session = self.store.get_session(session_id)
        if session is None:
            self._error(404, "session not found")
            return
        job = self.jobs.start_job(
            prompt=body["prompt"],
            cwd=session.get("cwd", ""),
            session_id=session_id,
            permission_mode=body.get("permission_mode", ""),
            model=str(body.get("model", "") or ""),
            effort=str(body.get("effort", "") or ""),
        )
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
        cwd = os.path.expanduser(cwd.strip())
        # claude requires a project dir; grok falls back to its workspace.
        if not cwd and self.runner.capabilities().get("requires_cwd", True):
            self._error(400, "'cwd' is required for a new session")
            return
        job = self.jobs.start_job(
            prompt=body["prompt"],
            cwd=cwd,
            permission_mode=body.get("permission_mode", ""),
            model=str(body.get("model", "") or ""),
            effort=str(body.get("effort", "") or ""),
        )
        self._send_json({"job_id": job.id}, status=200)

    def _handle_job_input(self, job_id, body):
        """Type a message into an interactive job's TUI (no daemon queue)."""
        if not isinstance(body, dict) or not isinstance(body.get("prompt"), str) \
                or not body["prompt"].strip():
            self._error(400, "body must be JSON with a non-empty 'prompt'")
            return
        reason = self.jobs.type_into_tui(job_id, body["prompt"])
        if reason:
            self._error(409, reason)
            return
        self._send_json({"ok": True}, status=202)

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
        queued, reason = self.jobs.enqueue(job_id, body["prompt"])
        if queued is None:
            self._error(409, reason)
            return
        self._send_json({"queued": queued}, status=202)

    def _handle_queue_cancel(self, job_id, qid):
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
        upload_dir = self.config.upload_path
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

    def _resolve_drop_file(self, raw_name):
        """Return (Path, None) or (None, error_message) for a drop filename."""
        name = _safe_drop_name(raw_name)
        if not name:
            return None, "invalid filename"
        drop_dir = self.config.drop_path.resolve()
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
        if not candidate.is_file():
            return None, "file not found"
        return candidate, None

    def _handle_drop_list(self):
        """List files the agent (or user) put in the host→phone drop folder."""
        drop_dir = self.config.drop_path
        try:
            drop_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._error(500, "drop dir unavailable: %s" % e)
            return
        files = []
        try:
            for entry in sorted(drop_dir.iterdir(), key=lambda p: p.name.lower()):
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                files.append({
                    "name": entry.name,
                    "size": int(st.st_size),
                    "mtime": int(st.st_mtime),
                })
        except OSError as e:
            self._error(500, "could not list drop: %s" % e)
            return
        self._send_json({
            "path": str(drop_dir.resolve()),
            "files": files,
        })

    def _handle_drop_download(self, raw_name):
        """Stream one drop file as raw bytes to the phone."""
        path, err = self._resolve_drop_file(raw_name)
        if path is None:
            status = 404 if err == "file not found" else 400
            self._error(status, err)
            return
        max_bytes = int(getattr(self.config, "max_drop_mb", 64) or 64) * 1024 * 1024
        try:
            size = path.stat().st_size
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
            data = path.read_bytes()
        except OSError as e:
            self._error(500, "read failed: %s" % e)
            return
        # ASCII-safe Content-Disposition filename; the real name is in the
        # URL path the client already knows.
        safe_ascii = "".join(
            ch if (ch.isalnum() or ch in "-_.") else "_" for ch in path.name
        ) or "file"
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         'attachment; filename="%s"' % safe_ascii)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Drop-Name", path.name)
        self.send_header("X-Drop-Size", str(len(data)))
        # Browser client downloads these cross-origin.
        self.send_header("Access-Control-Expose-Headers", "X-Drop-Name, X-Drop-Size")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _handle_drop_delete(self, raw_name):
        path, err = self._resolve_drop_file(raw_name)
        if path is None:
            status = 404 if err == "file not found" else 400
            self._error(status, err)
            return
        try:
            path.unlink()
        except OSError as e:
            self._error(500, "delete failed: %s" % e)
            return
        self._send_json({"ok": True, "name": path.name})

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
