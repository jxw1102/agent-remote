"""Shared Live TUI helpers: map client keys → tmux send-keys tokens.

Interactive managers own the pane names; this module only normalizes
input and builds capture payloads.
"""

from __future__ import annotations

import hashlib
import re
import time

# Pane geometry every interactive harness launches its tmux window with.
#
# 220 columns was "never wrap a long line", but nothing renders a 220-column
# pane comfortably: the web pane reflowed it into soup and the box drawing
# fell apart. 120 is the width a terminal agent is actually designed for,
# and clients get it in the frame payload so they can size the pane exactly.
TUI_COLS = 120
TUI_ROWS = 50

# Base key names (case-insensitive) → tmux send-keys tokens. Modifiers are
# parsed separately, so this table holds bare keys only.
_BASE_KEYS = {
    "escape": "Escape",
    "esc": "Escape",
    "enter": "Enter",
    "return": "Enter",
    "cr": "Enter",
    "backspace": "BSpace",
    "bspace": "BSpace",
    "bs": "BSpace",
    "delete": "DC",
    "del": "DC",
    "dc": "DC",
    "insert": "IC",
    "ins": "IC",
    "ic": "IC",
    "tab": "Tab",
    "btab": "BTab",
    "backtab": "BTab",
    "space": "Space",
    "spc": "Space",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "PPage",
    "pgup": "PPage",
    "ppage": "PPage",
    "pagedown": "NPage",
    "pgdn": "NPage",
    "npage": "NPage",
}
for _i in range(1, 13):
    _BASE_KEYS["f%d" % _i] = "F%d" % _i

# Modifier spellings clients may use → tmux modifier letter.
_MODS = {
    "ctrl": "C", "control": "C", "ctl": "C", "c": "C",
    "alt": "M", "meta": "M", "opt": "M", "option": "M", "m": "M",
    "shift": "S", "s": "S",
}


def _split_mods(key: str):
    """Peel 'ctrl+', 'alt+', 'c-', 'm-' … off the front of a lowered key name.

    Accepts both the plus form clients send ("Ctrl+Shift+Tab") and the tmux
    dash form ("C-M-x"), in any order, and returns (mods, base).
    """
    mods = set()
    while True:
        # Dash form: a single modifier letter followed by '-' and more key.
        if len(key) > 2 and key[0] in "cms" and key[1] == "-":
            mods.add(_MODS[key[0]])
            key = key[2:]
            continue
        # Plus form: 'ctrl+…'. A trailing '+' is the key itself ("ctrl++").
        head, sep, rest = key.partition("+")
        if sep and rest and head in _MODS:
            mods.add(_MODS[head])
            key = rest
            continue
        return mods, key


def map_key(name: str) -> str | None:
    """Return a tmux send-keys token for a key name, or None if unknown.

    Understands a bare printable character, a named key, and any
    Ctrl / Alt(Option) / Shift combination of the two.
    """
    raw = (name or "").strip()
    if not raw:
        return None
    # A single printable character is itself — including '+' and '-', which
    # must never be read as modifier syntax.
    if len(raw) == 1 and raw.isprintable():
        return raw
    mods, base = _split_mods(raw.lower().replace(" ", ""))
    if not base:
        return None

    tok = _BASE_KEYS.get(base)
    if tok is None:
        if len(base) == 1 and base.isprintable():
            tok = base
        else:
            return None

    if "S" in mods:
        # Terminals carry shift in the character itself, not as a modifier.
        if len(tok) == 1 and tok.isalpha():
            tok = tok.upper()
            mods.discard("S")
        elif tok == "Tab":
            tok = "BTab"          # the one name tmux has for shift-tab
            mods.discard("S")
        elif tok == "Enter":
            # What `claude /terminal-setup` binds shift-enter to: ESC CR.
            # A bare shift-enter is indistinguishable from enter on the wire,
            # so send the sequence the agent actually reads as "newline".
            tok = "Enter"
            mods.discard("S")
            mods.add("M")
        elif len(tok) == 1:
            mods.discard("S")     # shift on a symbol is already in the symbol

    prefix = ""
    for m in ("C", "M", "S"):     # tmux accepts any order; keep one canonical
        if m in mods:
            prefix += m + "-"
    return prefix + tok


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


def trim_blank_tail(text: str) -> str:
    """Drop the empty rows below the last drawn line of a pane capture.

    A 50-row window holding a 15-row conversation captures 35 blank rows, and
    every client faithfully rendered them: the pane scrolled to the bottom and
    showed a screenful of nothing. Colour runs are kept — only rows with no
    visible character at all go.
    """
    if not text:
        return ""
    lines = text.split("\n")
    while lines and not _ANSI_RE.sub("", lines[-1]).strip():
        lines.pop()
    return "\n".join(lines)


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
        # Real pane width, so a client can size its font to fit exactly
        # instead of guessing (0 on old daemons — clients fall back to the
        # longest line they were sent).
        "cols": TUI_COLS if attached else 0,
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
    text = trim_blank_tail(text)
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
            # A bare character is content, not a key name: -l keeps tmux from
            # reading it as one (and keeps '-'/';' out of argument parsing).
            args = (["-l", "-t", tui.name, tok] if len(tok) == 1
                    else ["-t", tui.name, tok])
            r = mgr._tmux("send-keys", *args)
            if getattr(r, "returncode", 0) not in (0, None):
                err = ""
                if getattr(r, "stderr", None):
                    err = r.stderr.decode("utf-8", errors="replace")[:200]
                return "tmux send-keys failed: %s" % (err or tok)
    except Exception as e:  # noqa: BLE001
        return "tmux input failed: %s" % e
    return ""
