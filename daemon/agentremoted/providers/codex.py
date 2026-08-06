"""Codex harness — OpenAI Codex CLI sessions for Agent Remote.

Sessions live in ``$CODEX_HOME/state_*.sqlite`` (``threads`` table) with the
transcript at ``rollout_path`` (JSONL). Turns run via::

    codex exec --json -C <cwd> [flags] <prompt>
    codex exec resume --json <session_id> <prompt>

Stream events (``--json``) are JSONL lines of the form::

    {"type":"thread.started","thread_id":"…"}
    {"type":"item.started","item":{"type":"command_execution","command":"…"}}
    {"type":"item.completed","item":{"type":"agent_message","text":"…"}}
    {"type":"turn.completed","usage":{…}}

Stdin is closed immediately so the CLI does not hang waiting for more input.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import providers
from ..render_blocks import inline_to_rich, markdown_to_blocks
from .. import search_util

log = logging.getLogger(__name__)

_MAX_TITLE = 80
_MAX_PREVIEW = 160
_STATE_GLOB = "state_*.sqlite"

# ------------------------------------------------------------------ usage
#
# Codex has no `codex usage` CLI flag. ChatGPT plan limits are exposed by the
# local app-server RPC ``account/rateLimits/read`` (what the TUI /status uses)
# and, as a fallback, HTTP ``/backend-api/codex/usage`` with OAuth from
# ~/.codex/auth.json. Shape matches Claude/Grok for the Usage sheet.

_USAGE_URL = "https://chatgpt.com/backend-api/codex/usage"
_TOKEN_URL = "https://auth.openai.com/oauth/token"
# ChatGPT desktop / Codex CLI public client id (from access-token claims).
_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_TOKEN_SKEW_S = 120  # refresh a bit early
_USAGE_UA = "codex_cli_rs/0.146.0"
_APP_SERVER_INIT_S = 8
_APP_SERVER_RPC_S = 12
_usage_app_server_lock = threading.Lock()


def _auth_path(config) -> Path:
    home = Path(getattr(config, "codex_home_path", None) or
                (Path.home() / ".codex")).expanduser()
    return home / "auth.json"


def _jwt_payload(token: str) -> dict:
    try:
        part = (token or "").split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode("ascii")))
    except (IndexError, ValueError, json.JSONDecodeError, OSError):
        return {}


def _jwt_fresh(token: str) -> bool:
    exp = _jwt_payload(token).get("exp")
    try:
        return float(exp) > time.time() + _TOKEN_SKEW_S
    except (TypeError, ValueError):
        return False


def _write_auth(path: Path, data: dict) -> None:
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


def _refresh_chatgpt_token(refresh_token: str, client_id: str = "") -> dict:
    """Exchange refresh_token for a new access_token. Returns updated token
    fields or {} on failure."""
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id or _OAUTH_CLIENT_ID,
    }).encode("utf-8")
    req = urllib.request.Request(
        _TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "codex_cli_rs",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return {}
    access = str(raw.get("access_token") or "").strip()
    if not access:
        return {}
    out = {"access_token": access}
    new_refresh = str(raw.get("refresh_token") or "").strip()
    if new_refresh:
        out["refresh_token"] = new_refresh
    id_tok = str(raw.get("id_token") or "").strip()
    if id_tok:
        out["id_token"] = id_tok
    return out


def _chatgpt_tokens(config) -> tuple[str, str]:
    """Return (access_token, account_id) from ~/.codex/auth.json, refreshing
    when the access JWT is near expiry. Empty strings if unavailable."""
    path = _auth_path(config)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    # API-key mode has no ChatGPT plan limits via this endpoint.
    if (data.get("auth_mode") or "").lower() == "apikey" and not (
            data.get("tokens") or {}):
        return "", ""
    tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
    access = str(tokens.get("access_token") or "").strip()
    account_id = str(tokens.get("account_id") or "").strip()
    refresh = str(tokens.get("refresh_token") or "").strip()
    if access and _jwt_fresh(access):
        if not account_id:
            authc = _jwt_payload(access).get("https://api.openai.com/auth") or {}
            account_id = str(authc.get("chatgpt_account_id") or "").strip()
        return access, account_id
    if refresh:
        claims = _jwt_payload(access) if access else {}
        client_id = str(claims.get("client_id") or _OAUTH_CLIENT_ID)
        updated = _refresh_chatgpt_token(refresh, client_id)
        if updated.get("access_token"):
            tokens.update(updated)
            data["tokens"] = tokens
            data["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                   time.gmtime())
            _write_auth(path, data)
            access = updated["access_token"]
            if not account_id:
                authc = _jwt_payload(access).get(
                    "https://api.openai.com/auth") or {}
                account_id = str(
                    authc.get("chatgpt_account_id") or "").strip()
            return access, account_id
    return access, account_id


def _clamp_pct(value) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _severity(pct: int) -> str:
    if pct >= 90:
        return "critical"
    if pct >= 75:
        return "warning"
    return "normal"


def _window_title(window_seconds) -> str:
    try:
        secs = int(window_seconds or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs <= 0:
        return "Usage limit"
    hours = secs // 3600
    if hours >= 24 * 6:  # ~weekly or monthly window
        days = max(1, round(hours / 24))
        if days >= 28:
            return "Monthly limit"
        if days >= 6:
            return "Weekly limit"
        return "%d-day limit" % days
    if hours >= 1:
        return "%d-hour limit" % hours
    mins = max(1, secs // 60)
    return "%d-min limit" % mins


def _fmt_reset_after(reset_after_seconds, reset_at) -> str:
    """Relative reset line; prefer reset_after_seconds, fall back to reset_at."""
    secs = None
    try:
        if reset_after_seconds is not None:
            secs = int(reset_after_seconds)
    except (TypeError, ValueError):
        secs = None
    if secs is None and reset_at is not None:
        try:
            secs = int(float(reset_at) - time.time())
        except (TypeError, ValueError):
            secs = None
    if secs is None:
        return ""
    if secs <= 0:
        return "Resets soon"
    days = secs // 86400
    hours = (secs % 86400) // 3600
    mins = (secs % 3600) // 60
    if days and hours:
        return "Resets in %d d %d hr" % (days, hours)
    if days:
        return "Resets in %d d" % days
    if hours and mins:
        return "Resets in %d hr %d min" % (hours, mins)
    if hours:
        return "Resets in %d hr" % hours
    if mins:
        return "Resets in %d min" % mins
    return "Resets soon"


def _bucket_from_window(title: str, window: dict) -> dict | None:
    if not isinstance(window, dict):
        return None
    if window.get("used_percent") is None and window.get("usedPercent") is None:
        return None
    pct = _clamp_pct(window.get("used_percent", window.get("usedPercent")))
    # App-server uses windowDurationMins; HTTP uses limit_window_seconds.
    win_s = window.get("limit_window_seconds", window.get("limitWindowSeconds"))
    if win_s is None:
        mins = window.get("window_duration_mins",
                          window.get("windowDurationMins"))
        try:
            win_s = int(mins) * 60 if mins is not None else None
        except (TypeError, ValueError):
            win_s = None
    label = title or _window_title(win_s)
    reset_at = window.get("reset_at", window.get("resetAt",
                          window.get("resets_at", window.get("resetsAt"))))
    return {
        "title": label,
        "percent": pct,
        "resets_text": _fmt_reset_after(
            window.get("reset_after_seconds", window.get("resetAfterSeconds")),
            reset_at,
        ),
        "severity": _severity(pct),
    }


def _buckets_from_app_server_rate_limits(result: dict) -> list:
    """Map account/rateLimits/read result → usage buckets."""
    if not isinstance(result, dict):
        return []
    # Prefer the codex limit id when present.
    by_id = result.get("rateLimitsByLimitId") or result.get(
        "rate_limits_by_limit_id") or {}
    rl = None
    if isinstance(by_id, dict) and by_id.get("codex"):
        rl = by_id.get("codex")
    if rl is None:
        rl = result.get("rateLimits") or result.get("rate_limits")
    if not isinstance(rl, dict):
        return []
    plan = str(rl.get("planType") or rl.get("plan_type") or "").strip()
    buckets = []
    primary = rl.get("primary") or {}
    b = _bucket_from_window(
        _window_title(
            (int(primary.get("windowDurationMins") or 0) * 60)
            if isinstance(primary, dict) else None),
        primary if isinstance(primary, dict) else {},
    )
    if b:
        if plan:
            b["title"] = "%s · %s" % (b["title"], plan)
        buckets.append(b)
    secondary = rl.get("secondary")
    if isinstance(secondary, dict):
        b2 = _bucket_from_window("Secondary limit", secondary)
        if b2:
            buckets.append(b2)
    credits = rl.get("credits") if isinstance(rl.get("credits"), dict) else {}
    if credits.get("unlimited"):
        buckets.append({
            "title": "Credits",
            "percent": 0,
            "resets_text": "Unlimited",
            "severity": "normal",
        })
    return buckets


def _codex_bin(config) -> str:
    return str(getattr(config, "codex_bin", None) or "codex")


def _fetch_usage_via_app_server(config) -> dict:
    """Primary path: brief stdio session with ``codex app-server``.

    Uses the same local auth/cache as the CLI and avoids ChatGPT edge/WAF
    that sometimes 403s bare HTTP probes from the daemon.
    """
    bin_path = _codex_bin(config)
    home = str(Path(getattr(config, "codex_home_path", None)
                    or (Path.home() / ".codex")).expanduser())
    env = os.environ.copy()
    env["CODEX_HOME"] = home
    # Keep noise down; we only need JSON-RPC on stdout.
    env.setdefault("RUST_LOG", "error")

    try:
        proc = subprocess.Popen(
            [bin_path, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "codex CLI not found on PATH"}
    except OSError as e:
        return {"ok": False, "error": "Could not start codex app-server: %s" % e}

    def _send(obj: dict) -> None:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(obj, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    def _recv_for_id(want_id, timeout_s: float):
        """Read stdout lines until a response with id==want_id or timeout.
        Skip notifications (no id / method-only)."""
        deadline = time.time() + timeout_s
        assert proc.stdout is not None
        while time.time() < deadline:
            if proc.poll() is not None:
                return None
            # Blocking readline with remaining budget via select
            import select
            remaining = max(0.05, deadline - time.time())
            ready, _, _ = select.select([proc.stdout], [], [], remaining)
            if not ready:
                continue
            line = proc.stdout.readline()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == want_id:
                return msg
            # notification — ignore
        return None

    try:
        _send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "agentremoted",
                    "title": "agentremoted",
                    "version": "2.4.1",
                },
                "capabilities": {},
            },
        })
        init = _recv_for_id(1, _APP_SERVER_INIT_S)
        if not init or init.get("error"):
            err = (init or {}).get("error") or {}
            return {
                "ok": False,
                "error": "codex app-server initialize failed: %s"
                         % (err.get("message") or "timeout"),
            }
        _send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        resp = _recv_for_id(2, _APP_SERVER_RPC_S)
        if not resp:
            return {"ok": False, "error": "codex rateLimits/read timed out"}
        if resp.get("error"):
            err = resp.get("error") or {}
            msg = str(err.get("message") or err)
            # Unauthenticated / API-key-only installs.
            low = msg.lower()
            if "auth" in low or "login" in low or "sign" in low:
                return {
                    "ok": False,
                    "error": "No Codex ChatGPT sign-in — run `codex login` on the host.",
                }
            return {"ok": False, "error": "codex rateLimits/read: %s" % msg}
        result = resp.get("result") if isinstance(resp.get("result"), dict) else {}
        buckets = _buckets_from_app_server_rate_limits(result)
        if not buckets:
            return {
                "ok": False,
                "error": "No Codex rate-limit windows in app-server response",
            }
        return {"ok": True, "buckets": buckets}
    except (BrokenPipeError, OSError) as e:
        return {"ok": False, "error": "codex app-server I/O error: %s" % e}
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def _buckets_from_codex_usage(raw: dict) -> list:
    """Map ChatGPT codex/usage JSON → [{title, percent, resets_text, severity}]."""
    buckets = []
    plan = str(raw.get("plan_type") or raw.get("planType") or "").strip()
    rl = raw.get("rate_limit") or raw.get("rateLimit") or {}
    if isinstance(rl, dict):
        primary = rl.get("primary_window") or rl.get("primaryWindow")
        b = _bucket_from_window(
            _window_title(
                (primary or {}).get("limit_window_seconds")
                if isinstance(primary, dict) else None),
            primary if isinstance(primary, dict) else {},
        )
        if b:
            if plan:
                b["title"] = "%s · %s" % (b["title"], plan)
            buckets.append(b)
        secondary = rl.get("secondary_window") or rl.get("secondaryWindow")
        if isinstance(secondary, dict):
            b2 = _bucket_from_window("Secondary limit", secondary)
            if b2:
                buckets.append(b2)
    # Code review window (when present).
    cr = raw.get("code_review_rate_limit") or raw.get("codeReviewRateLimit")
    if isinstance(cr, dict):
        win = cr.get("primary_window") or cr.get("primaryWindow") or cr
        b3 = _bucket_from_window("Code review", win if isinstance(win, dict) else {})
        if b3:
            buckets.append(b3)
    # Credits snapshot for paid overage plans.
    credits = raw.get("credits") if isinstance(raw.get("credits"), dict) else {}
    if credits.get("has_credits") or credits.get("unlimited"):
        bal = credits.get("balance")
        if credits.get("unlimited"):
            buckets.append({
                "title": "Credits",
                "percent": 0,
                "resets_text": "Unlimited",
                "severity": "normal",
            })
        elif bal is not None:
            try:
                # balance is remaining fraction or absolute — show as used%
                # when 0–1, else leave percent at 0 with balance text.
                fbal = float(bal)
                if 0 <= fbal <= 1:
                    pct = _clamp_pct((1.0 - fbal) * 100)
                    buckets.append({
                        "title": "Credits",
                        "percent": pct,
                        "resets_text": "%.0f%% remaining" % (fbal * 100),
                        "severity": _severity(pct),
                    })
                else:
                    buckets.append({
                        "title": "Credits",
                        "percent": 0,
                        "resets_text": "Balance %s" % bal,
                        "severity": "normal",
                    })
            except (TypeError, ValueError):
                pass
    return buckets


def _fetch_usage_via_http(config) -> dict:
    """Fallback: ChatGPT backend-api/codex/usage with OAuth from auth.json."""
    access, account_id = _chatgpt_tokens(config)
    if not access:
        return {
            "ok": False,
            "error": "No Codex ChatGPT sign-in found — run `codex login` on the host.",
        }
    headers = {
        "Authorization": "Bearer " + access,
        "Accept": "application/json",
        "User-Agent": _USAGE_UA,
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    req = urllib.request.Request(_USAGE_URL, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            path = _auth_path(config)
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                tokens = data.get("tokens") if isinstance(
                    data.get("tokens"), dict) else {}
                refresh = str(tokens.get("refresh_token") or "").strip()
            except (OSError, ValueError, json.JSONDecodeError):
                data, tokens, refresh = {}, {}, ""
            if refresh:
                updated = _refresh_chatgpt_token(refresh)
                if updated.get("access_token"):
                    tokens.update(updated)
                    data["tokens"] = tokens
                    _write_auth(path, data)
                    headers["Authorization"] = "Bearer " + updated["access_token"]
                    try:
                        req2 = urllib.request.Request(_USAGE_URL, headers=headers)
                        with urllib.request.urlopen(req2, timeout=20) as resp:
                            raw = json.loads(resp.read().decode("utf-8"))
                    except Exception:
                        return {
                            "ok": False,
                            "error": "Codex sign-in expired — run `codex login` on the host.",
                        }
                else:
                    return {
                        "ok": False,
                        "error": "Codex sign-in expired — run `codex login` on the host.",
                    }
            else:
                return {
                    "ok": False,
                    "error": "Codex sign-in expired — run `codex login` on the host.",
                }
        elif e.code == 403:
            return {
                "ok": False,
                "error": "ChatGPT blocked the usage request (HTTP 403).",
            }
        else:
            return {"ok": False, "error": "Usage request failed (HTTP %d)" % e.code}
    except (urllib.error.URLError, OSError) as e:
        return {"ok": False, "error": "Could not reach ChatGPT usage API: %s" % e}
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": "Unexpected usage response"}

    if not isinstance(raw, dict):
        return {"ok": False, "error": "Unexpected usage response"}
    buckets = _buckets_from_codex_usage(raw)
    if not buckets:
        plan = str(raw.get("plan_type") or "").strip() or "unknown"
        if (raw.get("rate_limit") or {}).get("allowed") is True:
            return {
                "ok": True,
                "buckets": [{
                    "title": "Plan · %s" % plan,
                    "percent": 0,
                    "resets_text": "No rate-limit windows reported",
                    "severity": "normal",
                }],
            }
        return {"ok": False, "error": "No Codex usage windows in response"}
    return {"ok": True, "buckets": buckets}


def fetch_usage(config) -> dict:
    """Return {"ok": True, "buckets": [...]} or {"ok": False, "error": str}.

    Prefer ``codex app-server`` rateLimits RPC (reliable, same auth as the
    CLI). Fall back to ChatGPT HTTP if app-server is unavailable.
    """
    with _usage_app_server_lock:
        primary = _fetch_usage_via_app_server(config)
    if primary.get("ok"):
        return primary
    # App-server missing / timed out / auth error — try HTTP once.
    secondary = _fetch_usage_via_http(config)
    if secondary.get("ok"):
        return secondary
    # Prefer the more specific of the two errors.
    err = primary.get("error") or secondary.get("error") or "usage failed"
    if secondary.get("error") and "app-server" in str(primary.get("error") or ""):
        err = "%s (HTTP fallback: %s)" % (
            primary.get("error"), secondary.get("error"))
    return {"ok": False, "error": err}


def _preview(text: str, n: int = _MAX_PREVIEW) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _munge_cwd(cwd: str) -> str:
    s = str(cwd or "").strip().replace("\\", "/")
    if not s:
        return "no-project"
    if s.startswith("/"):
        s = s[1:]
    return "-" + s.replace("/", "-").replace(" ", "-")


def _iso_from_unix(ts) -> str:
    try:
        t = int(ts or 0)
    except (TypeError, ValueError):
        return ""
    if t <= 0:
        return ""
    # state may store seconds or ms
    if t > 10_000_000_000:
        t = t // 1000
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
    except (OverflowError, ValueError, OSError):
        return ""


def _safe_json(line: str):
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


class CodexStore:
    """Read Codex threads from the on-disk SQLite index + rollout JSONL."""

    def __init__(self, home: Path, config=None):
        self.home = Path(home).expanduser()
        self.config = config

    # -- discovery ------------------------------------------------------

    def _state_db(self) -> Path | None:
        if not self.home.is_dir():
            return None
        # Prefer the highest numbered state_N.sqlite (schema evolves).
        candidates = sorted(self.home.glob(_STATE_GLOB), reverse=True)
        for path in candidates:
            if path.is_file():
                return path
        legacy = self.home / "state.sqlite"
        return legacy if legacy.is_file() else None

    def _connect(self):
        db = self._state_db()
        if db is None:
            return None
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            con.row_factory = sqlite3.Row
            return con
        except sqlite3.Error as e:
            log.warning("codex state db open failed: %s", e)
            return None

    def _rows(self, user_only: bool = True, project_cwd: str = None):
        con = self._connect()
        if con is None:
            return []
        try:
            q = ("SELECT id, title, cwd, model, git_branch, preview, "
                 "rollout_path, created_at, updated_at, archived, "
                 "first_user_message, has_user_event "
                 "FROM threads")
            clauses = []
            args = []
            if user_only:
                clauses.append("COALESCE(archived, 0) = 0")
                # Skip empty shells that never saw a user event when possible.
                clauses.append("(COALESCE(has_user_event, 1) = 1 "
                               "OR length(COALESCE(first_user_message,'')) > 0 "
                               "OR length(COALESCE(preview,'')) > 0)")
            if project_cwd:
                clauses.append("cwd = ?")
                args.append(project_cwd)
            if clauses:
                q += " WHERE " + " AND ".join(clauses)
            q += " ORDER BY COALESCE(updated_at, created_at) DESC"
            return list(con.execute(q, args))
        except sqlite3.Error as e:
            log.warning("codex threads query failed: %s", e)
            return []
        finally:
            con.close()

    # -- store API ------------------------------------------------------

    def list_projects(self):
        by_cwd = {}
        for row in self._rows(user_only=True):
            cwd = (row["cwd"] or "").strip() or "(no project)"
            rec = by_cwd.get(cwd)
            # Float epoch like claude/grok — ISO strings break multi merge sort
            # and Android ProjectDto (last_active: Double).
            ts = float(row["updated_at"] or row["created_at"] or 0)
            if rec is None:
                by_cwd[cwd] = {
                    "id": _munge_cwd(cwd if cwd != "(no project)" else ""),
                    "cwd": "" if cwd == "(no project)" else cwd,
                    "name": Path(cwd).name if cwd not in ("", "(no project)") else "no-project",
                    "session_count": 1,
                    "last_active": ts,
                }
            else:
                rec["session_count"] += 1
                if ts > float(rec.get("last_active") or 0):
                    rec["last_active"] = ts
        return sorted(by_cwd.values(), key=lambda p: p["last_active"], reverse=True)

    def list_sessions(self, project_id=None, limit=25, user_only=True):
        project_cwd = None
        if project_id and project_id != "no-project":
            # Reverse munge is lossy; match by scanning.
            for row in self._rows(user_only=user_only):
                if _munge_cwd(row["cwd"] or "") == project_id:
                    project_cwd = row["cwd"]
                    break
            if project_cwd is None and project_id:
                # No match — empty list rather than everything.
                return []
        rows = self._rows(user_only=user_only, project_cwd=project_cwd)
        limit = max(1, min(int(limit or 25), 200))
        return [self._summary(r) for r in rows[:limit]]

    def search_sessions(self, query, project_id=None, limit=25, user_only=True):
        if not (query or "").strip():
            return []
        q = query.strip()
        out = []
        for row in self._rows(user_only=user_only):
            if project_id and _munge_cwd(row["cwd"] or "") != project_id:
                continue
            hay = " ".join([
                row["title"] or "",
                row["preview"] or "",
                row["first_user_message"] or "",
                row["cwd"] or "",
            ])
            snippet = None
            if search_util.contains_ci(hay, q):
                for field in (row["title"], row["preview"], row["first_user_message"]):
                    if field and search_util.contains_ci(field, q):
                        snippet = search_util.make_snippet(field, q)
                        break
                snippet = snippet or search_util.make_snippet(hay, q)
            else:
                # Fall back to scanning the rollout file (cheap head scan).
                snippet = self._search_rollout(row["rollout_path"] or "", q)
            if not snippet:
                continue
            s = self._summary(row)
            s["snippet"] = snippet
            out.append(s)
            if len(out) >= max(1, min(int(limit or 25), 200)):
                break
        return out

    def get_session(self, session_id: str):
        con = self._connect()
        if con is None:
            return None
        try:
            row = con.execute(
                "SELECT id, title, cwd, model, git_branch, preview, "
                "rollout_path, created_at, updated_at, archived, "
                "first_user_message, has_user_event "
                "FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            con.close()
        if row is None:
            return None
        return self._summary(row)

    def get_messages(self, session_id: str, offset: int = None, limit: int = 50):
        sess = self.get_session(session_id)
        if sess is None:
            return None
        con = self._connect()
        path = ""
        if con is not None:
            try:
                r = con.execute(
                    "SELECT rollout_path FROM threads WHERE id = ?",
                    (session_id,),
                ).fetchone()
                path = (r["rollout_path"] if r else "") or ""
            except sqlite3.Error:
                path = ""
            finally:
                con.close()
        t0 = time.perf_counter()
        messages = _build_transcript(Path(path) if path else None)
        t1 = time.perf_counter()
        total = len(messages)
        if offset is None:
            offset = max(0, total - limit)
        offset = max(0, offset)
        window = messages[offset: offset + limit]
        for msg in window:
            _render_codex_message(msg)
        t2 = time.perf_counter()
        try:
            file_bytes = Path(path).stat().st_size if path else 0
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

    def known_session_ids(self) -> set:
        return {r["id"] for r in self._rows(user_only=False) if r["id"]}

    def _summary(self, row) -> dict:
        cwd = (row["cwd"] or "").strip()
        title = " ".join(str(row["title"] or "").split())
        if not title or title.lower() in ("", "new session", "untitled"):
            title = " ".join(str(row["first_user_message"] or row["preview"] or "").split())
        if not title:
            title = "Session %s" % (row["id"] or "")[:8]
        last = row["preview"] or row["first_user_message"] or ""
        try:
            size = Path(row["rollout_path"] or "").stat().st_size
        except OSError:
            size = 0
        return {
            "id": row["id"],
            "project_id": _munge_cwd(cwd),
            "cwd": cwd,
            "git_branch": row["git_branch"] or "",
            "title": _preview(title, _MAX_TITLE),
            "started": _iso_from_unix(row["created_at"]),
            "last_active": _iso_from_unix(row["updated_at"] or row["created_at"]),
            "last_role": "assistant" if last else "",
            "last_text": _preview(last),
            "model": row["model"] or "",
            "size_bytes": size,
        }

    @staticmethod
    def _search_rollout(path: str, query: str):
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i > 400:
                        break
                    ev = _safe_json(line)
                    if not isinstance(ev, dict):
                        continue
                    payload = ev.get("payload") or {}
                    if not isinstance(payload, dict):
                        continue
                    text = payload.get("message") or payload.get("text") or ""
                    if isinstance(text, list):
                        text = " ".join(str(t) for t in text)
                    if text and search_util.contains_ci(str(text), query):
                        return search_util.make_snippet(str(text), query)
        except OSError:
            return None
        return None


def _build_transcript(path: Path | None) -> list:
    """Coalesce rollout JSONL into [{role, text, ts}] for the phone."""
    if path is None or not path.is_file():
        return []
    messages = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                ev = _safe_json(line)
                if not isinstance(ev, dict):
                    continue
                ts = str(ev.get("timestamp") or "")
                payload = ev.get("payload") if ev.get("type") == "event_msg" else None
                if not isinstance(payload, dict):
                    continue
                ptype = str(payload.get("type") or "")
                if ptype == "user_message":
                    text = str(payload.get("message") or "").strip()
                    if text:
                        messages.append({
                            "uuid": "u%d" % len(messages),
                            "role": "user",
                            "ts": ts,
                            "text": text,
                        })
                elif ptype == "agent_message":
                    text = str(payload.get("message") or "").strip()
                    if text:
                        messages.append({
                            "uuid": "a%d" % len(messages),
                            "role": "assistant",
                            "ts": ts,
                            "text": text,
                        })
    except OSError:
        return messages
    return messages


def _render_codex_message(msg: dict) -> None:
    """Attach display blocks. User rows must be k=user so BB/Android/web
    paint the chevron + well chrome (same as Claude/Grok). Assistant stays
    markdown_to_blocks. Previously every role used markdown_to_blocks, so
    historical Codex prompts rendered as plain assistant paragraphs.
    """
    text = (msg.get("text") or "").strip()
    role = msg.get("role") or ""
    if not text or role not in ("assistant", "user"):
        return
    if role == "user":
        plain, rich = inline_to_rich(text)
        msg["blocks"] = [{
            "k": "user",
            "role": "user",
            "text": plain,
            "rich": rich,
            "fmt": "rich",
        }]
    else:
        msg["blocks"] = markdown_to_blocks(text, role="assistant")


class CodexRunner:
    name = "codex"

    def __init__(self, config):
        self.config = config
        self.store = CodexStore(config.codex_home_path, config)
        # Lazily created tmux-TUI manager for "interactive" jobs.
        self._interactive = None
        self._interactive_lock = threading.Lock()

    def _interactive_mgr(self):
        with self._interactive_lock:
            if self._interactive is None:
                from .codex_interactive import CodexInteractiveManager
                self._interactive = CodexInteractiveManager(self.config, self)
            return self._interactive

    def run_alternate(self, job, mode) -> bool:
        """Fully handle a job outside the subprocess pipeline. "interactive"
        drives a real ``codex`` TUI in tmux (same mode Claude/Grok expose)."""
        if mode != "interactive":
            return False
        self._interactive_mgr().run(job)
        return True

    def type_into_tui(self, session_id: str, text: str) -> str:
        """Type a message into a session's live interactive TUI (\"\" or err)."""
        return self._interactive_mgr().type_text(session_id, text)

    def capture_tui(self, session_id: str) -> dict:
        return self._interactive_mgr().capture_tui(session_id)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        return self._interactive_mgr().send_tui_keys(session_id, keys=keys, text=text)

    def usage(self) -> dict:
        """Subscription / plan rate limits for the Usage sheet."""
        return fetch_usage(self.config)

    def capabilities(self):
        from .codex_interactive import tmux_available
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
            # ChatGPT-plan rate limits via backend-api/codex/usage (OAuth).
            "can_show_usage": True,
            "turns": True,
            # "interactive" permission mode: turns run in a host tmux TUI.
            # Requires tmux on the host (same as Claude/Grok interactive).
            "interactive": has_tmux,
            "live_tui": has_tmux,
        }

    # Verified in codex's own TUI command list: /compact and /exit are
    # there, /rewind and /undo are NOT (its "Rewind" string is a sort enum).
    _BUILTIN_SLASH = ["/compact", "/exit"]

    def slash_commands(self):
        out = list(self._BUILTIN_SLASH)
        for extra in getattr(self.config, "slash_commands", None) or []:
            if isinstance(extra, str) and extra.strip():
                out.append(extra.strip())
        return sorted(set(out))

    def models(self):
        extras = list(getattr(self.config, "models", None) or [])
        # models_cache.json is optional flavour; extras always win for the picker.
        cached = []
        try:
            path = self.config.codex_home_path / "models_cache.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            models = data.get("models") if isinstance(data, dict) else None
            if isinstance(models, list):
                for m in models:
                    if isinstance(m, str) and m.strip():
                        cached.append(m.strip())
                    elif isinstance(m, dict):
                        mid = m.get("id") or m.get("slug") or m.get("name")
                        if mid:
                            cached.append(str(mid))
        except (OSError, ValueError, TypeError):
            pass
        seen = set()
        out = []
        for m in extras + cached:
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return out

    def efforts(self):
        return list(getattr(self.config, "efforts", None) or [])

    def prepare(self, job, mode):
        # `mode` is claude vocabulary; codex uses sandbox / bypass flags.
        if not job.cwd:
            raise providers.RunnerError("cwd is required for codex sessions")
        cwd = os.path.expanduser(job.cwd)
        if not os.path.isdir(cwd):
            raise providers.RunnerError("cwd does not exist: %s" % cwd)
        job.cwd = cwd

        bin_path = str(getattr(self.config, "codex_bin", "codex") or "codex")
        state = job.runner_state
        state["parts"] = []
        state["full"] = []

        # Global flags before the subcommand (exec / exec resume).
        cmd = [bin_path, "exec", "--json"]
        # Phone-driven turns often use non-git folders (and /tmp in tests).
        cmd.append("--skip-git-repo-check")

        sandbox = str(getattr(self.config, "codex_sandbox", "") or "").strip()
        if not sandbox:
            # Default: full auto for phone use (same spirit as claude bypass /
            # grok --yolo). Override with "read-only" / "workspace-write" in
            # config if you want a tighter box.
            sandbox = "danger-full-access"
        if sandbox in ("danger-full-access", "yolo"):
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd += ["-s", sandbox]

        if job.model and job.model not in ("", "default"):
            cmd += ["-m", job.model]

        # Extra flags from config (whitespace-split), e.g. "--profile work".
        extra = str(getattr(self.config, "codex_exec_flags", "") or "").split()
        cmd += extra

        cmd += ["-C", cwd]

        # Resume is a subcommand of exec:  codex exec resume [id] [prompt]
        if job.session_id:
            cmd += ["resume", job.session_id]

        cmd.append(job.prompt)

        env = dict(os.environ)
        home = str(self.config.codex_home_path)
        env["CODEX_HOME"] = home
        extra_env = getattr(self.config, "codex_env", None) or {}
        env.update({str(k): str(v) for k, v in extra_env.items()})

        # Close stdin in the job runner — jobs.py uses subprocess.Popen with
        # stdin=PIPE by default; we mark that we want DEVNULL via state and
        # rely on prepare's return. Actually JobManager always uses PIPE.
        # Closing happens if we don't write; but CLI may wait. jobs.py should
        # close stdin — check... Looking at jobs.py: it doesn't close stdin.
        # Workaround: the CLI still works if we pass prompt as argv (we do).
        # The "Reading additional input from stdin" is just a notice when
        # stdin is a pipe. Closing: set state flag and patch is heavy; instead
        # document that stdin is a pipe. For robustness, use a wrapper script
        # or set stdin via env. Looking at jobs again...

        return cmd, env

    def handle_stream_line(self, job, line: str):
        obj = _safe_json(line)
        if not isinstance(obj, dict):
            return
        et = str(obj.get("type") or "")
        state = job.runner_state

        if et == "thread.started":
            sid = str(obj.get("thread_id") or "").strip()
            if sid:
                job.new_session_id = sid
                job.add_event("init", session_id=sid,
                              model=job.model or "")
            return

        if et == "turn.started":
            job.set_phase("thinking", "")
            return

        if et in ("error", "turn.failed"):
            msg = (obj.get("message") or obj.get("error")
                   or (obj.get("item") or {}).get("text")
                   or "codex reported an error")
            with job.lock:
                if not job.error:
                    job.error = str(msg)
            job.add_event("text", text=str(msg),
                          blocks=markdown_to_blocks(str(msg)))
            return

        if et in ("item.started", "item.completed", "item.updated"):
            item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
            itype = str(item.get("type") or "")
            if itype == "agent_message":
                text = str(item.get("text") or "").strip()
                if text and et == "item.completed":
                    state.setdefault("parts", []).append(text)
                    state.setdefault("full", []).append(text)
                    job.add_event("text", text=text,
                                  blocks=markdown_to_blocks(text))
                    job.set_phase("writing", text[-160:])
            elif itype in ("command_execution", "command", "shell"):
                cmd = str(item.get("command") or item.get("cmd") or "shell")
                detail = str(item.get("aggregated_output") or "")[:200]
                status = str(item.get("status") or "")
                if et == "item.started" or status == "in_progress":
                    job.add_event("tool", name="shell", detail=cmd[:200])
                    job.set_phase("tool", cmd[:120])
                elif et == "item.completed":
                    # Keep phase as tool until next event; optional exit code.
                    code = item.get("exit_code")
                    if code not in (None, 0, "0"):
                        job.set_phase("tool", "exit %s" % code)
            elif itype in ("file_change", "patch", "apply_patch"):
                path = str(item.get("path") or item.get("file") or "edit")
                job.add_event("tool", name="edit", detail=path[:200])
                job.set_phase("tool", path[:120])
            elif itype in ("reasoning", "thought", "agent_reasoning"):
                job.set_phase("thinking", "")
            return

        if et == "turn.completed":
            full = "".join(state.get("full") or state.get("parts") or [])
            with job.lock:
                if full and not job.result_text:
                    job.result_text = full
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            job.add_event(
                "result",
                is_error=False,
                duration_ms=0,
                cost_usd=0,
                usage=usage,
            )
            return

    def tick(self, job):
        pass

    def finalize(self, job, returncode, stderr_tail):
        state = job.runner_state
        full = "".join(state.get("full") or state.get("parts") or [])
        with job.lock:
            if full and not job.result_text:
                job.result_text = full
        if returncode not in (0, None) and not job.error:
            tail = (stderr_tail or "").strip().splitlines()
            msg = tail[-1] if tail else ("codex exited with code %s" % returncode)
            with job.lock:
                job.error = msg
            return False
        return None

    def cleanup(self, job):
        return
