"""Grok provider: session store + turn runner (ported from grok-bb10's sidecar).

Grok stores sessions under:

    ~/.grok/sessions/<group>/<session-uuid>/
        summary.json    — info.{id,cwd}, git_root_dir, head_branch,
                          num_messages, generated_title, session_summary,
                          created_at/updated_at/last_active_at, current_model_id
    ~/.grok/sessions/<group>/<session-uuid>/updates.jsonl
        one ACP-ish record per line:
        {"params": {"update": {"sessionUpdate": <kind>, ...}},
         "timestamp": <epoch float>}
        kinds: user_message_chunk, agent_message_chunk, agent_thought_chunk,
               tool_call, turn_completed, plan, ...

Grok emits a *different* schema on CLI stdout (`--output-format
streaming-json`): flat records {"type": <kind>, "data"|"text": ..., ...}
ending with {"type": "end", "sessionId": ..., "stopReason": ..., "usage":
...}. Do not conflate the two — the store parses the disk format, the
runner parses the stdout format.

Grok has no interactive permission callback; its permission model is the
up-front flag list in config `grok_prompt_flags` (e.g. "--yolo --deny
Bash(rm*)"), appended to every invocation.
"""

import json
import math
import os
import re
import threading
import time
from pathlib import Path

from ..render_blocks import (
    COLOR_META,
    COLOR_META_THOUGHT,
    COLOR_META_WORKED,
    _esc,
    _wrap_color,
    inline_to_rich,
    markdown_to_blocks,
)
from .. import search_util

_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_MAX_PREVIEW = 200
_MAX_TITLE = 60
_TAIL_BYTES = 8 * 1024
_TITLE_SCAN_LINES = 400

# Titles grok writes that mean "no title".
_BLANK_TITLES = ("", "(no title)", "untitled", "new session")

# Grok's --output-format streaming-json only emits thought/text/end — tool
# activity is written to the session's updates.jsonl as tool_call /
# tool_call_update. Map those (and any stream tool events we do get) to the
# same live-status phases the claude provider uses, so the phone banner shows
# "Running: ls -la" instead of a bare "Grok is working...".
_DETAIL_KEYS = (
    "command", "target_file", "file_path", "path", "pattern", "url",
    "query", "prompt", "description", "old_string", "glob",
)
_PHASE_BY_TOOL = {
    # Claude-style names (stream fake / aliases)
    "Edit": "editing", "Write": "editing", "MultiEdit": "editing",
    "NotebookEdit": "editing", "Read": "reading", "Grep": "searching",
    "Glob": "searching", "Bash": "running", "WebFetch": "browsing",
    "WebSearch": "browsing", "Task": "delegating", "Agent": "delegating",
    # Grok Build tool names (updates.jsonl title / _meta.x.ai/tool.name)
    "read_file": "reading", "read_file_v2": "reading",
    "search_replace": "editing", "write": "editing", "write_file": "editing",
    "run_terminal_command": "running", "bash": "running",
    "grep": "searching", "glob": "searching", "list_dir": "searching",
    "web_search": "browsing", "web_fetch": "browsing", "open_page": "browsing",
    "browse_page": "browsing", "image_gen": "tool",
}
_KIND_TO_PHASE = {
    "read": "reading",
    "execute": "running",
    "edit": "editing",
    "search": "searching",
    "fetch": "browsing",
    "browse": "browsing",
}
# "Is a tool in flight?" used to be inferred from the phase name, but a phase
# is sticky: it could tell that a tool had STARTED, never that it finished, so
# the banner stayed on the last tool for the rest of the turn. The open
# toolCallIds answer it properly — see GrokRunner._track_open_tools.


def _is_session_id(value: str) -> bool:
    return bool(value) and bool(_SESSION_ID_RE.match(value))


# summary.json `session_kind` values that mean "the agent spawned this, not
# the human". Grok writes a full session tree (summary.json + updates.jsonl)
# for every subagent it delegates to, right next to the real ones — those
# must never reach the phone's session list. Only *known* non-user kinds are
# filtered, so a future kind for a human-started session still shows up.
_NON_USER_KINDS = frozenset({"subagent"})


def _is_user_session(summary: dict) -> bool:
    """False for agent-spawned sessions (grok's Task/Explore subagents)."""
    kind = str((summary or {}).get("session_kind") or "").strip().lower()
    return kind not in _NON_USER_KINDS


def _tool_detail(tool_input, max_len: int = 200) -> str:
    """One short single-line snippet for the phone status banner.

    Same contract as claude.tool_detail: collapse whitespace, then
    middle-ellipsis so head + tail stay readable.
    """
    if not isinstance(tool_input, dict):
        return ""
    for key in _DETAIL_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            text = " ".join(val.split())
            if len(text) <= max_len:
                return text
            if max_len < 3:
                return text[:max_len]
            keep = max_len - 1
            head = keep // 2
            tail = keep - head
            return text[:head] + "…" + text[-tail:]
    return ""


def _phase_for_tool(name: str, kind: str = "") -> str:
    if name:
        if name in _PHASE_BY_TOOL:
            return _PHASE_BY_TOOL[name]
        # Case-insensitive fallback (stream fakes use "Bash", disk uses
        # run_terminal_command).
        low = name.lower()
        for k, phase in _PHASE_BY_TOOL.items():
            if k.lower() == low:
                return phase
    if kind:
        return _KIND_TO_PHASE.get(str(kind).lower(), "tool")
    return "tool"


def _extract_error_text(obj: dict) -> str:
    """Pull a human message out of a streaming-json error-ish record."""
    for key in ("message", "error", "text", "data", "reason", "detail"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            for sub in ("message", "error", "text", "reason"):
                s = val.get(sub)
                if isinstance(s, str) and s.strip():
                    return s.strip()
    return ""


def _safe_json(line: str):
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _munge_cwd(cwd: str) -> str:
    """Same munging Claude Code uses for project dirs — reused here so
    project ids look identical to the app regardless of provider."""
    out = []
    for ch in cwd:
        out.append(ch if ch.isalnum() or ch in "-_" else "-")
    return "".join(out)


def _content_text(content) -> str:
    """Grok content may be a string, {"text"|"content": ...}, or a list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if isinstance(content.get("text"), str):
            return content["text"]
        return _content_text(content.get("content"))
    if isinstance(content, list):
        return "".join(_content_text(c) for c in content)
    return ""


# Grok's harness injects its own content as user_message_chunks the human
# never typed: <system-reminder> blocks (background-task completions, hook
# output, ambient notes), <monitor-event> reports from a background watch,
# and the hint line the runtime appends after one. Strip them so the phone's
# transcript only shows what the user said. Same role as claude.py's
# _human_user_text — keep the two in step.
#
# The opening tag may carry attributes (<monitor-event task_id="...">), which
# an earlier bare-tag pattern did not match — the whole ffmpeg log then read
# as a user prompt on the phone.
_INJECTED_TAGS = ("system-reminder", "task-notification", "system-warning",
                  "monitor-event")
_TAG_ALT = "|".join(_INJECTED_TAGS)
_INJECTED_BLOCK_RE = re.compile(
    r"<(%s)(?:\s[^>]*)?>.*?</\1>" % _TAG_ALT, re.S)
# A chunk split mid-block leaves an opening tag with no closer.
_INJECTED_OPEN_RE = re.compile(r"<(?:%s)(?:\s[^>]*)?>" % _TAG_ALT)
_TASK_HINT_RE = re.compile(
    r"^\s*Use get[a-z]*output\(.*$", re.M)


def _human_text(content) -> str:
    """Human-typed part of a user_message_chunk ("" = pure injection)."""
    text = _content_text(content)
    if "<" not in text:
        return text
    text = _INJECTED_BLOCK_RE.sub("", text)
    text = _TASK_HINT_RE.sub("", text)
    # An unclosed block (a chunk split mid-injection) would otherwise leak its
    # head: drop everything from the opening tag on.
    m = _INJECTED_OPEN_RE.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()


def _preview(text: str, max_len: int = _MAX_PREVIEW) -> str:
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _iso(ts: float) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
    except (OverflowError, OSError, ValueError):
        return ""


def _fmt_duration(seconds: float) -> str:
    """Grok-TUI-style durations: 3.4s / 14s / 1m14s / 1h2m."""
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        return "%.1fs" % seconds
    total = int(round(seconds))
    if total < 60:
        return "%ds" % total
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return "%dm%ds" % (minutes, sec) if sec else "%dm" % minutes
    hours, minutes = divmod(minutes, 60)
    return "%dh%dm" % (hours, minutes) if minutes else "%dh" % hours


def _status_blocks(text: str, meta_kind: str) -> list:
    if meta_kind == "thought":
        color = COLOR_META_THOUGHT
    elif meta_kind == "worked":
        color = COLOR_META_WORKED
    else:
        color = COLOR_META
    return [{
        "k": "meta",
        "role": "status",
        "metaKind": meta_kind,
        "text": text,
        "rich": _wrap_color("<i>%s</i>" % _esc(text), color),
        "fmt": "rich",
        "accent": 1,
    }]


class GrokStore:
    """Read-only view over grok's session tree."""

    def __init__(self, grok_home: Path):
        self.grok_home = grok_home
        self.sessions_root = grok_home / "sessions"

    # -- discovery -----------------------------------------------------

    def _iter_session_dirs(self):
        """Yield every session dir (has summary.json) across all groups."""
        if not self.sessions_root.is_dir():
            return
        for group in self.sessions_root.iterdir():
            if not group.is_dir():
                continue
            for child in group.iterdir():
                if (child.is_dir() and _is_session_id(child.name)
                        and (child / "summary.json").is_file()):
                    yield child

    def known_session_ids(self, user_only: bool = True) -> set:
        """Session ids on disk. `user_only` drops agent-spawned subagents —
        the new-session fs-diff scan must not latch onto one of those when a
        turn delegates before the phone has learned the real session id."""
        ids = set()
        for sdir in self._iter_session_dirs():
            if user_only and not _is_user_session(self._load_summary(sdir)):
                continue
            ids.add(sdir.name)
        return ids

    def find_session_dir(self, session_id: str) -> Path:
        if not _is_session_id(session_id) or not self.sessions_root.is_dir():
            return None
        for group in self.sessions_root.iterdir():
            if not group.is_dir():
                continue
            candidate = group / session_id
            if (candidate / "summary.json").is_file():
                return candidate
        return None

    @staticmethod
    def _load_summary(sdir: Path):
        try:
            with open(sdir / "summary.json", "r", encoding="utf-8",
                      errors="replace") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _project_cwd(summary: dict) -> str:
        info = summary.get("info") or {}
        return summary.get("git_root_dir") or info.get("cwd") or ""

    def list_projects(self) -> list:
        """Group sessions by project dir (grok has no project concept on
        disk; git_root_dir / cwd is the natural grouping)."""
        projects = {}
        for sdir in self._iter_session_dirs():
            summary = self._load_summary(sdir)
            if not summary:
                continue
            if int(summary.get("num_messages") or 0) <= 0:
                continue
            if not _is_user_session(summary):
                continue
            cwd = self._project_cwd(summary)
            pid = _munge_cwd(cwd) if cwd else "no-project"
            updates = sdir / "updates.jsonl"
            try:
                mtime = updates.stat().st_mtime
            except OSError:
                mtime = 0.0
            entry = projects.get(pid)
            if entry is None:
                projects[pid] = {
                    "id": pid,
                    "cwd": cwd,
                    # rstrip: grok records some cwds with a trailing slash,
                    # and basename("/root/worker/") is "".
                    "name": (os.path.basename(cwd.rstrip("/")) or "/") if cwd
                            else "(no project)",
                    "session_count": 1,
                    "last_active": mtime,
                }
            else:
                entry["session_count"] += 1
                entry["last_active"] = max(entry["last_active"], mtime)
        out = list(projects.values())
        out.sort(key=lambda p: p["last_active"], reverse=True)
        return out

    def list_sessions(self, project_id: str = None, limit: int = 25,
                      user_only: bool = True) -> list:
        rows = []
        for sdir in self._iter_session_dirs():
            summary = self._load_summary(sdir)
            if not summary:
                continue
            if int(summary.get("num_messages") or 0) <= 0:
                continue
            if user_only and not _is_user_session(summary):
                continue
            cwd = self._project_cwd(summary)
            pid = _munge_cwd(cwd) if cwd else "no-project"
            if project_id and pid != project_id:
                continue
            rows.append(self._session_summary(sdir, summary, pid))
        rows.sort(key=lambda r: r["last_active"] or r["started"], reverse=True)
        return rows[:limit]

    def search_sessions(self, query: str, project_id: str = None,
                        limit: int = 25, user_only: bool = True) -> list:
        """Full-text search over session titles + message text in updates.jsonl.

        Newest sessions first (by summary mtime). Returns session summaries
        plus a `snippet` of the first match for client-side highlighting.
        """
        q = search_util.normalize_query(query)
        if not q:
            return []

        candidates = []
        for sdir in self._iter_session_dirs():
            summary = self._load_summary(sdir)
            if not summary:
                continue
            if int(summary.get("num_messages") or 0) <= 0:
                continue
            if user_only and not _is_user_session(summary):
                continue
            cwd = self._project_cwd(summary)
            pid = _munge_cwd(cwd) if cwd else "no-project"
            if project_id and pid != project_id:
                continue
            try:
                mtime = (sdir / "summary.json").stat().st_mtime
            except OSError:
                mtime = 0
            candidates.append((mtime, sdir, summary, pid))
        candidates.sort(key=lambda t: t[0], reverse=True)

        results = []
        for _, sdir, summary, pid in candidates[:search_util.MAX_SCAN]:
            row = self._session_summary(sdir, summary, pid)
            hit = self._match_session(sdir, row, q)
            if hit is None:
                continue
            out = dict(row)
            out["snippet"] = hit
            results.append(out)
            if len(results) >= limit:
                break
        results.sort(key=search_util.rank_key, reverse=True)
        return results

    def _match_session(self, sdir: Path, row: dict, query: str):
        """Snippet for the first hit, or None. Title/last_text first, then
        a cheap line-reject scan of updates.jsonl for user/assistant text."""
        title = row.get("title") or ""
        if search_util.contains_ci(title, query):
            return search_util.make_snippet(title, query)
        last = row.get("last_text") or ""
        if search_util.contains_ci(last, query):
            return search_util.make_snippet(last, query)
        cwd = row.get("cwd") or ""
        if search_util.contains_ci(cwd, query):
            return search_util.make_snippet(cwd, query)

        updates = sdir / "updates.jsonl"
        if not updates.is_file():
            return None
        q_lower = query.casefold()
        role_map = {
            "user_message_chunk": "user",
            "agent_message_chunk": "assistant",
        }
        try:
            with open(updates, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if q_lower not in line.casefold():
                        continue
                    ev = _safe_json(line)
                    if not ev:
                        continue
                    update = (ev.get("params") or {}).get("update") or {}
                    kind = update.get("sessionUpdate", "")
                    if kind not in role_map:
                        continue
                    # Never match on injected content the user never typed.
                    text = (_human_text(update.get("content"))
                            if role_map[kind] == "user"
                            else _content_text(update.get("content")))
                    if search_util.contains_ci(text, query):
                        return search_util.make_snippet(text, query)
        except OSError:
            return None
        return None

    def get_session(self, session_id: str) -> dict:
        sdir = self.find_session_dir(session_id)
        if sdir is None:
            return None
        summary = self._load_summary(sdir)
        if not summary:
            return None
        cwd = self._project_cwd(summary)
        pid = _munge_cwd(cwd) if cwd else "no-project"
        return self._session_summary(sdir, summary, pid)

    # -- transcripts ---------------------------------------------------

    def get_messages(self, session_id: str, offset: int = None, limit: int = 50) -> dict:
        sdir = self.find_session_dir(session_id)
        if sdir is None:
            return None
        updates = sdir / "updates.jsonl"
        try:
            file_bytes = updates.stat().st_size
        except OSError:
            file_bytes = 0

        # Parse (read + coalesce all turns) spans the whole file; block
        # rendering runs only over the returned window. Both timed for the
        # client (see the claude provider for the rationale).
        t0 = time.perf_counter()
        messages = _build_transcript(updates)
        t1 = time.perf_counter()

        total = len(messages)
        if offset is None:
            offset = max(0, total - limit)
        offset = max(0, offset)
        window = messages[offset: offset + limit]
        for msg in window:
            _render_grok_message(msg)
        t2 = time.perf_counter()

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

    # -- internals -----------------------------------------------------

    def _session_summary(self, sdir: Path, summary: dict, project_id: str) -> dict:
        info = summary.get("info") or {}
        cwd = info.get("cwd") or summary.get("git_root_dir") or ""
        updates = sdir / "updates.jsonl"
        try:
            size = updates.stat().st_size
        except OSError:
            size = 0
        last_role, last_text, last_ts = _tail_preview(updates)
        return {
            "id": info.get("id") or sdir.name,
            "project_id": project_id,
            "cwd": cwd,
            "git_branch": summary.get("head_branch") or "",
            "title": self._display_title(summary, sdir),
            "started": summary.get("created_at") or "",
            "last_active": (summary.get("updated_at")
                            or summary.get("last_active_at")
                            or _iso(last_ts)),
            "last_role": last_role,
            "last_text": _preview(last_text),
            "model": summary.get("current_model_id") or "",
            "size_bytes": size,
        }

    @staticmethod
    def _display_title(summary: dict, sdir: Path) -> str:
        for key in ("generated_title", "session_summary"):
            title = " ".join(str(summary.get(key) or "").split())
            if title and title.lower() not in _BLANK_TITLES:
                return _preview(title, _MAX_TITLE)
        title = _first_user_preview(sdir / "updates.jsonl")
        if title:
            return _preview(title, _MAX_TITLE)
        sid = (summary.get("info") or {}).get("id") or sdir.name
        return "Session %s" % sid[:8]


def _first_user_preview(updates_path: Path) -> str:
    """First user text in the transcript head — grok's title fallback."""
    try:
        with open(updates_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _TITLE_SCAN_LINES:
                    break
                ev = _safe_json(line)
                if not ev:
                    continue
                update = (ev.get("params") or {}).get("update") or {}
                if update.get("sessionUpdate") == "user_message_chunk":
                    text = " ".join(_human_text(update.get("content")).split())
                    if text:
                        return text
    except OSError:
        pass
    return ""


def _tail_preview(updates_path: Path):
    """(last_role, last_text, last_ts) from the tail of updates.jsonl.

    Consecutive chunks of the same role are one streamed message — walk the
    tail backwards collecting them so the preview isn't a single token.
    """
    try:
        size = updates_path.stat().st_size
        with open(updates_path, "rb") as f:
            if size > _TAIL_BYTES:
                f.seek(size - _TAIL_BYTES)
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        if size > _TAIL_BYTES and lines:
            lines = lines[1:]
    except OSError:
        return "", "", 0.0

    role_map = {"user_message_chunk": "user", "agent_message_chunk": "assistant"}
    last_role = ""
    parts = []
    last_ts = 0.0
    for line in reversed(lines):
        ev = _safe_json(line)
        if not ev:
            continue
        if not last_ts:
            try:
                last_ts = float(ev.get("timestamp") or 0)
            except (TypeError, ValueError):
                last_ts = 0.0
        update = (ev.get("params") or {}).get("update") or {}
        role = role_map.get(update.get("sessionUpdate", ""))
        if role is None:
            if last_role:
                break  # the message run ended
            continue
        text = (_human_text(update.get("content")) if role == "user"
                else _content_text(update.get("content")))
        if not text:
            continue
        if not last_role:
            last_role = role
        elif role != last_role:
            break
        parts.append(text)
        if sum(len(p) for p in parts) > _MAX_PREVIEW * 2:
            break
    parts.reverse()
    return last_role, "".join(parts), last_ts


def _build_transcript(updates_path: Path) -> list:
    """updates.jsonl -> messages shaped like the claude provider's:
    {uuid, role, ts, text, blocks}, roles user/assistant/status.

    Consecutive same-role chunks coalesce into one message. Thought-chunk
    spans become a "Thought for Xs" status row under the user prompt;
    turn_completed adds a "Worked for Xs" status row (grok-TUI parity).
    Tool calls and raw thought text are transient job state — dropped here,
    exactly like the claude provider drops tool_use lines.
    """
    rows = []          # {role, ts, text, metaKind?}
    prompt_starts = [] # rows index where each human prompt begins (/rewind)
    cur = None         # {"role", "ts", "parts"}
    th_start = th_end = None
    th_secs = 0.0
    turn_start = None

    def flush():
        nonlocal cur
        if cur:
            text = "".join(cur["parts"]).strip()
            if text:
                rows.append({"role": cur["role"], "ts": cur["ts"], "text": text})
        cur = None

    def close_thought():
        nonlocal th_start, th_end, th_secs
        if th_start is None:
            return
        dur = max(0.0, (th_end or th_start) - th_start)
        # A single same-second chunk still represents a real blip of thinking.
        th_secs += dur if dur > 0 else 0.3
        th_start = th_end = None

    def append(role, text, ts):
        nonlocal cur
        if not text:
            return
        if cur and cur["role"] == role:
            cur["parts"].append(text)
        else:
            flush()
            cur = {"role": role, "ts": ts, "parts": [text]}

    def insert_thought_status(ts):
        """Place "Thought for X" directly under the last user prompt."""
        text = "Thought for %s" % _fmt_duration(th_secs)
        idx = None
        for i in range(len(rows) - 1, -1, -1):
            if rows[i]["role"] == "user":
                idx = i + 1
                break
        row = {"role": "status", "ts": ts, "text": text, "metaKind": "thought"}
        if idx is None:
            rows.append(row)
        elif idx < len(rows) and rows[idx].get("metaKind") == "thought":
            rows[idx] = row
        else:
            rows.insert(idx, row)

    try:
        f = open(updates_path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return []
    with f:
        for line in f:
            ev = _safe_json(line)
            if not ev:
                continue
            update = (ev.get("params") or {}).get("update") or {}
            kind = update.get("sessionUpdate", "")
            try:
                ts = float(ev.get("timestamp") or 0)
            except (TypeError, ValueError):
                ts = 0.0

            if kind == "user_message_chunk":
                close_thought()
                th_secs = 0.0
                turn_start = ts
                text = _human_text(update.get("content"))
                if text and (cur is None or cur["role"] != "user"):
                    # First chunk of a new human prompt: it will flush to
                    # exactly this row index (the group is contiguous).
                    prompt_starts.append(len(rows))
                append("user", text, ts)
            elif kind == "rewind_marker":
                # /rewind truncated grok's history, but updates.jsonl is
                # append-only so the rewound turns are still in it. Drop them
                # here too, or the phone would keep showing a conversation the
                # agent has forgotten. target_prompt_index is 0-based over the
                # human prompts.
                close_thought()
                flush()
                try:
                    idx = int(update.get("target_prompt_index"))
                except (TypeError, ValueError):
                    idx = -1
                if 0 <= idx < len(prompt_starts):
                    del rows[prompt_starts[idx]:]
                    del prompt_starts[idx:]
                th_secs = 0.0
                turn_start = 0.0
            elif kind == "agent_message_chunk":
                close_thought()
                append("assistant", _content_text(update.get("content")), ts)
            elif kind == "agent_thought_chunk":
                if th_start is None:
                    th_start = ts
                th_end = ts
            elif kind in ("tool_call", "plan"):
                close_thought()
                flush()
            elif kind == "turn_completed":
                close_thought()
                flush()
                if th_secs > 0:
                    insert_thought_status(ts)
                worked = 0.0
                if turn_start and ts > turn_start:
                    worked = ts - turn_start
                else:
                    usage = update.get("usage") or {}
                    try:
                        worked = float(usage.get("apiDurationMs") or 0) / 1000.0
                    except (TypeError, ValueError):
                        worked = 0.0
                # grok usage payloads can carry NaN — never let it near output
                if not math.isfinite(worked):
                    worked = 0.0
                if worked > 0:
                    rows.append({"role": "status", "ts": ts,
                                 "text": "Worked for %s" % _fmt_duration(worked),
                                 "metaKind": "worked"})
                th_secs = 0.0
                turn_start = None
            # everything else (session_info, current_mode_update, ...) ignored
    flush()

    messages = []
    for i, row in enumerate(rows):
        # Blocks are rendered later, only for the returned window (see
        # get_messages) — building them for every turn of a long session
        # just to discard all but the last page was wasted work.
        msg = {
            "uuid": "g%d" % i,
            "role": row["role"],
            "ts": _iso(row["ts"]),
            "text": row["text"],
        }
        if row.get("metaKind"):
            msg["metaKind"] = row["metaKind"]
        messages.append(msg)
    return messages


def _render_grok_message(msg: dict) -> None:
    """Attach display blocks to one transcript message in place."""
    role = msg["role"]
    text = msg["text"]
    if role == "user":
        if text.startswith("[shell] ! ") and "\n[output]\n" in text:
            cmd, rest = text.split("\n[output]\n", 1)
            cmd = cmd[len("[shell] "):]
            # Phone adds "[silent] …" so the agent does not reply; hide it
            # from the transcript UI (agent still saw it on the wire).
            if "\n[silent]" in rest:
                rest = rest.split("\n[silent]", 1)[0]
            plain, rich = inline_to_rich(cmd)
            blocks = [{"k": "user", "role": "user", "text": plain,
                       "rich": rich, "fmt": "rich"}]
            if rest.strip():
                blocks += markdown_to_blocks(rest, role="user")
            msg["blocks"] = blocks
        else:
            plain, rich = inline_to_rich(text)
            msg["blocks"] = [{"k": "user", "role": "user", "text": plain,
                              "rich": rich, "fmt": "rich"}]
    elif role == "status":
        msg["blocks"] = _status_blocks(text, msg.get("metaKind", "status"))
    else:
        msg["blocks"] = markdown_to_blocks(text, role="assistant")


class GrokRunner:
    """Executes one turn as `grok -p` with streaming-json output."""

    name = "grok"

    # How often tick() may rescan the session tree for a fresh session dir.
    _SCAN_INTERVAL = 0.8

    def __init__(self, config):
        self.config = config
        self.store = GrokStore(config.grok_home_path)
        # Lazily created tmux-TUI manager for "interactive" jobs.
        self._interactive = None
        self._interactive_lock = threading.Lock()

    def _interactive_mgr(self):
        with self._interactive_lock:
            if self._interactive is None:
                from .grok_interactive import GrokInteractiveManager
                self._interactive = GrokInteractiveManager(self.config, self)
            return self._interactive

    def run_alternate(self, job, mode) -> bool:
        """Fully handle a job outside the subprocess pipeline. "interactive"
        drives a real TUI in tmux — the path that doesn't hang the way
        headless `grok -p` does."""
        if mode != "interactive":
            return False
        self._interactive_mgr().run(job)
        return True

    def type_into_tui(self, session_id: str, text: str) -> str:
        """Type a message into a session's live interactive TUI ("" or err)."""
        return self._interactive_mgr().type_text(session_id, text)

    def capture_tui(self, session_id: str) -> dict:
        return self._interactive_mgr().capture_tui(session_id)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        return self._interactive_mgr().send_tui_keys(session_id, keys=keys, text=text)

    def usage(self) -> dict:
        """Subscription limits for the phone's Usage sheet. grok has no usage
        API, so they are read out of a dedicated throwaway TUI running its
        /usage command (grok_interactive.fetch_usage)."""
        return self._interactive_mgr().fetch_usage()

    def capabilities(self) -> dict:
        from .grok_interactive import tmux_available
        return {
            "queue": True,
            "stop": True,
            "projects": True,
            "ws_status": True,
            "permissions": False,
            "permission_modes": False,
            "requires_cwd": False,
            "can_set_model": True,
            "can_set_effort": True,
            # No usage endpoint for grok — the numbers come out of the TUI's
            # /usage command, so the Usage sheet needs tmux on the host. Say
            # so honestly: without it the phone keeps its old behaviour and
            # opens grok.com instead.
            "can_show_usage": tmux_available(),
            # "interactive" permission mode: turns run in a host tmux TUI.
            "interactive": True,
            "live_tui": tmux_available(),
            # "/rewind N" is driven in that TUI: grok's picker lists one point
            # per prompt, newest first, so N back == the Nth row. Conversation
            # only — reverting files is grok's default but the changes are
            # unrecoverable unless committed.
            "rewind": True,
        }

    def models(self) -> list:
        """Model ids the app offers for -m/--model. Config `models` extends
        the known grok families."""
        default = ["default", "grok-4.5", "grok-4"]
        extra = [str(m).strip() for m in
                 (getattr(self.config, "models", None) or []) if str(m).strip()]
        out = []
        for m in default + extra:
            if m not in out:
                out.append(m)
        return out

    def efforts(self) -> list:
        """Reasoning-effort levels for --effort (grok). Config `efforts`
        extends the list."""
        default = ["default", "low", "medium", "high"]
        extra = [str(e).strip() for e in
                 (getattr(self.config, "efforts", None) or []) if str(e).strip()]
        out = []
        for e in default + extra:
            if e not in out:
                out.append(e)
        return out

    # Interactive built-ins verified in grok's own TUI: /compact exists,
    # /exit closes it, /rewind opens the checkpoint picker.
    _BUILTIN_SLASH = ["/compact", "/exit", "/rewind"]

    def slash_commands(self) -> list:
        """The interactive built-ins plus whatever config adds — grok's CLI
        has no discoverable command set."""
        out = list(self._BUILTIN_SLASH)
        for extra in getattr(self.config, "slash_commands", None) or []:
            if isinstance(extra, str) and extra.strip():
                out.append(extra.strip())
        return sorted(set(out))

    def prepare(self, job, mode):
        # `mode` is claude vocabulary; grok's permission model is the static
        # flag list below (e.g. --yolo --deny ...), so mode is ignored.
        if not job.cwd:
            job.cwd = self._default_cwd()
        state = job.runner_state
        state["parts"] = []        # unflushed text chunks
        state["full"] = []         # every text chunk (for result_text)
        state["end"] = None
        state["seen_tool_ids"] = set()
        state["updates_path"] = None
        state["updates_offset"] = 0
        if not job.session_id:
            # The CLI doesn't reliably report the new session id; diff the
            # session tree against this snapshot while the job runs.
            state["before_ids"] = self.store.known_session_ids()
            state["last_scan"] = 0.0
        else:
            # Resume: tail updates.jsonl from the current end so historical
            # tool_calls from prior turns don't flood the banner.
            self._bind_updates(job, from_start=False)

        cmd = [self.config.grok_bin, "-p", job.prompt,
               "--output-format", "streaming-json"]
        if job.session_id:
            cmd += ["--resume", job.session_id]
        if job.cwd:
            cmd += ["--cwd", job.cwd]
        if job.model and job.model != "default":
            cmd += ["--model", job.model]
        if job.effort and job.effort != "default":
            cmd += ["--effort", job.effort]
        cmd += str(getattr(self.config, "grok_prompt_flags", "") or "").split()

        env = dict(os.environ)
        extra_env = getattr(self.config, "grok_env", None) or {}
        env.update({str(k): str(v) for k, v in extra_env.items()})
        # Never let the CLI self-update mid-turn.
        env.setdefault("GROK_DISABLE_AUTOUPDATER", "1")
        return cmd, env

    def handle_stream_line(self, job, line: str):
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(obj, dict):
            return
        et = str(obj.get("type") or "").lower()
        state = job.runner_state

        self._sniff_session_id(job, obj)

        # Explicit error events: record the message plainly; the CLI exits
        # and the turn fails via the exit code / end record.
        if et in ("error", "fatal", "session_error"):
            raw = _extract_error_text(obj) or "Grok reported an error"
            with job.lock:
                if not job.error:
                    job.error = raw
            job.add_event("text", text=raw, blocks=markdown_to_blocks(raw))
            return

        if et in ("thought", "thinking", "agent_thought"):
            # Leave the banner alone only while a tool is actually running
            # (tools come off updates.jsonl); between tools, thinking is what
            # grok is really doing.
            if self._thinking_ok(job):
                job.set_phase("thinking", "")

        elif et in ("text", "agent_message", "message"):
            chunk = obj.get("data")
            if not isinstance(chunk, str):
                chunk = obj.get("text")
            if isinstance(chunk, str) and chunk:
                state["parts"].append(chunk)
                state["full"].append(chunk)
                job.set_phase("writing", "".join(state["parts"])[-160:])

        elif et in ("tool", "tool_call", "tool_use", "toolcall"):
            # Rare on real grok streaming-json (tools live on disk), but the
            # smoke-test fake and any future stream still go through here.
            self._flush_text(job)
            self._apply_stream_tool(job, obj)

        elif et in ("tool_result", "tool_result_chunk"):
            # Keep the last tool phase visible until the next thought/text.
            pass

        elif et == "end":
            state["end"] = obj
            sid = obj.get("sessionId") or obj.get("session_id") or obj.get("id")
            if isinstance(sid, str):
                self._note_session_id(job, sid)
            if obj.get("is_error") or obj.get("error") is True:
                err_text = _extract_error_text(obj)
                with job.lock:
                    if not job.error:
                        job.error = err_text or "Grok turn failed"
        # unknown kinds are keep-alives — ignore

    def tick(self, job):
        state = job.runner_state
        # Session-id discovery for new sessions (fs-diff scan).
        if not job.session_id and not job.new_session_id and "before_ids" in state:
            now_m = time.monotonic()
            if now_m - state.get("last_scan", 0.0) >= self._SCAN_INTERVAL:
                state["last_scan"] = now_m
                self._scan_new_session(job)
        # Live tool status from the session journal (streaming-json omits it).
        self._poll_updates(job)

    def finalize(self, job, returncode, stderr_tail):
        state = job.runner_state
        if not job.session_id and not job.new_session_id and "before_ids" in state:
            self._scan_new_session(job)
        # Catch any tool_calls that landed just before exit.
        self._poll_updates(job)
        self._flush_text(job)

        end = state.get("end")
        result_text = "".join(state.get("full") or [])
        if not result_text and isinstance(end, dict):
            text = end.get("text")
            if isinstance(text, str):
                result_text = text
        with job.lock:
            job.result_text = result_text

        is_error = bool(job.error) or (end is None and returncode not in (0, None))
        job.add_event("result",
                      is_error=bool(is_error),
                      duration_ms=int((time.time() - job.started_at) * 1000),
                      cost_usd=0)
        # A clean successful `end` trumps a nonzero exit code (grok sometimes
        # exits nonzero after a completed turn).
        if job.error:
            return False
        return True if end is not None else None

    def cleanup(self, job):
        pass

    # -- internals -----------------------------------------------------

    def _default_cwd(self) -> str:
        raw = str(getattr(self.config, "grok_default_cwd", "") or "")
        path = (Path(raw).expanduser() if raw
                else self.config.grok_home_path / "workspace")
        try:
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
        except OSError:
            return str(Path.home())

    def _sniff_session_id(self, job, obj: dict):
        for key in ("sessionId", "session_id"):
            sid = obj.get(key)
            if isinstance(sid, str) and sid:
                self._note_session_id(job, sid)
                return

    def _note_session_id(self, job, sid: str):
        """First valid session id wins; mirrors the claude init event."""
        if not _is_session_id(sid):
            return
        first = False
        with job.lock:
            if job.new_session_id:
                return
            job.new_session_id = sid
            first = True
        if first:
            job.add_event("init", session_id=sid, model="")
            # New session: read updates from the start so tools that already
            # landed before we discovered the id still drive the banner.
            self._bind_updates(job, from_start=True)

    def _scan_new_session(self, job):
        state = job.runner_state
        before = state.get("before_ids") or set()
        fresh = self.store.known_session_ids() - before
        if not fresh:
            return
        if len(fresh) == 1:
            self._note_session_id(job, next(iter(fresh)))
            return
        # Concurrent creations: pick the newest summary.json. Subagent trees
        # spawned by this very turn are already excluded (known_session_ids
        # filters them) — otherwise the newest dir would usually be one of
        # them and the phone would attach to a subagent's transcript.
        best, best_mtime = None, -1.0
        for sid in fresh:
            sdir = self.store.find_session_dir(sid)
            if sdir is None:
                continue
            try:
                mtime = (sdir / "summary.json").stat().st_mtime
            except OSError:
                continue
            if mtime > best_mtime:
                best, best_mtime = sid, mtime
        if best:
            self._note_session_id(job, best)

    def _bind_updates(self, job, from_start: bool):
        """Point the updates.jsonl tail at this job's session journal."""
        sid = job.new_session_id or job.session_id
        if not sid:
            return
        sdir = self.store.find_session_dir(sid)
        if sdir is None:
            return
        path = sdir / "updates.jsonl"
        state = job.runner_state
        path_s = str(path)
        if state.get("updates_path") == path_s and not from_start:
            return
        state["updates_path"] = path_s
        if from_start:
            state["updates_offset"] = 0
        else:
            try:
                state["updates_offset"] = path.stat().st_size
            except OSError:
                state["updates_offset"] = 0

    def _poll_updates(self, job):
        """Tail updates.jsonl for tool_call events (the live-status source)."""
        state = job.runner_state
        if not state.get("updates_path"):
            # Session may have appeared via fs-diff / end sniff since prepare.
            if job.new_session_id or job.session_id:
                # Resume jobs already bound from end; late discovery = new.
                self._bind_updates(job, from_start=not bool(job.session_id))
        path = state.get("updates_path")
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        offset = int(state.get("updates_offset") or 0)
        if size < offset:
            offset = 0  # truncated / rewritten
        if size == offset:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                state["updates_offset"] = f.tell()
        except OSError:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if line:
                self._handle_update_line(job, line)

    def _handle_update_line(self, job, line: str):
        ev = _safe_json(line)
        if not isinstance(ev, dict):
            return
        upd = (ev.get("params") or {}).get("update")
        if not isinstance(upd, dict):
            # Some writers store the update object at the top level.
            if ev.get("sessionUpdate"):
                upd = ev
            else:
                return
        kind = upd.get("sessionUpdate") or ""
        if kind in ("tool_call", "tool_call_update"):
            self._note_plan_approval(job, upd, kind)
            self._note_ask_questions(job, upd, kind)
            self._track_open_tools(job, upd, kind)
        if kind == "plan":
            self._emit_todos(job, upd)
        elif kind == "tool_call":
            self._apply_disk_tool(job, upd, emit_event=True)
        elif kind == "tool_call_update":
            # Updates often carry the human title ("Execute `ls -la`") and
            # kind=execute after the bare tool_call; refresh phase/detail.
            # Only emit a banner event once per toolCallId.
            tid = str(upd.get("toolCallId") or "")
            seen = job.runner_state.setdefault("seen_tool_ids", set())
            emit = bool(tid) and tid not in seen
            if upd.get("title") or upd.get("rawInput") or upd.get("kind") or emit:
                self._apply_disk_tool(job, upd, emit_event=emit)
        elif job.runner_state.get("disk_text"):
            # Interactive mode (grok_interactive) has no stdout stream, so the
            # journal is also the source of the turn's text and its end.
            self._disk_text(job, kind, upd)

    # A tool_call_update carrying one of these is that tool's last word.
    _TOOL_DONE = frozenset({"completed", "failed", "cancelled", "canceled",
                            "error", "denied", "rejected", "timeout"})

    def _track_open_tools(self, job, upd: dict, kind: str):
        """Remember which tool calls are still in flight.

        The banner must not flip to "thinking" while a tool runs, but it also
        has to come BACK to thinking once the tool is done — grok thinks
        between tool calls for most of a long turn. Phase alone can't tell
        the difference (it is sticky), so track the open toolCallIds; the
        journal reports each one's completion."""
        tid = str(upd.get("toolCallId") or "")
        if not tid:
            return
        open_tools = job.runner_state.setdefault("open_tools", set())
        if kind == "tool_call":
            open_tools.add(tid)
        elif str(upd.get("status") or "").lower() in self._TOOL_DONE:
            open_tools.discard(tid)

    def _thinking_ok(self, job) -> bool:
        """True when no tool is in flight, so a thought chunk owns the banner."""
        return not job.runner_state.get("open_tools")

    @staticmethod
    def _ask_payload(upd: dict):
        """(toolCallId, questions) when this record is an ask_user_question
        panel carrying its questions, else (tid, None).

        grok puts the payload in rawInput with variant "AskUserQuestion";
        which record holds it varies (tool_call or the first
        tool_call_update), so both are accepted."""
        meta = (upd.get("_meta") or {}).get("x.ai/tool") or {}
        if not isinstance(meta, dict):
            meta = {}
        raw = upd.get("rawInput")
        raw = raw if isinstance(raw, dict) else {}
        tid = str(upd.get("toolCallId") or "")
        is_ask = (meta.get("name") == "ask_user_question"
                  or str(raw.get("variant") or "") == "AskUserQuestion")
        qs = raw.get("questions")
        if is_ask and isinstance(qs, list) and qs:
            return tid, qs
        return tid, None

    def _emit_todos(self, job, upd: dict):
        """Surface grok's todo list, which it shows in its own TUI.

        A "plan" journal record carries the whole list every time any item
        changes — entries of {content, status, priority} — so it is emitted
        as a markdown checklist (the phone renders blocks, no app change) and
        deduped: grok rewrites the list on each todo_write, and without the
        signature check the transcript would fill with near-identical copies.
        """
        entries = upd.get("entries")
        if not isinstance(entries, list) or not entries:
            return
        rows, done, current = [], 0, ""
        for e in entries:
            if not isinstance(e, dict):
                continue
            text = " ".join(str(e.get("content") or "").split())
            if not text:
                continue
            status = str(e.get("status") or "").lower()
            if status == "completed":
                done += 1
                rows.append("- [x] " + text)
            elif status == "in_progress":
                current = text
                rows.append("- [ ] **%s**" % text)
            else:
                rows.append("- [ ] " + text)
        if not rows:
            return
        sig = "\n".join(rows)
        state = job.runner_state
        if state.get("todo_sig") == sig:
            return
        state["todo_sig"] = sig
        head = "**Todo %d/%d**" % (done, len(rows))
        if current:
            head += " — " + current
        text = head + "\n" + sig
        job.add_event("text", text=text, blocks=markdown_to_blocks(text))

    def _note_ask_questions(self, job, upd: dict, kind: str):
        """Track grok's ask_user_question panel from the journal.

        Like plan approval it is modal: no turn_completed follows, and the
        panel keeps intercepting keys, so a prompt sent while it is up would
        be read as option keystrokes. The phone answers it instead."""
        tid, qs = self._ask_payload(upd)
        state = job.runner_state
        if qs:
            state["ask_call_id"] = tid
            state["ask_questions"] = qs
            return
        if tid and tid == state.get("ask_call_id") \
                and str(upd.get("status") or "").lower() in self._TOOL_DONE:
            state["ask_questions"] = None

    def ask_pending(self, session_id: str):
        """(call_id, questions) if this session's last ask panel is still
        blocking — read from the journal, so it survives a daemon restart and
        is visible before a turn starts.

        "Answered" cannot be judged from the tool's own status: grok often
        never writes a terminal update for an ask call (VPS session
        019fabc8: the panel resolved and no completion was ever recorded, so
        the phone was re-asked on every later turn). What does prove it is
        anything that can only happen once the panel is gone — the turn
        finishing, or another message being submitted — since the panel holds
        the turn and swallows the keyboard while it is up."""
        sdir = self.store.find_session_dir(session_id) if session_id else None
        if sdir is None:
            return None
        pending = None
        try:
            with open(sdir / "updates.jsonl", "r", encoding="utf-8",
                      errors="replace") as f:
                for line in f:
                    if "tool_call" not in line and "turn_completed" not in line \
                            and "user_message_chunk" not in line:
                        continue
                    ev = _safe_json(line)
                    if not isinstance(ev, dict):
                        continue
                    upd = (ev.get("params") or {}).get("update") or {}
                    if not isinstance(upd, dict):
                        continue
                    kind = upd.get("sessionUpdate") or ""
                    if kind in ("turn_completed", "user_message_chunk"):
                        pending = None
                        continue
                    tid, qs = self._ask_payload(upd)
                    if qs:
                        pending = (tid, qs)
                    elif (pending and tid == pending[0]
                          and str(upd.get("status") or "").lower()
                          in self._TOOL_DONE):
                        pending = None
        except OSError:
            return None
        return pending

    def _note_plan_approval(self, job, upd: dict, kind: str):
        """Track grok's plan-approval panel from the journal alone.

        `exit_plan_mode` (tool kind "exit_plan") opens a modal approval view
        that only a keypress can clear, and no turn_completed follows it — so
        an interactive turn would hang. The matching tool_call_update
        status=completed is written the instant the panel is answered, which
        is how the interactive runner knows it may carry on. Pane scraping is
        deliberately avoided: this is all on disk."""
        meta = (upd.get("_meta") or {}).get("x.ai/tool") or {}
        if not isinstance(meta, dict):
            meta = {}
        is_plan_exit = (meta.get("kind") == "exit_plan"
                        or upd.get("kind") == "exit_plan"
                        or meta.get("name") == "exit_plan_mode"
                        or upd.get("title") == "exit_plan_mode")
        tid = str(upd.get("toolCallId") or "")
        state = job.runner_state
        if kind == "tool_call" and is_plan_exit:
            state["plan_call_id"] = tid
            state["plan_pending"] = True
            return
        if kind == "tool_call_update" and tid and tid == state.get("plan_call_id"):
            if str(upd.get("status") or "") in ("completed", "failed",
                                                "cancelled", "canceled"):
                state["plan_pending"] = False

    def plan_awaiting(self, session_id: str) -> bool:
        """Does this session sit on an unanswered plan approval? Read from
        the session's own plan_mode.json (written by grok, not scraped)."""
        sdir = self.store.find_session_dir(session_id) if session_id else None
        if sdir is None:
            return False
        try:
            with open(sdir / "plan_mode.json", "r", encoding="utf-8",
                      errors="replace") as f:
                return bool(json.load(f).get("awaiting_plan_approval"))
        except (OSError, ValueError):
            return False

    def plan_text(self, session_id: str) -> str:
        """The plan under review — grok writes it to plan.md in the session
        dir, so the phone gets real markdown instead of a boxed pane render."""
        sdir = self.store.find_session_dir(session_id) if session_id else None
        if sdir is None:
            return ""
        try:
            with open(sdir / "plan.md", "r", encoding="utf-8",
                      errors="replace") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _disk_text(self, job, kind: str, upd: dict):
        state = job.runner_state
        if kind == "agent_message_chunk":
            chunk = _content_text(upd.get("content"))
            if chunk:
                state["parts"].append(chunk)
                state["full"].append(chunk)
                job.set_phase("writing", "".join(state["parts"])[-160:])
        elif kind == "agent_thought_chunk":
            if self._thinking_ok(job):
                job.set_phase("thinking", "")
        elif kind == "turn_completed":
            state["open_tools"] = set()
            # The turn could not have ended with a panel still up, so any ask
            # is answered even when grok wrote no completion for it.
            state["ask_questions"] = None
            state["turn_done"] = True

    def _apply_disk_tool(self, job, upd: dict, emit_event: bool):
        meta = ((upd.get("_meta") or {}).get("x.ai/tool") or {})
        if not isinstance(meta, dict):
            meta = {}
        name = (meta.get("name")
                or upd.get("title")
                or "tool")
        name = str(name)[:120]
        raw_input = upd.get("rawInput")
        if not isinstance(raw_input, dict):
            raw_input = meta.get("input") if isinstance(meta.get("input"), dict) else {}
        detail = _tool_detail(raw_input)
        title = upd.get("title")
        if not detail and isinstance(title, str) and title and title != name:
            detail = title[:200]
        kind = upd.get("kind") or meta.get("kind") or ""
        phase = _phase_for_tool(name, str(kind or ""))
        tid = str(upd.get("toolCallId") or "")
        seen = job.runner_state.setdefault("seen_tool_ids", set())
        if emit_event:
            if tid:
                if tid in seen:
                    emit_event = False
                else:
                    seen.add(tid)
            if emit_event:
                self._flush_text(job)
                job.add_event("tool", name=name, detail=detail or "")
        if name or detail:
            job.set_phase(phase, detail or name)

    def _apply_stream_tool(self, job, obj: dict):
        title = obj.get("title") or obj.get("name") or obj.get("tool")
        data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
        inp = obj.get("input") if isinstance(obj.get("input"), dict) else {}
        if not title and data:
            title = data.get("name") or data.get("title")
        if not inp and data:
            inp = data.get("input") if isinstance(data.get("input"), dict) else data
        title = str(title or "tool")[:120]
        detail = _tool_detail(inp)
        phase = _phase_for_tool(title, str(obj.get("kind") or ""))
        job.add_event("tool", name=title, detail=detail or "")
        job.set_phase(phase, detail or title)

    def _flush_text(self, job):
        state = job.runner_state
        parts = state.get("parts") or []
        if not parts:
            return
        text = "".join(parts).strip()
        state["parts"] = []
        if text:
            job.add_event("text", text=text, blocks=markdown_to_blocks(text))
