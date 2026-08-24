"""Shared Live TUI helpers: map client keys → tmux send-keys tokens.

Interactive managers own the pane names; this module only normalizes
input and builds capture payloads.
"""

from __future__ import annotations

import hashlib
import re
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


# Residual ESC sequences (belt-and-suspenders after capture-pane without -e).
_ANSI_RE = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC … BEL/ST
    r"|\x1bP[^\x1b]*(?:\x1b\\)?"          # DCS
    r"|\x1b\[[0-9;:<=>?]*[ -/]*[@-~]"     # CSI (incl. colon RGB)
    r"|\x1b[()][0-9A-Za-z]"               # charset
    r"|\x1b."                             # other 2-byte ESC
)


def _box_approx(code: int) -> str:
    """Map a Box Drawing / Block Elements code point to plain ASCII."""
    if code in (0x2500, 0x2501, 0x2504, 0x2505, 0x2508, 0x2509,
                0x254C, 0x254D, 0x2550, 0x2574, 0x2576, 0x2578, 0x257A):
        return "-"
    if code in (0x2502, 0x2503, 0x2506, 0x2507, 0x250A, 0x250B,
                0x254E, 0x254F, 0x2551, 0x2575, 0x2577, 0x2579, 0x257B):
        return "|"
    if code == 0x2571:
        return "/"
    if code == 0x2572:
        return "\\"
    if 0x2580 <= code <= 0x259F:  # block elements / shades
        return "#" if code >= 0x2588 else "."
    return "+"  # corners, tees, crosses


def plain_tui_text(text: str) -> str:
    """Readable plain pane for BB and other non-ANSI clients.

    Strips residual escapes, maps box-drawing / braille / private-use
    chrome to ASCII, keeps real letters (incl. CJK) and newlines.
    """
    if not text:
        return ""
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = _ANSI_RE.sub("", s)
    out = []
    for ch in s:
        o = ord(ch)
        if ch in ("\n", "\t"):
            out.append(ch)
            continue
        if o < 32 or o == 0x7F:
            continue
        # zero-width / soft hyphen / BOM
        if o in (0x00AD, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF):
            continue
        if 0x2500 <= o <= 0x259F:  # Box Drawing + Block Elements
            out.append(_box_approx(o))
            continue
        if 0x2800 <= o <= 0x28FF:  # Braille (spinners)
            out.append(" ")
            continue
        if 0xE000 <= o <= 0xF8FF or 0xF0000 <= o <= 0xFFFFD:  # PUA / powerline
            out.append(" ")
            continue
        # Common TUI punctuation / bullets → ASCII
        if o in (0x2022, 0x25CF, 0x25CB, 0x25A0, 0x25A1, 0x25AA,
                 0x25AB, 0x25B6, 0x25C0, 0x25E6, 0x2219, 0x30FB):
            out.append("*")
            continue
        if o in (0x2013, 0x2014, 0x2212):
            out.append("-")
            continue
        if o in (0x2018, 0x2019, 0x2032):
            out.append("'")
            continue
        if o in (0x201C, 0x201D):
            out.append('"')
            continue
        if o == 0x2026:
            out.append("...")
            continue
        if o in (0x00A0, 0x2007, 0x202F):  # nbsp variants
            out.append(" ")
            continue
        out.append(ch)
    return "".join(out)


def idle_eviction_victim(tuis, incoming_isolate_root: str = ""):
    """Pick an idle TUI to kill for the fleet cap, or None to overflow.

    Guest panes (``isolate_root`` set) are never evicted to make room for
    the host account. An incoming guest may evict idle host TUIs first,
    then the least-recently-used guest if the fleet is all guests.
    A TUI with ``job`` set is mid-turn and is never a candidate.
    """
    items = list(tuis or [])
    idle = [t for t in items if getattr(t, "job", None) is None]
    if not idle:
        return None
    incoming_guest = bool((incoming_isolate_root or "").strip())
    host_idle = [t for t in idle
                 if not str(getattr(t, "isolate_root", "") or "").strip()]
    if incoming_guest:
        pool = host_idle or idle
    else:
        pool = host_idle
        if not pool:
            return None
    return min(pool, key=lambda t: float(getattr(t, "last_used", 0) or 0))


def frame_payload(session_id: str, text: str, attached: bool,
                  job_id: str = "", error: str = "",
                  *, ansi: bool = False) -> dict:
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
        "ansi": bool(ansi),
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


def capture_session(mgr, session_id: str, *, ansi: bool = False) -> dict:
    """Capture the tmux pane for a session via an interactive manager.

    Default is plain text (no SGR, decorative chrome simplified) so BB and
    other mono clients stay readable. Pass ``ansi=True`` when the client
    can render colours (web / Android request ``?ansi=1``).
    """
    tui = _find_tui(mgr, session_id)
    if tui is None:
        return frame_payload(session_id, "", False,
                             error="no interactive TUI for this session",
                             ansi=ansi)
    alive = mgr._tmux_alive(tui.name)
    if not alive:
        return frame_payload(session_id, "", False,
                             error="the host TUI has exited",
                             ansi=ansi)
    want_ansi = bool(ansi)
    try:
        text = mgr._pane_text(tui.name, ansi=want_ansi) or ""
    except TypeError:
        text = mgr._pane_text(tui.name) or ""
    if not want_ansi:
        text = plain_tui_text(text)
    job_id = ""
    job = getattr(tui, "job", None)
    if job is not None:
        job_id = getattr(job, "id", "") or ""
    return frame_payload(session_id, text, True, job_id=job_id, ansi=want_ansi)


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
