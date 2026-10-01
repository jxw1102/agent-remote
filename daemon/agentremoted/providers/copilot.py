"""GitHub Copilot CLI (``copilot``) sessions for Agent Remote.

Every session is one folder under ``~/.copilot/session-state/<uuid>/``:

    workspace.yaml   id, cwd, name, created_at, updated_at (flat YAML)
    events.jsonl     the whole session as an event log, one JSON object per
                     line: {"type", "data", "id", "timestamp", "parentId"}

The event vocabulary is the same one ``--output-format json`` streams, so one
reading of it serves the transcript, the process view, headless turns and the
interactive TUI:

    user.message           data.content (the human's words; transformedContent
                           is the harness-wrapped copy and is ignored)
    assistant.message      data.content (text, may be "") + data.toolRequests
    tool.execution_start   data.toolName, data.arguments
    tool.execution_complete data.success, data.result.content
    assistant.turn_end     one per model call; the turn is over once an
                           assistant.message with NO toolRequests has landed
    result                 (stream only) exitCode + usage.premiumRequests

Turns run headless via::

    copilot -p <prompt> --output-format json --allow-all-tools
            [--session-id <uuid> | --resume <uuid>] [--model M]
            [--reasoning-effort E]

``--session-id`` names a NEW session, so the daemon knows the id before the
CLI starts. ``/rewind N`` cuts events.jsonl at the Nth-last user.message;
``--resume`` rebuilds the conversation from that file (verified: a rewound
session forgets the dropped turn). Plan usage is GitHub's premium-request
quota from ``api.github.com/copilot_internal/user``, read with the CLI's own
login token. Interactive mode lives in :mod:`copilot_interactive`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import providers
from .. import search_util
from .. import steps as steps_mod
from .. import titles
from ..render_blocks import inline_to_rich, markdown_to_blocks

log = logging.getLogger(__name__)

_MAX_TITLE = 80
_MAX_PREVIEW = 160
_USAGE_TTL_S = 300
_USAGE_URL = "https://api.github.com/copilot_internal/user"
_USAGE_TIMEOUT_S = 20
# Levels `copilot --reasoning-effort` accepts; the picker offers the useful
# middle of the range (none/minimal/max stay reachable via config.efforts).
_EFFORTS = ["low", "medium", "high", "xhigh"]

_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


# ---------------------------------------------------------------- helpers

def is_session_id(value: str) -> bool:
    return bool(value) and bool(_SESSION_ID_RE.match(str(value)))


def safe_json(line: str):
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _preview(text: str, n: int = _MAX_PREVIEW) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _munge_cwd(cwd: str) -> str:
    s = str(cwd or "").strip().replace("\\", "/")
    if not s:
        return "no-project"
    if s.startswith("/"):
        s = s[1:]
    return "-" + s.replace("/", "-").replace(" ", "-")


def parse_workspace_yaml(text: str) -> dict:
    """The flat ``key: value`` YAML Copilot writes (stdlib only, no PyYAML).

    Handles plain scalars, single/double-quoted strings, and the block
    scalars (``|``, ``|-``, ``>``, ``>-``) Copilot uses for a multi-line
    session name. Anything more exotic is kept verbatim, not guessed at.
    """
    out = {}
    lines = str(text or "").splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#") or raw[:1] in " \t-":
            continue
        key, sep, val = raw.partition(":")
        if not sep:
            continue
        val = val.strip()
        if val[:1] in ("|", ">") and val.rstrip("+-0123456789") in ("|", ">"):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
                block.append(lines[i].strip())
                i += 1
            while block and not block[-1]:
                block.pop()
            val = ("\n" if val[0] == "|" else " ").join(block)
        elif len(val) >= 2 and val[0] == "'" and val[-1] == "'":
            val = val[1:-1].replace("''", "'")
        elif len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            try:
                val = json.loads(val)
            except ValueError:
                val = val[1:-1]
        out[key.strip()] = val
    return out


def _iso(ts: str) -> str:
    """Normalise an ISO timestamp to ``YYYY-MM-DDTHH:MM:SSZ``."""
    s = str(ts or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(ts: str) -> float:
    s = str(ts or "").strip()
    if not s:
        return 0.0
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def user_text(data: dict) -> str:
    """The words the human typed. ``content`` is exactly that; the harness
    wraps it into ``transformedContent`` (datetime, system reminders), which
    is deliberately never shown."""
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [p.get("text") for p in content
                 if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "\n".join(parts).strip()
    return ""


def assistant_text(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    content = data.get("content")
    return content.strip() if isinstance(content, str) else ""


def tool_requests(data: dict) -> list:
    reqs = data.get("toolRequests") if isinstance(data, dict) else None
    return [r for r in reqs if isinstance(r, dict)] if isinstance(reqs, list) else []


def tool_detail(name: str, args) -> str:
    """One line naming what a call is about (description > command > path)."""
    obj = args if isinstance(args, dict) else {}
    for key in ("description", "intentionSummary", "command", "path",
                "file_path", "filePath", "pattern", "query", "url"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return " ".join(val.split())[:200]
    return name or ""


def _result_text(data: dict) -> str:
    res = data.get("result") if isinstance(data, dict) else None
    if isinstance(res, dict):
        content = res.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(str(c.get("text") if isinstance(c, dict) else c)
                             for c in content)
        try:
            return json.dumps(res, indent=1, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(res)
    if isinstance(res, str):
        return res
    err = data.get("error") if isinstance(data, dict) else None
    return str(err or "")


def is_human_user_event(ev: dict) -> bool:
    return (isinstance(ev, dict) and ev.get("type") == "user.message"
            and bool(user_text(ev.get("data"))))


def _render(msg: dict) -> None:
    text = (msg.get("text") or "").strip()
    role = msg.get("role") or ""
    if not text or role not in ("assistant", "user"):
        return
    if role == "user":
        plain, rich = inline_to_rich(text)
        msg["blocks"] = [{"k": "user", "role": "user", "text": plain,
                          "rich": rich, "fmt": "rich"}]
    else:
        msg["blocks"] = markdown_to_blocks(text, role="assistant")


def build_transcript(path, want_steps: bool = False):
    """events.jsonl -> ([{uuid, role, ts, text}], [(pos, step)]).

    Conversation only by default. With ``want_steps`` each tool request, its
    result, and any plaintext reasoning come back as (line, step) rows for
    :func:`steps.attach`.
    """
    messages, step_rows = [], []
    p = Path(path) if path else None
    if p is None or not p.is_file():
        return messages, step_rows
    tool_names = {}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for ln, line in enumerate(f):
                ev = safe_json(line)
                if ev is None:
                    continue
                et = ev.get("type")
                data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                ts = _iso(ev.get("timestamp"))
                if et == "user.message":
                    text = user_text(data)
                    if text:
                        messages.append({"uuid": "u%d" % ln, "role": "user",
                                         "ts": ts, "text": text, "_pos": ln})
                elif et == "assistant.message":
                    text = assistant_text(data)
                    if text:
                        messages.append({"uuid": "a%d" % ln, "role": "assistant",
                                         "ts": ts, "text": text, "_pos": ln})
                    if want_steps:
                        reasoning = data.get("reasoningText")
                        if isinstance(reasoning, str) and reasoning.strip():
                            step_rows.append((ln, steps_mod.thinking(
                                "ct%d" % ln, ts, reasoning.strip())))
                        for j, req in enumerate(tool_requests(data)):
                            name = str(req.get("name") or "tool")
                            args = req.get("arguments")
                            tool_names[str(req.get("toolCallId") or "")] = name
                            step_rows.append((ln + j * 0.001, steps_mod.tool_use(
                                "cu%d_%d" % (ln, j), ts, name,
                                tool_detail(name, args),
                                steps_mod.format_tool_use(name, args),
                                lang=steps_mod.lang_for_tool_use(name, args))))
                elif et == "tool.execution_complete" and want_steps:
                    name = tool_names.get(str(data.get("toolCallId") or ""), "")
                    body = steps_mod.format_tool_result(_result_text(data), name)
                    step_rows.append((ln, steps_mod.tool_result(
                        "cr%d" % ln, ts, bool(data.get("success", True)), body,
                        lang=steps_mod.lang_for_tool_result(name, "", body))))
    except OSError:
        pass
    return messages, step_rows


# ---------------------------------------------------------------- store

class CopilotStore:
    """Read Copilot CLI sessions from ``<home>/session-state/<id>/``."""

    supports_steps = True     # `?detail=steps` (see agentremoted.steps)

    def __init__(self, home, config=None):
        self.home = Path(home).expanduser()
        self.config = config
        self.titler = None
        self._lock = threading.Lock()
        # events path -> ((mtime_ns, size), bits) so listing is stat-cheap
        self._bits = {}

    @property
    def root(self) -> Path:
        return self.home / "session-state"

    def session_dir(self, session_id: str):
        sid = str(session_id or "").strip()
        if not is_session_id(sid):
            return None
        d = self.root / sid
        return d if d.is_dir() else None

    def events_path(self, session_id: str) -> str:
        d = self.session_dir(session_id)
        return str(d / "events.jsonl") if d is not None else ""

    @staticmethod
    def _workspace(d: Path) -> dict:
        try:
            return parse_workspace_yaml(
                (d / "workspace.yaml").read_text(encoding="utf-8"))
        except OSError:
            return {}

    def _scan_bits(self, events: Path) -> dict:
        """First prompt, last message, model and human-turn count for one
        session, cached on the log's (mtime, size)."""
        try:
            st = events.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return {"first": "", "last_role": "", "last_text": "",
                    "model": "", "turns": 0, "size": 0}
        with self._lock:
            hit = self._bits.get(str(events))
            if hit and hit[0] == key:
                return hit[1]
        first = last_text = last_role = model = ""
        turns = 0
        try:
            with open(events, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"user.message"' not in line and '"assistant.message"' not in line \
                            and '"session.model_change"' not in line:
                        continue
                    ev = safe_json(line)
                    if ev is None:
                        continue
                    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                    et = ev.get("type")
                    if et == "user.message":
                        t = user_text(data)
                        if t:
                            turns += 1
                            first = first or t
                            last_role, last_text = "user", t
                    elif et == "assistant.message":
                        t = assistant_text(data)
                        if t:
                            last_role, last_text = "assistant", t
                        model = str(data.get("model") or model)
                    elif et == "session.model_change":
                        model = str(data.get("newModel") or model)
        except OSError:
            pass
        bits = {"first": first, "last_role": last_role, "last_text": last_text,
                "model": model, "turns": turns, "size": key[1]}
        with self._lock:
            if len(self._bits) > 2000:
                self._bits.clear()
            self._bits[str(events)] = (key, bits)
        return bits

    def _rows(self, user_only: bool = True, project_cwd: str = None) -> list:
        root = self.root
        if not root.is_dir():
            return []
        rows = []
        try:
            dirs = [d for d in root.iterdir() if d.is_dir() and is_session_id(d.name)]
        except OSError:
            return []
        for d in dirs:
            ws = self._workspace(d)
            cwd = str(ws.get("cwd") or "").strip()
            if user_only and titles.is_titler_cwd(cwd):
                continue
            if project_cwd is not None and cwd != project_cwd:
                continue
            events = d / "events.jsonl"
            if user_only:
                # A TUI that opened and closed with nothing typed leaves a
                # folder but no conversation; those are not sessions.
                if not events.is_file() or self._scan_bits(events)["turns"] == 0:
                    continue
            rows.append((d, ws))
        rows.sort(key=lambda r: _epoch(r[1].get("updated_at") or r[1].get("created_at")),
                  reverse=True)
        return rows

    def list_projects(self):
        by_cwd = {}
        for d, ws in self._rows(user_only=True):
            cwd = str(ws.get("cwd") or "").strip()
            ts = _epoch(ws.get("updated_at") or ws.get("created_at"))
            rec = by_cwd.get(cwd)
            if rec is None:
                by_cwd[cwd] = {"id": _munge_cwd(cwd), "cwd": cwd,
                               "name": Path(cwd).name if cwd else "no-project",
                               "session_count": 1, "last_active": ts}
            else:
                rec["session_count"] += 1
                rec["last_active"] = max(rec["last_active"], ts)
        return sorted(by_cwd.values(), key=lambda p: p["last_active"], reverse=True)

    def _cwd_for_project(self, project_id, user_only):
        if not project_id or project_id == "no-project":
            return None
        for _d, ws in self._rows(user_only=user_only):
            cwd = str(ws.get("cwd") or "")
            if _munge_cwd(cwd) == project_id:
                return cwd
        return ""

    def list_sessions(self, project_id=None, limit=25, user_only=True):
        cwd = self._cwd_for_project(project_id, user_only)
        if cwd == "":
            return []
        rows = self._rows(user_only=user_only, project_cwd=cwd)
        limit = max(1, min(int(limit or 25), 200))
        return [self._summary(d, ws) for d, ws in rows[:limit]]

    def search_sessions(self, query, project_id=None, limit=25, user_only=True):
        return list(self.iter_search_sessions(query, project_id, limit, user_only))

    def iter_search_sessions(self, query, project_id=None, limit=25, user_only=True):
        q = (query or "").strip()
        if not q:
            return
        limit = max(1, min(int(limit or 25), 100))
        found = 0
        later = []
        for d, ws in self._rows(user_only=user_only):
            cwd = str(ws.get("cwd") or "")
            if project_id and _munge_cwd(cwd) != project_id:
                continue
            name = str(ws.get("name") or "")
            if search_util.contains_ci(name, q) or search_util.contains_ci(cwd, q):
                s = self._summary(d, ws)
                s["snippet"] = search_util.make_snippet(name or cwd, q)
                yield s
                found += 1
                if found >= limit:
                    return
            else:
                later.append((d, ws))
        for d, ws in later:
            if found >= limit:
                return
            msgs, _ = build_transcript(d / "events.jsonl")
            hit = next((m["text"] for m in msgs
                        if search_util.contains_ci(m["text"], q)), None)
            if hit is None:
                continue
            s = self._summary(d, ws)
            s["snippet"] = search_util.make_snippet(hit, q)
            yield s
            found += 1

    def get_session(self, session_id: str):
        d = self.session_dir(session_id)
        if d is None:
            return None
        return self._summary(d, self._workspace(d))

    def get_messages(self, session_id: str, offset: int = None, limit: int = 50,
                     steps: bool = False):
        d = self.session_dir(session_id)
        if d is None:
            return None
        events = d / "events.jsonl"
        t0 = time.perf_counter()
        messages, step_rows = build_transcript(events, want_steps=bool(steps))
        t1 = time.perf_counter()
        total = len(messages)
        if offset is None:
            offset = max(0, total - limit)
        offset = max(0, offset)
        window = messages[offset: offset + limit]
        for m in window:
            _render(m)
        if steps:
            steps_mod.attach(window, step_rows)
        for m in messages:
            m.pop("_pos", None)
        t2 = time.perf_counter()
        try:
            size = events.stat().st_size
        except OSError:
            size = 0
        return {"session_id": session_id, "total": total, "offset": offset,
                "messages": window,
                "timing": {"parse_ms": round((t1 - t0) * 1000, 1),
                           "render_ms": round((t2 - t1) * 1000, 1),
                           "total_ms": round((t2 - t0) * 1000, 1),
                           "count_total": total, "count_window": len(window),
                           "file_bytes": size}}

    def get_step(self, session_id: str, ref: str):
        """Full body behind one truncated step (rebuilt from the log)."""
        d = self.session_dir(session_id)
        if d is None or not ref:
            return None
        m = re.fullmatch(r"(cu)(\d+)_(\d+)|(cr|ct)(\d+)", ref)
        if not m:
            return None
        want = int(m.group(2) if m.group(1) else m.group(5))
        try:
            with open(d / "events.jsonl", "r", encoding="utf-8", errors="replace") as f:
                for ln, line in enumerate(f):
                    if ln != want:
                        continue
                    ev = safe_json(line) or {}
                    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
                    if m.group(1):
                        reqs = tool_requests(data)
                        j = int(m.group(3))
                        if j >= len(reqs):
                            return None
                        req = reqs[j]
                        text = steps_mod.format_tool_use(
                            str(req.get("name") or "tool"), req.get("arguments"))
                    elif m.group(4) == "cr":
                        text = steps_mod.format_tool_result(_result_text(data))
                    else:
                        text = str(data.get("reasoningText") or "")
                    return {"ref": ref, "text": text, "bytes": len(text)}
        except OSError:
            return None
        return None

    def known_session_ids(self) -> set:
        return {d.name for d, _ws in self._rows(user_only=False)}

    def _derived_title(self, session_id: str, first: str) -> str:
        if self.config is None or not session_id or not first:
            return ""
        cache = titles.shared_cache(self.config)
        sig = titles.sig_for(first)
        got = cache.get(session_id, sig)
        if got:
            return got
        cache.request(session_id, sig, first, self.titler)
        return ""

    def _summary(self, d: Path, ws: dict) -> dict:
        cwd = str(ws.get("cwd") or "").strip()
        bits = self._scan_bits(d / "events.jsonl")
        name = " ".join(str(ws.get("name") or "").split())
        # Copilot names a session after its opening prompt verbatim unless it
        # (or the user) gave it a real name; derive a short one then.
        auto_named = str(ws.get("user_named") or "").lower() != "true"
        first = " ".join(str(bits.get("first") or "").split())
        if not name or titles.looks_blank(name) or (auto_named and name == first):
            name = self._derived_title(d.name, first) or name or first
        if not name:
            name = "Session %s" % d.name[:8]
        return {
            "id": d.name,
            "project_id": _munge_cwd(cwd),
            "cwd": cwd,
            "git_branch": str(ws.get("branch") or ""),
            "title": _preview(name, _MAX_TITLE),
            "started": _iso(ws.get("created_at")),
            "last_active": _iso(ws.get("updated_at") or ws.get("created_at")),
            "last_role": bits.get("last_role") or "",
            "last_text": _preview(bits.get("last_text") or ""),
            "model": bits.get("model") or "",
            "size_bytes": int(bits.get("size") or 0),
        }


# ---------------------------------------------------------------- runner

def _copilot_bin(config) -> str:
    return str(getattr(config, "copilot_bin", None) or "copilot")


def copilot_env(config, base: dict = None) -> dict:
    env = dict(base if base is not None else os.environ)
    extras = [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin",
              "/usr/local/bin"]
    cur = env.get("PATH", "")
    env["PATH"] = ":".join([p for p in extras if p not in cur.split(":")] + [cur])
    env.update({str(k): str(v)
                for k, v in (getattr(config, "copilot_env", None) or {}).items()})
    # Copilot's own updater would stall a headless turn behind a prompt.
    env.setdefault("COPILOT_DISABLE_AUTO_UPDATE", "1")
    return env


def _read_cli_config(home: Path) -> dict:
    """``~/.copilot/config.json`` is JSON with leading ``//`` comment lines."""
    try:
        raw = (home / "config.json").read_text(encoding="utf-8")
    except OSError:
        return {}
    body = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("//"))
    try:
        data = json.loads(body)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class CopilotRunner:
    name = "copilot"

    _BUILTIN_SLASH = ["/rewind"]
    _usage_lock = threading.Lock()
    _usage_cache = (0.0, None)

    def __init__(self, config):
        self.config = config
        self.store = CopilotStore(self._home(), config)
        self._interactive = None
        self._interactive_lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------

    def _home(self) -> Path:
        raw = getattr(self.config, "copilot_home_path", None)
        if raw:
            return Path(raw)
        return Path(str(getattr(self.config, "copilot_home", "")
                        or (Path.home() / ".copilot"))).expanduser()

    def _bin(self) -> str:
        return _copilot_bin(self.config)

    def _interactive_mgr(self):
        with self._interactive_lock:
            if self._interactive is None:
                from .copilot_interactive import CopilotInteractiveManager
                self._interactive = CopilotInteractiveManager(self.config, self)
            return self._interactive

    def run_alternate(self, job, mode) -> bool:
        if mode != "interactive":
            return False
        self._interactive_mgr().run(job)
        return True

    def resume_alternate(self, job) -> None:
        self._interactive_mgr().resume(job)

    def type_into_tui(self, session_id: str, text: str) -> str:
        return self._interactive_mgr().type_text(session_id, text)

    def capture_tui(self, session_id: str, *, ansi: bool = False) -> dict:
        return self._interactive_mgr().capture_tui(session_id, ansi=ansi)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        return self._interactive_mgr().send_tui_keys(session_id, keys=keys, text=text)

    # -- capabilities -----------------------------------------------------

    def capabilities(self) -> dict:
        from .copilot_interactive import tmux_available
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
            "can_set_effort": True,
            "can_show_usage": True,
            "interactive": has_tmux,
            "live_tui": has_tmux,
            # "/rewind N" cuts events.jsonl at the Nth-last prompt.
            "rewind": True,
        }

    def auth_health(self) -> dict:
        on_path = bool(shutil.which(self._bin())
                       or Path(os.path.expanduser("~/.local/bin/copilot")).is_file())
        tail = "" if on_path else "; `copilot` not on PATH"
        login = self._login()
        if self._token():
            return {"cli": "copilot", "cli_on_path": on_path,
                    "mode": "subscription",
                    "status": "ok" if on_path else "warning",
                    "detail": ("GitHub Copilot login: %s" % login if login
                               else "GitHub Copilot token present") + tail}
        return {"cli": "copilot", "cli_on_path": on_path, "mode": "none",
                "status": "missing",
                "detail": "No Copilot login on this host — run `copilot login`" + tail}

    def slash_commands(self) -> list:
        out = list(self._BUILTIN_SLASH)
        for extra in getattr(self.config, "slash_commands", None) or []:
            if isinstance(extra, str) and extra.strip():
                out.append(extra.strip())
        return sorted(set(out))

    def models(self) -> list:
        """``auto`` first, then config extras, then every model this host's
        own Copilot history has used. The CLI has no model listing and its
        login token cannot read the model API, so nothing here is guessed."""
        out = ["auto"]
        for m in getattr(self.config, "models", None) or []:
            if isinstance(m, str) and m.strip() and m.strip() not in out:
                out.append(m.strip())
        for m in self._history_models():
            if m not in out:
                out.append(m)
        return out

    _models_seen = (0.0, [])

    def _history_models(self) -> list:
        at, cached = type(self)._models_seen
        if time.time() - at < 600:
            return list(cached)
        seen = []
        root = self.store.root
        try:
            dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                          key=lambda d: d.stat().st_mtime, reverse=True)[:40]
        except OSError:
            dirs = []
        for d in dirs:
            try:
                with open(d / "events.jsonl", "r", encoding="utf-8",
                          errors="replace") as f:
                    for line in f:
                        if '"session.model_change"' not in line:
                            continue
                        ev = safe_json(line) or {}
                        m = str((ev.get("data") or {}).get("newModel") or "").strip()
                        if m and m not in seen:
                            seen.append(m)
            except OSError:
                continue
        type(self)._models_seen = (time.time(), seen)
        return list(seen)

    def efforts(self) -> list:
        extra = [e for e in (getattr(self.config, "efforts", None) or [])
                 if isinstance(e, str) and e.strip()]
        return extra or list(_EFFORTS)

    def title_for(self, text: str) -> str:
        cwd = str(titles.titler_cwd())
        cmd = [self._bin(), "-p", titles.prompt_for(text), "--silent",
               "--allow-all-tools"]
        try:
            out = subprocess.run(cmd, cwd=cwd, env=copilot_env(self.config),
                                 capture_output=True, text=True,
                                 timeout=180).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        return titles.title_from_output(out)

    # -- usage ------------------------------------------------------------

    def _cli_config(self) -> dict:
        return _read_cli_config(self._home())

    def _token(self) -> str:
        extra = getattr(self.config, "copilot_env", None) or {}
        for key in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
            val = str(extra.get(key) or os.environ.get(key) or "").strip()
            if val:
                return val
        tokens = self._cli_config().get("copilotTokens")
        if isinstance(tokens, dict):
            last = self._cli_config().get("lastLoggedInUser") or {}
            want = "%s:%s" % (last.get("host") or "", last.get("login") or "")
            if want in tokens and isinstance(tokens[want], str):
                return tokens[want].strip()
            for val in tokens.values():
                if isinstance(val, str) and val.strip():
                    return val.strip()
        return ""

    def _login(self) -> str:
        last = self._cli_config().get("lastLoggedInUser")
        return str(last.get("login") or "") if isinstance(last, dict) else ""

    def usage(self) -> dict:
        """Premium-request quota for the Usage sheet (cached 5 min)."""
        login = self._login()
        now = time.monotonic()
        at, cached = type(self)._usage_cache
        if cached is not None and now - at < _USAGE_TTL_S:
            return dict(cached)
        with type(self)._usage_lock:
            at, cached = type(self)._usage_cache
            if cached is not None and time.monotonic() - at < _USAGE_TTL_S:
                return dict(cached)
            token = self._token()
            base = {"provider": "copilot", "account": login, "account_id": login}
            if not token:
                return dict(base, ok=False, error="No Copilot login on this host")
            url = str(getattr(self.config, "copilot_usage_url", "") or _USAGE_URL)
            req = urllib.request.Request(url, headers={
                "Authorization": "token " + token,
                "Accept": "application/json",
                "User-Agent": "agentremoted",
            })
            try:
                with urllib.request.urlopen(req, timeout=_USAGE_TIMEOUT_S) as r:
                    data = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return dict(base, ok=False, error=(
                    "Copilot sign-in expired" if e.code in (401, 403)
                    else "Could not load Copilot usage (HTTP %d)" % e.code))
            except (urllib.error.URLError, OSError, ValueError) as e:
                return dict(base, ok=False, error="Could not load Copilot usage: %s" % e)
            result = dict(base, ok=True, buckets=usage_buckets(data))
            if not result["buckets"]:
                result = dict(base, ok=False, error="Copilot reported no quota")
            else:
                type(self)._usage_cache = (time.monotonic(), dict(result))
            return result

    # -- rewind -----------------------------------------------------------

    def rewind_session(self, session_id: str, steps: int):
        """Cut events.jsonl at the Nth-last human prompt. ``--resume`` and the
        TUI rebuild the conversation from that file, so the cut file IS the
        rewound session. A ``.rewind-bak`` copy is left beside it.
        Conversation only: files on disk are not restored."""
        sid = (session_id or "").strip()
        path_s = self.store.events_path(sid)
        if not path_s or not Path(path_s).is_file():
            raise providers.RunnerError("session log not found")
        self._interactive_mgr().close_for_session(sid)
        path = Path(path_s)
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines(True)
        marks = []
        for i, line in enumerate(lines):
            ev = safe_json(line)
            if is_human_user_event(ev):
                marks.append((i, user_text(ev.get("data"))))
        if not marks:
            raise providers.RunnerError("nothing to rewind — no prompts yet")
        steps = max(1, min(int(steps), len(marks)))
        cut, text = marks[-steps]
        try:
            (path.parent / (path.name + ".rewind-bak")).write_text(raw, encoding="utf-8")
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[:cut])
        return steps, " ".join(text.split())[:120]

    # -- headless turn ----------------------------------------------------

    def prepare(self, job, mode):
        if not job.cwd:
            raise providers.RunnerError("cwd is required for Copilot sessions")
        cwd = os.path.expanduser(job.cwd)
        if not os.path.isdir(cwd):
            raise providers.RunnerError("cwd does not exist: %s" % cwd)
        job.cwd = cwd
        state = job.runner_state
        state["parts"] = []
        cmd = [self._bin(), "-p", job.prompt, "--output-format", "json",
               "--allow-all-tools"]
        cmd += str(getattr(self.config, "copilot_flags", "") or "").split()
        if job.session_id:
            cmd += ["--resume", job.session_id]
        else:
            # Name the new session up front: the client can open it the
            # moment the job starts, and no directory scan is needed.
            sid = str(uuid.uuid4())
            cmd += ["--session-id", sid]
            with job.lock:
                job.new_session_id = sid
        if job.model and job.model not in ("", "default"):
            cmd += ["--model", job.model]
        effort = str(getattr(job, "effort", "") or "").strip()
        if effort and effort != "default":
            cmd += ["--reasoning-effort", effort]
        return cmd, copilot_env(self.config)

    def handle_stream_line(self, job, line: str):
        ev = safe_json(line)
        if ev is None:
            return
        et = str(ev.get("type") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        state = job.runner_state

        if et == "user.message":
            if not state.get("init"):
                state["init"] = True
                job.add_event("init", session_id=job.new_session_id or job.session_id,
                              model=job.model or "")
            job.set_phase("thinking", "")
            return
        if et in ("assistant.turn_start", "model.call_start"):
            job.set_phase("thinking", "")
            return
        if et == "assistant.message_delta":
            job.set_phase("writing", "")
            return
        if et == "assistant.message":
            text = assistant_text(data)
            if text:
                state.setdefault("parts", []).append(text)
                job.add_event("text", text=text, blocks=markdown_to_blocks(text))
                job.set_phase("writing", text[-160:])
            for req in tool_requests(data):
                name = str(req.get("name") or "tool")
                job.set_phase("tool", tool_detail(name, req.get("arguments"))[:120])
            return
        if et == "tool.execution_start":
            name = str(data.get("toolName") or "tool")
            detail = tool_detail(name, data.get("arguments"))
            job.add_event("tool", name=name, detail=detail[:200])
            job.set_phase("tool", (detail or name)[:120])
            return
        if et == "tool.execution_complete":
            job.set_phase("thinking", "")
            return
        if et == "result":
            code = ev.get("exitCode")
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            full = "\n\n".join(state.get("parts") or [])
            sid = str(ev.get("sessionId") or "").strip()
            with job.lock:
                if sid and not job.session_id:
                    job.new_session_id = sid
                if full and not job.result_text:
                    job.result_text = full
                if code not in (0, None) and not job.error:
                    job.error = "copilot exited with code %s" % code
            job.add_event("result", is_error=code not in (0, None),
                          duration_ms=int(usage.get("sessionDurationMs") or 0),
                          cost_usd=0, usage=usage)
            return
        if et.endswith(".error") or et == "error":
            msg = str(data.get("message") or data.get("error") or et)
            with job.lock:
                if not job.error:
                    job.error = msg
            job.add_event("text", text=msg, blocks=markdown_to_blocks(msg))

    def tick(self, job):
        pass

    def finalize(self, job, returncode, stderr_tail):
        full = "\n\n".join(job.runner_state.get("parts") or [])
        with job.lock:
            if full and not job.result_text:
                job.result_text = full
        if returncode not in (0, None) and not job.error:
            tail = (stderr_tail or "").strip().splitlines()
            with job.lock:
                job.error = tail[-1] if tail else "copilot exited with code %s" % returncode
            return False
        return None

    def cleanup(self, job):
        return


def _pct(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _severity(pct: int) -> str:
    return "critical" if pct >= 90 else "warning" if pct >= 75 else "normal"


def _reset_text(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return "Resets " + s
    return "Resets " + dt.strftime("%b %-d")


def usage_buckets(data: dict) -> list:
    """``copilot_internal/user`` -> Usage-sheet buckets.

    The premium-request allowance is a real share of a monthly limit, so it
    is a bar. Unlimited quotas (chat/completions on paid seats) carry no
    percentage and are shown as text only (show_bar=false)."""
    if not isinstance(data, dict):
        return []
    plan = str(data.get("copilot_plan") or "").strip().capitalize()
    reset = _reset_text(data.get("quota_reset_date"))
    snaps = data.get("quota_snapshots") if isinstance(data.get("quota_snapshots"), dict) else {}
    out = []
    prem = snaps.get("premium_interactions")
    if isinstance(prem, dict) and not prem.get("unlimited"):
        total = int(prem.get("entitlement") or 0)
        left = int(prem.get("remaining") or 0)
        if prem.get("percent_remaining") is not None:
            used_pct = 100 - float(prem.get("percent_remaining"))
        else:
            used_pct = ((total - left) * 100.0 / total) if total else 0
        pct = _pct(used_pct)
        detail = "%d / %d used" % (max(0, total - left), total) if total else ""
        out.append({
            "title": "Premium requests" + (" · %s" % plan if plan else ""),
            "percent": pct,
            "resets_text": " · ".join(x for x in (detail, reset) if x),
            "severity": _severity(pct),
        })
    elif isinstance(prem, dict) and prem.get("unlimited"):
        out.append({"title": "Premium requests", "percent": 0, "show_bar": False,
                    "resets_text": "Unlimited", "severity": "normal"})
    return out
