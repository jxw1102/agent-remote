"""Claude Code provider: session store + turn runner.

Claude Code stores one JSONL file per session under:

    ~/.claude/projects/<munged-cwd>/<session-uuid>.jsonl

where <munged-cwd> is the project working directory with path separators
and dots replaced by '-'. Each line is a JSON object; the interesting ones:

    {"type": "summary", "summary": "...", "leafUuid": "..."}
    {"type": "ai-title", "aiTitle": "...", "sessionId": "..."}
    {"type": "user",      "timestamp": ..., "sessionId": ..., "cwd": ...,
     "gitBranch": ..., "message": {"role": "user", "content": <str|blocks>}}
    {"type": "assistant", ..., "message": {"role": "assistant",
     "content": [{"type": "text"|"tool_use"|"thinking", ...}]}}

Everything here is defensive: unknown line types are skipped, malformed
lines are ignored, and content may be either a plain string or a block list.

Turns run as `claude -p --resume <id> --output-format stream-json`; the
runner parses the stream into job events. In non-bypass permission modes it
wires up the helper MCP tool (permission_mcp.py) so approval prompts reach
the phone, and pre-allows the host's own MCP servers so only edits and shell
commands ever ask.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from ..config import CONFIG_DIR
from .. import steps as steps_mod
from .. import titles
from ..render_blocks import inline_to_rich, markdown_to_blocks
from .. import providers
from .. import search_util

log = logging.getLogger(__name__)

# How much we read from the head/tail of a transcript when building the
# session list. Enough for metadata without parsing multi-MB files.
_HEAD_BYTES = 64 * 1024
_TAIL_BYTES = 64 * 1024
# Window for the "is this the user's own session" check (see
# ClaudeStore._is_user_session). Generous: the whole point is that anything
# shorter than this we can judge with certainty.
_PROVENANCE_BYTES = 256 * 1024
# ...and how long a just-touched transcript is given the benefit of the doubt,
# so a turn in flight is never missing from the list while we wait for its
# first reply to land on disk.
_FRESH_SECONDS = 300

_MAX_PREVIEW = 200
_MAX_TITLE = 60

# Fields worth surfacing when a tool use is shown or needs approval.
# Prefer human `description` (Bash / run_terminal_command almost always set
# it) over the raw `command` so the live-status banner stays short and
# readable; fall through to path/url/etc. for tools that never send one.
_DETAIL_KEYS = (
    "description", "command", "file_path", "path", "pattern", "url",
    "prompt", "query",
)

# Tool name -> what the agent is doing right now (live-status banner verb).
_PHASE_BY_TOOL = {
    "Edit": "editing",
    "Write": "editing",
    "MultiEdit": "editing",
    "NotebookEdit": "editing",
    "Read": "reading",
    "Grep": "searching",
    "Glob": "searching",
    "Bash": "running",
    "WebFetch": "browsing",
    "WebSearch": "browsing",
    "Task": "delegating",
    "Agent": "delegating",
    "TaskCreate": "planning",
    "TaskUpdate": "planning",
    "TaskList": "planning",
    "TodoWrite": "planning",
    "TodoRead": "planning",
}

# Claude Code task list tools. Current CLIs use TaskCreate/TaskUpdate and
# persist items under ~/.claude/tasks/<session-id>/*.json; older builds used
# TodoWrite with the full list only in the transcript tool input.
_TASK_TOOLS = frozenset({
    "TaskCreate", "TaskUpdate", "TaskList", "TodoWrite", "TodoRead",
})
_TASKS_ROOT = Path.home() / ".claude" / "tasks"
_TODO_JSONL_SCAN = 256 * 1024

def tool_detail(tool_input: dict, max_len: int = 200) -> str:
    """One short single-line snippet for the phone status banner / permission.

    Prefers a human `description` when the tool provided one (typical for
    Bash), else the first non-empty key in `_DETAIL_KEYS`. Collapses
    whitespace so a multi-line Bash `command` cannot expand the status
    strip into many wraps; then middle-ellipsis so head + tail stay
    readable (path prefix and filename, command verb and last args).
    """
    if not isinstance(tool_input, dict):
        return ""
    # Task tools: subject / status beats generic DETAIL_KEYS.
    for key in ("subject", "content", "status", "taskId", "activeForm"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            text = " ".join(val.split())
            if len(text) > max_len:
                text = text[: max_len - 1] + "…"
            return text
    todos = tool_input.get("todos")
    if isinstance(todos, list) and todos:
        return "%d todos" % len(todos)
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


def _task_status_norm(raw) -> str:
    s = str(raw or "pending").strip().lower()
    if s in ("completed", "complete", "done", "finished"):
        return "completed"
    if s in ("in_progress", "in-progress", "active", "doing", "running", "wip"):
        return "in_progress"
    if s in ("cancelled", "canceled", "dropped", "deleted"):
        return "cancelled"
    return "pending"


def _task_item_from_disk(obj: dict, fallback_id: str = "") -> dict:
    content = str(obj.get("subject") or obj.get("content") or "").strip()
    if not content:
        return None
    return {
        "id": str(obj.get("id") or fallback_id or ""),
        "content": content,
        "status": _task_status_norm(obj.get("status")),
        "activeForm": str(obj.get("activeForm") or ""),
        "description": str(obj.get("description") or ""),
    }


def _task_items_from_todowrite(todos) -> list:
    """Normalize TodoWrite `todos: [{content,status,activeForm}]` rows."""
    out = []
    if not isinstance(todos, list):
        return out
    for i, t in enumerate(todos):
        if not isinstance(t, dict):
            continue
        content = str(t.get("content") or t.get("subject") or "").strip()
        if not content:
            continue
        out.append({
            "id": str(t.get("id") or i + 1),
            "content": content,
            "status": _task_status_norm(t.get("status")),
            "activeForm": str(t.get("activeForm") or ""),
            "description": str(t.get("description") or ""),
        })
    return out


def load_claude_tasks(session_id: str, session_file: Path = None) -> list:
    """Current todo/task list for a Claude Code session.

    Prefer on-disk TaskCreate storage (~/.claude/tasks/<session_id>/*.json).
    Fall back to the last TodoWrite payload in the transcript for older
    sessions that never wrote the tasks directory.
    """
    if not session_id or not _is_safe_id(session_id):
        return []
    items = []
    tdir = _TASKS_ROOT / session_id
    if tdir.is_dir():
        for f in tdir.glob("*.json"):
            try:
                obj = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            item = _task_item_from_disk(obj, fallback_id=f.stem)
            if item:
                items.append(item)
        if items:
            def _sort_key(it):
                sid = it.get("id") or ""
                return (0, int(sid)) if str(sid).isdigit() else (1, str(sid))
            items.sort(key=_sort_key)
            return items
    if session_file is not None:
        return _todos_from_jsonl_tail(session_file)
    return []


def _todos_from_jsonl_tail(path: Path) -> list:
    """Last TodoWrite tool_use input in the transcript (legacy path)."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    try:
        with open(path, "rb") as f:
            if size > _TODO_JSONL_SCAN:
                f.seek(size - _TODO_JSONL_SCAN)
            chunk = f.read()
    except OSError:
        return []
    text = chunk.decode("utf-8", errors="replace")
    last = None
    for line in text.splitlines():
        if "TodoWrite" not in line:
            continue
        obj = _safe_json(line)
        if not isinstance(obj, dict):
            continue
        msg = obj.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "TodoWrite":
                todos = (block.get("input") or {}).get("todos")
                if isinstance(todos, list) and todos:
                    last = todos
    return _task_items_from_todowrite(last) if last else []


def format_todos_markdown(items: list):
    """(markdown_text, signature) matching grok's phone-friendly checklist."""
    rows, done, current = [], 0, ""
    for e in items or []:
        if not isinstance(e, dict):
            continue
        text = " ".join(str(e.get("content") or "").split())
        if not text:
            continue
        status = _task_status_norm(e.get("status"))
        if status == "completed":
            done += 1
            rows.append("- [x] " + text)
        elif status == "cancelled":
            rows.append("- [ ] ~~" + text + "~~")
        elif status == "in_progress":
            current = text
            rows.append("- [ ] **%s**" % text)
        else:
            rows.append("- [ ] " + text)
    if not rows:
        return "", ""
    sig = "\n".join(rows)
    head = "**Todo %d/%d**" % (done, len(rows))
    if current:
        head += " — " + current
    return head + "\n" + sig, sig


def emit_claude_todos(job, session_id: str = "", *, tool_input=None) -> bool:
    """Push a deduped markdown checklist onto the job when the list changes.

    ``tool_input`` with a TodoWrite-shaped ``todos`` list is used immediately
    (disk may lag or never exist for that format). Otherwise re-read
    ``~/.claude/tasks/<session_id>/``.
    """
    items = None
    if isinstance(tool_input, dict) and isinstance(tool_input.get("todos"), list):
        items = _task_items_from_todowrite(tool_input.get("todos"))
    sid = (session_id or getattr(job, "session_id", None)
           or getattr(job, "new_session_id", None)
           or (job.runner_state or {}).get("session_id") or "")
    if not items and sid:
        items = load_claude_tasks(str(sid))
    if not items:
        return False
    text, sig = format_todos_markdown(items)
    if not text:
        return False
    state = job.runner_state
    if state.get("todo_sig") == sig:
        return False
    state["todo_sig"] = sig
    # Same as grok: one markdown checklist text event (GFM checkboxes).
    job.add_event("text", text=text, blocks=markdown_to_blocks(text))
    current = ""
    for i in items:
        if _task_status_norm(i.get("status")) == "in_progress":
            current = i.get("activeForm") or i.get("content") or ""
            break
    if current:
        job.set_phase("planning", current[-160:])
    return True


# ------------------------------------------------------------- subscription usage
#
# The interactive TUI's /usage view is backed by an OAuth endpoint. Headless
# `claude -p` never surfaces it, so we call the same endpoint ourselves with
# the subscription's OAuth token and hand the phone ready-to-render buckets
# (title + percent + a reset line already formatted in the host's timezone,
# so the phone needs no date math — Qt 4.8 chokes on fractional-second ISO).

_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
_USAGE_HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",
    "anthropic-version": "2023-06-01",
    "Content-Type": "application/json",
    "User-Agent": "agentremoted",
}

# Live model catalog via the Anthropic Models API, reached with the same
# subscription OAuth token as the usage endpoint. Cached so /api/ping does not
# round-trip to Anthropic on every call. The picker offered concrete ids
# (`claude-opus-4-8`) rather than the CLI aliases (`opus`) so it can't lag a
# release the way the `opus` alias did in `claude -p`.
_MODELS_URL = "https://api.anthropic.com/v1/models?limit=100"
_MODELS_TTL_S = 3600
# The 1M-context beta tag `claude -p` accepts on --model; the Models API never
# emits it (it's a CLI construct), so we append it ourselves for 1M models.
_ONE_M_CONTEXT = 1_000_000
# (fetched_at, [{"id", "context"}]) — last good result, reused on any failure.
_models_cache = {"at": 0.0, "models": []}

# Usage cache: Anthropic rate-limits /api/oauth/usage aggressively (HTTP 429).
# Fresh TTL avoids re-hitting on every Usage sheet open; stale TTL still
# serves the last good bars when Anthropic is throttling.
_USAGE_TTL_S = 90
_USAGE_STALE_S = 30 * 60
# After a 429, do not call Anthropic until Retry-After (capped) elapses.
_USAGE_COOLDOWN_CAP_S = 60 * 60  # never sleep longer than 1h in our head
_usage_cache = {
    "at": 0.0,
    "buckets": [],
    "lock": threading.Lock(),
    "cooldown_until": 0.0,  # epoch: skip Anthropic until then
}


# ------------------------------------------------------------- session titles
#
# Claude Code's own session titles (ai-title / first user line) are often long
# or noisy on a phone. We summarize each session into a short mobile-friendly
# title with the cheap Haiku model and persist the session_id -> title map
# under ~/.agentremoted so it is computed rarely. Generation runs on a background
# worker so the sessions list never blocks on the API; the phone picks the
# nicer title up on its next poll.
#
# The title is regenerated on every context compaction: before the first
# compaction it names the first user message (stable); after each compaction it
# re-summarizes from that compaction's summary blob (the richest description of
# the session so far). The sig is keyed to the compaction *count*, so a title
# regenerates exactly once per compaction and stays stable in between.
# Titling machinery is shared with grok/codex — see ../titles.py. Kept under
# the old private names so this module's call sites read unchanged.
_TITLE_SIG_VERSION = titles.SIG_VERSION
_TITLE_MAX_CHARS = titles.MAX_CHARS
_clean_title = titles.clean_title


# OAuth token refresh, the same grant the CLI performs. When the cached access
# token in the CLI's credential store has expired we exchange the refresh
# token for a fresh one and write it back, so /usage and the models catalog
# keep working between interactive CLI runs (headless `claude -p` refreshes its
# own copy but may not touch the store the daemon reads).
#
# The store itself is platform-dependent: on Linux/WSL the CLI writes
# ~/.claude/.credentials.json; on macOS it keeps the same JSON blob in the
# login Keychain (generic password, service "Claude Code-credentials") and
# writes NO file at all — so a file-only reader finds nothing to sign in with
# and every OAuth feature (usage, models, titles, headless MCP) goes dark.
_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_TOKEN_SKEW_MS = 60_000  # refresh a touch early to dodge edge-of-expiry 401s

# Every real Claude OAuth token (setup-token or store accessToken) starts with
# this. A configured token that doesn't — a template placeholder like
# "PASTE-TOKEN-HERE", a truncated paste — must not shadow a valid sign-in in
# the credential store: Anthropic answers garbage bearers with long 429
# backoffs, which reads as "usage broken / no models" with no hint why.
_TOKEN_PREFIX = "sk-ant-"
_bad_env_token_warned = False


def _reject_bad_env_token(tok) -> str:
    """The configured token if it can possibly be real, else "" (warn once)."""
    global _bad_env_token_warned
    tok = str(tok or "").strip()
    if not tok:
        return ""
    if tok.startswith(_TOKEN_PREFIX):
        return tok
    if not _bad_env_token_warned:
        _bad_env_token_warned = True
        log.warning(
            "claude: CLAUDE_CODE_OAUTH_TOKEN in env/config is not a Claude "
            "token (expected %s…) — ignoring it and using the CLI's own "
            "sign-in instead", _TOKEN_PREFIX)
    return ""

_KEYCHAIN_SERVICE = "Claude Code-credentials"
# `security` blocks on a GUI unlock prompt when the login keychain is locked;
# a daemon must never hang on that, so give up quickly and report no sign-in.
_KEYCHAIN_TIMEOUT_S = 10


def _creds_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


def _keychain_enabled() -> bool:
    # AGENTREMOTED_NO_KEYCHAIN opts out (tests; hosts where the keychain is
    # locked and the `security` round-trip would only ever time out).
    return (sys.platform == "darwin"
            and not os.environ.get("AGENTREMOTED_NO_KEYCHAIN"))


def _keychain_read() -> dict:
    """The credentials blob from the macOS login Keychain, or {}."""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password",
             "-s", _KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=_KEYCHAIN_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    try:
        data = json.loads(proc.stdout.strip())
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _keychain_write(data: dict) -> None:
    """Update the Keychain item in place (-U), as the CLI itself does."""
    try:
        subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", _KEYCHAIN_SERVICE, "-a", getpass.getuser(),
             "-w", json.dumps(data)],
            capture_output=True, timeout=_KEYCHAIN_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        pass


def _read_creds():
    """(credentials blob, store) from wherever the CLI keeps it; ({}, "").

    Reads both the JSON file and (on macOS) the login Keychain, and picks
    whichever holds the fresher claudeAiOauth token: a machine that migrated
    between storage modes can be left with a stale copy in one store, and
    blindly preferring that one would break usage/models while a perfectly
    good sign-in sits in the other.
    """
    candidates = []
    try:
        data = json.loads(_creds_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            candidates.append((data, "file"))
    except (OSError, ValueError):
        pass
    if _keychain_enabled():
        data = _keychain_read()
        if data:
            candidates.append((data, "keychain"))
    if not candidates:
        return {}, ""

    def freshness(item):
        exp = (item[0].get("claudeAiOauth") or {}).get("expiresAt")
        return exp if isinstance(exp, (int, float)) else 0

    return max(candidates, key=freshness)


def _save_creds(data: dict, store: str) -> None:
    """Write refreshed tokens back to the store they came from."""
    if store == "keychain":
        _keychain_write(data)
    elif store == "file":
        _write_creds(_creds_path(), data)


def _refresh_oauth(refresh_token: str) -> dict:
    """Exchange a refresh token for a fresh access token. Returns the updated
    claudeAiOauth fields (accessToken plus, when present, refreshToken and a
    recomputed expiresAt), or {} on any failure."""
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": _OAUTH_CLIENT_ID,
    }).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_URL, data=body,
        headers={"Content-Type": "application/json", "User-Agent": "agentremoted"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    access = str(raw.get("access_token", "")).strip()
    if not access:
        return {}
    out = {"accessToken": access}
    new_refresh = str(raw.get("refresh_token", "")).strip()
    if new_refresh:
        out["refreshToken"] = new_refresh
    expires_in = raw.get("expires_in")
    if isinstance(expires_in, (int, float)):
        out["expiresAt"] = int(time.time() * 1000 + expires_in * 1000)
    return out


def _write_creds(path: Path, data: dict) -> None:
    """Atomically rewrite the credentials file, preserving 0600 perms."""
    tmp = path.parent / (path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(str(tmp), 0o600)
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass


def _oauth_token(config) -> str:
    """The subscription OAuth access token.

    Prefer an explicit long-lived token (`claude setup-token`) from the
    daemon's env/config. Otherwise read the interactive credential store
    (JSON file or macOS Keychain); if its access token has expired, exchange
    the stored refresh token for a fresh one and write the new tokens back
    (mirroring what the CLI does on each run), so /usage keeps working even
    when no job has run recently."""
    env = getattr(config, "claude_env", None) or {}
    tok = _reject_bad_env_token(env.get("CLAUDE_CODE_OAUTH_TOKEN")
                                or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
    if tok:
        return tok
    data, store = _read_creds()
    oauth = data.get("claudeAiOauth") or {}
    access = str(oauth.get("accessToken", "")).strip()
    expires_at = oauth.get("expiresAt")
    fresh = (isinstance(expires_at, (int, float))
             and time.time() * 1000 < expires_at - _TOKEN_SKEW_MS)
    if access and fresh:
        return access
    refresh_token = str(oauth.get("refreshToken", "")).strip()
    if refresh_token:
        updated = _refresh_oauth(refresh_token)
        if updated.get("accessToken"):
            oauth.update(updated)
            data["claudeAiOauth"] = oauth
            _save_creds(data, store)
            return str(updated["accessToken"])
    # Expired and could not refresh: hand back whatever we have so the caller's
    # 401 path shows "sign-in expired" rather than a misleading "no sign-in".
    return access


def _parse_reset(iso: str):
    if not iso:
        return None
    s = iso.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # Drop fractional seconds and retry (defensive against odd precision).
        s2 = re.sub(r"\.\d+", "", s)
        try:
            return datetime.fromisoformat(s2)
        except ValueError:
            return None


def _fmt_reset(iso: str) -> str:
    """Relative reset line matching Claude desktop /usage, e.g.
    "Resets in 1 hr 35 min" or "Resets in 23 hr 5 min"."""
    dt = _parse_reset(iso)
    if dt is None:
        return ""
    try:
        local = dt.astimezone()
        now = datetime.now(local.tzinfo)
        secs = int((local - now).total_seconds())
    except (OSError, ValueError, OverflowError, TypeError):
        return ""
    if secs <= 0:
        return "Resets soon"
    hours = secs // 3600
    mins = (secs % 3600) // 60
    # Desktop always uses the short units "hr" / "min" (no plurals).
    if hours and mins:
        return "Resets in %d hr %d min" % (hours, mins)
    if hours:
        return "Resets in %d hr" % hours
    if mins:
        return "Resets in %d min" % mins
    return "Resets soon"


def _limit_title(kind: str, scope) -> str:
    """Bucket labels aligned with Claude desktop "Your usage limits":
    "5-hour limit", "Weekly · all models", "Weekly · Fable"."""
    if kind in ("session", "five_hour"):
        return "5-hour limit"
    if kind in ("weekly_all", "seven_day"):
        return "Weekly \u00b7 all models"
    if kind in ("weekly_scoped", "seven_day_opus"):
        name = ""
        if isinstance(scope, dict):
            name = ((scope.get("model") or {}).get("display_name") or "").strip()
        if not name and kind == "seven_day_opus":
            name = "Opus"
        return "Weekly \u00b7 %s" % (name or "scoped model")
    return (kind or "usage").replace("_", " ").title()


def _clamp_pct(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _buckets_from_usage(raw: dict) -> list:
    """Flatten the endpoint's response into [{title, percent, resets_text,
    severity}] rows. Prefer the modern `limits` array; fall back to the flat
    five_hour / seven_day* fields on older responses."""
    buckets = []
    limits = raw.get("limits")
    if isinstance(limits, list):
        for lim in limits:
            if not isinstance(lim, dict) or lim.get("percent") is None:
                continue
            buckets.append({
                "title": _limit_title(lim.get("kind", ""), lim.get("scope")),
                "percent": _clamp_pct(lim.get("percent")),
                "resets_text": _fmt_reset(lim.get("resets_at", "")),
                "severity": str(lim.get("severity") or "normal"),
            })
    if buckets:
        return buckets
    # Older shape: named objects with a `utilization` float.
    for key, kind in (("five_hour", "five_hour"),
                      ("seven_day", "seven_day"),
                      ("seven_day_opus", "seven_day_opus")):
        b = raw.get(key)
        if isinstance(b, dict) and b.get("utilization") is not None:
            buckets.append({
                "title": _limit_title(kind, None),
                "percent": _clamp_pct(b.get("utilization")),
                "resets_text": _fmt_reset(b.get("resets_at", "")),
                "severity": "normal",
            })
    return buckets


def _fmt_retry_after(seconds) -> str:
    """Human wait line from Retry-After (seconds or HTTP-date)."""
    secs = None
    if seconds is None or seconds == "":
        return ""
    try:
        secs = int(float(str(seconds).strip()))
    except (TypeError, ValueError):
        # HTTP-date form — ignore, rare
        return ""
    if secs <= 0:
        return ""
    if secs < 90:
        return "Try again in about %d s." % secs
    mins = (secs + 30) // 60
    if mins < 90:
        return "Try again in about %d min." % max(1, mins)
    hours = (mins + 30) // 60
    return "Try again in about %d hr." % max(1, hours)


def _parse_retry_after_s(headers) -> int:
    if not headers:
        return 0
    try:
        ra = headers.get("Retry-After")
    except Exception:
        ra = None
    if ra is None or ra == "":
        return 300  # default 5 min cooldown if 429 without header
    try:
        return max(0, min(_USAGE_COOLDOWN_CAP_S, int(float(str(ra).strip()))))
    except (TypeError, ValueError):
        return 300


def account_identity(config=None) -> dict:
    """Stable Claude account labels for cross-host usage dedup.

    Prefer local ``~/.claude.json`` ``oauthAccount`` (no network). Returns
    ``{"account": email-or-label, "account_id": uuid}`` — either may be "".
    """
    email = ""
    account_id = ""
    display = ""
    try:
        data = json.loads(
            (Path.home() / ".claude.json").read_text(encoding="utf-8"))
        oa = data.get("oauthAccount") if isinstance(data, dict) else None
        if isinstance(oa, dict):
            email = str(oa.get("emailAddress") or oa.get("email") or "").strip()
            account_id = str(oa.get("accountUuid") or oa.get("uuid") or "").strip()
            display = str(oa.get("displayName") or "").strip()
        if not account_id and isinstance(data, dict):
            account_id = str(data.get("userID") or "").strip()
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    # API key mode: no subscription seat — still tag so hosts don't merge
    # incorrectly with OAuth seats.
    if not email and not account_id and config is not None:
        env = getattr(config, "claude_env", None) or {}
        api_key = str(env.get("ANTHROPIC_API_KEY")
                      or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if api_key:
            return {"account": "api-key", "account_id": "api-key"}
    account = email or display or account_id
    return {"account": account, "account_id": account_id or account}


def _with_identity(data: dict, config=None) -> dict:
    """Stamp provider + account fields onto a usage payload."""
    out = dict(data or {})
    out["provider"] = "claude"
    ident = account_identity(config)
    out["account"] = ident.get("account") or ""
    out["account_id"] = ident.get("account_id") or out["account"]
    return out


def _usage_from_cache(allow_stale: bool, force_any: bool = False) -> dict | None:
    """Return a copy of the cached ok response, or None if unusable.

    force_any: during Anthropic cooldown, serve whatever we have even if older
    than the normal stale window (better than empty bars).
    """
    with _usage_cache["lock"]:
        buckets = list(_usage_cache["buckets"] or [])
        at = float(_usage_cache["at"] or 0)
    if not buckets or at <= 0:
        return None
    age = time.time() - at
    if age <= _USAGE_TTL_S or (allow_stale and age <= _USAGE_STALE_S) or force_any:
        out = {"ok": True, "buckets": buckets, "cached": True, "cache_age_s": int(age)}
        return out
    return None


def _store_usage_cache(buckets: list) -> None:
    with _usage_cache["lock"]:
        _usage_cache["buckets"] = list(buckets or [])
        _usage_cache["at"] = time.time()
        _usage_cache["cooldown_until"] = 0.0


def _set_usage_cooldown(seconds: int) -> None:
    until = time.time() + max(0, int(seconds))
    with _usage_cache["lock"]:
        # Only extend, never shrink a longer cooldown already in effect.
        if until > float(_usage_cache.get("cooldown_until") or 0):
            _usage_cache["cooldown_until"] = until


def _usage_cooldown_remaining() -> int:
    with _usage_cache["lock"]:
        until = float(_usage_cache.get("cooldown_until") or 0)
    return max(0, int(until - time.time()))


def fetch_usage(config) -> dict:
    """Return {"ok": True, "buckets": [...]} or {"ok": False, "error": str}.

    Caches successful Anthropic responses. On HTTP 429 (Anthropic rate limit),
    honors Retry-After as a local cooldown (no further Anthropic calls), serves
    the last good snapshot when available, and phrases wait time in minutes.

    Always includes ``provider`` / ``account`` / ``account_id`` so multi-host
    clients can merge the same Claude seat across daemons.
    """
    # Fast path: fresh cache (multi /api/usage + repeated opens).
    cached = _usage_from_cache(allow_stale=False)
    if cached is not None:
        return _with_identity(cached, config)

    # Respect Anthropic cooldown — do not burn more 429s.
    cool = _usage_cooldown_remaining()
    if cool > 0:
        wait = _fmt_retry_after(cool)
        stale = _usage_from_cache(allow_stale=True, force_any=True)
        if stale is not None:
            stale["stale"] = True
            stale["error"] = (
                "Anthropic usage cooldown — showing last snapshot. %s" % wait
            ).strip()
            return _with_identity(stale, config)
        return _with_identity({
            "ok": False,
            "error": (
                "Anthropic rate-limited the usage API (HTTP 429). "
                "The daemon is fine. %s" % wait
            ).strip(),
        }, config)

    token = _oauth_token(config)
    if not token:
        stale = _usage_from_cache(allow_stale=True)
        if stale is not None:
            stale["error"] = "Using cached usage — no Claude sign-in for a refresh."
            return _with_identity(stale, config)
        return _with_identity({
            "ok": False,
            "error": "No Claude sign-in found — run `claude` on the Mac to log in.",
        }, config)
    headers = dict(_USAGE_HEADERS)
    headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(_USAGE_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return _with_identity({
                "ok": False,
                "error": "Claude sign-in expired — run `claude` on the Mac.",
            }, config)
        if e.code == 429:
            # Anthropic rate-limited this host — not agentremoted.
            cool_s = _parse_retry_after_s(getattr(e, "headers", None))
            _set_usage_cooldown(cool_s)
            wait = _fmt_retry_after(cool_s)
            log.warning(
                "claude usage: Anthropic 429; cooldown %ss (%s)",
                cool_s, wait,
            )
            stale = _usage_from_cache(allow_stale=True, force_any=True)
            if stale is not None:
                stale["stale"] = True
                stale["error"] = (
                    "Anthropic rate-limited usage — showing last snapshot. %s" % wait
                ).strip()
                return _with_identity(stale, config)
            return _with_identity({
                "ok": False,
                "error": (
                    "Anthropic rate-limited the usage API (HTTP 429). "
                    "The daemon is fine. %s" % wait
                ).strip(),
            }, config)
        log.warning("claude usage: HTTP %s", e.code)
        stale = _usage_from_cache(allow_stale=True)
        if stale is not None:
            stale["stale"] = True
            stale["error"] = "Usage refresh failed (HTTP %d); showing cache." % e.code
            return _with_identity(stale, config)
        return _with_identity({
            "ok": False,
            "error": "Usage request failed (HTTP %d)" % e.code,
        }, config)
    except (urllib.error.URLError, OSError) as e:
        stale = _usage_from_cache(allow_stale=True)
        if stale is not None:
            stale["stale"] = True
            stale["error"] = "Could not reach Anthropic; showing cache."
            return _with_identity(stale, config)
        return _with_identity({
            "ok": False,
            "error": "Could not reach Anthropic: %s" % e,
        }, config)
    except (json.JSONDecodeError, ValueError):
        return _with_identity({
            "ok": False,
            "error": "Unexpected usage response",
        }, config)

    buckets = _buckets_from_usage(raw)
    if buckets:
        _store_usage_cache(buckets)
    return _with_identity(
        {"ok": True, "buckets": buckets, "cached": False}, config)


def list_models(config) -> list:
    """Live models from the Anthropic Models API as [{"id", "context"}], in the
    API's order (newest first). Cached for _MODELS_TTL_S; returns the last good
    result (or [] if never fetched) on any failure so the picker never empties.
    """
    now = time.time()
    cached = _models_cache["models"]
    if cached and now - _models_cache["at"] < _MODELS_TTL_S:
        return list(cached)
    token = _oauth_token(config)
    if not token:
        return list(cached)
    headers = dict(_USAGE_HEADERS)
    headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(_MODELS_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            json.JSONDecodeError, ValueError):
        return list(cached)
    models = []
    for m in raw.get("data", []):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        try:
            ctx = int(m.get("max_input_tokens") or 0)
        except (TypeError, ValueError):
            ctx = 0
        models.append({"id": str(m["id"]), "context": ctx})
    if models:
        _models_cache["at"] = now
        _models_cache["models"] = models
    return list(models or cached)


def _title_source(compactions: int, first: str, last_summary: str):
    """Pick the (sig, api_text) pair for a session's current compaction count.

    No compaction yet: title the first user message — the sig is the hash of
    that (immutable) message, so an early title never churns. After N
    compactions: title the latest compaction summary blob (a full recap of the
    session so far); the sig is keyed to the compaction count, so it regenerates
    exactly once per compaction and is stable in between."""
    if compactions == 0 or not last_summary:
        digest = hashlib.sha1((first or "").encode("utf-8")).hexdigest()[:16]
        return "%s:0:%s" % (_TITLE_SIG_VERSION, digest), first
    return "%s:c%d" % (_TITLE_SIG_VERSION, compactions), last_summary


def summarize_title(config, text: str) -> str:
    """Public entry point for one-off retitling (server.py's regenerate button).

    Same Haiku call the background titler uses, run synchronously: the caller is
    a button press that wants a title back in the response. Works for any
    harness — the token is the Claude subscription either way.
    """
    return titles.summarize(config, text, _oauth_token)


def _safe_json(line: str):
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _content_blocks(message: dict) -> list:
    content = (message or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return []


# Claude Code injects harness content into the transcript as `user`-role
# lines the human never typed: <system-reminder> blocks, background
# <task-notification>s, hook output, slash-command envelopes. Strip them so
# the phone only shows what the user actually said.
_INJECTED_TAGS = ("system-reminder", "task-notification", "system-warning",
                  "user-prompt-submit-hook", "monitor-event")
_TAG_ALT = "|".join(_INJECTED_TAGS)
# The opening tag may carry attributes (<monitor-event task_id="...">), which
# a bare-tag pattern would miss — the block then reads as a user prompt.
_INJECTED_BLOCK_RE = re.compile(
    r"<(%s)(?:\s[^>]*)?>.*?</\1>" % _TAG_ALT, re.S)
_INJECTED_OPEN_RE = re.compile(r"<(?:%s)(?:\s[^>]*)?>" % _TAG_ALT)
_COMMAND_NAME_RE = re.compile(r"<command-name>(.*?)</command-name>", re.S)
_COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
_LOCAL_STDOUT_RE = re.compile(
    r"<local-command-std(out|err)>.*?</local-command-std\1>", re.S)
# The harness appends this hint line *after* the </task-notification> block,
# so it survives _INJECTED_BLOCK_RE and would otherwise show as a user prompt.
_TASK_RESULT_HINT_RE = re.compile(
    r"^\s*Read the output file to retrieve the result:.*$", re.M)


def _clean_user_text(text: str) -> str:
    """The human-typed remainder of a user line ("" = nothing was typed)."""
    if "<" not in text:
        return text.strip()
    # A slash command is stored as an envelope; show it as "/cmd args".
    m = _COMMAND_NAME_RE.search(text)
    if m:
        cmd = m.group(1).strip()
        args = _COMMAND_ARGS_RE.search(text)
        arg_text = args.group(1).strip() if args else ""
        return (cmd + " " + arg_text).strip() if arg_text else cmd
    text = _LOCAL_STDOUT_RE.sub("", text)
    text = _INJECTED_BLOCK_RE.sub("", text)
    text = _TASK_RESULT_HINT_RE.sub("", text)
    # An unclosed block (truncated injection) would leak its head.
    m = _INJECTED_OPEN_RE.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()


def _human_user_text(obj: dict) -> str:
    """Text the human typed on a `user` line; "" for synthetic lines."""
    # isMeta: harness injections. isCompactSummary: the "This session is being
    # continued..." blob Claude Code writes after a context compaction — it is
    # not something the human typed, so keep it out of the transcript, turn
    # counts, and title source.
    if obj.get("isMeta") or obj.get("isCompactSummary"):
        return ""
    return _clean_user_text(_text_of(obj.get("message")))


# ---------------------------------------------------------------- process view
#
# The default transcript is the *result*: what the human asked and what the
# agent said. Everything else in a turn — the tool calls, their output, the
# thinking — is dropped by _parse_line (83% of the records in a working
# session). `?detail=steps` attaches that material to the messages it happened
# between, as "steps".
#
# Steps are children of a message, never messages of their own. A tool_result
# record is `type: "user"` in the JSONL, so promoting one to a top-level user
# item would corrupt every client that counts user messages — the web's
# "Rewind to here" computes /rewind N that way and would cut the session at
# the wrong point.
def _result_text(block: dict) -> str:
    """tool_result content is a string on some CLI versions and a list of
    blocks on others."""
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, dict) and b.get("type") == "image":
                parts.append("[image]")
        return "\n".join(p for p in parts if p)
    return ""


def _steps_of(obj: dict) -> list:
    """Process records inside one transcript line, in content order."""
    if obj.get("isSidechain"):
        return []
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list):
        return []
    uid = obj.get("uuid", "")
    ts = obj.get("timestamp", "")
    out = []
    for i, b in enumerate(content):
        if not isinstance(b, dict):
            continue
        kind = b.get("type")
        ref = "%s:%d" % (uid, i)
        if kind == "tool_use":
            raw = b.get("input")
            name = b.get("name", "?")
            full = steps_mod.format_tool_use(name, raw)
            out.append(steps_mod.tool_use(
                ref, ts, name,
                tool_detail(raw if isinstance(raw, dict) else {}), full))
        elif kind == "tool_result":
            raw_text = _result_text(b)
            out.append(steps_mod.tool_result(
                ref, ts, not bool(b.get("is_error")),
                steps_mod.format_tool_result(raw_text)))
        elif kind == "thinking":
            out.append(steps_mod.thinking(ref, ts, b.get("thinking")))
    return out


def _step_full(obj: dict, index: int) -> str:
    """Full text of one step, for the expand endpoint."""
    content = (obj.get("message") or {}).get("content")
    if not isinstance(content, list) or index >= len(content):
        return None
    b = content[index]
    if not isinstance(b, dict):
        return None
    if b.get("type") == "tool_result":
        return steps_mod.format_tool_result(_result_text(b))
    if b.get("type") == "thinking":
        return b.get("thinking") or ""
    if b.get("type") == "tool_use":
        return steps_mod.format_tool_use(b.get("name", "?"), b.get("input"))
    return None


def _text_of(message: dict) -> str:
    parts = []
    for block in _content_blocks(message):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p).strip()


def _preview(text: str, max_len: int = _MAX_PREVIEW) -> str:
    text = " ".join(text.split())
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


# Phone/web composers inject attachments as:
#   [attached: /Users/…/.agentremoted/uploads/….png]
# Using that path (or its basename) as the session *title* makes the left
# list repeat the same filename the transcript already shows as a chip.
_ATTACHED_LINE_RE = re.compile(r"^\[attached:\s*([^\]]+)\]\s*$", re.I)
_ATTACHED_ANY_RE = re.compile(r"\[attached:\s*([^\]]+)\]", re.I)
_FILENAME_TITLE_RE = re.compile(
    r"(?i)^(?:screenshot[\s_\-]*.*)?\S+\.(?:png|jpe?g|gif|webp|heic|bmp|pdf|mov|mp4|m4a|wav|zip)$"
)


def _is_attachment_only(text: str) -> bool:
    """True when the human message is only one or more [attached: …] lines."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    return all(_ATTACHED_LINE_RE.match(ln) for ln in lines)


def _strip_attachment_lines(text: str) -> str:
    """Remove [attached: …] lines; keep the rest of the prompt."""
    if not text or "[attached:" not in text.lower():
        return (text or "").strip()
    kept = []
    for ln in text.splitlines():
        if _ATTACHED_LINE_RE.match(ln.strip()):
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def _attachment_label(text: str) -> str:
    """Generic list label (Image / PDF / File) — never the raw filename."""
    name = ""
    m = _ATTACHED_ANY_RE.search(text or "")
    if m:
        name = os.path.basename(m.group(1).strip().strip("\"'"))
    elif text:
        name = os.path.basename(text.strip())
    ext = name.rsplit(".", 1)[-1].lower() if name and "." in name else ""
    if ext in ("png", "jpg", "jpeg", "gif", "webp", "heic", "bmp"):
        return "Image"
    if ext == "pdf":
        return "PDF"
    if ext in ("mp4", "mov", "webm"):
        return "Video"
    if ext in ("mp3", "wav", "m4a", "aac"):
        return "Audio"
    return "Attachment"


def _looks_like_filename_title(title: str) -> bool:
    t = " ".join((title or "").split())
    if not t:
        return False
    if _FILENAME_TITLE_RE.match(t):
        return True
    # Common phone/desktop screenshot basenames without needing a full match.
    low = t.lower()
    return low.startswith("screenshot") and any(
        low.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".heic", ".webp")
    )


def _list_title_source(first_user: str, last_text: str, last_role: str,
                       cwd: str, fallback: str) -> str:
    """Pick list-row title material that is not a bare attachment filename."""
    for candidate in (first_user, last_text if last_role == "user" else ""):
        prose = _strip_attachment_lines(candidate)
        if prose:
            return prose
    # Attachment-only (or title would be the filename): generic label + folder.
    attach_src = first_user if _is_attachment_only(first_user) else (
        last_text if _is_attachment_only(last_text) else "")
    if attach_src or _looks_like_filename_title(fallback):
        folder = (cwd or "").rstrip("/").rsplit("/", 1)[-1] if cwd else ""
        label = _attachment_label(attach_src or fallback or "")
        return ("%s · %s" % (label, folder)) if folder else label
    return fallback or ""


def _list_preview_text(last_text: str, title: str) -> str:
    """last_text for the list row — drop if it only repeats the title/filename."""
    if not last_text:
        return ""
    if _is_attachment_only(last_text):
        return ""  # chip already shows the file in the transcript
    prose = _strip_attachment_lines(last_text)
    if not prose:
        return ""
    # Same string as title (after collapse) → no second line.
    if " ".join(prose.split()).lower() == " ".join((title or "").split()).lower():
        return ""
    return prose


def _read_head_lines(path: Path, max_bytes: int):
    with open(path, "rb") as f:
        chunk = f.read(max_bytes)
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # Last line may be truncated by the byte cut — drop it unless we read
    # the whole file.
    if len(chunk) == max_bytes and lines:
        lines = lines[:-1]
    return lines


def _read_tail_lines(path: Path, max_bytes: int):
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
        chunk = f.read()
    text = chunk.decode("utf-8", errors="replace")
    lines = text.splitlines()
    # First line may be a partial line if we seeked into the middle.
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def _is_safe_id(value: str) -> bool:
    """Reject anything that could escape the projects directory."""
    return bool(value) and all(ch.isalnum() or ch in "-_." for ch in value) and ".." not in value


class ClaudeStore:
    """Read-only view over the Claude Code projects directory."""

    def __init__(self, projects_dir: Path, config=None):
        self.projects_dir = projects_dir
        # Short mobile-friendly titles summarized by Haiku, cached on disk.
        # Shared with the other harnesses: one worker, one file.
        self._titles = titles.TitleCache(config, _oauth_token)
        # Set by providers.build_one to this harness's own generator.
        self.titler = None
        # (mtime_ns, size) -> (compactions, first_user_text, last_summary) so
        # the sessions list doesn't re-scan an unchanged transcript.
        self._scan_memo = {}
        # (mtime_ns, size) -> is-the-user's-own-session verdict, same deal.
        self._user_memo = {}

    def _scan_session(self, path: Path):
        """Full-file scan for the number of context compactions, the first
        user message, and the text of the most-recent compaction summary.
        Memoized by (mtime_ns, size) so a given transcript is scanned at most
        once until it changes."""
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return 0, "", ""
        cached = self._scan_memo.get(path.stem)
        if cached and cached[0] == key:
            return cached[1]
        compactions = 0
        first = ""
        last_summary = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    obj = _safe_json(line)
                    if not obj or obj.get("type") != "user":
                        continue
                    if obj.get("isCompactSummary"):
                        compactions += 1
                        last_summary = _text_of(obj.get("message"))
                        continue
                    if not first:
                        text = _human_user_text(obj)
                        if text:
                            first = text
        except OSError:
            return 0, "", ""
        result = (compactions, first, last_summary)
        self._scan_memo[path.stem] = (key, result)
        return result

    def _is_user_session(self, path: Path) -> bool:
        """True for transcripts worth showing the human.

        Two kinds of file sit next to the real sessions and are not the
        user's own work:

        * subagent transcripts (`isSidechain`) — Claude Code writes those
          under <session>/subagents/, which the *.jsonl globs already miss,
          but flag them here too so a layout change can never leak them in;
        * throwaway shells where the TUI opened and quit without ever calling
          the model: "/exit", a cancelled "/resume", a bare "!command", an
          empty file. No model reply ⇒ nothing happened ⇒ not a session.

        Head-only (and memoized) because this also gates the projects list:
        a real session's first reply is always in the first few KB, while the
        throwaway shells are a handful of lines in total. When the file is
        larger than the window we can't finish the check cheaply, so we keep
        the session — hiding real work is far worse than one stale row.
        """
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            return False
        cached = self._user_memo.get(path.stem)
        if cached and cached[0] == key:
            return cached[1]
        try:
            lines = _read_head_lines(path, _PROVENANCE_BYTES)
        except OSError:
            return False
        has_reply = False
        sidechain = False
        for line in lines:
            obj = _safe_json(line)
            if not obj:
                continue
            if obj.get("isSidechain") is True:
                sidechain = True
                break
            if obj.get("type") != "assistant":
                continue
            message = obj.get("message") or {}
            # Skip Claude Code's synthetic "<...>" assistant lines (API
            # errors, interrupts) — those are not a model answering.
            if not str(message.get("model") or "").startswith("<") \
                    and _text_of(message):
                has_reply = True
                break
        truncated = st.st_size > _PROVENANCE_BYTES
        verdict = not sidechain and (has_reply or truncated)
        if not verdict and not sidechain \
                and time.time() - st.st_mtime < _FRESH_SECONDS:
            # A turn that started seconds ago has no reply on disk yet. Show
            # it, and don't memoize the guess — once it goes quiet without one
            # it was a throwaway shell after all.
            return True
        self._user_memo[path.stem] = (key, verdict)
        return verdict

    # -- discovery -----------------------------------------------------

    def list_projects(self) -> list:
        """Projects sorted by most recently active first."""
        projects = []
        if not self.projects_dir.is_dir():
            return projects
        for entry in self.projects_dir.iterdir():
            if not entry.is_dir():
                continue
            # Same filter as list_sessions, so "N sessions" matches the rows
            # the phone actually gets when the project is opened.
            files = [f for f in entry.glob("*.jsonl")
                     if self._is_user_session(f)]
            if not files:
                continue
            latest = max(f.stat().st_mtime for f in files)
            cwd = self._project_cwd(entry, files)
            projects.append({
                "id": entry.name,
                "cwd": cwd,
                "name": (os.path.basename(cwd.rstrip("/")) or "/") if cwd
                        else entry.name,
                "session_count": len(files),
                "last_active": latest,
            })
        projects.sort(key=lambda p: p["last_active"], reverse=True)
        return projects

    def list_sessions(self, project_id: str = None, limit: int = 25,
                      user_only: bool = True) -> list:
        """Session summaries, most recent first."""
        files = []
        if not self.projects_dir.is_dir():
            return []
        if project_id:
            proj = self._project_dir(project_id)
            if proj is None:
                return []
            files = list(proj.glob("*.jsonl"))
        else:
            for entry in self.projects_dir.iterdir():
                if entry.is_dir():
                    files.extend(entry.glob("*.jsonl"))
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        sessions = []
        seen_ids = set()
        for f in files:
            if user_only and not self._is_user_session(f):
                continue
            # Same uuid can exist under two project dirs after moves/copies;
            # keep the newest mtime only (files already newest-first).
            sid = f.stem
            if sid in seen_ids:
                continue
            summary = self._session_summary(f)
            if summary:
                seen_ids.add(sid)
                sessions.append(summary)
            if len(sessions) >= limit:
                break
        return sessions

    def search_sessions(self, query: str, project_id: str = None,
                        limit: int = 25, user_only: bool = True) -> list:
        """Full-text search; batch API. Prefer iter_search_sessions for streaming."""
        results = list(self.iter_search_sessions(
            query, project_id=project_id, limit=limit, user_only=user_only))
        results.sort(key=search_util.rank_key, reverse=True)
        return results

    def iter_search_sessions(self, query: str, project_id: str = None,
                             limit: int = 25, user_only: bool = True):
        """Yield hits as they are found (newest sessions first).

        Two passes so the UI can paint quickly:
          1) title / last_text / cwd only (cheap)
          2) head+tail body scan for the rest
        """
        q = search_util.normalize_query(query)
        if not q:
            return
        files = []
        if not self.projects_dir.is_dir():
            return
        if project_id:
            proj = self._project_dir(project_id)
            if proj is None:
                return
            files = list(proj.glob("*.jsonl"))
        else:
            for entry in self.projects_dir.iterdir():
                if entry.is_dir():
                    files.extend(entry.glob("*.jsonl"))
        try:
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        except OSError:
            pass

        limit = max(1, min(int(limit or 25), 100))
        yielded = 0
        need_body = []  # (path, summary) after meta miss

        for path in files[:search_util.MAX_SCAN]:
            if user_only and not self._is_user_session(path):
                continue
            summary = self._session_summary(path, light=True)
            if not summary:
                continue
            hit = self._match_meta(summary, q)
            if hit is not None:
                row = dict(summary)
                row["snippet"] = hit
                yield row
                yielded += 1
                if yielded >= limit:
                    return
            else:
                need_body.append((path, summary))

        for path, summary in need_body:
            if yielded >= limit:
                return
            hit = self._match_body(path, q)
            if hit is None:
                continue
            row = dict(summary)
            row["snippet"] = hit
            yield row
            yielded += 1

    def _match_session(self, path: Path, summary: dict, query: str):
        """Return a snippet for the first hit, or None (meta then body)."""
        hit = self._match_meta(summary, query)
        if hit is not None:
            return hit
        return self._match_body(path, query)

    @staticmethod
    def _match_meta(summary: dict, query: str):
        """Title / last_text / cwd only — no file body I/O."""
        title = summary.get("title") or ""
        if search_util.contains_ci(title, query):
            return search_util.make_snippet(title, query)
        last = summary.get("last_text") or ""
        if search_util.contains_ci(last, query):
            return search_util.make_snippet(last, query)
        cwd = summary.get("cwd") or ""
        if search_util.contains_ci(cwd, query):
            return search_util.make_snippet(cwd, query)
        return None

    def _match_body(self, path: Path, query: str):
        """Head+tail transcript scan. Line reject skips JSON parse for most lines."""
        q_folded = query.lower() if query.isascii() else query.casefold()
        ascii_q = query.isascii()
        try:
            for line in self._search_body_lines(path):
                if not search_util.line_may_match(line, q_folded, ascii_needle=ascii_q):
                    continue
                msg = self._parse_line(line)
                if not msg:
                    continue
                text = msg.get("text") or ""
                if search_util.contains_ci(text, query):
                    return search_util.make_snippet(text, query)
        except OSError:
            return None
        return None

    @staticmethod
    def _search_body_lines(path: Path):
        """Yield transcript lines for body search (full small files; head+tail large)."""
        try:
            size = path.stat().st_size
        except OSError:
            return
        head_n = search_util.SEARCH_HEAD_BYTES
        tail_n = search_util.SEARCH_TAIL_BYTES
        if size <= head_n + tail_n:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                yield from f
            return
        for line in _read_head_lines(path, head_n):
            yield line
        for line in _read_tail_lines(path, tail_n):
            yield line

    def find_session_file(self, session_id: str) -> Path:
        """Locate a session transcript by uuid across all projects."""
        if not _is_safe_id(session_id) or not self.projects_dir.is_dir():
            return None
        for entry in self.projects_dir.iterdir():
            if not entry.is_dir():
                continue
            candidate = entry / (session_id + ".jsonl")
            if candidate.is_file():
                return candidate
        return None

    # -- transcripts ---------------------------------------------------

    supports_steps = True     # `?detail=steps` (see _steps_of)

    def get_step(self, session_id: str, ref: str):
        """Full text behind one truncated step. `ref` is "<record uuid>:<block
        index>" — the block index matters because one record holds several."""
        path = self.find_session_file(session_id)
        if path is None or not ref or ":" not in ref:
            return None
        uid, _, idx = ref.rpartition(":")
        try:
            index = int(idx)
        except ValueError:
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if uid not in line:
                    continue      # cheap reject before the JSON parse
                obj = _safe_json(line)
                if not isinstance(obj, dict) or obj.get("uuid") != uid:
                    continue
                text = _step_full(obj, index)
                if text is None:
                    return None
                return {"ref": ref, "text": text, "bytes": len(text)}
        return None

    def get_messages(self, session_id: str, offset: int = None, limit: int = 50,
                     steps: bool = False) -> dict:
        """Parsed transcript. Default window is the *end* of the session,
        which is what a phone client wants first.

        Parse (read + JSON + text extraction) touches the whole file so we
        can count total and locate the tail; block rendering (markdown ->
        typed blocks + syntax highlight) runs only over the returned
        window. Both phases are timed into `timing` for the client.
        """
        path = self.find_session_file(session_id)
        if path is None:
            return None
        try:
            file_bytes = path.stat().st_size
        except OSError:
            file_bytes = 0

        t0 = time.perf_counter()
        messages = []
        step_rows = []      # (record position, uuid, step) — only when asked
        # uuid -> parentUuid for EVERY record, not just the messages: the
        # chain threads through system / file-history-snapshot lines, so a
        # message's parent is usually not another message.
        parent_of = {}
        pos = 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                obj = _safe_json(line)
                if isinstance(obj, dict):
                    uid = obj.get("uuid")
                    if uid:
                        parent_of[uid] = obj.get("parentUuid")
                msg = self._parse_line(line, obj)
                if msg:
                    msg["_pos"] = pos
                    messages.append(msg)
                # A record can hold BOTH text and tool_use, so steps are read
                # from every line, not only the ones _parse_line rejected.
                if steps and isinstance(obj, dict):
                    for st in _steps_of(obj):
                        step_rows.append((pos, obj.get("uuid", ""), st))
                pos += 1
        # Computed before the filter: _active_branch replaces the list.
        branch = _branch_uuids(messages, parent_of) if steps else None
        messages = _active_branch(messages, parent_of)
        t1 = time.perf_counter()

        total = len(messages)
        if offset is None:
            offset = max(0, total - limit)
        offset = max(0, offset)
        window = messages[offset: offset + limit]
        for msg in window:
            msg["blocks"] = _render_blocks(msg["role"], msg["text"])
        if steps:
            live = [(pos, st) for pos, uid, st in step_rows
                    if branch is None or not uid or uid in branch]
            steps_mod.attach(window, live)
        for msg in messages:
            msg.pop("_pos", None)
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

    def get_session(self, session_id: str) -> dict:
        path = self.find_session_file(session_id)
        if path is None:
            return None
        return self._session_summary(path)

    # -- internals -----------------------------------------------------

    def _project_dir(self, project_id: str) -> Path:
        if not _is_safe_id(project_id):
            return None
        proj = self.projects_dir / project_id
        return proj if proj.is_dir() else None

    def _project_cwd(self, entry: Path, files: list) -> str:
        """Recover the real cwd from any transcript line (the directory
        name munging is lossy, so read it from the data)."""
        newest = max(files, key=lambda f: f.stat().st_mtime)
        for line in _read_head_lines(newest, _HEAD_BYTES):
            obj = _safe_json(line)
            if obj and obj.get("cwd"):
                return obj["cwd"]
        return ""

    def _session_summary(self, path: Path, *, light: bool = False) -> dict:
        """Build a session list row.

        ``light=True`` (search path): skip compaction scan and do not queue
        Haiku title generation — use cached titles only. Cuts search latency
        and avoids kicking off dozens of title jobs per keystroke.
        """
        head = _read_head_lines(path, _HEAD_BYTES)
        tail = _read_tail_lines(path, _TAIL_BYTES)

        summary_text = ""
        ai_title = ""
        first_user_text = ""
        first_attach_only = ""
        cwd = ""
        git_branch = ""
        started = ""
        for line in head:
            obj = _safe_json(line)
            if not obj:
                continue
            if obj.get("type") == "summary" and not summary_text:
                summary_text = obj.get("summary", "")
            if obj.get("type") == "ai-title" and not ai_title:
                ai_title = obj.get("aiTitle", "")
            if not cwd and obj.get("cwd"):
                cwd = obj["cwd"]
            if not git_branch and obj.get("gitBranch"):
                git_branch = obj["gitBranch"]
            if not started and obj.get("timestamp"):
                started = obj["timestamp"]
            if obj.get("type") == "user":
                t = _human_user_text(obj)
                if not t:
                    continue
                # Prefer a real prompt over a lone [attached: …] for titles.
                if not first_user_text:
                    if _is_attachment_only(t):
                        if not first_attach_only:
                            first_attach_only = t
                    else:
                        first_user_text = t
                elif first_attach_only and not _is_attachment_only(t):
                    # Upgrade: later user message has prose.
                    first_user_text = t
        if not first_user_text and first_attach_only:
            first_user_text = first_attach_only

        last_ts = ""
        last_role = ""
        last_text = ""
        tail_title = ""
        model = ""
        for line in reversed(tail):
            obj = _safe_json(line)
            if not obj:
                continue
            if obj.get("type") == "ai-title" and not tail_title:
                tail_title = obj.get("aiTitle", "")
            if not last_ts and obj.get("timestamp"):
                last_ts = obj["timestamp"]
            # The model that answered last — skip Claude Code's synthetic
            # (non-model) assistant lines like "<synthetic>".
            if not model and obj.get("type") == "assistant":
                m = (obj.get("message") or {}).get("model") or ""
                if m and not m.startswith("<"):
                    model = m
            if obj.get("type") in ("user", "assistant") and not last_text:
                if obj["type"] == "user":
                    text = _human_user_text(obj)
                else:
                    text = _text_of(obj.get("message"))
                # Prefer real prose over a lone [attached: …] for list preview.
                if text and (obj["type"] == "assistant" or not _is_attachment_only(text)):
                    last_role = obj["type"]
                    last_text = text
                elif text and not last_text:
                    last_role = obj["type"]
                    last_text = text
            if last_ts and last_text and tail_title and model:
                break

        # Prefer a Haiku-summarized title (cached on disk). It is regenerated on
        # every context compaction: early on it names the first message, then
        # re-summarizes from each compaction's summary blob. Until it is
        # generated, fall back to Claude Code's own title / first line so the
        # row is never blank.
        # Never use a bare attachment filename as the list title — the
        # transcript already shows that chip (user: "do not repeat the same").
        prose_fallback = _list_title_source(
            first_user_text, last_text, last_role, cwd,
            tail_title or ai_title or summary_text or first_user_text or path.stem)
        base_title = _preview(prose_fallback, _MAX_TITLE) or path.stem
        title = base_title
        if light:
            # Any cached title for this id — never queue Haiku from search.
            with self._titles._lock:
                entry = self._titles._map.get(path.stem)
                if entry and entry.get("title"):
                    cached_title = entry["title"]
                    # Ignore cached titles that are just the attachment name.
                    if not _looks_like_filename_title(cached_title):
                        title = cached_title
        else:
            compactions, first, last_summary = self._scan_session(path)
            # Title the prose part of the first message, not [attached: …].
            first_for_title = _strip_attachment_lines(first_user_text) or first_user_text
            if not first:
                first = first_for_title or _strip_attachment_lines(last_text) or last_text
            if first and _is_attachment_only(first):
                first = prose_fallback
            if first or last_summary:
                sig, src = _title_source(compactions, first, last_summary)
                # Don't send attachment-only blobs to Haiku as the title source.
                if src and _is_attachment_only(src):
                    src = prose_fallback
                cached = self._titles.get(path.stem, sig)
                if cached and not _looks_like_filename_title(cached):
                    title = cached
                elif src and not _is_attachment_only(src):
                    self._titles.request(path.stem, sig, src, self.titler)

        try:
            size_bytes = path.stat().st_size
        except OSError:
            size_bytes = 0
        preview = _list_preview_text(last_text, title)
        return {
            "id": path.stem,
            "project_id": path.parent.name,
            "cwd": cwd,
            "git_branch": git_branch,
            "title": title,
            "started": started,
            "last_active": last_ts,
            "last_role": last_role,
            "last_text": _preview(preview) if preview else "",
            "model": model,
            "size_bytes": size_bytes,
        }

    def _parse_line(self, line: str, obj=None) -> dict:
        if obj is None:
            obj = _safe_json(line)
        if not obj or obj.get("type") not in ("user", "assistant"):
            return None
        message = obj.get("message") or {}
        if obj["type"] == "user":
            # Only what the human typed — harness injections (reminders,
            # task notifications, hook output) are not conversation.
            text = _human_user_text(obj)
        else:
            text = _text_of(message)
        # Tool activity is transient job state (the app shows it live in
        # the status ticker); text-less lines (tool_use / tool_result
        # plumbing) are not part of the persisted conversation.
        if not text:
            return None
        # Blocks are rendered later, only for the returned window — see
        # get_messages. Rendering every line of a long transcript (markdown
        # + syntax highlight) just to discard all but the last page was the
        # bulk of transcript-open latency.
        return {
            "uuid": obj.get("uuid", ""),
            "role": obj["type"],
            "ts": obj.get("timestamp", ""),
            "text": text,
        }


def _branch_uuids(messages: list, parent_of: dict):
    """Every record uuid reachable from the newest message, or None when the
    chain cannot be trusted.

    Same walk and same give-up rule as _active_branch, but it keeps the whole
    reachable set rather than just the messages — steps need it, because a
    tool call's uuid is never a message uuid. None means "do not filter",
    matching _active_branch's "show too much beats show nothing".
    """
    if len(messages) < 2:
        return None
    keep, seen = set(), set()
    uid = messages[-1].get("uuid")
    while uid and uid not in seen:
        seen.add(uid)
        keep.add(uid)
        uid = parent_of.get(uid)
    if not keep:
        return None
    branch = [m for m in messages if m.get("uuid") in keep]
    if not branch or (len(branch) == 1 and len(messages) > 1):
        return None
    return keep


def _active_branch(messages: list, parent_of: dict) -> list:
    """The messages still on the conversation's live branch.

    A Claude Code transcript is a TREE, not a log: every record carries
    `parentUuid`, and /rewind does not delete anything — it moves the
    insertion point back, so the next turn hangs off an earlier node and the
    abandoned turns stay in the file. Rendering the file in order therefore
    showed a conversation the agent had already discarded, which is what made
    rewind look broken on every client (proved on session 07b5e7c4: the file
    reads ONE, TWO, THREE, FOUR while the live branch is ONE -> FOUR).

    So walk from the newest message up through parentUuid and keep only what
    is reachable. Falls back to the full list whenever the chain cannot be
    trusted (no uuids, or a broken link losing the earlier turns), since
    showing too much beats showing an empty transcript.
    """
    if len(messages) < 2:
        return messages
    keep, seen = set(), set()
    uid = messages[-1].get("uuid")
    while uid and uid not in seen:
        seen.add(uid)
        keep.add(uid)
        uid = parent_of.get(uid)
    if not keep:
        return messages
    branch = [m for m in messages if m.get("uuid") in keep]
    # Every message needs a uuid for this to mean anything; if the walk kept
    # only the tail (a chain broken by an older CLI's records), trust the file.
    if not branch or (len(branch) == 1 and len(messages) > 1):
        return messages
    return branch


def _render_blocks(role: str, text: str) -> list:
    """Rich display blocks for the phone (Cascades-safe HTML).

    Assistant markdown becomes typed blocks (p/h/li/code/…); user text gets
    inline styling only.

    Shell escape messages ("[shell] ! cmd\\n[output]\\n```…```") are split
    on the "[output]" marker: the command line becomes a user block, the
    output becomes rendered code block(s).
    """
    if not text:
        return []
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
            return blocks
        plain, rich = inline_to_rich(text)
        return [{"k": "user", "role": "user", "text": plain,
                 "rich": rich, "fmt": "rich"}]
    return markdown_to_blocks(text, role=role)


def _mcp_tool_prefix(server: str) -> str:
    """Server name as it appears inside a tool id.

    The CLI munges anything outside [A-Za-z0-9_-] to "_", so the connector
    "claude.ai Atlassian" calls itself mcp__claude_ai_Atlassian__search and
    the plugin "plugin:atlassian:atlassian" becomes
    mcp__plugin_atlassian_atlassian__*. Permission rules match the munged
    form, so --allowedTools must use it too.
    """
    return re.sub(r"[^A-Za-z0-9_-]", "_", server)


# ------------------------------------------------------------- headless MCP
#
# Headless `claude -p` never attaches the OAuth tokens the interactive TUI
# stores for remote MCP servers (the credential store's "mcpOAuth" map):
# plugin servers report needs-auth despite a valid token on disk, and
# claude.ai connectors sit in "pending" forever, so not one MCP tool loads.
# The proven fix is to hand the same server to --mcp-config with an explicit
# Authorization: Bearer header — then it connects and exposes every tool.
# So each turn we mirror every locally-authenticated remote server into the
# per-turn config, refreshing an expired access token with the stored
# (rotating!) refresh token and writing the rotated pair back so the TUI's
# copy stays alive too.

_MCP_TOKEN_SKEW_MS = 300_000  # refresh remote-MCP tokens 5 min early


def _mcp_token_endpoint(entry: dict) -> str:
    """token_endpoint of the auth server that issued this mcpOAuth entry,
    via RFC 8414 discovery (path-inserted form first, suffix form second)."""
    state = entry.get("discoveryState") or {}
    issuer = str(state.get("authorizationServerUrl", "")).strip().rstrip("/")
    if not issuer:
        return ""
    parts = urllib.parse.urlsplit(issuer)
    candidates = []
    if parts.path and parts.path != "/":
        candidates.append("%s://%s/.well-known/oauth-authorization-server%s"
                          % (parts.scheme, parts.netloc, parts.path))
    candidates.append(issuer + "/.well-known/oauth-authorization-server")
    for url in candidates:
        req = urllib.request.Request(url, headers={
            "User-Agent": "agentremoted", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            continue
        endpoint = str(meta.get("token_endpoint", "")).strip()
        if endpoint:
            return endpoint
    return ""


def _refresh_mcp_oauth(entry: dict) -> dict:
    """Rotate one remote-MCP OAuth token (form-encoded refresh grant, the
    standard OAuth 2.1 shape). Returns the updated fields, or {} on failure."""
    token_url = _mcp_token_endpoint(entry)
    refresh = str(entry.get("refreshToken", "")).strip()
    if not token_url or not refresh:
        return {}
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": str(entry.get("clientId", "")),
    }
    if str(entry.get("clientSecret", "")).strip():
        form["client_secret"] = str(entry["clientSecret"]).strip()
    req = urllib.request.Request(
        token_url, data=urllib.parse.urlencode(form).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "agentremoted"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return {}
    access = str(raw.get("access_token", "")).strip()
    if not access:
        return {}
    out = {"accessToken": access}
    if str(raw.get("refresh_token", "")).strip():
        out["refreshToken"] = str(raw["refresh_token"]).strip()
    if isinstance(raw.get("expires_in"), (int, float)):
        out["expiresAt"] = int(time.time() * 1000 + raw["expires_in"] * 1000)
    if str(raw.get("scope", "")).strip():
        out["scope"] = str(raw["scope"]).strip()
    return out


def _mcp_oauth_servers() -> dict:
    """Every remote MCP server with a locally stored OAuth login (one TUI
    `/mcp` sign-in creates it), as --mcp-config entries that carry the token
    in an explicit Bearer header. Keys are the munged tool-prefix names, so
    the tools keep the ids the TUI transcript shows."""
    data, store = _read_creds()
    oauth = data.get("mcpOAuth")
    if not isinstance(oauth, dict):
        return {}
    # Newest entry per server name — stale logins linger under old config
    # hashes (the key is "<serverName>|<hash>").
    best = {}
    for key, entry in oauth.items():
        if not isinstance(entry, dict):
            continue
        if not str(entry.get("serverUrl", "")).strip():
            continue
        if not str(entry.get("accessToken", "")).strip():
            continue
        srv = str(entry.get("serverName") or str(key).split("|")[0]).strip()
        exp = entry.get("expiresAt") or 0
        cur = best.get(srv)
        if cur is None or exp > (cur[1].get("expiresAt") or 0):
            best[srv] = (key, entry)
    servers = {}
    dirty = False
    now_ms = time.time() * 1000
    for srv, (key, entry) in best.items():
        name = _mcp_tool_prefix(srv)
        if not name or name == "bb10":
            continue
        exp = entry.get("expiresAt")
        fresh = (isinstance(exp, (int, float))
                 and now_ms < exp - _MCP_TOKEN_SKEW_MS)
        if not fresh:
            updated = _refresh_mcp_oauth(entry)
            if updated:
                entry.update(updated)
                oauth[key] = entry
                dirty = True
            elif not str(entry.get("refreshToken", "")).strip():
                continue  # long-dead login, nothing to forward
        servers[name] = {
            "type": "http",
            "url": str(entry["serverUrl"]).strip(),
            "headers": {"Authorization":
                        "Bearer " + str(entry["accessToken"]).strip()},
        }
    if dirty:
        data["mcpOAuth"] = oauth
        _save_creds(data, store)
    return servers


class ClaudeRunner:
    """Executes one turn as `claude -p` with stream-json output."""

    name = "claude"

    def __init__(self, config):
        self.config = config
        # MCP server names seen in the CLI's own init event (plugin- and
        # connector-provided servers exist in no config file we could read).
        self._seen_mcp_servers = set()
        # Lazily created tmux-TUI manager for "interactive" jobs.
        self._interactive = None
        self._interactive_lock = threading.Lock()

    def _interactive_mgr(self):
        with self._interactive_lock:
            if self._interactive is None:
                from .claude_interactive import InteractiveManager
                self._interactive = InteractiveManager(self.config)
            return self._interactive

    def run_alternate(self, job, mode) -> bool:
        """Fully handle a job outside the subprocess pipeline. "interactive"
        drives a real TUI in tmux — the only path where claude.ai connectors
        work (their OAuth never reaches headless `claude -p`)."""
        if mode != "interactive":
            return False
        self._interactive_mgr().run(job)
        return True

    def resume_alternate(self, job) -> None:
        """Continue an interactive job after daemon restart (tmux TUI adopted)."""
        self._interactive_mgr().resume(job)

    def rewind_session(self, session_id: str, steps: int):
        """Cut the session transcript back N human messages (conversation
        only — files on disk are not restored). Claude Code rebuilds its
        context from the JSONL on --resume, in -p and TUI modes alike, so
        truncating the file IS the rewind (verified: a truncated session
        forgets the dropped turns and keeps its id). Returns
        (steps_done, preview_of_first_dropped_message)."""
        sid = (session_id or "").strip()
        if not sid or not _is_safe_id(sid):
            raise providers.RunnerError("rewind needs an existing session")
        path = None
        projects_dir = self.config.projects_path
        if projects_dir.is_dir():
            for entry in projects_dir.iterdir():
                candidate = entry / (sid + ".jsonl")
                if entry.is_dir() and candidate.is_file():
                    path = candidate
                    break
        if path is None:
            raise providers.RunnerError("session transcript not found")
        # A live TUI holds the pre-cut conversation in memory; close it so
        # the next interactive turn --resume's the rewound session.
        self._interactive_mgr().close_for_session(sid)
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        records = []
        for idx, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                records.append((idx, obj))
        # Human prompts on the ACTIVE branch only: a host-side TUI rewind
        # leaves abandoned turns in the file (the transcript is a parentUuid
        # tree), and those must not count — the same walk get_messages uses.
        chain, last_uuid = {}, None
        for _, obj in records:
            u = obj.get("uuid")
            if u and obj.get("type") in ("user", "assistant"):
                chain[u] = obj.get("parentUuid")
                last_uuid = u
        active, broken = set(), False
        node = last_uuid
        while node:
            if node in active:
                break  # cycle guard
            active.add(node)
            parent = chain.get(node)
            if parent is not None and parent not in chain:
                broken = True  # link lost — the walk would drop early turns
                break
            node = parent
        human = []
        for idx, obj in records:
            if obj.get("type") != "user":
                continue
            u = obj.get("uuid")
            if not broken and active and u and u not in active:
                continue
            if _human_user_text(obj):
                human.append((idx, obj))
        if not human:
            raise providers.RunnerError(
                "nothing to rewind — no user messages yet")
        steps = max(1, min(int(steps), len(human)))
        cut_idx, target = human[-steps]
        backup = path.parent / (path.name + ".rewind-bak")
        try:
            backup.write_text(raw, encoding="utf-8")
        except OSError:
            pass  # safety net only; the rewind itself still proceeds
        with open(path, "w", encoding="utf-8") as f:
            if cut_idx:
                f.write("\n".join(lines[:cut_idx]) + "\n")
        preview = " ".join(_human_user_text(target).split())[:120]
        return steps, preview

    def type_into_tui(self, session_id: str, text: str) -> str:
        """Type a message into a session's live interactive TUI ("" or err)."""
        return self._interactive_mgr().type_text(session_id, text)

    def capture_tui(self, session_id: str, *, ansi: bool = False) -> dict:
        return self._interactive_mgr().capture_tui(session_id, ansi=ansi)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        return self._interactive_mgr().send_tui_keys(session_id, keys=keys, text=text)

    def on_hook(self, payload: dict, secret: str, tui_name: str = "") -> bool:
        """/internal/hook bridge for TUI SessionStart/Stop hook posts."""
        return self._interactive_mgr().on_hook(payload, secret, tui_name)

    def capabilities(self) -> dict:
        return {
            "queue": True,
            "stop": True,
            "projects": True,
            "ws_status": True,
            "permissions": True,
            "permission_modes": True,
            "requires_cwd": True,
            "can_set_model": True,
            # `claude -p` has no reasoning-effort flag.
            "can_set_effort": False,
            # Subscription usage (session + weekly limits) via /api/oauth/usage.
            "can_show_usage": True,
            # "interactive" permission mode: turns run in a host tmux TUI,
            # where claude.ai connectors work.
            "interactive": True,
            # Live TUI: clients can capture the pane and send keys.
            "live_tui": True,
            # "/rewind N": the daemon cuts the session transcript back N
            # user messages (conversation only) and the next turn resumes
            # from there. Session-file surgery — works in BOTH exec modes.
            "rewind": True,
        }

    def auth_health(self) -> dict:
        """Local credential snapshot for /api/ping (no network)."""
        import shutil
        bin_path = str(getattr(self.config, "claude_bin", None) or "claude")
        on_path = bool(shutil.which(bin_path) or shutil.which("claude"))
        env = getattr(self.config, "claude_env", None) or {}
        api_key = str(env.get("ANTHROPIC_API_KEY")
                      or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        oauth_env = _reject_bad_env_token(
            env.get("CLAUDE_CODE_OAUTH_TOKEN")
            or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"))
        data, _store = _read_creds()
        oauth = data.get("claudeAiOauth") or {}
        access = str(oauth.get("accessToken", "")).strip()
        expires_at = oauth.get("expiresAt")
        fresh = (isinstance(expires_at, (int, float))
                 and time.time() * 1000 < expires_at - _TOKEN_SKEW_MS)
        has_refresh = bool(str(oauth.get("refreshToken", "")).strip())

        if api_key:
            detail = ("ANTHROPIC_API_KEY is set — Claude Code bills API rates "
                      "(overrides a Pro/Max login when both exist)")
            if not on_path:
                detail = "API key present, but `claude` is not on PATH"
            return {
                "cli": "claude",
                "cli_on_path": on_path,
                "mode": "api_key",
                "status": "ok" if on_path else "warning",
                "detail": detail,
            }
        if oauth_env:
            return {
                "cli": "claude",
                "cli_on_path": on_path,
                "mode": "subscription",
                "status": "ok" if on_path else "warning",
                "detail": ("CLAUDE_CODE_OAUTH_TOKEN set"
                           + ("" if on_path else "; `claude` not on PATH")),
            }
        if access and fresh:
            return {
                "cli": "claude",
                "cli_on_path": on_path,
                "mode": "subscription",
                "status": "ok" if on_path else "warning",
                "detail": ("claude.ai subscription login looks valid"
                           + ("" if on_path else "; `claude` not on PATH")),
            }
        if access or has_refresh:
            return {
                "cli": "claude",
                "cli_on_path": on_path,
                "mode": "subscription",
                "status": "expired",
                "detail": "Claude sign-in expired or needs refresh — run `claude` /login on this host",
            }
        return {
            "cli": "claude",
            "cli_on_path": on_path,
            "mode": "none",
            "status": "missing",
            "detail": ("No Claude login or API key on this host"
                       + ("" if on_path else "; `claude` not on PATH")),
        }

    def usage(self) -> dict:
        return fetch_usage(self.config)

    def efforts(self) -> list:
        return []

    def models(self) -> list:
        """Model names the app offers for --model, fetched live from the
        Anthropic Models API: concrete ids (`claude-opus-4-8`) plus a `[1m]`
        variant for 1M-context models. Concrete ids (not the CLI aliases) so
        the picker can't lag a release the way the `opus` alias did in
        `claude -p`. Falls back to the aliases when the API is unreachable;
        config `models` can still append exact ids."""
        names = []
        for m in list_models(self.config):
            names.append(m["id"])
            if m.get("context", 0) >= _ONE_M_CONTEXT:
                names.append(m["id"] + "[1m]")
        if not names:
            # Offline / no token: the CLI aliases still resolve on the host.
            names = ["opus", "sonnet", "haiku"]
        extra = [str(m).strip() for m in
                 (getattr(self.config, "models", None) or []) if str(m).strip()]
        out = ["default"]
        for m in names + extra:
            if m and m not in out:
                out.append(m)
        return out

    # Only the two session-control built-ins are advertised. The rest of the
    # TUI's commands are interactive-only UI (dialogs, pickers, status panes)
    # that a phone can't drive, and headless -p answers "Unknown skill" — so
    # the app's gate, which consults this list, must keep blocking them.
    # Interactive built-ins this harness really has, verified in its TUI.
    # /clear is deliberately absent: wiping the conversation from a phone
    # is a footgun, and the daemon is the only whitelist now — clients
    # ban anything not advertised here.
    # /rewind is served by the DAEMON (session-file surgery in jobs.py), so
    # unlike the TUI built-ins it also works on headless turns.
    # /goal is a multi-client control/skill entry — always advertised so
    # BB/Android/web/iOS allow it (they refuse anything not on this list).
    _BUILTIN_SLASH = ["/compact", "/exit", "/goal", "/rewind"]

    def slash_commands(self) -> list:
        """Everything a phone-typed /command can reach: config extras,
        ~/.claude/commands/*.md, user skills (~/.claude/skills/*/SKILL.md),
        and the interactive TUI built-ins. Plugin skills are namespaced
        (/plugin:skill) and pass the app's command gate untouched, so they
        need no listing."""
        commands = set(self._BUILTIN_SLASH)
        for extra in getattr(self.config, "slash_commands", None) or []:
            if isinstance(extra, str) and extra.strip():
                commands.add(extra.strip())
        home = Path.home() / ".claude"
        try:
            for f in (home / "commands").glob("*.md"):
                commands.add("/" + f.stem)
        except OSError:
            pass
        try:
            for f in (home / "skills").glob("*/SKILL.md"):
                commands.add("/" + f.parent.name)
        except OSError:
            pass
        return sorted(commands)

    def title_for(self, text: str) -> str:
        """Name a Claude session using Claude.

        Unlike the other two this needs no subprocess: one short Haiku request
        over the subscription token the daemon already refreshes, so it is fast
        and creates no session.
        """
        return summarize_title(self.config, text)

    def prepare(self, job, mode):
        cmd = [
            self.config.claude_bin,
            "-p", job.prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", mode,
        ]
        # Remote MCP servers the TUI has logged into, re-served with explicit
        # Bearer headers — headless `claude -p` cannot use the stored OAuth
        # itself (plugins report needs-auth, connectors hang "pending").
        mcp_servers = _mcp_oauth_servers()
        # Route permission prompts to the phone unless we're in auto/bypass
        # (which never prompts) or plan (which never acts).
        if mode not in ("bypassPermissions", "plan"):
            mcp_servers["bb10"] = self._permission_server(job)
        cfg_path = self._write_mcp_config(mcp_servers) if mcp_servers else ""
        if cfg_path:
            job.runner_state["mcp_config_path"] = cfg_path
            cmd += ["--mcp-config", cfg_path]
            if "bb10" in mcp_servers:
                cmd += ["--permission-prompt-tool", "mcp__bb10__approve"]
        if mode not in ("bypassPermissions", "plan"):
            # The ask-modes are about edits and shell commands; answering an
            # MCP call on the phone is pure friction (and stalls the turn for
            # a whole tool-heavy MCP workflow). Pre-allow the host's MCP
            # servers so only Bash/Edit-style tools reach the Allow/Deny panel.
            # Per-server ids ("mcp__jira"), one argv token each: the CLI takes
            # no wildcard here, and plugin server names contain colons.
            servers = set(self._mcp_server_names(job))
            servers.update(n for n in mcp_servers if n != "bb10")
            if servers:
                cmd += ["--allowedTools"] + ["mcp__" + s for s in sorted(servers)]
        if job.session_id:
            cmd += ["--resume", job.session_id]
        # "default" (or empty) = let the CLI pick; anything else is an alias
        # (opus/sonnet/haiku) or an exact model id.
        if job.model and job.model != "default":
            cmd += ["--model", job.model]
        env = None
        extra_env = dict(getattr(self.config, "claude_env", None) or {})
        # Same guard as _oauth_token: a placeholder token forwarded into the
        # CLI's env would shadow its own sign-in and fail every turn's auth.
        if "CLAUDE_CODE_OAUTH_TOKEN" in extra_env and \
                not _reject_bad_env_token(extra_env["CLAUDE_CODE_OAUTH_TOKEN"]):
            del extra_env["CLAUDE_CODE_OAUTH_TOKEN"]
        if extra_env:
            env = dict(os.environ)
            env.update({str(k): str(v) for k, v in extra_env.items()})
        return cmd, env

    def handle_stream_line(self, job, line: str):
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return
        kind = obj.get("type", "")

        if kind == "system" and obj.get("subtype") == "init":
            with job.lock:
                job.new_session_id = obj.get("session_id", "")
            for srv in obj.get("mcp_servers") or []:
                name = (srv or {}).get("name") if isinstance(srv, dict) else None
                if name and name != "bb10":
                    self._seen_mcp_servers.add(_mcp_tool_prefix(str(name)))
            job.add_event("init", session_id=obj.get("session_id", ""),
                          model=obj.get("model", ""))

        elif kind == "assistant":
            message = obj.get("message") or {}
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    t = block["text"]
                    job.add_event("text", text=t,
                                  blocks=markdown_to_blocks(t))
                    job.set_phase("writing", t[-160:])
                elif block.get("type") == "tool_use":
                    name = block.get("name", "?")
                    if name.startswith("mcp__"):
                        # Exact prefix, straight from a real call — the most
                        # reliable source of a server's permission-rule name.
                        self._seen_mcp_servers.add(name[5:].split("__")[0])
                    tool_input = block.get("input") or {}
                    if name in _TASK_TOOLS:
                        # Full checklist instead of a one-line tool stub —
                        # same pattern as grok's plan/todo_write events.
                        sid = (obj.get("session_id")
                               or getattr(job, "session_id", None)
                               or getattr(job, "new_session_id", None)
                               or "")
                        if isinstance(tool_input, dict):
                            emit_claude_todos(job, str(sid or ""),
                                              tool_input=tool_input)
                        else:
                            emit_claude_todos(job, str(sid or ""))
                        continue
                    detail = tool_detail(tool_input if isinstance(tool_input, dict) else {})
                    job.add_event("tool", name=name, detail=detail)
                    job.set_phase(_PHASE_BY_TOOL.get(name, "tool"),
                                  detail or name)
                elif block.get("type") == "thinking":
                    job.set_phase("thinking", "")

        elif kind == "result":
            with job.lock:
                job.result_text = obj.get("result", "") or ""
                if obj.get("is_error"):
                    job.error = job.result_text or "claude reported an error"
            job.add_event("result",
                          is_error=bool(obj.get("is_error")),
                          duration_ms=obj.get("duration_ms", 0),
                          cost_usd=obj.get("total_cost_usd", 0))
            # Final disk re-read: TaskUpdate files may land after the tool line.
            sid = (getattr(job, "session_id", None)
                   or getattr(job, "new_session_id", None) or "")
            if sid:
                emit_claude_todos(job, str(sid))

    def tick(self, job):
        # Catch TaskCreate/TaskUpdate disk writes that lag the stream line.
        now = time.time()
        last = float(job.runner_state.get("todo_tick_at") or 0)
        if now - last < 1.5:
            return
        job.runner_state["todo_tick_at"] = now
        sid = (getattr(job, "session_id", None)
               or getattr(job, "new_session_id", None)
               or job.runner_state.get("session_id") or "")
        if sid:
            emit_claude_todos(job, str(sid))

    def finalize(self, job, returncode, stderr_tail):
        return None  # exit code decides

    def cleanup(self, job):
        path = job.runner_state.pop("mcp_config_path", "")
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _mcp_server_names(self, job) -> list:
        """MCP server names Claude Code would load for this job's cwd.

        Read straight from the CLI's own config (no `claude mcp list`
        subprocess per turn): ~/.claude.json holds user-scope servers plus a
        per-project block, and <cwd>/.mcp.json holds project-scope ones.
        Plugin-provided servers ("plugin:atlassian:atlassian") live in no such
        file, so we also keep whatever the last init event announced.
        Our own "bb10" approval server is excluded — it must not be
        pre-allowed as a callable tool.
        """
        names = set(self._seen_mcp_servers)
        cwd = str(getattr(job, "cwd", "") or "")
        sources = []
        try:
            data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
            sources.append(data)
            if cwd:
                sources.append((data.get("projects") or {}).get(cwd) or {})
        except (OSError, ValueError, AttributeError):
            pass
        if cwd:
            try:
                sources.append(json.loads(
                    (Path(cwd) / ".mcp.json").read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
        for src in sources:
            servers = src.get("mcpServers") if isinstance(src, dict) else None
            if isinstance(servers, dict):
                names.update(_mcp_tool_prefix(str(k)) for k in servers)
        names.discard("bb10")
        return sorted(n for n in names if n)

    def _permission_server(self, job) -> dict:
        """Our permission-prompt MCP server: bridges Allow/Deny back to this
        daemon over localhost, authenticated by the job's per-run nonce (no
        shared token in the subprocess env)."""
        import sys
        script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "permission_mcp.py")
        port = int(getattr(self.config, "port", 8473) or 8473)
        timeout = float(getattr(self.config, "permission_timeout", 300) or 300)
        return {
            "command": sys.executable,
            "args": [script],
            "env": {
                "AGENTREMOTE_PERM_URL": "http://127.0.0.1:%d/internal/permission" % port,
                "AGENTREMOTE_JOB_ID": job.id,
                "AGENTREMOTE_PERM_NONCE": job.perm_nonce,
                "AGENTREMOTE_PERM_TIMEOUT": str(int(timeout)),
            },
        }

    def _write_mcp_config(self, servers: dict) -> str:
        """Write the per-turn --mcp-config file (mkstemp = 0600: it can carry
        live Bearer tokens; cleanup() deletes it after the turn)."""
        cfg = {"mcpServers": servers}
        try:
            fd, path = tempfile.mkstemp(prefix="bb10-mcp-", suffix=".json")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
            return path
        except OSError:
            return ""
