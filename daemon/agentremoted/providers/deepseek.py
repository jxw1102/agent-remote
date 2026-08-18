"""DeepSeek Harness provider: talks to `dsh web` on localhost.

DeepSeek's product UI is a local web app (`npx @deepseek-ai/dsh web`, default
http://127.0.0.1:3080). There is no official TUI. This adapter is a client of
that host's `/api` RPC (same contract the official browser uses):

    session.list / search / history / create / prompt / cancel / models

The phone never speaks to :3080. agentremoted stays the authenticated front.

`run_alternate` owns every turn — JobManager never spawns a `dsh` subprocess.
Stop maps to `session.cancel`. Continue reuses the session id (the host
resumes a cold session automatically).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .. import providers
from .. import search_util
from ..render_blocks import markdown_to_blocks
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


def _is_user_event(kind: str) -> bool:
    k = kind.lower()
    return k in ("user/message", "user_message", "user") or k.endswith("/user/message")


def _is_assistant_event(kind: str) -> bool:
    k = kind.lower()
    return k in ("assistant/message", "assistant_message", "assistant") or (
        "assistant/message" in k and "chunk" not in k)


def _is_tool_event(kind: str) -> bool:
    k = kind.lower()
    return "tool" in k and "chunk" not in k


def _tool_name(ev: dict) -> str:
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    for src in (ev, data, data.get("tool") if isinstance(data.get("tool"), dict) else {}):
        if not isinstance(src, dict):
            continue
        for key in ("name", "toolName", "tool", "id"):
            val = src.get(key)
            if isinstance(val, str) and val.strip() and val not in (
                    "tool", "tool_use", "tool_result"):
                return val.strip()[:80]
    return "tool"


class DeepseekStore:
    """Session list/history via dsh web RPC (not a filesystem walk)."""

    supports_steps = True

    def __init__(self, config=None, client=None):
        self.config = config
        self.client = client or _client_for(config)
        self.titler = None

    def _list_raw(self) -> list:
        try:
            data = self.client.call("session.list", {})
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
            data = self.client.call("session.search", {"query": q})
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
            hist = self.client.call("session.history", {
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
                page = self.client.call("session.history", payload)
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

    def get_messages(self, session_id, offset=None, limit=50, steps=False):
        sid = str(session_id or "").strip()
        if not sid:
            return None
        raw = self._history_pages(sid, max_messages=400)
        if raw is None:
            return None
        built = []
        pending_steps = []
        for entry in raw:
            ev = _unwrap_event(entry)
            kind = _event_type(ev)
            text = _event_text(ev).strip()
            ts = _event_ts(ev)
            uuid = str(ev.get("id") or ev.get("uuid") or ev.get("seq") or "")
            if _is_user_event(kind) and text:
                built.append({
                    "uuid": uuid, "role": "user", "ts": ts, "text": text,
                    "blocks": markdown_to_blocks(text),
                    "steps": pending_steps or None,
                })
                pending_steps = []
                continue
            if _is_assistant_event(kind) and text:
                msg = {
                    "uuid": uuid, "role": "assistant", "ts": ts, "text": text,
                    "blocks": markdown_to_blocks(text),
                }
                if pending_steps:
                    msg["steps"] = pending_steps
                    pending_steps = []
                built.append(msg)
                continue
            if steps and _is_tool_event(kind):
                pending_steps.append({
                    "kind": "tool_use",
                    "name": _tool_name(ev),
                    "detail": (text or "")[:200],
                    "preview": text[:512] if text else "",
                    "ok": True,
                    "recorded": True,
                    "truncated": False,
                    "ref": uuid,
                    "ts": ts,
                    "bytes": len(text or ""),
                    "lang": "",
                })
        if pending_steps and built:
            last = built[-1]
            extra = list(last.get("steps") or []) + pending_steps
            last["steps"] = extra
        total = len(built)
        lim = min(max(int(limit or 50), 1), 500)
        if offset is None:
            start = max(0, total - lim)
        else:
            start = max(0, int(offset))
        window = built[start:start + lim]
        if not steps:
            for m in window:
                m.pop("steps", None)
        return {
            "session_id": sid,
            "total": total,
            "offset": start,
            "messages": window,
        }


class DeepseekRunner:
    name = "deepseek"

    def __init__(self, config):
        self.config = config
        self.client = _client_for(config)

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

    def auth_health(self) -> dict:
        url = self.client.base
        parsed = urlparse(url)
        detail = "dsh web at %s" % url
        try:
            self.client.call("session.list", {}, timeout=4)
            status = "ok"
        except DshError as e:
            status = "missing"
            detail = str(e)
        cred = ""
        home = Path(str(getattr(self.config, "dsh_home", "") or "~/.dsh")).expanduser()
        if (home / ".credentials.yaml").is_file() or (
                home / ".env").is_file() or os.environ.get("DEEPSEEK_API_KEY"):
            cred = "key"
        return {
            "cli": "dsh",
            "cli_on_path": True,
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
            self.client.call("session.cancel", {"sessionId": sid}, timeout=8)
        except DshError as e:
            log.info("dsh session.cancel: %s", e)

    def _run_turn(self, job):
        sid = (job.session_id or "").strip()
        cwd = (job.cwd or "").strip()
        try:
            if not sid:
                created = self.client.call("session.create",
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
                    self.client.call("session.selectModel", sel)
                except DshError as e:
                    log.info("dsh selectModel ignored: %s", e)
            self.client.call("session.prompt", {
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
                page = self.client.call("session.history", {
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
                    assistant = _event_text(ev)
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
                items = self.client.call("session.list", {})
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
