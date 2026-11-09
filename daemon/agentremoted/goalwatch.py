"""Keeping a Codex goal moving.

A Codex thread can carry a *goal*: an objective the CLI pursues across many
turns, restarting itself after each one (``<codex_internal_context
source="goal">``). It does not always restart. The thread then sits at an
idle prompt with an active goal and nothing happening — work that looks
finished but is not, which is invisible from a phone.

This module owns the part that must survive a daemon restart: which sessions
the human asked the daemon to keep moving, and what the daemon has done about
it. The watching itself lives in the Codex interactive manager, which is the
thing that knows whether a TUI is mid-turn and can type into it.

Enrolment is always a human act through the API — the daemon never adopts a
goal on its own initiative, because resuming one spends the user's tokens.

Two guards keep a stalled goal from becoming an expensive loop:

  * a *cooldown* after each resume, so a thread that answers slowly is not
    nudged twice for the same stall;
  * a *give-up count*: a resume that does not move the goal's own counters
    (``tokens_used`` in Codex's goals database) is a resume that did not
    work, and after a few of those the daemon stops and says so rather than
    typing into a dead prompt for ever.
"""

import json
import threading
import time

from .config import CONFIG_DIR

# How long a watched thread must sit idle, with its goal still active, before
# the daemon types /goal resume. Long enough that a human reading the answer
# has a moment to reply themselves.
DEFAULT_IDLE_S = 300
# Never resume twice inside this window, however idle it looks.
DEFAULT_COOLDOWN_S = 180
# Consecutive resumes that moved nothing before the daemon gives up.
DEFAULT_MAX_STALE_RESUMES = 3

# What the daemon types. Codex's own slash command; the TUI runs it like any
# other typed line.
RESUME_COMMAND = "/goal resume"


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class GoalWatch:
    """The watch list, persisted next to the other daemon state."""

    def __init__(self, path=None):
        self._path = path or (CONFIG_DIR / "goalwatch.json")
        self._lock = threading.Lock()
        # session id -> record (see _blank)
        self._rows = {}
        self._load()

    @staticmethod
    def _blank(now=None):
        return {
            "on": True,
            "since": now if now is not None else time.time(),
            "resumes": 0,
            "last_resume_at": 0.0,
            # Goal counters seen at the last resume, to tell a resume that
            # worked from one that changed nothing.
            "last_tokens": 0.0,
            "stale_resumes": 0,
            # Set when the daemon has given up; cleared by switching the
            # watch off and on again.
            "stopped_why": "",
        }

    # ---- persistence -------------------------------------------------

    def _load(self):
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        rows = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(rows, dict):
            return
        for sid, val in rows.items():
            if not isinstance(val, dict):
                continue
            row = self._blank(_num(val.get("since")))
            row.update({
                "on": bool(val.get("on", True)),
                "resumes": int(_num(val.get("resumes"))),
                "last_resume_at": _num(val.get("last_resume_at")),
                "last_tokens": _num(val.get("last_tokens")),
                "stale_resumes": int(_num(val.get("stale_resumes"))),
                "stopped_why": str(val.get("stopped_why") or ""),
            })
            self._rows[str(sid)] = row

    def _save_locked(self):
        """Atomic replace. Caller holds the lock."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.parent / (self._path.name + ".tmp")
            tmp.write_text(json.dumps({"sessions": self._rows}),
                           encoding="utf-8")
            tmp.replace(self._path)
        except OSError:
            pass  # a watch list that cannot be written still works in memory

    # ---- membership ---------------------------------------------------

    def set(self, session_id: str, on: bool) -> dict:
        """Start or stop watching one session. Returns its record."""
        sid = (session_id or "").strip()
        if not sid:
            return {}
        with self._lock:
            row = self._rows.get(sid)
            if on:
                # Switching it on again is also how a user clears a give-up.
                if row is None:
                    row = self._blank()
                else:
                    row = dict(row)
                    row["on"] = True
                    row["stopped_why"] = ""
                    row["stale_resumes"] = 0
                self._rows[sid] = row
            elif row is not None:
                row = dict(row)
                row["on"] = False
                self._rows[sid] = row
            else:
                row = self._blank()
                row["on"] = False
                self._rows[sid] = row
            self._save_locked()
            return dict(row)

    def get(self, session_id: str) -> dict:
        with self._lock:
            row = self._rows.get((session_id or "").strip())
            return dict(row) if row else {}

    def watching(self) -> list:
        """Session ids the daemon should be keeping moving."""
        with self._lock:
            return [sid for sid, row in self._rows.items()
                    if row.get("on") and not row.get("stopped_why")]

    def snapshot(self) -> dict:
        with self._lock:
            return {sid: dict(row) for sid, row in self._rows.items()}

    # ---- what the watcher did ------------------------------------------

    def note_resume(self, session_id: str, tokens_used: float) -> None:
        sid = (session_id or "").strip()
        with self._lock:
            row = dict(self._rows.get(sid) or self._blank())
            # A resume that moved nothing since the last one is a resume that
            # did not work; the count resets as soon as the goal advances.
            if row["last_resume_at"] and tokens_used <= row["last_tokens"]:
                row["stale_resumes"] = int(row["stale_resumes"]) + 1
            else:
                row["stale_resumes"] = 0
            row["resumes"] = int(row["resumes"]) + 1
            row["last_resume_at"] = time.time()
            row["last_tokens"] = _num(tokens_used)
            self._rows[sid] = row
            self._save_locked()

    def give_up(self, session_id: str, why: str) -> None:
        sid = (session_id or "").strip()
        with self._lock:
            row = dict(self._rows.get(sid) or self._blank())
            row["stopped_why"] = str(why or "stopped")
            self._rows[sid] = row
            self._save_locked()


_shared = None
_shared_lock = threading.Lock()


def shared() -> GoalWatch:
    """One watch list per daemon."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = GoalWatch()
        return _shared
