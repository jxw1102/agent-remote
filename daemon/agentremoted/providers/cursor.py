"""Cursor Agent provider: session store + headless `cursor-agent --print`.

Cursor CLI stores chats under:

    ~/.cursor/chats/<cwd-md5>/<session-uuid>/
        meta.json     — cwd, createdAtMs, updatedAtMs, title
        store.db      — content-addressed resume-state snapshots

Transcripts (human-readable) live next to the IDE project cache:

    ~/.cursor/projects/<munged-cwd>/agent-transcripts/<uuid>/<uuid>.jsonl

Each JSONL line is either a role record
``{"role": "user"|"assistant", "message": {"content": [...]}}`` or a
``{"type": "turn_ended", ...}`` marker. User text is often wrapped in
``<user_query>`` (plus a ``<timestamp>``). Assistant lines use Claude-style
content blocks (text / tool_use).

Turns run as:

    cursor-agent --print --output-format stream-json --force --trust \\
        [--workspace <cwd>] [--resume <id>] [--model <id>] <prompt>

Stream-json is Claude-shaped on stdout (system/init, assistant, result) with
Cursor-specific ``tool_call`` events (``globToolCall``, …). Headless and
detached tmux Live TUI turns both auto-approve (``--force``); Cursor does not
provide this adapter a phone-driven permission callback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .. import providers
from .. import search_util
from .. import steps as steps_mod
from .. import titles
from ..render_blocks import inline_to_rich, markdown_to_blocks

_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_HEAD_BYTES = 64 * 1024
_TAIL_BYTES = 64 * 1024
_MAX_PREVIEW = 200
_MAX_TITLE = 60
_FRESH_SECONDS = 300
_MODELS_TTL_S = 900
_USAGE_TTL_S = 300
_USAGE_URL = "https://api2.cursor.sh"

_USER_QUERY_RE = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
_TIMESTAMP_RE = re.compile(r"<timestamp>.*?</timestamp>", re.S)
_REDACTED_RE = re.compile(r"\[REDACTED\]")

_DESC_KEYS = (
    "description", "subject", "content", "activeForm", "status",
)
_CMD_KEYS = (
    "command", "file_path", "path", "target_file", "target_directory",
    "targetDirectory", "pattern", "globPattern", "glob", "url", "query",
    "prompt", "old_string",
)

_PHASE_BY_TOOL = {
    "Edit": "editing", "Write": "editing", "Delete": "editing",
    "Read": "reading",
    "Grep": "searching", "Glob": "searching", "LS": "searching",
    "Bash": "running", "Shell": "running",
    "WebFetch": "browsing", "WebSearch": "browsing",
    "Task": "delegating", "Agent": "delegating",
}

_TOOL_NAME = {
    "glob": "Glob", "grep": "Grep", "ls": "Glob",
    "read": "Read", "readfile": "Read",
    "write": "Write", "writefile": "Write",
    "edit": "Edit", "applypatch": "Edit", "stredreplace": "Edit",
    "delete": "Edit", "deletefile": "Edit",
    "shell": "Bash", "shellcommand": "Bash", "bash": "Bash",
    "websearch": "WebSearch", "webfetch": "WebFetch",
    "task": "Task", "todo": "TodoWrite",
}

_FALLBACK_MODELS = [
    "auto", "composer-2.5", "composer-2.5-fast",
]


def _is_session_id(value: str) -> bool:
    return bool(value) and bool(_SESSION_ID_RE.match(value))


def _safe_json(line: str):
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _munge_cwd(cwd: str) -> str:
    """Cursor project dir names: path separators and dots become '-'."""
    p = os.path.abspath(os.path.expanduser(cwd or ""))
    if p.startswith("/private/"):
        p = p[len("/private"):]
    out = []
    for ch in p.lstrip("/"):
        out.append(ch if ch.isalnum() or ch in "-_" else "-")
    return "".join(out)


def _ms_to_iso(ms) -> str:
    try:
        n = float(ms)
    except (TypeError, ValueError):
        return ""
    if n > 1e12:
        n = n / 1000.0
    if n <= 0:
        return ""
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ""


def _clip_detail(text, max_len: int) -> str:
    t = " ".join(str(text or "").split())
    if not t:
        return ""
    if len(t) <= max_len:
        return t
    if max_len < 3:
        return t[:max_len]
    keep = max_len - 1
    head = keep // 2
    tail = keep - head
    return t[:head] + "…" + t[-tail:]


def _tool_status_parts(tool_input, desc_max: int = 120, cmd_max: int = 280):
    if not isinstance(tool_input, dict):
        return "", ""
    desc = ""
    for key in _DESC_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            desc = _clip_detail(val, desc_max)
            break
    cmd = ""
    for key in _CMD_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            cmd = _clip_detail(val, cmd_max)
            break
    if desc and cmd and desc == cmd:
        return desc, ""
    if not desc and cmd:
        return cmd, ""
    return desc, cmd


def _phase_for_tool(name: str) -> str:
    if name in _PHASE_BY_TOOL:
        return _PHASE_BY_TOOL[name]
    low = (name or "").lower()
    for k, phase in _PHASE_BY_TOOL.items():
        if k.lower() == low:
            return phase
    return "tool"


def _unwrap_tool_call(tc: dict):
    """Cursor stream ``tool_call`` blob → (display_name, args dict)."""
    if not isinstance(tc, dict):
        return "tool", {}
    for key, val in tc.items():
        if not isinstance(key, str) or not key.endswith("ToolCall"):
            continue
        if not isinstance(val, dict):
            continue
        raw = key[:-8]
        mapped = _TOOL_NAME.get(raw.lower())
        name = mapped or (raw[:1].upper() + raw[1:] if raw else "tool")
        args = val.get("args") if isinstance(val.get("args"), dict) else {}
        return name, args
    name = str(tc.get("name") or tc.get("tool") or "").strip()
    args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
    return name or "tool", args


def _content_blocks(message) -> list:
    if isinstance(message, dict):
        content = message.get("content")
    else:
        content = message
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return []


def _text_of(message) -> str:
    parts = []
    for block in _content_blocks(message):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(p for p in parts if p).strip()


def _human_user_text(obj: dict) -> str:
    raw = _text_of(obj.get("message") if isinstance(obj.get("message"), dict)
                   else obj)
    if not raw:
        return ""
    m = _USER_QUERY_RE.search(raw)
    if m:
        raw = m.group(1)
    raw = _TIMESTAMP_RE.sub("", raw)
    return " ".join(raw.split()).strip() if "<" in raw else raw.strip()


def _assistant_text(obj: dict) -> str:
    text = _text_of(obj.get("message") if isinstance(obj.get("message"), dict)
                    else obj)
    text = _REDACTED_RE.sub("", text).strip()
    return text


def _preview(text: str, max_len: int = _MAX_PREVIEW) -> str:
    text = " ".join((text or "").split())
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _read_head_lines(path: Path, nbytes: int) -> list:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(nbytes).splitlines()
    except OSError:
        return []


def _read_tail_lines(path: Path, nbytes: int) -> list:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > nbytes:
                f.seek(size - nbytes)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()
    except OSError:
        return []


def _load_meta(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _render_blocks(role: str, text: str) -> list:
    if not text:
        return []
    if role == "assistant":
        return markdown_to_blocks(text)
    return inline_to_rich(text)


class CursorStore:
    """Read-only view over ~/.cursor chats + agent-transcripts."""

    def __init__(self, cursor_home: Path, config=None):
        self.cursor_home = Path(cursor_home).expanduser()
        self.config = config
        self._titles = titles.TitleCache(config, None)
        self.titler = None

    def _chats_root(self) -> Path:
        return self.cursor_home / "chats"

    def _projects_root(self) -> Path:
        return self.cursor_home / "projects"

    def _iter_chat_dirs(self):
        root = self._chats_root()
        if not root.is_dir():
            return
        try:
            hashes = list(root.iterdir())
        except OSError:
            return
        for hdir in hashes:
            if not hdir.is_dir():
                continue
            try:
                kids = list(hdir.iterdir())
            except OSError:
                continue
            for sdir in kids:
                if sdir.is_dir() and _is_session_id(sdir.name):
                    yield sdir

    def _transcript_path(self, session_id: str, cwd: str = "") -> Path | None:
        sid = (session_id or "").strip()
        if not _is_session_id(sid):
            return None
        candidates = []
        if cwd:
            munged = _munge_cwd(cwd)
            candidates.append(
                self._projects_root() / munged / "agent-transcripts" / sid
                / (sid + ".jsonl"))
            # macOS /tmp vs /private/tmp
            if cwd.startswith("/private/"):
                candidates.append(
                    self._projects_root() / _munge_cwd(cwd[len("/private"):])
                    / "agent-transcripts" / sid / (sid + ".jsonl"))
            elif cwd.startswith("/tmp/") or cwd.startswith("/var/"):
                candidates.append(
                    self._projects_root() / _munge_cwd("/private" + cwd)
                    / "agent-transcripts" / sid / (sid + ".jsonl"))
        for path in candidates:
            if path.is_file():
                return path
        root = self._projects_root()
        if not root.is_dir():
            return None
        try:
            for proj in root.iterdir():
                path = proj / "agent-transcripts" / sid / (sid + ".jsonl")
                if path.is_file():
                    return path
        except OSError:
            return None
        return None

    def find_session_dir(self, session_id: str) -> Path | None:
        sid = (session_id or "").strip()
        if not _is_session_id(sid):
            return None
        root = self._chats_root()
        if not root.is_dir():
            return None
        try:
            for hdir in root.iterdir():
                candidate = hdir / sid
                if candidate.is_dir() and (candidate / "meta.json").is_file():
                    return candidate
        except OSError:
            return None
        return None

    def find_session_file(self, session_id: str) -> Path | None:
        sdir = self.find_session_dir(session_id)
        cwd = ""
        if sdir is not None:
            cwd = str((_load_meta(sdir / "meta.json") or {}).get("cwd") or "")
        return self._transcript_path(session_id, cwd)

    def _is_user_session(self, sdir: Path, meta: dict) -> bool:
        cwd = str(meta.get("cwd") or "")
        if titles.is_titler_cwd(cwd):
            return False
        if meta.get("hasConversation") is False:
            # A brand-new turn may not have flipped the flag yet.
            try:
                age = time.time() - (sdir / "meta.json").stat().st_mtime
            except OSError:
                age = 999
            return age < _FRESH_SECONDS
        return True

    def list_projects(self) -> list:
        grouped = {}
        for sdir in self._iter_chat_dirs():
            meta = _load_meta(sdir / "meta.json")
            if not meta or not self._is_user_session(sdir, meta):
                continue
            cwd = str(meta.get("cwd") or "").rstrip("/")
            if not cwd:
                continue
            pid = _munge_cwd(cwd) or "no-project"
            rec = grouped.get(pid)
            try:
                mtime = (sdir / "meta.json").stat().st_mtime
            except OSError:
                mtime = 0
            if rec is None:
                grouped[pid] = {
                    "id": pid,
                    "cwd": cwd,
                    "name": os.path.basename(cwd) or pid,
                    "session_count": 1,
                    "last_active": mtime,
                }
            else:
                rec["session_count"] += 1
                if mtime > rec["last_active"]:
                    rec["last_active"] = mtime
                    rec["cwd"] = cwd
                    rec["name"] = os.path.basename(cwd) or pid
        projects = list(grouped.values())
        projects.sort(key=lambda p: p["last_active"], reverse=True)
        return projects

    def list_sessions(self, project_id: str = None, limit: int = 25,
                      user_only: bool = True) -> list:
        rows = []
        for sdir in self._iter_chat_dirs():
            meta = _load_meta(sdir / "meta.json")
            if not meta:
                continue
            if user_only and not self._is_user_session(sdir, meta):
                continue
            cwd = str(meta.get("cwd") or "")
            pid = _munge_cwd(cwd) if cwd else "no-project"
            if project_id and pid != project_id:
                continue
            summary = self._session_summary(sdir, meta, pid)
            if summary:
                rows.append(summary)
        rows.sort(key=lambda r: r.get("last_active") or "", reverse=True)
        return rows[: max(1, int(limit or 25))]

    def search_sessions(self, query: str, project_id: str = None,
                        limit: int = 25, user_only: bool = True) -> list:
        results = list(self.iter_search_sessions(
            query, project_id=project_id, limit=limit, user_only=user_only))
        results.sort(key=search_util.rank_key, reverse=True)
        return results

    def iter_search_sessions(self, query: str, project_id: str = None,
                             limit: int = 25, user_only: bool = True):
        q = search_util.normalize_query(query)
        if not q:
            return
        limit = max(1, min(int(limit or 25), 100))
        yielded = 0
        sessions = self.list_sessions(
            project_id=project_id, limit=search_util.MAX_SCAN,
            user_only=user_only)
        need_body = []
        for row in sessions:
            hay = " ".join([
                row.get("title") or "",
                row.get("last_text") or "",
                row.get("cwd") or "",
            ])
            if search_util.contains_ci(hay, q):
                out = dict(row)
                out["snippet"] = search_util.make_snippet(hay, q)
                yield out
                yielded += 1
                if yielded >= limit:
                    return
            else:
                need_body.append(row)
        for row in need_body:
            path = self._transcript_path(row["id"], row.get("cwd") or "")
            if path is None:
                continue
            blob = "\n".join(
                _read_head_lines(path, search_util.SEARCH_HEAD_BYTES)
                + _read_tail_lines(path, search_util.SEARCH_TAIL_BYTES))
            if not search_util.contains_ci(blob, q):
                continue
            out = dict(row)
            out["snippet"] = search_util.make_snippet(blob, q)
            yield out
            yielded += 1
            if yielded >= limit:
                return

    supports_steps = True

    def get_step(self, session_id: str, ref: str):
        if not ref or ":" not in ref:
            return None
        sdir = self.find_session_dir(session_id)
        cwd = ""
        if sdir is not None:
            cwd = str((_load_meta(sdir / "meta.json") or {}).get("cwd") or "")
        path = self._transcript_path(session_id, cwd)
        if path is None:
            return None
        uid, _, idx = ref.rpartition(":")
        try:
            index = int(idx)
        except ValueError:
            return None
        pos = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                obj = _safe_json(line)
                if isinstance(obj, dict):
                    line_id = str(obj.get("uuid") or pos)
                    if line_id == uid or (not obj.get("uuid") and str(pos) == uid):
                        blocks = _content_blocks(obj.get("message"))
                        if 0 <= index < len(blocks):
                            b = blocks[index]
                            if isinstance(b, dict) and b.get("type") == "text":
                                text = b.get("text") or ""
                            elif isinstance(b, dict) and b.get("type") == "tool_use":
                                text = steps_mod.format_tool_use(
                                    b.get("name", "?"), b.get("input"))
                            elif isinstance(b, dict) and b.get("type") == "tool_result":
                                raw = b.get("content")
                                text = raw if isinstance(raw, str) else json.dumps(
                                    raw or {}, default=str)
                            else:
                                text = json.dumps(b, default=str)
                            return {"ref": ref, "text": text, "bytes": len(text)}
                pos += 1
        return None

    def get_session(self, session_id: str) -> dict:
        sdir = self.find_session_dir(session_id)
        if sdir is None:
            # Transcript-only (IDE session without chats/ meta).
            path = self._transcript_path(session_id)
            if path is None:
                return None
            dummy_meta = {"cwd": "", "hasConversation": True}
            pid = path.parent.parent.parent.name
            return self._session_summary_from_transcript(
                session_id, path, dummy_meta, pid)
        meta = _load_meta(sdir / "meta.json")
        cwd = str(meta.get("cwd") or "")
        return self._session_summary(sdir, meta, _munge_cwd(cwd) if cwd else "")

    def get_messages(self, session_id: str, offset: int = None, limit: int = 50,
                     steps: bool = False) -> dict:
        sdir = self.find_session_dir(session_id)
        cwd = ""
        if sdir is not None:
            cwd = str((_load_meta(sdir / "meta.json") or {}).get("cwd") or "")
        path = self._transcript_path(session_id, cwd)
        if path is None:
            return None
        t0 = time.perf_counter()
        messages = []
        step_rows = []
        tool_meta = {} if steps else None
        pos = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                obj = _safe_json(line)
                msg = self._parse_line(obj)
                if msg:
                    msg["_pos"] = pos
                    messages.append(msg)
                if steps and isinstance(obj, dict):
                    for st in _steps_of(obj, tool_meta, pos):
                        step_rows.append((pos, msg.get("uuid") if msg else "", st))
                pos += 1
        t1 = time.perf_counter()
        total = len(messages)
        if offset is None:
            offset = max(0, total - limit)
        offset = max(0, offset)
        window = messages[offset: offset + limit]
        for msg in window:
            msg["blocks"] = _render_blocks(msg["role"], msg["text"])
        if steps:
            live = [(p, st) for p, _uid, st in step_rows]
            steps_mod.attach(window, live)
        for msg in messages:
            msg.pop("_pos", None)
        t2 = time.perf_counter()
        try:
            file_bytes = path.stat().st_size
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

    def _parse_line(self, obj) -> dict | None:
        if not isinstance(obj, dict):
            return None
        role = str(obj.get("role") or obj.get("type") or "")
        if role not in ("user", "assistant"):
            return None
        if role == "user":
            text = _human_user_text(obj)
        else:
            text = _assistant_text(obj)
        if not text:
            return None
        ts = obj.get("timestamp") or ""
        if isinstance(ts, (int, float)):
            ts = _ms_to_iso(ts)
        return {
            "uuid": obj.get("uuid") or "",
            "role": role,
            "ts": ts,
            "text": text,
        }

    def _session_summary(self, sdir: Path, meta: dict, project_id: str) -> dict:
        sid = sdir.name
        cwd = str(meta.get("cwd") or "")
        path = self._transcript_path(sid, cwd)
        return self._session_summary_from_transcript(sid, path, meta, project_id)

    def _session_summary_from_transcript(
            self, sid: str, path: Path | None, meta: dict,
            project_id: str) -> dict:
        cwd = str(meta.get("cwd") or "")
        first_user = ""
        last_text = ""
        last_role = ""
        model = ""
        if path is not None and path.is_file():
            for line in _read_head_lines(path, _HEAD_BYTES):
                obj = _safe_json(line)
                if not obj:
                    continue
                if not first_user:
                    t = _human_user_text(obj) if (
                        obj.get("role") == "user" or obj.get("type") == "user"
                    ) else ""
                    if t:
                        first_user = t
            for line in reversed(_read_tail_lines(path, _TAIL_BYTES)):
                obj = _safe_json(line)
                if not obj:
                    continue
                role = obj.get("role") or obj.get("type")
                if role == "assistant" and not last_text:
                    t = _assistant_text(obj)
                    if t:
                        last_text, last_role = t, "assistant"
                elif role == "user" and not last_text:
                    t = _human_user_text(obj)
                    if t:
                        last_text, last_role = t, "user"
                if last_text:
                    break
        started = _ms_to_iso(meta.get("createdAtMs"))
        last_active = _ms_to_iso(meta.get("updatedAtMs")) or started
        if path is not None and path.is_file() and not last_active:
            try:
                last_active = _ms_to_iso(path.stat().st_mtime * 1000)
            except OSError:
                pass
        meta_title = str(meta.get("title") or "").strip()
        if meta_title.lower() in titles.BLANK:
            meta_title = ""
        base_title = meta_title or _preview(first_user, _MAX_TITLE) or sid
        title = base_title
        if first_user:
            sig = titles.sig_for(first_user) if hasattr(titles, "sig_for") else (
                hashlib.sha1(first_user.encode("utf-8")).hexdigest()[:16])
            cached = self._titles.get(sid, sig)
            if cached:
                title = cached
            else:
                self._titles.request(sid, sig, first_user, self.titler)
        try:
            size_bytes = path.stat().st_size if path and path.is_file() else 0
        except OSError:
            size_bytes = 0
        return {
            "id": sid,
            "project_id": project_id or _munge_cwd(cwd) or "no-project",
            "cwd": cwd,
            "git_branch": "",
            "title": title,
            "started": started,
            "last_active": last_active,
            "last_role": last_role,
            "last_text": _preview(last_text) if last_text else "",
            "model": model,
            "size_bytes": size_bytes,
        }


def _steps_of(obj: dict, tool_meta: dict, pos: int) -> list:
    content = _content_blocks(obj.get("message") if isinstance(obj, dict) else None)
    if not content:
        return []
    out = []
    ts = obj.get("timestamp") or ""
    uid = obj.get("uuid") or str(pos)
    for i, b in enumerate(content):
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        ref = "%s:%d" % (uid, i)
        if kind == "tool_use":
            raw = b.get("input")
            name = b.get("name", "?")
            full = steps_mod.format_tool_use(name, raw)
            lang = steps_mod.lang_for_tool_use(name, raw)
            path = steps_mod.path_from_input(raw)
            tid = b.get("id")
            if tool_meta is not None and tid:
                tool_meta[str(tid)] = (name, path)
            desc, cmd = _tool_status_parts(raw if isinstance(raw, dict) else {})
            out.append(steps_mod.tool_use(
                ref, ts, name, cmd or desc, full, lang=lang))
        elif kind == "tool_result":
            raw = b.get("content")
            if isinstance(raw, str):
                body = raw
            elif isinstance(raw, list):
                body = "\n".join(
                    (x.get("text") or "") if isinstance(x, dict) else str(x)
                    for x in raw)
            else:
                body = json.dumps(raw or {}, default=str)
            name, path = "", ""
            tid = b.get("tool_use_id")
            if tool_meta is not None and tid and str(tid) in tool_meta:
                name, path = tool_meta[str(tid)]
            body = steps_mod.format_tool_result(body, name)
            lang = steps_mod.lang_for_tool_result(name, path, body)
            out.append(steps_mod.tool_result(
                ref, ts, not bool(b.get("is_error")), body, lang=lang))
        elif kind == "thinking":
            thought = b.get("thinking") or b.get("text") or ""
            if thought:
                out.append(steps_mod.thinking(ref, ts, thought))
    return out


def _decode_store_meta(raw) -> tuple[dict, bool]:
    """Return (metadata, was_hex_encoded).

    Cursor currently stores JSON as lowercase hex text in meta.value. Accept
    plain JSON too so a storage migration does not make rewind destructive.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="strict")
    text = str(raw or "").strip()
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value, False
    except (json.JSONDecodeError, ValueError):
        pass
    try:
        value = json.loads(bytes.fromhex(text).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise providers.RunnerError("unrecognized Cursor chat metadata") from e
    if not isinstance(value, dict):
        raise providers.RunnerError("unrecognized Cursor chat metadata")
    return value, True


def _encode_store_meta(value: dict, as_hex: bool) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return raw.encode("utf-8").hex() if as_hex else raw


def _json_blob_role(data: bytes) -> str:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return ""
    return str(value.get("role") or "") if isinstance(value, dict) else ""


def _severity(percent: float) -> str:
    if percent >= 95:
        return "danger"
    if percent >= 80:
        return "warning"
    return "normal"


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _usage_reset_text(*values) -> str:
    dates = []
    for value in values:
        try:
            n = float(value)
            if n > 1e12:
                n /= 1000.0
            dates.append(datetime.fromtimestamp(n, tz=timezone.utc))
            continue
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        if isinstance(value, str):
            try:
                dates.append(datetime.fromisoformat(value.replace("Z", "+00:00")))
            except ValueError:
                pass
    if not dates:
        return ""
    dt = max(dates)
    return "Resets " + dt.strftime("%b %-d")


class CursorRunner:
    """Executes one turn as `cursor-agent --print` with stream-json."""

    name = "cursor"
    _usage_cache = (0.0, None)
    _usage_lock = threading.Lock()

    def __init__(self, config):
        self.config = config
        self.store = CursorStore(self._home(), config)
        self._guest_stores = {}
        self._interactive = None
        self._interactive_lock = threading.Lock()

    def _interactive_mgr(self):
        with self._interactive_lock:
            if self._interactive is None:
                from .cursor_interactive import CursorInteractiveManager
                self._interactive = CursorInteractiveManager(self.config, self)
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
        return self._interactive_mgr().send_tui_keys(
            session_id, keys=keys, text=text)

    def _store_for(self, job=None) -> CursorStore:
        root = str(getattr(job, "isolate_root", "") or "").strip()
        if not root:
            return self.store
        key = os.path.realpath(os.path.expanduser(root))
        store = self._guest_stores.get(key)
        if store is None:
            store = CursorStore(Path(key) / ".cursor", self.config)
            self._guest_stores[key] = store
        return store

    def _bin(self) -> str:
        return str(getattr(self.config, "cursor_bin", None) or "cursor-agent")

    def _home(self) -> Path:
        raw = getattr(self.config, "cursor_home_path", None)
        if raw:
            return Path(raw)
        return Path(str(getattr(self.config, "cursor_home", "")
                        or (Path.home() / ".cursor"))).expanduser()

    def capabilities(self) -> dict:
        from .cursor_interactive import tmux_available
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
            "can_show_usage": True,
            "interactive": has_tmux,
            "live_tui": has_tmux,
            # Cursor keeps immutable content-addressed snapshots in store.db.
            # Rewind moves latestRootBlobId to an earlier complete snapshot.
            "rewind": True,
        }

    def rewind_session(self, session_id: str, steps: int):
        """Move Cursor's root pointer back N prompts and trim the UI journal.

        ``blobs`` is an immutable SHA-256-addressed DAG. Root snapshots start
        with references to the system/context blobs; old complete roots remain
        in insertion order after later turns. Updating only
        ``meta.latestRootBlobId`` is sufficient for ``--resume`` to rebuild
        the older conversation (verified against Cursor CLI 2026.09).
        """
        sid = str(session_id or "").strip()
        # A live Cursor process holds the old root in memory. Close its idle
        # pane before changing the root so the next turn reloads the rewind.
        self._interactive_mgr().close_for_session(sid)
        sdir = self.store.find_session_dir(sid)
        session_store = self.store
        if sdir is None:
            for guest_store in list(self._guest_stores.values()):
                sdir = guest_store.find_session_dir(sid)
                if sdir is not None:
                    session_store = guest_store
                    break
        db_path = (sdir / "store.db") if sdir is not None else None
        if db_path is None or not db_path.is_file():
            raise providers.RunnerError("Cursor session store not found")

        db_backup = db_path.with_name(db_path.name + ".rewind-bak")
        try:
            source = sqlite3.connect(str(db_path), timeout=5)
            backup = sqlite3.connect(str(db_backup))
            source.backup(backup)
            backup.close()
            source.close()
        except sqlite3.Error as e:
            raise providers.RunnerError(
                "could not back up Cursor session store: %s" % e)

        con = None
        try:
            con = sqlite3.connect(str(db_path), timeout=5)
            # Resolve and update one stable snapshot. This incorporates WAL
            # contents and prevents Cursor/IDE writers from advancing the
            # root while the rewind target is being selected.
            con.execute("BEGIN IMMEDIATE")
            meta_row = con.execute(
                "SELECT value FROM meta WHERE key='0'").fetchone()
            if not meta_row:
                con.close()
                raise providers.RunnerError("Cursor session metadata not found")
            meta, was_hex = _decode_store_meta(meta_row[0])
            current = str(meta.get("latestRootBlobId") or "")
            rows = list(con.execute(
                "SELECT rowid, id, data FROM blobs ORDER BY rowid"))
        except providers.RunnerError:
            if con is not None:
                con.close()
            raise
        except sqlite3.Error as e:
            if con is not None:
                con.close()
            raise providers.RunnerError("could not read Cursor session store: %s" % e)

        blobs = {str(blob_id): bytes(data) for _rowid, blob_id, data in rows}
        rowids = {str(blob_id): int(rowid) for rowid, blob_id, _data in rows}
        if current not in blobs:
            con.close()
            raise providers.RunnerError("Cursor current snapshot not found")

        raw_ids = {}
        for blob_id in blobs:
            if len(blob_id) != 64:
                continue
            try:
                raw_ids[bytes.fromhex(blob_id)] = blob_id
            except ValueError:
                continue
        refs = {}
        for blob_id, data in blobs.items():
            found = set()
            # Protobuf encodes each content hash as a 32-byte bytes field:
            # one-byte field tag, 0x20 length, then the raw SHA-256. Looking
            # only after 0x20 is substantially faster than testing every
            # 32-byte window in large system-prompt blobs.
            pos = -1
            while True:
                pos = data.find(b"\x20", pos + 1)
                if pos < 0:
                    break
                ref = raw_ids.get(data[pos + 1:pos + 33])
                if ref and ref != blob_id:
                    found.add(ref)
            refs[blob_id] = found

        system_ids = {
            blob_id for blob_id, data in blobs.items()
            if _json_blob_role(data) == "system"
        }
        roots = []
        for rowid, blob_id, data in rows:
            if len(data) < 34 or data[:2] != b"\x0a\x20":
                continue
            first_ref = raw_ids.get(data[2:34])
            if first_ref in system_ids:
                roots.append((int(rowid), str(blob_id)))
        if not roots or current not in {blob_id for _rowid, blob_id in roots}:
            con.close()
            raise providers.RunnerError(
                "unsupported Cursor session format — no snapshot roots")

        closure_cache = {}

        def closure(blob_id):
            if blob_id in closure_cache:
                return closure_cache[blob_id]
            seen, todo = set(), [blob_id]
            while todo:
                item = todo.pop()
                if item in seen:
                    continue
                seen.add(item)
                todo.extend(refs.get(item, ()))
            closure_cache[blob_id] = seen
            return seen

        user_ids = {
            blob_id for blob_id, data in blobs.items()
            if _json_blob_role(data) == "user"
        }
        initial_count = min(
            len(closure(blob_id) & user_ids) for _rowid, blob_id in roots)
        prompt_counts = {
            blob_id: max(0, len(closure(blob_id) & user_ids) - initial_count)
            for _rowid, blob_id in roots
        }
        current_count = prompt_counts[current]
        if current_count <= 0:
            con.close()
            raise providers.RunnerError("nothing to rewind — no user messages yet")
        steps = max(1, min(int(steps), current_count))
        target_count = current_count - steps
        before_current = [
            (rowid, blob_id) for rowid, blob_id in roots
            if rowid < rowids[current] and prompt_counts[blob_id] == target_count
        ]
        if not before_current:
            con.close()
            raise providers.RunnerError(
                "unsupported Cursor session format — prior snapshot not found")
        target = max(before_current)[1]

        cwd = str((_load_meta(sdir / "meta.json") or {}).get("cwd") or "")
        transcript = session_store._transcript_path(sid, cwd)
        transcript_raw = None
        cut = None
        preview = ""
        if transcript is not None and transcript.is_file():
            try:
                transcript_raw = transcript.read_text(
                    encoding="utf-8", errors="replace")
            except OSError as e:
                con.close()
                raise providers.RunnerError(
                    "could not read Cursor transcript: %s" % e)
            lines = transcript_raw.splitlines()
            marks = []
            for i, line in enumerate(lines):
                obj = _safe_json(line)
                if isinstance(obj, dict) and (
                        obj.get("role") == "user" or obj.get("type") == "user"):
                    text = _human_user_text(obj)
                    if text:
                        marks.append((i, text))
            if marks:
                visible_steps = min(steps, len(marks))
                cut, preview = marks[-visible_steps]

        transcript_backup = (
            transcript.with_name(transcript.name + ".rewind-bak")
            if transcript is not None else None)
        original_meta = meta_row[0]
        updated = False
        try:
            if transcript_raw is not None and transcript_backup is not None:
                transcript_backup.write_text(transcript_raw, encoding="utf-8")
            meta["latestRootBlobId"] = target
            con.execute(
                "UPDATE meta SET value=? WHERE key='0'",
                (_encode_store_meta(meta, was_hex),))
            con.commit()
            updated = True
            if transcript_raw is not None and cut is not None:
                kept = transcript_raw.splitlines()[:cut]
                transcript.write_text(
                    ("\n".join(kept) + "\n") if kept else "",
                    encoding="utf-8")
        except (OSError, sqlite3.Error) as e:
            try:
                con.rollback()
            except sqlite3.Error:
                pass
            if updated:
                try:
                    con.execute(
                        "UPDATE meta SET value=? WHERE key='0'",
                        (original_meta,))
                    con.commit()
                except sqlite3.Error:
                    pass
            raise providers.RunnerError("Cursor rewind failed: %s" % e)
        finally:
            con.close()
        return steps, " ".join(preview.split())[:120]

    def _usage_token(self) -> str:
        extra = getattr(self.config, "cursor_env", None) or {}
        token = str(extra.get("CURSOR_ACCESS_TOKEN")
                    or os.environ.get("CURSOR_ACCESS_TOKEN") or "").strip()
        if token:
            return token
        if shutil.which("security"):
            try:
                proc = subprocess.run([
                    "security", "find-generic-password",
                    "-s", "cursor-access-token", "-a", "cursor-user", "-w",
                ], capture_output=True, text=True, timeout=5)
                token = (proc.stdout or "").strip()
                if token:
                    return token
            except (OSError, subprocess.SubprocessError):
                pass
        if shutil.which("secret-tool"):
            try:
                proc = subprocess.run([
                    "secret-tool", "lookup",
                    "service", "cursor-access-token",
                    "account", "cursor-user",
                ], capture_output=True, text=True, timeout=5)
                token = (proc.stdout or "").strip()
                if token:
                    return token
            except (OSError, subprocess.SubprocessError):
                pass
        candidates = [
            Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb",
            Path.home() / ".config/Cursor/User/globalStorage/state.vscdb",
        ]
        for path in candidates:
            if not path.is_file():
                continue
            try:
                db = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
                row = db.execute(
                    "SELECT value FROM ItemTable "
                    "WHERE key='cursorAuth/accessToken'").fetchone()
                db.close()
                token = str(row[0] if row else "").strip()
                if token:
                    return token
            except sqlite3.Error:
                continue
        return ""

    def _usage_identity(self) -> tuple[str, str]:
        try:
            data = json.loads(
                (self._home() / "cli-config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            data = {}
        info = data.get("authInfo") if isinstance(data, dict) else {}
        if not isinstance(info, dict):
            info = {}
        account = str(info.get("email") or info.get("displayName") or "").strip()
        account_id = str(info.get("authId") or info.get("userId") or account).strip()
        return account, account_id

    def _usage_request(self, token: str, path: str, method="POST") -> dict:
        base = str(getattr(self.config, "cursor_usage_url", "")
                   or _USAGE_URL).rstrip("/")
        headers = {
            "Authorization": "Bearer " + token,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
            "User-Agent": "agentremoted-cursor-usage",
        }
        req = urllib.request.Request(
            base + path, data=b"{}" if method == "POST" else None,
            headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        return raw if isinstance(raw, dict) else {}

    def usage(self) -> dict:
        """Cursor plan usage from the same dashboard API used by `/usage`."""
        account, account_id = self._usage_identity()
        now = time.monotonic()
        at, cached = type(self)._usage_cache
        if cached is not None and now - at < _USAGE_TTL_S:
            return dict(cached)
        with type(self)._usage_lock:
            at, cached = type(self)._usage_cache
            if cached is not None and time.monotonic() - at < _USAGE_TTL_S:
                return dict(cached)
            token = self._usage_token()
            if not token:
                return {
                    "ok": False, "error": "Cursor login token not found",
                    "provider": "cursor", "account": account,
                    "account_id": account_id,
                }
            errors = []

            def fetch(path, method="POST"):
                try:
                    return self._usage_request(token, path, method=method)
                except (urllib.error.HTTPError, urllib.error.URLError, OSError,
                        ValueError, json.JSONDecodeError) as e:
                    errors.append(e)
                    return {}

            period = fetch(
                "/aiserver.v1.DashboardService/GetCurrentPeriodUsage")
            plan = fetch("/aiserver.v1.DashboardService/GetPlanInfo")
            aggregate = fetch(
                "/aiserver.v1.DashboardService/GetAggregatedUsageEvents")
            legacy = fetch("/auth/usage", method="GET")
            if not any((period, plan, aggregate, legacy)):
                e = errors[0] if errors else None
                code = e.code if isinstance(e, urllib.error.HTTPError) else 0
                result = {
                    "ok": False,
                    "error": ("Cursor sign-in expired" if code in (401, 403)
                              else "Could not load Cursor usage"),
                    "provider": "cursor", "account": account,
                    "account_id": account_id,
                }
                return result

            plan_info = plan.get("planInfo") if isinstance(
                plan.get("planInfo"), dict) else {}
            plan_name = str(plan_info.get("planName") or "Cursor")
            reset = _usage_reset_text(
                period.get("billingCycleEnd"), plan_info.get("billingCycleEnd"))
            buckets = []
            plan_usage = period.get("planUsage") if isinstance(
                period.get("planUsage"), dict) else {}
            used = _as_float(plan_usage.get("totalSpend"))
            limit = (_as_float(plan_usage.get("limit"))
                     or _as_float(plan_info.get("includedAmountCents")))
            if limit > 0:
                percent = _as_float(plan_usage.get("totalPercentUsed"),
                                    used * 100.0 / limit)
                detail = "$%.2f / $%.2f" % (used / 100.0, limit / 100.0)
                buckets.append({
                    "title": "Plan · %s" % plan_name,
                    "percent": max(0, min(100, round(percent))),
                    "resets_text": detail + ((" · " + reset) if reset else ""),
                    "severity": _severity(percent),
                })
            spend = period.get("spendLimitUsage") if isinstance(
                period.get("spendLimitUsage"), dict) else {}
            spend_limit = (_as_float(spend.get("individualLimit"))
                           or _as_float(spend.get("pooledLimit")))
            spend_used = (_as_float(spend.get("individualUsed"))
                          or _as_float(spend.get("pooledUsed")))
            if spend_limit > 0:
                percent = spend_used * 100.0 / spend_limit
                buckets.append({
                    "title": "On-demand spend",
                    "percent": max(0, min(100, round(percent))),
                    "resets_text": "$%.2f / $%.2f%s" % (
                        spend_used / 100.0, spend_limit / 100.0,
                        (" · " + reset) if reset else ""),
                    "severity": _severity(percent),
                })
            if not buckets:
                req = legacy.get("gpt-4") if isinstance(
                    legacy.get("gpt-4"), dict) else {}
                req_used = _as_float(req.get("numRequestsTotal")
                                     or req.get("numRequests"))
                req_limit = _as_float(req.get("maxRequestUsage"))
                if req_limit > 0:
                    percent = req_used * 100.0 / req_limit
                    buckets.append({
                        "title": "Requests · %s" % plan_name,
                        "percent": max(0, min(100, round(percent))),
                        "resets_text": "%d / %d%s" % (
                            req_used, req_limit,
                            (" · " + reset) if reset else ""),
                        "severity": _severity(percent),
                    })
            if not buckets:
                # No plan or spend limit to measure against (Enterprise seats
                # report pooled spend only), so there is no percentage: the
                # row is money, and clients hide the bar on show_bar=false.
                cost = _as_float(aggregate.get("totalCostCents"))
                detail = "$%.2f used this cycle" % (cost / 100.0)
                buckets.append({
                    "title": "Usage · %s" % plan_name,
                    "percent": 0,
                    "show_bar": False,
                    "resets_text": detail + ((" · " + reset) if reset else ""),
                    "severity": "normal",
                })
            result = {
                "ok": True, "buckets": buckets, "provider": "cursor",
                "account": account, "account_id": account_id,
            }
            type(self)._usage_cache = (time.monotonic(), dict(result))
            return result

    def auth_health(self) -> dict:
        bin_path = self._bin()
        on_path = bool(
            shutil.which(bin_path)
            or shutil.which("cursor-agent")
            or Path(os.path.expanduser("~/.local/bin/cursor-agent")).is_file()
        )
        extra = getattr(self.config, "cursor_env", None) or {}
        api_key = str(
            extra.get("CURSOR_API_KEY")
            or os.environ.get("CURSOR_API_KEY") or ""
        ).strip()
        logged_in = False
        cfg = self._home() / "cli-config.json"
        if cfg.is_file():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                data = {}
            info = data.get("authInfo") if isinstance(data, dict) else None
            if isinstance(info, dict) and (info.get("authId") or info.get("email")):
                logged_in = True
        if api_key:
            return {
                "cli": "cursor-agent",
                "cli_on_path": on_path,
                "mode": "api_key",
                "status": "ok" if on_path else "warning",
                "detail": ("CURSOR_API_KEY is set"
                           + ("" if on_path else "; `cursor-agent` not on PATH")),
            }
        if logged_in and on_path:
            return {
                "cli": "cursor-agent",
                "cli_on_path": True,
                "mode": "subscription",
                "status": "ok",
                "detail": "Cursor CLI login present on this host",
            }
        if on_path:
            return {
                "cli": "cursor-agent",
                "cli_on_path": True,
                "mode": "unknown",
                "status": "warning",
                "detail": "cursor-agent on PATH — run `cursor-agent login` if turns fail",
            }
        return {
            "cli": "cursor-agent",
            "cli_on_path": False,
            "mode": "none",
            "status": "missing",
            "detail": "`cursor-agent` not on PATH",
        }

    _models_cache = (0.0, None)

    def _cli_models(self) -> list:
        now = time.monotonic()
        at, cached = type(self)._models_cache
        if cached is not None and now - at < _MODELS_TTL_S:
            return list(cached)
        ids = []
        try:
            out = subprocess.run(
                [self._bin(), "--list-models"],
                capture_output=True, text=True, timeout=20,
            ).stdout or ""
            listing = False
            for line in out.splitlines():
                stripped = line.strip()
                if stripped.lower().startswith("available models"):
                    listing = True
                    continue
                if not listing or not stripped or stripped.startswith("-"):
                    continue
                token = stripped.split(" ", 1)[0].strip()
                if token and token not in ids and not token.startswith("("):
                    ids.append(token)
        except (OSError, subprocess.SubprocessError):
            ids = []
        type(self)._models_cache = (now, ids)
        return list(ids)

    def models(self) -> list:
        live = self._cli_models()
        extra = [str(m).strip() for m in
                 (getattr(self.config, "models", None) or []) if str(m).strip()]
        out = []
        for m in (live or _FALLBACK_MODELS) + extra:
            if m and m not in out:
                out.append(m)
        if "auto" not in out:
            out.insert(0, "auto")
        elif out[0] != "auto":
            out.remove("auto")
            out.insert(0, "auto")
        return out

    def efforts(self) -> list:
        return []

    _BUILTIN_SLASH = [
        "/about", "/ask", "/auto-review", "/bedrock", "/clear", "/compact",
        "/config", "/context", "/copy", "/copy-conversation-id",
        "/copy-request-id", "/debug", "/exit", "/feedback",
        "/fork", "/full-conversation", "/goal", "/help", "/line-numbers",
        "/logs", "/max-mode", "/mcp", "/model", "/open", "/plan", "/plugin",
        "/quit", "/rename", "/resume", "/rewind", "/rules", "/run-everything",
        "/sandbox", "/setup-terminal", "/shell", "/show-thinking", "/skills",
        "/status-indicators", "/summarize", "/update", "/usage", "/vim",
    ]

    def slash_commands(self) -> list:
        out = list(self._BUILTIN_SLASH)
        for extra in getattr(self.config, "slash_commands", None) or []:
            if isinstance(extra, str) and extra.strip():
                out.append(extra.strip())
        return sorted(set(out))

    def title_for(self, text: str) -> str:
        cwd = str(titles.titler_cwd())
        cmd = [
            self._bin(), "--print", "--output-format", "text",
            "--force", "--trust", "--workspace", cwd,
            titles.prompt_for(text),
        ]
        env = dict(os.environ)
        extra = getattr(self.config, "cursor_env", None) or {}
        env.update({str(k): str(v) for k, v in extra.items()})
        try:
            out = subprocess.run(
                cmd, cwd=cwd, env=env, capture_output=True,
                text=True, timeout=120).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        return titles.title_from_output(out)

    def prepare(self, job, mode):
        self._store_for(job)
        if not job.cwd:
            default = str(getattr(self.config, "cursor_default_cwd", "")
                          or "").strip()
            if default:
                job.cwd = os.path.expanduser(default)
        if not job.cwd:
            raise providers.RunnerError(
                "cwd is required for Cursor Agent sessions")
        cwd = os.path.expanduser(job.cwd)
        if not os.path.isdir(cwd):
            raise providers.RunnerError("cwd does not exist: %s" % cwd)
        job.cwd = cwd

        state = job.runner_state
        state["parts"] = []
        state["full"] = []
        state["open_tools"] = set()

        cmd = [
            self._bin(),
            "--print",
            "--output-format", "stream-json",
        ]
        flags = str(getattr(self.config, "cursor_prompt_flags", "") or "").split()
        if not flags:
            flags = ["--force", "--trust"]
        cmd += flags
        if not str(getattr(job, "isolate_root", "") or "").strip():
            cmd += ["--workspace", cwd]
        if job.session_id:
            cmd += ["--resume", job.session_id]
        if job.model and job.model not in ("", "default", "auto"):
            cmd += ["--model", job.model]
        cmd.append(job.prompt)

        env = dict(os.environ)
        extra = getattr(self.config, "cursor_env", None) or {}
        env.update({str(k): str(v) for k, v in extra.items()})
        return cmd, env

    def handle_stream_line(self, job, line: str):
        obj = _safe_json(line)
        if not isinstance(obj, dict):
            return
        kind = str(obj.get("type") or "")
        subtype = str(obj.get("subtype") or "")
        state = job.runner_state

        if kind == "system" and subtype == "init":
            sid = str(obj.get("session_id") or "").strip()
            if sid:
                with job.lock:
                    job.new_session_id = sid
            job.add_event("init", session_id=sid,
                          model=str(obj.get("model") or job.model or ""))
            return

        if kind in ("error", "fatal"):
            raw = (obj.get("message") or obj.get("error")
                   or obj.get("result") or "Cursor Agent reported an error")
            with job.lock:
                if not job.error:
                    job.error = str(raw)
            job.add_event("text", text=str(raw),
                          blocks=markdown_to_blocks(str(raw)))
            return

        if kind == "thinking":
            if not state.get("open_tools"):
                job.set_phase("thinking", "")
            return

        if kind == "assistant":
            text = _assistant_text(obj)
            if not text:
                return
            if subtype == "delta":
                state.setdefault("parts", []).append(text)
                state.setdefault("full", []).append(text)
                job.set_phase("writing", "".join(state["parts"])[-160:])
                return
            if text == state.get("last_emitted"):
                return
            self._flush_text(job)
            state["last_emitted"] = text
            state.setdefault("full", []).append(text)
            job.add_event("text", text=text, blocks=markdown_to_blocks(text))
            job.set_phase("writing", text[-160:])
            return

        if kind == "tool_call":
            name, args = _unwrap_tool_call(
                obj.get("tool_call") if isinstance(obj.get("tool_call"), dict)
                else obj)
            call_id = str(obj.get("call_id") or obj.get("toolCallId") or name)
            desc, cmd = _tool_status_parts(args)
            if subtype in ("started", "start", ""):
                state.setdefault("open_tools", set()).add(call_id)
                self._flush_text(job)
                job.add_event("tool", name=name, detail=cmd or desc)
                job.set_phase(_phase_for_tool(name), desc or cmd or name)
            elif subtype in ("completed", "end", "error"):
                state.setdefault("open_tools", set()).discard(call_id)
            return

        if kind == "result":
            self._flush_text(job)
            result = obj.get("result")
            if isinstance(result, str) and result.strip():
                with job.lock:
                    if not job.result_text:
                        job.result_text = result.strip()
            if obj.get("is_error") or subtype == "error":
                err = result if isinstance(result, str) else "cursor-agent error"
                with job.lock:
                    if not job.error:
                        job.error = str(err)
            job.add_event(
                "result",
                is_error=bool(obj.get("is_error") or subtype == "error"),
                duration_ms=obj.get("duration_ms") or 0,
                cost_usd=0,
            )

    def _flush_text(self, job):
        state = job.runner_state
        parts = state.get("parts") or []
        if not parts:
            return
        text = "".join(parts)
        state["parts"] = []
        if text.strip():
            job.add_event("text", text=text, blocks=markdown_to_blocks(text))

    def tick(self, job):
        return

    def finalize(self, job, returncode, stderr_tail):
        self._flush_text(job)
        state = job.runner_state
        full = "".join(state.get("full") or [])
        with job.lock:
            if full and not job.result_text:
                job.result_text = full
        if returncode not in (0, None) and not job.error:
            tail = (stderr_tail or "").strip().splitlines()
            msg = tail[-1] if tail else (
                "cursor-agent exited with code %s" % returncode)
            with job.lock:
                job.error = msg
            return False
        return None

    def cleanup(self, job):
        return
