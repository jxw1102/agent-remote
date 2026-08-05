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

import hashlib
import json
import os
import queue
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from ..config import CONFIG_DIR
from ..render_blocks import inline_to_rich, markdown_to_blocks
from .. import search_util

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
_DETAIL_KEYS = ("command", "file_path", "path", "pattern", "url", "prompt", "query")

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
}

def tool_detail(tool_input: dict, max_len: int = 200) -> str:
    """One short single-line snippet for the phone status banner / permission.

    Collapses whitespace so a multi-line Bash `command` cannot expand the
    status strip into many wraps; then middle-ellipsis so head + tail stay
    readable (path prefix and filename, command verb and last args).
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
_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
_TITLE_MODEL = "claude-haiku-4-5-20251001"
_TITLE_MAX_CHARS = 42
_TITLE_SIG_VERSION = "v3"  # bump to invalidate every cached title after a logic change
_TITLE_INPUT_CHARS = 4000  # hard cap on the API input
_TITLE_SYSTEM = (
    "You name coding sessions. From the text below, reply with ONLY a short "
    "topic title of at most 5 words. No markdown, no quotes, no trailing "
    "punctuation, no leading 'Title:'."
)


# OAuth token refresh, the same grant the CLI performs. When the cached access
# token in ~/.claude/.credentials.json has expired we exchange the refresh
# token for a fresh one and write it back, so /usage and the models catalog
# keep working between interactive CLI runs (headless `claude -p` refreshes its
# own copy but may not touch the file the daemon reads).
_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_TOKEN_SKEW_MS = 60_000  # refresh a touch early to dodge edge-of-expiry 401s


def _creds_path() -> Path:
    return Path.home() / ".claude" / ".credentials.json"


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
    daemon's env/config. Otherwise read the interactive credentials file; if
    its access token has expired, exchange the stored refresh token for a fresh
    one and write the new tokens back (mirroring what the CLI does on each
    run), so /usage keeps working even when no job has run recently."""
    env = getattr(config, "claude_env", None) or {}
    tok = env.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok and str(tok).strip():
        return str(tok).strip()
    path = _creds_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
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
            _write_creds(path, data)
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


def fetch_usage(config) -> dict:
    """Return {"ok": True, "buckets": [...]} or {"ok": False, "error": str}."""
    token = _oauth_token(config)
    if not token:
        return {"ok": False,
                "error": "No Claude sign-in found — run `claude` on the Mac to log in."}
    headers = dict(_USAGE_HEADERS)
    headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(_USAGE_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {"ok": False,
                    "error": "Claude sign-in expired — run `claude` on the Mac."}
        return {"ok": False, "error": "Usage request failed (HTTP %d)" % e.code}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": "Could not reach Anthropic: %s" % e}
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": "Unexpected usage response"}
    return {"ok": True, "buckets": _buckets_from_usage(raw)}


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


def _clean_title(text: str) -> str:
    """Normalize a model reply into a bare title: drop markdown/quotes/leading
    '#', collapse whitespace, trim trailing punctuation, clamp for mobile."""
    t = " ".join((text or "").split())
    t = t.lstrip("#").strip()
    t = t.strip("*_`\"'").strip()
    t = t.rstrip(".:;,").strip()
    if len(t) > _TITLE_MAX_CHARS:
        t = t[: _TITLE_MAX_CHARS - 1].rstrip() + "\u2026"
    return t


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


def _summarize_title(config, text: str) -> str:
    """One short title from Haiku via the subscription OAuth token, or "" on
    any failure (caller keeps the heuristic title)."""
    text = text.strip()[:_TITLE_INPUT_CHARS]
    if not text:
        return ""
    token = _oauth_token(config)
    if not token:
        return ""
    body = json.dumps({
        "model": _TITLE_MODEL,
        "max_tokens": 32,
        "system": _TITLE_SYSTEM,
        "messages": [{"role": "user",
                      "content": "Coding session to name:\n\n" + text}],
    }).encode("utf-8")
    req = urllib.request.Request(_MESSAGES_URL, data=body, headers={
        "Authorization": "Bearer " + token,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
        "User-Agent": "agentremoted",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            json.JSONDecodeError, ValueError):
        return ""
    out = "".join(b.get("text", "") for b in raw.get("content", [])
                  if isinstance(b, dict) and b.get("type") == "text")
    return _clean_title(out)


class _TitleCache:
    """Persistent session_id -> {title, sig} map, filled lazily by a single
    background worker so the sessions list never blocks on the Haiku call."""

    def __init__(self, config):
        self._config = config
        self._path = CONFIG_DIR / "session_titles.json"
        self._lock = threading.Lock()
        self._map = {}
        self._queue = queue.Queue()
        self._pending = set()
        self._worker = None
        self._load()

    def _load(self):
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._map = {k: v for k, v in data.items()
                         if isinstance(v, dict) and v.get("title")}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.parent / (self._path.name + ".tmp")
            tmp.write_text(json.dumps(self._map), encoding="utf-8")
            os.replace(str(tmp), str(self._path))
        except OSError:
            pass

    def get(self, session_id: str, sig: str) -> str:
        with self._lock:
            entry = self._map.get(session_id)
            if entry and entry.get("sig") == sig:
                return entry.get("title", "")
        return ""

    def request(self, session_id: str, sig: str, text: str) -> None:
        """Queue a title generation unless one is cached (same sig) or already
        in flight for this session."""
        if not text:
            return
        with self._lock:
            entry = self._map.get(session_id)
            if entry and entry.get("sig") == sig:
                return
            if session_id in self._pending:
                return
            self._pending.add(session_id)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, daemon=True)
                self._worker.start()
        self._queue.put((session_id, sig, text))

    def _run(self):
        while True:
            session_id, sig, text = self._queue.get()
            try:
                title = _summarize_title(self._config, text)
                if title:
                    with self._lock:
                        self._map[session_id] = {"title": title, "sig": sig}
                        self._save()
            finally:
                with self._lock:
                    self._pending.discard(session_id)
                self._queue.task_done()


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
        self._titles = _TitleCache(config)
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
        for f in files:
            if user_only and not self._is_user_session(f):
                continue
            summary = self._session_summary(f)
            if summary:
                sessions.append(summary)
            if len(sessions) >= limit:
                break
        return sessions

    def search_sessions(self, query: str, project_id: str = None,
                        limit: int = 25, user_only: bool = True) -> list:
        """Full-text search over session titles + human-visible message text.

        Scans the most recent MAX_SCAN sessions (optionally filtered by
        project). Returns session summaries plus a `snippet` of the first
        match; the phone highlights the query client-side.
        """
        q = search_util.normalize_query(query)
        if not q:
            return []
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

        results = []
        for path in files[:search_util.MAX_SCAN]:
            if user_only and not self._is_user_session(path):
                continue
            summary = self._session_summary(path)
            if not summary:
                continue
            hit = self._match_session(path, summary, q)
            if hit is None:
                continue
            row = dict(summary)
            row["snippet"] = hit
            results.append(row)
            if len(results) >= limit:
                break
        results.sort(key=search_util.rank_key, reverse=True)
        return results

    def _match_session(self, path: Path, summary: dict, query: str):
        """Return a snippet for the first hit in this session, or None.

        Cheap fields (title / last_text / cwd) first; only open the full
        transcript when those miss. Line-level `query in line` reject skips
        JSON parse for the vast majority of lines.
        """
        title = summary.get("title") or ""
        if search_util.contains_ci(title, query):
            return search_util.make_snippet(title, query)
        last = summary.get("last_text") or ""
        if search_util.contains_ci(last, query):
            return search_util.make_snippet(last, query)
        cwd = summary.get("cwd") or ""
        if search_util.contains_ci(cwd, query):
            return search_util.make_snippet(cwd, query)

        q_lower = query.casefold()
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if q_lower not in line.casefold():
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

    def get_messages(self, session_id: str, offset: int = None, limit: int = 50) -> dict:
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
        # uuid -> parentUuid for EVERY record, not just the messages: the
        # chain threads through system / file-history-snapshot lines, so a
        # message's parent is usually not another message.
        parent_of = {}
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                obj = _safe_json(line)
                if isinstance(obj, dict):
                    uid = obj.get("uuid")
                    if uid:
                        parent_of[uid] = obj.get("parentUuid")
                msg = self._parse_line(line, obj)
                if msg:
                    messages.append(msg)
        messages = _active_branch(messages, parent_of)
        t1 = time.perf_counter()

        total = len(messages)
        if offset is None:
            offset = max(0, total - limit)
        offset = max(0, offset)
        window = messages[offset: offset + limit]
        for msg in window:
            msg["blocks"] = _render_blocks(msg["role"], msg["text"])
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

    def _session_summary(self, path: Path) -> dict:
        head = _read_head_lines(path, _HEAD_BYTES)
        tail = _read_tail_lines(path, _TAIL_BYTES)

        summary_text = ""
        ai_title = ""
        first_user_text = ""
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
            if obj.get("type") == "user" and not first_user_text:
                first_user_text = _human_user_text(obj)

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
                if text:
                    last_role = obj["type"]
                    last_text = text
            if last_ts and last_text and tail_title and model:
                break

        # Prefer a Haiku-summarized title (cached on disk). It is regenerated on
        # every context compaction: early on it names the first message, then
        # re-summarizes from each compaction's summary blob. Until it is
        # generated, fall back to Claude Code's own title / first line so the
        # row is never blank.
        base_title = _preview(tail_title or ai_title or summary_text
                              or first_user_text, _MAX_TITLE) or path.stem
        title = base_title
        compactions, first, last_summary = self._scan_session(path)
        if not first:
            first = first_user_text or last_text
        if first or last_summary:
            sig, src = _title_source(compactions, first, last_summary)
            cached = self._titles.get(path.stem, sig)
            if cached:
                title = cached
            else:
                self._titles.request(path.stem, sig, src)

        return {
            "id": path.stem,
            "project_id": path.parent.name,
            "cwd": cwd,
            "git_branch": git_branch,
            "title": title,
            "started": started,
            "last_active": last_ts,
            "last_role": last_role,
            "last_text": _preview(last_text),
            "model": model,
            "size_bytes": path.stat().st_size,
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
# stores for remote MCP servers (~/.claude/.credentials.json "mcpOAuth"):
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
    path = _creds_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
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
        _write_creds(path, data)
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

    def type_into_tui(self, session_id: str, text: str) -> str:
        """Type a message into a session's live interactive TUI ("" or err)."""
        return self._interactive_mgr().type_text(session_id, text)

    def capture_tui(self, session_id: str) -> dict:
        return self._interactive_mgr().capture_tui(session_id)

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
            # "/rewind N" (N user messages back) is driven in that TUI. Not a
            # grok capability: its /rewind takes a prompt, not a count.
            "rewind": True,
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
    _BUILTIN_SLASH = ["/compact", "/exit", "/rewind"]

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
        extra_env = getattr(self.config, "claude_env", None) or {}
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
                    detail = tool_detail(block.get("input") or {})
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

    def tick(self, job):
        pass

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
