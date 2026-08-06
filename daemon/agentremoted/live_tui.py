"""Shared Live TUI helpers: map client keys → tmux send-keys tokens.

Interactive managers own the pane names; this module only normalizes
input and builds capture payloads.
"""

from __future__ import annotations

import hashlib
import time

# Client key names (case-insensitive) → tmux send-keys tokens.
_KEY_MAP = {
    "escape": "Escape",
    "esc": "Escape",
    "enter": "Enter",
    "return": "Enter",
    "backspace": "BSpace",
    "bs": "BSpace",
    "delete": "DC",
    "del": "DC",
    "tab": "Tab",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PPage",
    "pagedown": "NPage",
    "pgup": "PPage",
    "pgdn": "NPage",
    "ctrl+c": "C-c",
    "c-c": "C-c",
    "ctrl+d": "C-d",
    "c-d": "C-d",
    "ctrl+z": "C-z",
    "c-z": "C-z",
    "ctrl+a": "C-a",
    "c-a": "C-a",
    "ctrl+e": "C-e",
    "c-e": "C-e",
    "ctrl+u": "C-u",
    "c-u": "C-u",
    "ctrl+k": "C-k",
    "c-k": "C-k",
    "ctrl+l": "C-l",
    "c-l": "C-l",
    "ctrl+w": "C-w",
    "c-w": "C-w",
}


def map_key(name: str) -> str | None:
    """Return a tmux send-keys token for a named key, or None if unknown."""
    raw = (name or "").strip()
    if not raw:
        return None
    # Single printable character (not space-only)
    if len(raw) == 1 and raw.isprintable():
        return raw
    key = raw.lower().replace(" ", "")
    if key in _KEY_MAP:
        return _KEY_MAP[key]
    # Ctrl+letter form
    if key.startswith("ctrl+") and len(key) == 6 and key[5].isalpha():
        return "C-" + key[5]
    if key.startswith("c-") and len(key) == 3 and key[2].isalpha():
        return "C-" + key[2]
    return None


def map_keys(keys) -> list:
    """Map a list of client key names; drop unknowns."""
    out = []
    if not isinstance(keys, (list, tuple)):
        return out
    for item in keys:
        tok = map_key(str(item or ""))
        if tok is not None:
            out.append(tok)
    return out


def frame_payload(session_id: str, text: str, attached: bool,
                  job_id: str = "", error: str = "") -> dict:
    """Build the GET /tui JSON body."""
    body = text if attached else ""
    seq = int(hashlib.sha1(body.encode("utf-8", errors="replace")).hexdigest()[:12], 16)
    return {
        "session_id": session_id or "",
        "job_id": job_id or "",
        "attached": bool(attached),
        "text": body,
        "seq": seq,
        "cols": 0,
        "rows": body.count("\n") + 1 if body else 0,
        "cursor": None,
        "error": error or "",
        "ts": time.time(),
    }


def _find_tui(mgr, session_id: str):
    """Locate a live TUI object for session_id on an interactive manager."""
    sid = (session_id or "").strip()
    if not sid:
        return None
    lock = getattr(mgr, "_lock", None)
    tuis = getattr(mgr, "_tuis", None) or {}
    if lock is not None:
        with lock:
            for t in tuis.values():
                if getattr(t, "session_id", None) == sid:
                    return t
    else:
        for t in tuis.values():
            if getattr(t, "session_id", None) == sid:
                return t
    return None


def capture_session(mgr, session_id: str) -> dict:
    """Capture the tmux pane for a session via an interactive manager."""
    tui = _find_tui(mgr, session_id)
    if tui is None:
        return frame_payload(session_id, "", False,
                             error="no interactive TUI for this session")
    alive = mgr._tmux_alive(tui.name)
    if not alive:
        return frame_payload(session_id, "", False,
                             error="the host TUI has exited")
    # Prefer ANSI capture so web/Android can render colours; BB strips SGR.
    try:
        text = mgr._pane_text(tui.name, ansi=True) or ""
    except TypeError:
        text = mgr._pane_text(tui.name) or ""
    job_id = ""
    job = getattr(tui, "job", None)
    if job is not None:
        job_id = getattr(job, "id", "") or ""
    return frame_payload(session_id, text, True, job_id=job_id)


def send_to_session(mgr, session_id: str, keys=None, text: str = "") -> str:
    """Send keys and/or literal text into the session TUI. Returns \"\" or error."""
    tui = _find_tui(mgr, session_id)
    if tui is None:
        return "no interactive TUI for this session"
    if not mgr._tmux_alive(tui.name):
        return "the host TUI has exited"

    tokens = map_keys(keys)
    literal = text if isinstance(text, str) else ""
    if not tokens and not literal:
        return "empty input"

    try:
        if literal:
            # -l: literal, no key-name interpretation
            r = mgr._tmux("send-keys", "-l", "-t", tui.name, literal)
            if getattr(r, "returncode", 0) not in (0, None):
                err = ""
                if getattr(r, "stderr", None):
                    err = r.stderr.decode("utf-8", errors="replace")[:200]
                return "tmux send-keys failed: %s" % (err or "error")
        for tok in tokens:
            r = mgr._tmux("send-keys", "-t", tui.name, tok)
            if getattr(r, "returncode", 0) not in (0, None):
                err = ""
                if getattr(r, "stderr", None):
                    err = r.stderr.decode("utf-8", errors="replace")[:200]
                return "tmux send-keys failed: %s" % (err or tok)
    except Exception as e:  # noqa: BLE001
        return "tmux input failed: %s" % e
    return ""
