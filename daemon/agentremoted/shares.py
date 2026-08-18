"""Read-only session share links.

A share is a capability token, not a session id. The public URL is
``/share/<token>``; the token hashes to one row that names exactly one
session. Changing the token (or presenting it as the daemon auth token)
cannot reach any other session or any write API.

Tokens expire after seven days. The raw token is never stored — only its
SHA-256 — so a leaked ``shares.json`` is not a working link list.
"""

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

from .config import CONFIG_DIR

TTL_S = 7 * 24 * 3600
TOKEN_BYTES = 32
# urlsafe_b64(32 bytes) is 43 chars; accept a little slack either side.
TOKEN_RE_LEN = (20, 64)
_MAX_SHARES = 500
_MAX_PER_SESSION = 20

_HEX = set("0123456789abcdef")


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def looks_like_token(raw: str) -> bool:
    t = str(raw or "").strip()
    lo, hi = TOKEN_RE_LEN
    if not (lo <= len(t) <= hi):
        return False
    return all(ch.isalnum() or ch in "-_" for ch in t)


def mint_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


class ShareStore:
    """Persistent share-token table for one daemon."""

    def __init__(self, path=None):
        self._path = Path(path) if path else (CONFIG_DIR / "shares.json")
        self._lock = threading.Lock()
        self._rows = {}  # hash -> record
        self._load()

    def _load(self):
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        rows = raw.get("shares")
        if not isinstance(rows, dict):
            return
        now = time.time()
        for key, val in rows.items():
            if not isinstance(val, dict) or not isinstance(key, str):
                continue
            if len(key) != 64 or any(c not in _HEX for c in key.lower()):
                continue
            rec = _record(val)
            if rec is None:
                continue
            if rec["expires_at"] <= now:
                continue
            self._rows[key.lower()] = rec

    def _save_locked(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.parent / (self._path.name + ".tmp")
            tmp.write_text(json.dumps({"shares": self._rows},
                                      allow_nan=False), encoding="utf-8")
            os.replace(str(tmp), str(self._path))
            try:
                os.chmod(str(self._path), 0o600)
            except OSError:
                pass
        except OSError:
            pass

    def _prune_locked(self, now: float):
        stale = [k for k, r in self._rows.items() if r["expires_at"] <= now]
        for k in stale:
            self._rows.pop(k, None)
        if len(self._rows) <= _MAX_SHARES:
            return
        ordered = sorted(self._rows.items(), key=lambda kv: kv[1]["created_at"])
        for k, _ in ordered[:len(self._rows) - _MAX_SHARES]:
            self._rows.pop(k, None)

    def create(self, *, session_id: str, provider: str = "",
               guest_root: str = "", title: str = "") -> dict:
        """Mint a new token bound to *session_id*. Returns the public record
        plus the raw ``token`` (shown once)."""
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session id required")
        now = time.time()
        token = mint_token()
        digest = token_hash(token)
        rec = {
            "session_id": sid,
            "provider": str(provider or ""),
            "guest_root": str(guest_root or ""),
            "title": str(title or "")[:200],
            "created_at": now,
            "expires_at": now + TTL_S,
        }
        with self._lock:
            self._prune_locked(now)
            # Cap per session so a stuck client cannot fill the file.
            same = [k for k, r in self._rows.items()
                    if r["session_id"] == sid
                    and r.get("guest_root", "") == rec["guest_root"]]
            if len(same) >= _MAX_PER_SESSION:
                same.sort(key=lambda k: self._rows[k]["created_at"])
                for k in same[:len(same) - _MAX_PER_SESSION + 1]:
                    self._rows.pop(k, None)
            self._rows[digest] = rec
            self._save_locked()
        return _public(rec, token=token)

    def resolve(self, token: str):
        """Record for a live token, or None (unknown / expired / malformed)."""
        if not looks_like_token(token):
            return None
        digest = token_hash(token.strip())
        now = time.time()
        with self._lock:
            rec = self._rows.get(digest)
            if rec is None:
                return None
            if rec["expires_at"] <= now:
                self._rows.pop(digest, None)
                self._save_locked()
                return None
            return dict(rec)


def _record(val: dict):
    sid = str(val.get("session_id") or "").strip()
    exp = _num(val.get("expires_at"))
    if not sid or exp <= 0:
        return None
    return {
        "session_id": sid,
        "provider": str(val.get("provider") or ""),
        "guest_root": str(val.get("guest_root") or ""),
        "title": str(val.get("title") or "")[:200],
        "created_at": _num(val.get("created_at")),
        "expires_at": exp,
    }


def _public(rec: dict, token: str = "") -> dict:
    out = {
        "session_id": rec["session_id"],
        "provider": rec.get("provider") or "",
        "title": rec.get("title") or "",
        "created_at": rec["created_at"],
        "expires_at": rec["expires_at"],
        "expires_in": max(0, int(rec["expires_at"] - time.time())),
    }
    if token:
        out["token"] = token
        out["path"] = "/share/" + token
    return out


def _num(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


# Served when the built web/share.html is not packaged with the daemon.
# The JS talks to /api/share/<token> on the same origin — no daemon token.
FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Shared session · Agent Remote</title>
<style>
:root { --bg:#0b0b0d; --surface:#15171b; --text:#e6e8ec; --dim:#7a8394;
        --accent:#9aa4b2; --line:#23262d; --radius:10px;
        --mono:ui-monospace,Menlo,Consolas,monospace; }
html,body { margin:0; background:var(--bg); color:var(--text);
            font:15px/1.55 system-ui,-apple-system,sans-serif; }
header { max-width:900px; margin:0 auto; padding:20px 20px 8px;
         border-bottom:1px solid var(--line); }
.brand { display:flex; align-items:center; gap:10px; color:var(--dim);
         font-size:13px; }
h1 { font-size:20px; margin:10px 0 4px; }
.sub { color:var(--dim); font-size:13px; }
main { max-width:900px; margin:0 auto; padding:20px; }
.msg { margin:0 0 16px; }
.msg.user { background:var(--surface); border-radius:var(--radius);
            padding:10px 14px; white-space:pre-wrap; }
.msg.assistant { white-space:pre-wrap; }
.empty { color:var(--dim); text-align:center; margin:18vh 0; }
.empty h2 { color:var(--text); }
button { font:inherit; color:var(--text); background:var(--surface);
         border:1px solid var(--line); border-radius:8px; padding:6px 12px;
         cursor:pointer; display:block; margin:0 auto 18px; }
</style>
</head>
<body>
<header>
  <div class="brand">Agent Remote · shared session · read only</div>
  <h1 id="title">Shared session</h1>
  <div id="sub" class="sub"></div>
</header>
<main id="main"><p class="empty">Loading…</p></main>
<script>
(function () {
  var parts = location.pathname.replace(/\\/+$/, "").split("/");
  var token = parts[parts.length - 1] || "";
  var main = document.getElementById("main");
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  function fail(title, detail) {
    document.getElementById("title").textContent = title;
    main.textContent = "";
    var box = el("div", "empty");
    box.appendChild(el("h2", null, title));
    box.appendChild(el("p", null, detail || ""));
    main.appendChild(box);
  }
  function render(data) {
    document.getElementById("title").textContent = data.title || "Shared session";
    var bits = ["Read only"];
    if (data.provider) bits.unshift(data.provider);
    if (data.expires_in) {
      var d = Math.max(1, Math.round(data.expires_in / 86400));
      bits.push(d === 1 ? "expires in 1 day" : "expires in " + d + " days");
    }
    document.getElementById("sub").textContent = bits.join(" · ");
    main.textContent = "";
    (data.messages || []).forEach(function (m) {
      var role = m.role === "user" ? "user" : (m.role || "assistant");
      main.appendChild(el("div", "msg " + role, m.text || ""));
    });
    if (!(data.messages || []).length)
      main.appendChild(el("p", "empty", "This session has no messages yet."));
  }
  if (!token) { fail("Link not found", "This address is not a share link."); return; }
  fetch("/api/share/" + encodeURIComponent(token), { headers: { Accept: "application/json" } })
    .then(function (r) {
      return r.json().then(function (j) { return { ok: r.ok, status: r.status, body: j }; });
    })
    .then(function (res) {
      if (!res.ok) {
        fail(res.status === 404 ? "Link not found" : "Could not open this session",
             (res.body && res.body.error) || "This share link is invalid or has expired.");
        return;
      }
      render(res.body);
    })
    .catch(function () {
      fail("Could not open this session", "The daemon did not respond.");
    });
})();
</script>
</body>
</html>
"""
