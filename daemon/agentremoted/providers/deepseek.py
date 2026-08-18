"""DeepSeek Harness provider: talks to `dsh web` on localhost.

DeepSeek's product UI is a local web app (`npx @deepseek-ai/dsh web`, default
http://127.0.0.1:3080). There is no official TUI. This adapter is a client of
that host's `/api` RPC (same contract the official browser uses):

    session.list / search / history / create / prompt / cancel / models

The phone never speaks to :3080. agentremoted stays the authenticated front.

If `dsh web` is already answering on the configured loopback URL, we use it.
Otherwise the daemon starts ``dsh web --host 127.0.0.1 --port …`` (see
`dsh_host.DshHost`). A remote `dsh_url` is adopt-only.

`run_alternate` owns every turn — JobManager never spawns a per-turn `dsh`
subprocess. Stop maps to `session.cancel`. Continue reuses the session id
(the host resumes a cold session automatically).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .. import providers
from .. import search_util
from .. import steps as steps_mod
from ..render_blocks import markdown_to_blocks
from .dsh_host import DshHost
from .dsh_rpc import DEFAULT_URL, DshClient, DshError

log = logging.getLogger(__name__)

_MAX_PREVIEW = 200
_MAX_TITLE = 80
_POLL_S = 0.45
_DEFAULT_MODELS = ("deepseek-v4-pro", "deepseek-v4-flash")


def _client_for(config) -> DshClient:
    url = str(getattr(config, "dsh_url", "") or DEFAULT_URL).strip()
    return DshClient(url)


def _munge_cwd(cwd: str) -> str:
    raw = (cwd or "").strip().rstrip("/")
    if not raw:
        return "no-project"
    return raw.replace("/", "-").replace(".", "-").replace(" ", "-")


def _iso(ts) -> str:
    try:
        n = float(ts)
    except (TypeError, ValueError):
        return ""
    if n > 1e12:
        n = n / 1000.0
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, ValueError):
        return ""


def _title_from_row(row: dict) -> str:
    proj = row.get("projections") if isinstance(row, dict) else None
    values = (proj or {}).get("values") if isinstance(proj, dict) else None
    if isinstance(values, dict):
        title = values.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()[:_MAX_TITLE]
        if isinstance(title, dict):
            t = str(title.get("title") or title.get("text") or "").strip()
            if t:
                return t[:_MAX_TITLE]
    return ""


def _unwrap_event(entry) -> dict:
    if not isinstance(entry, dict):
        return {}
    ev = entry.get("event")
    return ev if isinstance(ev, dict) else entry


def _event_type(ev: dict) -> str:
    return str(ev.get("type") or ev.get("kind") or ev.get("name") or "")


def _blocks_text(blocks) -> str:
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    parts = []
    for b in blocks:
        if isinstance(b, str):
            parts.append(b)
        elif isinstance(b, dict):
            if b.get("type") in (None, "text") and b.get("text"):
                parts.append(str(b.get("text")))
            elif b.get("text"):
                parts.append(str(b.get("text")))
    return "".join(parts)


def _event_text(ev: dict) -> str:
    if not ev:
        return ""
    for key in ("text", "content"):
        val = ev.get(key)
        got = _blocks_text(val)
        if got:
            return got
    data = ev.get("data")
    if isinstance(data, dict):
        for key in ("text", "content", "message"):
            got = _blocks_text(data.get(key))
            if got:
                return got
        msg = data.get("message")
        if isinstance(msg, dict):
            got = _blocks_text(msg.get("content") or msg.get("text"))
            if got:
                return got
    msg = ev.get("message")
    if isinstance(msg, dict):
        got = _blocks_text(msg.get("content") or msg.get("text"))
        if got:
            return got
    if isinstance(msg, str):
        return msg
    return ""


def _event_ts(ev: dict) -> str:
    for key in ("ts", "timestamp", "at", "createdAt"):
        if ev.get(key) is not None:
            iso = _iso(ev.get(key))
            if iso:
                return iso
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    if data.get("ts") is not None:
        return _iso(data.get("ts"))
    return ""


# dsh injects harness content into the transcript as user-role events the human
# never typed: <system-reminder> blocks (background-task completions, hook
# output, ambient notes) and <task-notification>s. Strip them so the phone only
# shows what the user actually said. Same role as claude.py's _human_user_text
# and grok.py's _human_text — keep all three in step.
_INJECTED_TAGS = ("system-reminder", "task-notification", "system-warning",
                  "monitor-event")
_TAG_ALT = "|".join(_INJECTED_TAGS)
# The opening tag may carry attributes (<monitor-event task_id="...">), which a
# bare-tag pattern would miss — the block then reads as a user prompt.
_INJECTED_BLOCK_RE = re.compile(
    r"<(%s)(?:\s[^>]*)?>.*?</\1>" % _TAG_ALT, re.S)
# A chunk split mid-block leaves an opening tag with no closer.
_INJECTED_OPEN_RE = re.compile(r"<(?:%s)(?:\s[^>]*)?>" % _TAG_ALT)


def _clean_user_text(text: str) -> str:
    """Human-typed part of a user event ("" = pure injection)."""
    text = text.strip()
    if "<" not in text:
        return text
    text = _INJECTED_BLOCK_RE.sub("", text)
    # An unclosed block (an event split mid-injection) would otherwise leak its
    # head: drop everything from the opening tag on.
    m = _INJECTED_OPEN_RE.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()


def _is_user_event(kind: str) -> bool:
    k = kind.lower()
    return k in ("user/message", "user_message", "user") or k.endswith("/user/message")


def _is_assistant_event(kind: str) -> bool:
    k = kind.lower()
    return k in ("assistant/message", "assistant_message", "assistant") or (
        "assistant/message" in k and "chunk" not in k)


def _is_tool_result_event(kind: str) -> bool:
    return kind.lower() == "tool/result"


def _tool_name(ev: dict) -> str:
    """Tool name on a tool/start (or similar) event, if any."""
    if not isinstance(ev, dict):
        return ""
    for key in ("name", "tool", "toolName"):
        val = ev.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for key in ("name", "tool", "toolName"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _content_blocks(ev: dict) -> list:
    """Content-block list of a message-producing event, or []."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    msg = data.get("message") if isinstance(data, dict) else None
    if isinstance(msg, dict):
        content = msg.get("content")
    else:
        content = data.get("content")
    return content if isinstance(content, list) else []


def _text_blocks_only(blocks: list) -> str:
    """The user-visible `text` blocks of an assistant message, joined."""
    parts = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            parts.append(str(b["text"]))
    return "".join(parts)


def _tool_result_parts(ev: dict):
    """(body_text, is_error) for a `tool/result` event, or (None, False)."""
    blocks = _content_blocks(ev)
    if not blocks:
        return None, False
    b = blocks[0]
    if isinstance(b, str):
        return b, False
    if not isinstance(b, dict):
        return None, False
    is_err = bool(b.get("isError"))
    c = b.get("content")
    body = _blocks_text(c) if c is not None else str(b.get("text") or "")
    return body, is_err


def _tool_result_call_id(ev: dict) -> str:
    """callId a tool/result correlates with (for lang on the result body)."""
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    msg = data.get("message") if isinstance(data, dict) else None
    if isinstance(msg, dict):
        src = msg.get("source")
        if isinstance(src, dict):
            return str(src.get("callId") or "")
    return ""


def _tool_detail(raw, name: str = "") -> str:
    """One short single-line snippet for a tool-call step's detail row."""
    obj = raw if isinstance(raw, dict) else None
    if obj is None and isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except (TypeError, ValueError, json.JSONDecodeError):
                obj = None
    if not isinstance(obj, dict):
        return ""
    for key in ("description", "command", "target_directory", "target_file",
                "path", "file", "query", "pattern", "url"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return " ".join(val.split())[:200]
    return ""


class DeepseekStore:
    """Session list/history via dsh web RPC (not a filesystem walk)."""

    supports_steps = True

    def __init__(self, config=None, client=None, host=None):
        self.config = config
        self.host = host
        if self.host is None and client is None and config is not None:
            self.host = DshHost(config)
        self.client = client or (self.host.client if self.host
                                 else _client_for(config))
        self.titler = None

    def _call(self, method, payload=None, timeout=None):
        if self.host is not None:
            return self.host.call(method, payload, timeout=timeout)
        return self.client.call(method, payload, timeout=timeout)

    def _list_raw(self) -> list:
        try:
            data = self._call("session.list", {})
        except DshError as e:
            log.warning("dsh session.list: %s", e)
            return []
        if isinstance(data, dict):
            items = data.get("items") or data.get("sessions") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        return [r for r in items if isinstance(r, dict)]

    def _sid(self, row: dict) -> str:
        return str(row.get("sessionId") or row.get("id") or "").strip()

    def _is_user_row(self, row: dict) -> bool:
        if row.get("origin") == "subagent":
            return False
        if row.get("parentSessionId"):
            return False
        if row.get("blank") is True:
            return False
        return True

    def _summary(self, row: dict, last_text: str = "") -> dict:
        sid = self._sid(row)
        cwd = str(row.get("cwd") or "").strip()
        title = _title_from_row(row)
        updated = row.get("updatedAt") or row.get("updated_at") or 0
        return {
            "id": sid,
            "project_id": _munge_cwd(cwd),
            "cwd": cwd,
            "git_branch": "",
            "title": title or (last_text[:_MAX_TITLE] if last_text else ""),
            "started": _iso(updated),
            "last_active": _iso(updated),
            "last_role": "",
            "last_text": last_text[:_MAX_PREVIEW],
            "model": "",
            "size_bytes": 0,
            "provider": "deepseek",
            "running": bool(row.get("running")),
        }

    def list_projects(self) -> list:
        projects = {}
        for row in self._list_raw():
            if not self._is_user_row(row):
                continue
            cwd = str(row.get("cwd") or "").strip()
            pid = _munge_cwd(cwd)
            updated = 0.0
            try:
                updated = float(row.get("updatedAt") or 0)
                if updated > 1e12:
                    updated = updated / 1000.0
            except (TypeError, ValueError):
                pass
            entry = projects.get(pid)
            if entry is None:
                projects[pid] = {
                    "id": pid,
                    "cwd": cwd,
                    "name": (os.path.basename(cwd.rstrip("/")) or "/") if cwd
                            else "(no project)",
                    "session_count": 1,
                    "last_active": updated,
                }
            else:
                entry["session_count"] += 1
                entry["last_active"] = max(entry["last_active"], updated)
        out = list(projects.values())
        out.sort(key=lambda p: p["last_active"], reverse=True)
        return out

    def list_sessions(self, project_id=None, limit=25, user_only=True) -> list:
        want = str(project_id or "").strip()
        rows = []
        for row in self._list_raw():
            if user_only and not self._is_user_row(row):
                continue
            cwd = str(row.get("cwd") or "").strip()
            if want and _munge_cwd(cwd) != want:
                continue
            rows.append(self._summary(row))
        rows.sort(key=lambda r: r.get("last_active") or "", reverse=True)
        return rows[: max(1, int(limit or 25))]

    def search_sessions(self, query, project_id=None, limit=25,
                        user_only=True) -> list:
        q = search_util.normalize_query(query)
        if not q:
            return []
        try:
            data = self._call("session.search", {"query": q})
        except DshError as e:
            log.warning("dsh session.search: %s", e)
            return []
        items = []
        if isinstance(data, dict):
            items = data.get("items") or []
        listed = {self._sid(r): r for r in self._list_raw()}
        want = str(project_id or "").strip()
        out = []
        for hit in items:
            if not isinstance(hit, dict):
                continue
            sid = str(hit.get("sessionId") or hit.get("id") or "").strip()
            row = listed.get(sid) or {"sessionId": sid}
            if user_only and listed.get(sid) and not self._is_user_row(listed[sid]):
                continue
            cwd = str(row.get("cwd") or "").strip()
            if want and _munge_cwd(cwd) != want:
                continue
            summary = self._summary(row, str(hit.get("snippet") or ""))
            summary["snippet"] = str(hit.get("snippet") or "")[:400]
            out.append(summary)
            if len(out) >= max(1, int(limit or 25)):
                break
        return out

    def get_session(self, session_id: str):
        sid = str(session_id or "").strip()
        if not sid:
            return None
        for row in self._list_raw():
            if self._sid(row) == sid:
                return self._summary(row)
        # Direct history still resolves a session the list hid (subagent).
        try:
            hist = self._call("session.history", {
                "sessionId": sid, "maxMessages": 1,
            })
        except DshError:
            return None
        if isinstance(hist, dict):
            return self._summary({"sessionId": sid})
        return None

    def _history_pages(self, session_id: str, max_messages=80):
        """Newest-first pages of raw events, then chronological."""
        events = []
        before = None
        remaining = max(1, int(max_messages))
        for _ in range(12):
            payload = {"sessionId": session_id, "maxMessages": min(remaining, 40)}
            if before is not None:
                payload["beforeSeq"] = before
            try:
                page = self._call("session.history", payload)
            except DshError as e:
                if e.code == "session-not-found":
                    return None
                log.warning("dsh session.history: %s", e)
                break
            if not isinstance(page, dict):
                break
            batch = page.get("events") or []
            if not batch:
                if not events:
                    return []
                break
            events = list(batch) + events
            remaining -= len(batch)
            if not page.get("hasMore") or remaining <= 0:
                break
            first = _unwrap_event(batch[0])
            seq = first.get("seq")
            if seq is None:
                break
            before = seq
        return events

    def _find_event(self, session_id, seq):
        """Locate one event by its seq, re-reading enough history pages."""
        try:
            want = int(seq)
        except (TypeError, ValueError):
            return None
        raw = self._history_pages(session_id, max_messages=400)
        if not raw:
            return None
        for entry in raw:
            ev = _unwrap_event(entry)
            if ev.get("seq") == want:
                return ev
        return None

    def get_step(self, session_id: str, ref: str):
        """Full text behind one truncated step.

        `ref` is "<seq>:<blockIndex>" — the block index matters because an
        assistant message can hold several tool-call / reasoning blocks. dsh is
        RPC-backed (no transcript file), so this re-reads history and
        re-extracts the block the same way get_messages did.
        """
        if not ref or ":" not in ref:
            return None
        seq_str, _, idx_str = ref.partition(":")
        try:
            index = int(idx_str)
        except ValueError:
            return None
        ev = self._find_event(session_id, seq_str)
        if ev is None:
            return None
        kind = _event_type(ev)
        if _is_tool_result_event(kind):
            body, _ = _tool_result_parts(ev)
            text = steps_mod.format_tool_result(body or "")
        else:
            blocks = _content_blocks(ev)
            if index < 0 or index >= len(blocks):
                return None
            b = blocks[index]
            if not isinstance(b, dict):
                return None
            bt = b.get("type")
            if bt == "tool-call":
                text = steps_mod.format_tool_use(
                    b.get("name") or "tool", b.get("arguments"))
            elif bt == "reasoning":
                text = b.get("text") or ""
            else:
                text = b.get("text") or ""
        if text is None:
            return None
        return {"ref": ref, "text": text, "bytes": len(text)}

    def get_messages(self, session_id, offset=None, limit=50, steps=False):
        sid = str(session_id or "").strip()
        if not sid:
            return None
        raw = self._history_pages(sid, max_messages=400)
        if raw is None:
            return None
        built = []
        step_rows = []      # (pos, step)
        # tool-call id -> (name, path) so a later tool/result can pick a lang.
        tool_meta = {} if steps else None
        pos = 0
        for entry in raw:
            ev = _unwrap_event(entry)
            kind = _event_type(ev)
            seq = ev.get("seq")
            ts = _event_ts(ev)
            uuid = str(ev.get("id") or ev.get("uuid") or seq or "")
            if _is_user_event(kind):
                text = _clean_user_text(_event_text(ev))
                if not text:
                    continue
                built.append({
                    "uuid": uuid, "role": "user", "ts": ts, "text": text,
                    "blocks": markdown_to_blocks(text), "_pos": pos,
                })
                pos += 1
                continue
            if _is_assistant_event(kind):
                blocks = _content_blocks(ev)
                text = _text_blocks_only(blocks)
                if not text and not blocks:
                    # Flat event (tests / legacy history) with no content blocks.
                    text = _event_text(ev)
                if not text and not (steps and blocks):
                    continue
                msg = {
                    "uuid": uuid, "role": "assistant", "ts": ts,
                    "text": text, "blocks": markdown_to_blocks(text),
                    "_pos": pos,
                }
                built.append(msg)
                if steps and blocks:
                    for i, b in enumerate(blocks):
                        if not isinstance(b, dict):
                            continue
                        bt = b.get("type")
                        if bt == "tool-call":
                            cid = str(b.get("id") or "")
                            name = b.get("name") or "tool"
                            args = b.get("arguments")
                            path = steps_mod.path_from_input(args)
                            if cid:
                                tool_meta[str(cid)] = (name, path)
                            full = steps_mod.format_tool_use(name, args)
                            lang = steps_mod.lang_for_tool_use(name, args)
                            step_rows.append((pos, steps_mod.tool_use(
                                "%s:%d" % (seq, i), ts, name,
                                _tool_detail(args, name), full, lang=lang)))
                        elif bt == "reasoning":
                            step_rows.append((pos, steps_mod.thinking(
                                "%s:%d" % (seq, i), ts, b.get("text"))))
                pos += 1
                continue
            if steps and _is_tool_result_event(kind):
                body, is_err = _tool_result_parts(ev)
                if body is None:
                    body = ""
                cid = _tool_result_call_id(ev)
                name, path = "", ""
                if tool_meta is not None and cid:
                    name, path = tool_meta.get(str(cid), ("", ""))
                full = steps_mod.format_tool_result(body, name=name)
                lang = steps_mod.lang_for_tool_result(name, path, full)
                # Attach to the assistant message that made the call, not the
                # one that follows it — the result belongs to its tool_use.
                at = (built[-1]["_pos"] if built else 0)
                step_rows.append((at, steps_mod.tool_result(
                    "%s:0" % seq, ts, not is_err, full, lang=lang)))
        total = len(built)
        lim = min(max(int(limit or 50), 1), 500)
        if offset is None:
            start = max(0, total - lim)
        else:
            start = max(0, int(offset))
        window = built[start:start + lim]
        if steps:
            steps_mod.attach(window, step_rows)
        for m in built:
            m.pop("_pos", None)
        return {
            "session_id": sid,
            "total": total,
            "offset": start,
            "messages": window,
        }


class DeepseekRunner:
    name = "deepseek"

    def __init__(self, config, host=None, client=None):
        self.config = config
        self.host = host
        if self.host is None and client is None:
            self.host = DshHost(config)
        self.client = client or (self.host.client if self.host
                                 else _client_for(config))

    def ensure_host(self) -> bool:
        """Adopt or start dsh web. Called once at daemon boot."""
        if self.host is None:
            return True
        return self.host.ensure()

    def capabilities(self) -> dict:
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
            "can_show_usage": False,
            "interactive": False,
            "live_tui": False,
            "rewind": False,
        }

    def _call(self, method, payload=None, timeout=None):
        if self.host is not None:
            return self.host.call(method, payload, timeout=timeout)
        return self.client.call(method, payload, timeout=timeout)

    def auth_health(self) -> dict:
        # Ping must stay cheap: probe only, and kick a background start if
        # the host is down. Turns / session list wait on ensure().
        url = self.client.base
        parsed = urlparse(url)
        detail = "dsh web at %s" % url
        try:
            self.client.call("session.list", {}, timeout=4)
            status = "ok"
            if self.host is not None and self.host.source == "managed":
                detail = "dsh web at %s (managed)" % url
            elif self.host is not None and self.host.source == "external":
                detail = "dsh web at %s (external)" % url
        except DshError as e:
            status = "missing"
            detail = str(e)
            if self.host is not None:
                self.host.ensure_bg()
                if self.host.last_error:
                    detail = self.host.last_error
        cred = ""
        home = Path(str(getattr(self.config, "dsh_home", "") or "~/.dsh")).expanduser()
        if (home / ".credentials.yaml").is_file() or (
                home / ".env").is_file() or os.environ.get("DEEPSEEK_API_KEY"):
            cred = "key"
        on_path = True
        if self.host is not None:
            on_path = bool(self.host.resolve_bin())
        else:
            raw = str(getattr(self.config, "dsh_bin", "") or "dsh").strip() or "dsh"
            on_path = bool(shutil.which(raw) or shutil.which("dsh"))
        return {
            "cli": "dsh",
            "cli_on_path": on_path,
            "mode": "api_key" if cred else "unknown",
            "status": status,
            "detail": detail,
            "host": parsed.hostname or "",
        }

    def models(self) -> list:
        extras = list(getattr(self.config, "models", None) or [])
        found = []
        try:
            data = self.client.call("llm.models", {})
            groups = []
            if isinstance(data, dict):
                groups = data.get("groups") or data.get("models") or []
            if isinstance(groups, list):
                for g in groups:
                    if isinstance(g, str):
                        found.append(g)
                    elif isinstance(g, dict):
                        for m in (g.get("models") or []):
                            if isinstance(m, str):
                                found.append(m)
                            elif isinstance(m, dict) and m.get("id"):
                                found.append(str(m["id"]))
        except DshError:
            pass
        out = []
        for name in list(found) + list(_DEFAULT_MODELS) + [str(x) for x in extras]:
            if name and name not in out:
                out.append(name)
        return out

    def efforts(self) -> list:
        extras = list(getattr(self.config, "efforts", None) or [])
        base = ["low", "medium", "high"]
        out = []
        for name in base + [str(x) for x in extras]:
            if name and name not in out:
                out.append(name)
        return out

    def slash_commands(self) -> list:
        extra = list(getattr(self.config, "slash_commands", None) or [])
        return [str(x) for x in extra if x]

    def run_alternate(self, job, mode) -> bool:
        """Every DeepSeek turn goes through dsh web, any exec mode."""
        self._run_turn(job)
        return True

    def resume_alternate(self, job) -> None:
        """Re-attach after a daemon restart: watch until dsh reports idle."""
        sid = job.new_session_id or job.session_id
        if not sid:
            job.status = "error"
            job.error = "no session to resume"
            return
        self._watch(job, sid, after_text="")

    def cancel_job(self, job) -> None:
        sid = job.new_session_id or job.session_id
        if not sid:
            return
        try:
            self._call("session.cancel", {"sessionId": sid}, timeout=8)
        except DshError as e:
            log.info("dsh session.cancel: %s", e)

    def _run_turn(self, job):
        sid = (job.session_id or "").strip()
        cwd = (job.cwd or "").strip()
        try:
            if not sid:
                created = self._call("session.create",
                                     {"cwd": cwd} if cwd else {})
                if isinstance(created, dict):
                    sid = str(created.get("sessionId") or "")
                elif isinstance(created, str):
                    sid = created
                if not sid:
                    raise DshError("session.create returned no id")
                job.new_session_id = sid
            else:
                job.new_session_id = sid
            if job.model:
                sel = {"sessionId": sid, "provider": "deepseek-official",
                       "model": job.model}
                if job.effort:
                    sel["reasoningEffort"] = job.effort
                try:
                    self._call("session.selectModel", sel)
                except DshError as e:
                    log.info("dsh selectModel ignored: %s", e)
            self._call("session.prompt", {
                "sessionId": sid,
                "mode": "steer",
                "content": [{"type": "text", "text": job.prompt or ""}],
            })
        except DshError as e:
            job.status = "error"
            job.error = str(e)
            job.add_event("error", text=str(e))
            return
        job.status = "running"
        job.set_phase("working", "DeepSeek")
        self._watch(job, sid, after_text=job.prompt or "")

    def _watch(self, job, sid: str, after_text: str):
        timeout = float(getattr(self.config, "turn_timeout", 1800) or 1800)
        deadline = time.time() + timeout if timeout > 0 else 0
        seen_assistant = ""
        saw_turn_end = False
        idle_ticks = 0
        while True:
            if job.status == "stopped":
                self.cancel_job(job)
                return
            if deadline and time.time() > deadline:
                self.cancel_job(job)
                job.status = "error"
                job.error = "turn timed out"
                return
            try:
                page = self._call("session.history", {
                    "sessionId": sid, "maxMessages": 30,
                })
            except DshError as e:
                if e.code == "session-not-found":
                    job.status = "error"
                    job.error = str(e)
                    return
                time.sleep(_POLL_S)
                continue
            events = (page or {}).get("events") or [] if isinstance(page, dict) else []
            assistant = ""
            running_hint = False
            for entry in events:
                ev = _unwrap_event(entry)
                kind = _event_type(ev)
                if _is_assistant_event(kind):
                    # Streaming copy shows the user-visible text only — the
                    # reasoning blocks stay out of the live transcript; they
                    # belong in process view as thinking steps, same as
                    # get_messages does. Fall back to the flat text for
                    # block-less (legacy/test) events.
                    blocks = _content_blocks(ev)
                    assistant = _text_blocks_only(blocks) if blocks \
                        else _event_text(ev)
                if kind in ("turn/end", "turn_end", "turn/completed"):
                    saw_turn_end = True
                if kind in ("turn/start", "tool/start", "tool_call"):
                    running_hint = True
                    name = _tool_name(ev)
                    if name and name != "tool":
                        job.set_phase("tool", name)
                        job.add_event("tool", name=name, detail=_event_text(ev)[:200])
            if assistant and assistant != seen_assistant:
                delta = assistant[len(seen_assistant):] if assistant.startswith(
                    seen_assistant) else assistant
                if delta.strip():
                    job.add_event("text", text=delta)
                    job.result_text = assistant
                    job.set_phase("writing", "")
                seen_assistant = assistant
            listed = None
            try:
                items = self._call("session.list", {})
                rows = (items or {}).get("items") if isinstance(items, dict) else items
                for row in rows or []:
                    if isinstance(row, dict) and str(row.get("sessionId") or "") == sid:
                        listed = row
                        break
            except DshError:
                listed = None
            running = bool(listed.get("running")) if listed else running_hint
            if listed and not listed.get("running") and (saw_turn_end or seen_assistant):
                job.status = "done"
                job.result_text = seen_assistant
                return
            if not running:
                idle_ticks += 1
                if idle_ticks >= 3 and (saw_turn_end or seen_assistant):
                    job.status = "done"
                    job.result_text = seen_assistant
                    return
            else:
                idle_ticks = 0
            time.sleep(_POLL_S)
