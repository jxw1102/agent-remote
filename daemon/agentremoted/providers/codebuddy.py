"""Tencent CodeBuddy Code (``codebuddy`` / ``cbc``) for Agent Remote.

Sessions are JSONL under

    ${CODEBUDDY_CONFIG_DIR:-~/.codebuddy}/projects/<munged-cwd>/<uuid>.jsonl

Two record shapes live there, and the store reads both:

* older CLI builds wrote Claude-Code-shaped lines directly, so the store is
  a :class:`agentremoted.providers.claude.ClaudeStore` on that directory;
* the current ``ptc`` agent (``codebuddy.ai/mainAgent: "ptc"`` in the
  ``session-meta`` records) writes Codex-shaped lines — ``message`` records
  with ``input_text``/``output_text`` blocks, separate ``reasoning`` /
  ``function_call`` / ``function_call_result`` records, ``id``/``parentId``
  instead of ``uuid``/``parentUuid``, and epoch-ms timestamps.
  :func:`_ptc_record` folds those into the Claude shape record by record, so
  listing, transcripts, steps, search and /rewind all share the one seam.

Headless turns run::

    codebuddy -p <prompt> --output-format stream-json --verbose \\
        --dangerously-skip-permissions --permission-mode bypassPermissions \\
        [--resume <uuid>] [--model <id>] [--effort <level>]

Interactive turns (when ``tmux`` is on PATH) drive the real TUI in a
detached ``cbc-*`` session. See :mod:`codebuddy_interactive`.

``stream-json`` matches Claude Code (``system``/``init``, ``assistant``,
``result``). ``-y`` is required for tool use in print mode. CodeBuddy still
asks on HIGH/CRITICAL checks; a phone cannot answer those in headless mode,
so a turn that hits one waits until the job timeout. Plan mode is the
exception: it is passed through without ``-y``. The interactive TUI lifts
a permission dialog onto the phone when the pane text matches the Claude
dialog shape.

Login is ``CODEBUDDY_API_KEY``, ``CODEBUDDY_AUTH_TOKEN``, an ``apiKeyHelper``
in settings, or the CLI's own ``CodeBuddyExtension`` auth file. ``codebuddy``
on this host was 2.169.0 when the flag set was checked.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import procutil
from .. import titles
from ..render_blocks import markdown_to_blocks
from .claude import (ClaudeStore, _human_user_text, _is_safe_id, _safe_json,
                     tool_status_parts)
from .. import providers

_MODELS_TTL_S = 900
_HELP_RE = re.compile(r"Currently supported:\s*\(([^)]*)\)")
_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
_EFFORT_OK = set(_EFFORTS) | {"minimal"}
_PERM_MODES = {
    "acceptEdits", "bypassPermissions", "default", "plan", "dontAsk", "auto",
}
# Aliases and ids from `codebuddy --help` on 2.169.0. Live help replaces this
# when the binary is on PATH.
_FALLBACK_MODELS = [
    "default-model", "fast-model", "balanced-model", "primary-model", "deep-model",
    "hy4-preview", "hy3", "deepseek-v4.1-flash", "gpt-6-astra", "gpt-5.6-sol",
    "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", "gpt-5.4", "gemini-3.5-flash",
    "glm-5.3-flash", "glm-5.3", "glm-5.2", "kimi-k3", "kimi-k2.6",
    "kimi-k2.8-preview",
]
_PHASE_BY_TOOL = {
    "Edit": "editing", "Write": "editing", "MultiEdit": "editing",
    "Delete": "editing", "NotebookEdit": "editing",
    "Read": "reading",
    "Grep": "searching", "Glob": "searching", "LS": "searching",
    "Bash": "running", "Shell": "running",
    "WebFetch": "browsing", "WebSearch": "browsing",
    "Task": "delegating", "Agent": "delegating",
}
_AUTH_PRODUCT = "Tencent-Cloud.coding-copilot.info"


def _iso_ms(ms):
    """Epoch milliseconds -> Claude's ISO-8601 timestamp string."""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return ""
    try:
        stamp = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ptc_record(obj):
    """One ptc-agent record folded into the Claude Code record schema.

    Returns ``obj`` untouched when it is not one of the ptc shapes. Every
    record that carries ``id``/``parentId`` gets ``uuid``/``parentUuid``
    mirrored, because the branch walk in ClaudeStore.get_messages threads
    through ALL records, not only the messages; and any numeric epoch-ms
    timestamp becomes the ISO string the API contract promises clients.
    """
    if not isinstance(obj, dict):
        return obj
    rec = dict(obj)
    stamp = obj.get("timestamp")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
        iso = _iso_ms(stamp)
        if iso:
            rec["timestamp"] = iso
    kind = obj.get("type")
    if kind == "message" and obj.get("role") in ("user", "assistant"):
        role = obj["role"]
        blocks = []
        for block in obj.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") in ("input_text", "output_text") \
                    and block.get("text"):
                blocks.append({"type": "text", "text": block["text"]})
        message = {"role": role, "content": blocks}
        if role == "assistant":
            model = str((obj.get("providerData") or {}).get("model") or "")
            if model:
                message["model"] = model
        rec.update(type=role, uuid=obj.get("id"),
                   parentUuid=obj.get("parentId"), message=message)
    elif kind == "function_call":
        raw = obj.get("arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        message = {"role": "assistant", "content": [{
            "type": "tool_use",
            "id": obj.get("callId") or obj.get("id"),
            "name": obj.get("name") or "tool",
            "input": args,
        }]}
        # Tool calls carry the model that made them. Sessions whose assistant
        # text records omit providerData still get a model in their summary
        # (the Android/web "Model" row used to fall back to "default").
        model = str((obj.get("providerData") or {}).get("model") or "")
        if model:
            message["model"] = model
        rec.update(type="assistant", uuid=obj.get("id"),
                   parentUuid=obj.get("parentId"), message=message)
    elif kind == "function_call_result":
        output = obj.get("output")
        if isinstance(output, dict):
            text = output.get("text") or ""
        elif isinstance(output, str):
            text = output
        else:
            text = ""
        rec.update(type="user", uuid=obj.get("id"),
                   parentUuid=obj.get("parentId"),
                   message={"role": "user", "content": [{
                       "type": "tool_result",
                       "tool_use_id": obj.get("callId") or obj.get("id"),
                       "content": text,
                       "is_error": obj.get("status") not in (None, "", "completed"),
                   }]})
    elif kind == "reasoning":
        parts = []
        for block in list(obj.get("rawContent") or []) + list(obj.get("content") or []):
            if isinstance(block, dict) and block.get("text"):
                parts.append(block["text"])
        rec.update(type="assistant", uuid=obj.get("id"),
                   parentUuid=obj.get("parentId"),
                   message={"role": "assistant", "content": [{
                       "type": "thinking", "thinking": "\n".join(parts),
                   }]})
    elif "id" in obj and "uuid" not in obj:
        # Chain-bearing passthrough records (session-meta) must still be
        # reachable from the parentUuid walk.
        rec["uuid"] = obj["id"]
        if "parentId" in obj:
            rec["parentUuid"] = obj["parentId"]
    return rec


def codebuddy_home(config) -> Path:
    raw = getattr(config, "codebuddy_home_path", None)
    if raw:
        return Path(raw)
    return Path(str(getattr(config, "codebuddy_home", "")
                    or (Path.home() / ".codebuddy"))).expanduser()


def codebuddy_bin(config) -> str:
    return str(getattr(config, "codebuddy_bin", None) or "codebuddy")


def codebuddy_env(config, base: dict = None) -> dict:
    """Process env plus ``CODEBUDDY_CONFIG_DIR`` and ``codebuddy_env``."""
    env = dict(os.environ if base is None else base)
    env["CODEBUDDY_CONFIG_DIR"] = str(codebuddy_home(config))
    extra = getattr(config, "codebuddy_env", None) or {}
    env.update({str(k): str(v) for k, v in extra.items()})
    return env


def auth_info_dir() -> Path:
    """Directory of ``*.info`` login files the CLI reads (outside ~/.codebuddy)."""
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return home / "Library" / "Application Support" / "CodeBuddyExtension" / "Data" / "Public" / "auth"
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(local) / "CodeBuddyExtension" / "Data" / "Public" / "auth"
    return home / ".local" / "share" / "CodeBuddyExtension" / "Data" / "Public" / "auth"


def _read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _settings(home: Path) -> dict:
    merged = {}
    for name in ("settings.json", "settings.local.json"):
        data = _read_json(home / name)
        if data:
            merged.update(data)
    return merged


def _env_value(config, home: Path, key: str) -> str:
    extra = getattr(config, "codebuddy_env", None) or {}
    val = str(extra.get(key) or os.environ.get(key) or "").strip()
    if val:
        return val
    env = _settings(home).get("env")
    if isinstance(env, dict):
        return str(env.get(key) or "").strip()
    return ""


def _login_file() -> Path | None:
    """Newest CodeBuddy auth ``.info``, preferring the product id file.

    ``CODEBUDDY_CLI_AUTH_FILE`` overrides the directory scan (tests, and the
    same knob the DSH CodeBuddy plugin documents).
    """
    override = str(os.environ.get("CODEBUDDY_CLI_AUTH_FILE") or "").strip()
    if override:
        path = Path(override).expanduser()
        return path if path.is_file() and path.stat().st_size > 2 else None
    directory = auth_info_dir()
    if not directory.is_dir():
        return None
    preferred = directory / _AUTH_PRODUCT
    candidates = [preferred] if preferred.is_file() else []
    try:
        candidates.extend(
            p for p in directory.glob("*.info")
            if p.is_file() and p != preferred)
    except OSError:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            if path.stat().st_size > 2 and _read_json(path):
                return path
        except OSError:
            continue
    return None


class CodebuddyStore(ClaudeStore):
    """Both CodeBuddy transcript shapes under ``<home>/projects``."""

    def __init__(self, home: Path, config=None):
        self.home = Path(home)
        super().__init__(self.home / "projects", config)

    def _rec(self, obj):
        # Old sessions are already Claude-shaped (identity-convert); ptc
        # sessions need the fold. Cheap either way: dict copies, no I/O.
        return _ptc_record(obj)


_USAGE_TTL_S = 60.0
_usage_cache = (0.0, None)
_usage_lock = threading.Lock()


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return ("%.1fM" if n < 10_000_000 else "%.0fM") % (n / 1_000_000.0)
    if n >= 1_000:
        return ("%.1fk" if n < 10_000 else "%.0fk") % (n / 1_000.0)
    return str(int(n))


def _usage_stats(home: Path) -> dict:
    """CodeBuddy has no quota endpoint; aggregate its own transcripts.

    Every function_call entry carries providerData.usage (input/output/
    total tokens) and the model that made it, so the same numbers the CLI
    /stats dashboard shows can be summed locally - no extra process, no
    network. Days are local dates; a session counts once it has any message.
    """
    projects = home / "projects"
    today = datetime.now().date()
    week_start = today - timedelta(days=6)
    days = {}
    models = {}
    sessions = set()
    active = set()
    if not projects.is_dir():
        return {"days": days, "models": models, "sessions": sessions,
                "active": active, "today": today, "week_start": week_start}
    for path in projects.glob("*/*.jsonl"):
        had_msg = False
        try:
            handle = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                obj = _safe_json(line)
                if not isinstance(obj, dict):
                    continue
                kind = obj.get("type")
                if kind not in ("message", "function_call"):
                    continue
                had_msg = True
                try:
                    day = datetime.fromtimestamp(
                        int(obj.get("timestamp")) / 1000).date()
                except (TypeError, ValueError, OSError, OverflowError):
                    continue
                row = days.setdefault(
                    day, {"messages": 0, "tools": 0, "tokens": 0})
                if kind == "message":
                    row["messages"] += 1
                    continue
                row["tools"] += 1
                provider = obj.get("providerData") or {}
                usage = provider.get("usage") if isinstance(
                    provider.get("usage"), dict) else {}
                try:
                    tokens = int(usage.get("totalTokens") or 0)
                except (TypeError, ValueError):
                    tokens = 0
                row["tokens"] += tokens
                model = str(provider.get("model") or "")
                if model and day >= week_start:
                    models[model] = models.get(model, 0) + tokens
        if had_msg:
            sessions.add(path.stem)
    for day, row in days.items():
        if row["messages"] or row["tools"]:
            active.add(day)
    return {"days": days, "models": models, "sessions": sessions,
            "active": active, "today": today, "week_start": week_start}


class CodebuddyRunner:
    """One turn as ``codebuddy -p`` with stream-json on stdout."""

    name = "codebuddy"
    _models_cache = (0.0, None)
    _models_lock = threading.Lock()

    def __init__(self, config):
        self.config = config
        self._interactive = None
        self._interactive_lock = threading.Lock()

    def _interactive_mgr(self):
        with self._interactive_lock:
            if self._interactive is None:
                from .codebuddy_interactive import CodebuddyInteractiveManager
                self._interactive = CodebuddyInteractiveManager(self.config)
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

    def on_hook(self, payload: dict, secret: str, tui_name: str = "") -> bool:
        return self._interactive_mgr().on_hook(payload, secret, tui_name)

    def _bin(self) -> str:
        return codebuddy_bin(self.config)

    def _home(self) -> Path:
        return codebuddy_home(self.config)

    def _on_path(self) -> bool:
        binary = self._bin()
        return bool(shutil.which(binary) or (binary and Path(binary).is_file()))

    def capabilities(self) -> dict:
        from .codebuddy_interactive import tmux_available
        has_tmux = tmux_available()
        return {
            "queue": True,
            "stop": True,
            "projects": True,
            "ws_status": True,
            # Headless turns auto-approve. Interactive can still lift a
            # pane dialog onto the phone; that does not need this flag.
            "permissions": False,
            "permission_modes": False,
            "requires_cwd": True,
            "can_set_model": True,
            "can_set_effort": True,
            # Activity/token stats from the local transcripts; CodeBuddy
            # has no subscription-limits endpoint to query.
            "can_show_usage": True,
            "interactive": has_tmux,
            "live_tui": has_tmux,
            "rewind": True,
        }

    def usage(self) -> dict:
        """Activity and token usage, read from the local transcripts.

        CodeBuddy publishes no subscription quota (no /usage command and no
        limits endpoint), so the Usage sheet shows what the CLI's own /stats
        dashboard reports: today, the last week, which models did the work,
        and the activity streak."""
        global _usage_cache
        now = time.monotonic()
        cached_at, cached = _usage_cache
        if cached is not None and now - cached_at < _USAGE_TTL_S:
            return dict(cached)
        with _usage_lock:
            cached_at, cached = _usage_cache
            if cached is not None and time.monotonic() - cached_at < _USAGE_TTL_S:
                return dict(cached)
            stats = _usage_stats(self._home())
            day = stats["days"]

            def window(since):
                row = {"messages": 0, "tools": 0, "tokens": 0}
                for date, val in day.items():
                    if date >= since:
                        for key in row:
                            row[key] += val[key]
                return row

            if not stats["sessions"]:
                result = {"ok": False, "error": "No CodeBuddy activity yet",
                          "provider": "codebuddy", "account": "",
                          "account_id": ""}
                _usage_cache = (time.monotonic(), dict(result))
                return result
            today = window(stats["today"])
            week = window(stats["week_start"])
            streak = 0
            cur = stats["today"]
            if cur not in stats["active"]:
                cur -= timedelta(days=1)
            while cur in stats["active"]:
                streak += 1
                cur -= timedelta(days=1)

            def line(row):
                return "%s tokens · %d messages · %d tool calls" % (
                    _fmt_tokens(row["tokens"]), row["messages"], row["tools"])

            buckets = [
                {"title": "Today", "percent": 0, "show_bar": False,
                 "severity": "normal", "resets_text": line(today)},
                {"title": "Last 7 days", "percent": 0, "show_bar": False,
                 "severity": "normal", "resets_text": line(week)},
            ]
            top = sorted(stats["models"].items(), key=lambda kv: -kv[1])[:3]
            if top:
                buckets.append({
                    "title": "Models · 7d", "percent": 0, "show_bar": False,
                    "severity": "normal",
                    "resets_text": " · ".join(
                        "%s %s" % (name, _fmt_tokens(tokens))
                        for name, tokens in top),
                })
            buckets.append({
                "title": "Activity", "percent": 0, "show_bar": False,
                "severity": "normal",
                "resets_text": "%d session%s · %d active day%s · streak %d" % (
                    len(stats["sessions"]), "" if len(stats["sessions"]) == 1 else "s",
                    len(stats["active"]), "" if len(stats["active"]) == 1 else "s",
                    streak),
            })
            result = {"ok": True, "buckets": buckets, "provider": "codebuddy",
                      "account": "", "account_id": ""}
            _usage_cache = (time.monotonic(), dict(result))
            return result

    def auth_health(self) -> dict:
        on_path = self._on_path()
        tail = "" if on_path else "; `codebuddy` not on PATH"
        home = self._home()
        if _env_value(self.config, home, "CODEBUDDY_API_KEY"):
            return {
                "cli": "codebuddy",
                "cli_on_path": on_path,
                "mode": "api_key",
                "status": "ok" if on_path else "warning",
                "detail": "CODEBUDDY_API_KEY is set" + tail,
            }
        if _env_value(self.config, home, "CODEBUDDY_AUTH_TOKEN"):
            return {
                "cli": "codebuddy",
                "cli_on_path": on_path,
                "mode": "token",
                "status": "ok" if on_path else "warning",
                "detail": "CODEBUDDY_AUTH_TOKEN is set" + tail,
            }
        if str(_settings(home).get("apiKeyHelper") or "").strip():
            return {
                "cli": "codebuddy",
                "cli_on_path": on_path,
                "mode": "helper",
                "status": "ok" if on_path else "warning",
                "detail": "apiKeyHelper is configured in CodeBuddy settings" + tail,
            }
        login = _login_file()
        if login is not None:
            return {
                "cli": "codebuddy",
                "cli_on_path": on_path,
                "mode": "subscription",
                "status": "ok" if on_path else "warning",
                "detail": "CodeBuddy login file is present" + tail,
            }
        return {
            "cli": "codebuddy",
            "cli_on_path": on_path,
            "mode": "none",
            "status": "missing",
            "detail": ("No CodeBuddy login or API key on this host — "
                       "run `codebuddy` and sign in, or set CODEBUDDY_API_KEY") + tail,
        }

    def efforts(self) -> list:
        extra = [str(e).strip() for e in
                 (getattr(self.config, "efforts", None) or []) if str(e).strip()]
        out = []
        for name in list(_EFFORTS) + extra:
            if name and name not in out:
                out.append(name)
        return out

    def models(self) -> list:
        live = self._cli_models()
        extra = [str(m).strip() for m in
                 (getattr(self.config, "models", None) or []) if str(m).strip()]
        out = ["default"]
        for name in (live or _FALLBACK_MODELS) + extra:
            if name and name not in out and name != "default":
                out.append(name)
        return out

    def _cli_models(self) -> list:
        now = time.time()
        with self._models_lock:
            cached_at, cached = CodebuddyRunner._models_cache
            if cached is not None and now - cached_at < _MODELS_TTL_S:
                return cached
        binary = self._bin()
        if not (shutil.which(binary) or Path(binary).is_file()):
            return []
        try:
            out = subprocess.run(
                procutil.argv_for([binary, "--help"]), capture_output=True, text=True,
                timeout=20, env=codebuddy_env(self.config)).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        match = _HELP_RE.search(out or "")
        if not match:
            return []
        names = []
        for part in match.group(1).split(","):
            name = part.strip()
            if name and name not in names:
                names.append(name)
        with self._models_lock:
            CodebuddyRunner._models_cache = (time.time(), names)
        return names

    _BUILTIN_SLASH = ["/goal", "/rewind"]

    def slash_commands(self) -> list:
        commands = set(self._BUILTIN_SLASH)
        for extra in getattr(self.config, "slash_commands", None) or []:
            if isinstance(extra, str) and extra.strip():
                commands.add(extra.strip())
        home = self._home()
        try:
            for path in (home / "commands").glob("*.md"):
                commands.add("/" + path.stem)
        except OSError:
            pass
        try:
            for path in (home / "skills").glob("*/SKILL.md"):
                commands.add("/" + path.parent.name)
        except OSError:
            pass
        return sorted(commands)

    def title_for(self, text: str) -> str:
        cwd = str(titles.titler_cwd())
        cmd = [
            self._bin(), "-p", titles.prompt_for(text),
            "--output-format", "text",
            "--dangerously-skip-permissions",
        ]
        cmd = procutil.argv_for(cmd)
        try:
            out = subprocess.run(
                cmd, cwd=cwd, env=codebuddy_env(self.config),
                capture_output=True, text=True, timeout=90).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
        return titles.title_from_output(out)

    def prepare(self, job, mode):
        if not job.cwd:
            default = str(getattr(self.config, "codebuddy_default_cwd", "") or "").strip()
            if default:
                job.cwd = os.path.expanduser(default)
        if not job.cwd:
            raise providers.RunnerError(
                "CodeBuddy needs a project directory")
        binary = self._bin()
        if not (shutil.which(binary) or Path(binary).is_file()):
            raise providers.RunnerError(
                "`%s` is not on PATH — install CodeBuddy Code "
                "(`npm install -g @tencent-ai/codebuddy-code`)" % binary)
        chosen = (mode or "").strip() or "bypassPermissions"
        if chosen == "interactive" or chosen not in _PERM_MODES:
            chosen = "bypassPermissions"
        cmd = [
            binary, "-p", job.prompt or "",
            "--output-format", "stream-json",
            "--verbose",
        ]
        # Print mode blocks tool use unless this is set. Plan must not edit.
        if chosen != "plan":
            cmd.append("--dangerously-skip-permissions")
        cmd += ["--permission-mode", chosen]
        cmd += str(getattr(self.config, "codebuddy_flags", "") or "").split()
        if job.session_id:
            cmd += ["--resume", job.session_id]
        model = str(getattr(job, "model", "") or "").strip()
        if model and model != "default":
            cmd += ["--model", model]
        effort = str(getattr(job, "effort", "") or "").strip()
        if effort and effort != "default":
            cmd += ["--effort", effort]
        return cmd, codebuddy_env(self.config)

    def handle_stream_line(self, job, line: str):
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(obj, dict):
            return
        kind = obj.get("type") or ""
        if kind == "system" and obj.get("subtype") == "init":
            sid = str(obj.get("session_id") or obj.get("sessionId") or "")
            with job.lock:
                if sid:
                    job.new_session_id = sid
            job.add_event("init", session_id=sid, model=obj.get("model") or "")
            return
        if kind == "assistant":
            message = obj.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for block in content or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text"):
                    text = block["text"]
                    job.add_event("text", text=text, blocks=markdown_to_blocks(text))
                    job.set_phase("writing", text[-160:])
                elif block.get("type") == "tool_use":
                    name = block.get("name") or "tool"
                    tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
                    desc, cmd = tool_status_parts(tool_input)
                    job.add_event("tool", name=name, detail=cmd or desc)
                    job.set_phase(_PHASE_BY_TOOL.get(name, "tool"), desc or cmd or name)
                elif block.get("type") == "thinking":
                    job.set_phase("thinking", "")
            return
        if kind == "result":
            text = obj.get("result") or ""
            if not text and isinstance(obj.get("errors"), list):
                text = "; ".join(str(item) for item in obj["errors"] if item)
            is_error = bool(obj.get("is_error"))
            with job.lock:
                if text:
                    job.result_text = text
                if is_error:
                    job.error = text or "codebuddy reported an error"
            job.add_event("result", is_error=is_error,
                          duration_ms=obj.get("duration_ms") or 0,
                          cost_usd=obj.get("total_cost_usd") or 0)

    def tick(self, job):
        return None

    def finalize(self, job, returncode, stderr_tail):
        return None

    def cleanup(self, job):
        return None

    def rewind_session(self, session_id: str, steps: int):
        """Cut the JSONL at the Nth-last human message on the active branch.

        CodeBuddy rebuilds context from that file on ``--resume``, same as
        Claude Code. Files on disk are not restored. Returns
        ``(steps_done, preview)``.
        """
        sid = (session_id or "").strip()
        if not sid or not _is_safe_id(sid):
            raise providers.RunnerError("rewind needs an existing session")
        # A live TUI holds the pre-cut conversation in memory. Close it so
        # the next interactive turn --resume's the rewound file.
        self._interactive_mgr().close_for_session(sid)
        projects = self._home() / "projects"
        path = None
        if projects.is_dir():
            for entry in projects.iterdir():
                candidate = entry / (sid + ".jsonl")
                if entry.is_dir() and candidate.is_file():
                    path = candidate
                    break
        if path is None:
            raise providers.RunnerError("session transcript not found")
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        records = []
        for idx, line in enumerate(lines):
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                records.append((idx, _ptc_record(obj)))
        chain, last_uuid = {}, None
        for _, obj in records:
            uid = obj.get("uuid")
            if uid and obj.get("type") in ("user", "assistant"):
                chain[uid] = obj.get("parentUuid")
                last_uuid = uid
        active, broken = set(), False
        node = last_uuid
        while node:
            if node in active:
                break
            active.add(node)
            parent = chain.get(node)
            if parent is not None and parent not in chain:
                broken = True
                break
            node = parent
        human = []
        for idx, obj in records:
            if obj.get("type") != "user":
                continue
            uid = obj.get("uuid")
            if not broken and active and uid and uid not in active:
                continue
            if _human_user_text(obj):
                human.append((idx, obj))
        if not human:
            raise providers.RunnerError("nothing to rewind — no user messages yet")
        steps = max(1, min(int(steps), len(human)))
        cut_idx, target = human[-steps]
        backup = path.parent / (path.name + ".rewind-bak")
        try:
            backup.write_text(raw, encoding="utf-8")
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as handle:
            if cut_idx:
                handle.write("\n".join(lines[:cut_idx]) + "\n")
        preview = " ".join(_human_user_text(target).split())[:120]
        return steps, preview
