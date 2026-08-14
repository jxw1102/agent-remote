"""Focus list state — the projects the human is actively carrying.

The session list is *derived*: every row comes from an on-disk transcript the
agent CLI wrote, so the daemon owns no session record it could hang a flag on.
This module adds the two things that cannot be derived from a transcript:

  * membership — the human enrolled this session by acting on it *through the
    daemon* (created it, or sent it a prompt), and has not marked it done;
  * title overrides — a manual rename, or a regenerated title, that outlives
    whatever the provider's own titling produced.

Membership is opt-in by human action, never by inference. ``enroll()`` is only
ever called from request handlers that sit behind the auth gate in
``server.py``, so the work an agent does on its own initiative — hook posts on
``/internal/hook``, permission callbacks, subagent transcripts — can never put
a card on the list. That asymmetry is the whole point of the feature: Focus is
a list of *your* commitments, not a log of everything that ran.

Focus is a *filter*, not a second layout: clients keep painting the one session
list they already have, narrowed to members and with the state tag below added
to each row.

State is the opposite of membership: always derived, never stored. A row's
state is a pure function of live job state, computed fresh on each request
(:func:`state_for`), so the tag cannot drift out of sync with what the session
is actually doing.
"""

import json
import os
import threading
import time

from .config import CONFIG_DIR

# A session enrolled before its real id exists is keyed by the job that will
# create it; `rekey()` migrates the card once the provider reports the id.
JOB_KEY_PREFIX = "job:"

STATE_NEEDS_ANSWER = "needs_answer"
STATE_FAILED = "failed"
STATE_WORKING = "working"
STATE_TURN_FINISHED = "turn_finished"

# Urgency order — most wanting-of-a-human first. Clients use these ids as-is
# (tag text, colour, sort weight), so renaming one is a client-visible API
# change. `failed` outranks `working`: a broken turn needs you, a running one
# does not.
STATES = (STATE_NEEDS_ANSWER, STATE_FAILED, STATE_WORKING,
          STATE_TURN_FINISHED)

STATE_LABELS = {
    STATE_NEEDS_ANSWER: "needs answer",
    STATE_FAILED: "failed",
    STATE_WORKING: "working",
    STATE_TURN_FINISHED: "turn finished",
}

# Cards marked done are kept this long so an accidental swipe can be undone,
# then pruned. They stay out of Focus the whole time.
_DONE_GRACE_S = 7 * 24 * 3600

# A `job:<id>` card is a placeholder waiting for its session id, which arrives
# from the job list on the next listing. If the daemon restarts mid-turn the
# job is gone and the placeholder can never resolve, so it is dropped once it
# is far older than any turn could still be running.
_PLACEHOLDER_TTL_S = 3600

# Hard cap on stored cards, oldest-first eviction. Guards against a runaway
# client enrolling without bound; a real focus list is a handful of projects.
_MAX_MEMBERS = 500

_TITLE_MAX = 120

# Transcript timestamps carry one-second precision, so a cursor stamped with
# sub-second precision is not comparable to them. `enroll` aligns to the start
# of the current second: the turn being enrolled writes its output inside that
# same second and must still count as unread.
_CURSOR_GRANULARITY_S = 1.0


def state_for(*, running: bool, pending: bool, failed: bool = False) -> str:
    """Which state tag a focus row carries right now.

    Tested in urgency order, because the states overlap: a job that is running
    *and* blocked on a question or a tool permission is "needs answer" — the
    agent is not actually making progress, and tagging it "working" would hide
    the one row that wants a human this second.

    There is deliberately no read/unread split: whether you have opened a
    finished turn is not a property of the session, and tracking it meant a
    stored cursor compared against one-second transcript timestamps.
    """
    if pending:
        return STATE_NEEDS_ANSWER
    if running:
        return STATE_WORKING
    if failed:
        return STATE_FAILED
    return STATE_TURN_FINISHED


def clean_title(text: str) -> str:
    """Normalise a human-typed title: one line, trimmed, length-capped."""
    t = " ".join(str(text or "").split())
    if len(t) > _TITLE_MAX:
        t = t[:_TITLE_MAX - 1].rstrip() + "…"
    return t


class Focus:
    """Persistent focus-list state, shared by every client of this daemon.

    Deliberately server-side rather than per-client: the same list has to look
    identical on the web app, the phone, and the BlackBerry, and a
    localStorage list would fork three ways on the first offline edit.
    """

    def __init__(self, path=None):
        self._path = path or (CONFIG_DIR / "focus.json")
        self._lock = threading.Lock()
        self._members = {}   # key -> {added_at, done_at, seen_at, provider, cwd, job_id}
        self._titles = {}    # key -> {title, manual, at}
        self._load()

    # ---- persistence -------------------------------------------------

    def _load(self):
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        members = raw.get("members")
        if isinstance(members, dict):
            for key, val in members.items():
                if isinstance(val, dict):
                    self._members[str(key)] = {
                        "added_at": _num(val.get("added_at")),
                        "done_at": _num(val.get("done_at")) or None,
                        "seen_at": _num(val.get("seen_at")),
                        "provider": str(val.get("provider") or ""),
                        "cwd": str(val.get("cwd") or ""),
                        "job_id": str(val.get("job_id") or ""),
                    }
        titles = raw.get("titles")
        if isinstance(titles, dict):
            for key, val in titles.items():
                if isinstance(val, dict) and val.get("title"):
                    self._titles[str(key)] = {
                        "title": clean_title(val.get("title")),
                        "manual": bool(val.get("manual")),
                        "at": _num(val.get("at")),
                    }

    def _save_locked(self):
        """Atomic replace. Caller holds the lock."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.parent / (self._path.name + ".tmp")
            tmp.write_text(json.dumps({
                "members": self._members,
                "titles": self._titles,
            }), encoding="utf-8")
            os.replace(str(tmp), str(self._path))
        except OSError:
            # A list that cannot persist still works for this process; losing
            # it on restart beats failing the request that touched it.
            pass

    def _prune_locked(self, now: float):
        stale = [k for k, m in self._members.items()
                 if m.get("done_at") and now - m["done_at"] > _DONE_GRACE_S]
        stale += [k for k, m in self._members.items()
                  if k.startswith(JOB_KEY_PREFIX)
                  and now - _num(m.get("added_at")) > _PLACEHOLDER_TTL_S]
        for k in stale:
            self._members.pop(k, None)
            self._titles.pop(k, None)
        if len(self._members) > _MAX_MEMBERS:
            ordered = sorted(self._members.items(),
                            key=lambda kv: kv[1].get("added_at") or 0.0)
            for k, _ in ordered[:len(self._members) - _MAX_MEMBERS]:
                self._members.pop(k, None)

    # ---- membership --------------------------------------------------

    def enroll(self, key: str, *, provider: str = "", cwd: str = "",
               job_id: str = "") -> bool:
        """Put a card on the focus list for a human action on ``key``.

        Idempotent, and it *revives*: sending a prompt to something you had
        marked done means you are working on it again, so the done stamp is
        cleared. Returns True when this call changed the list.
        """
        key = str(key or "").strip()
        if not key:
            return False
        now = time.time()
        cursor = float(int(now)) - _CURSOR_GRANULARITY_S
        with self._lock:
            member = self._members.get(key)
            if member is None:
                self._members[key] = {
                    "added_at": now,
                    "done_at": None,
                    "seen_at": cursor,
                    "provider": str(provider or ""),
                    "cwd": str(cwd or ""),
                    "job_id": str(job_id or ""),
                }
                changed = True
            else:
                changed = member.get("done_at") is not None
                member["done_at"] = None
                member["seen_at"] = max(_num(member.get("seen_at")), cursor)
                if provider and not member.get("provider"):
                    member["provider"] = str(provider)
                if cwd and not member.get("cwd"):
                    member["cwd"] = str(cwd)
                if job_id:
                    member["job_id"] = str(job_id)
            self._prune_locked(now)
            self._save_locked()
        return changed

    def rekey(self, old_key: str, new_key: str) -> bool:
        """Migrate a card from its ``job:<id>`` placeholder to a session id.

        Called from the listing path, which is the first moment both ids are
        known. Merges rather than clobbers: if the session already has a card
        (a resumed session that was enrolled before), the older ``added_at``
        wins and an active card beats a done one.
        """
        old_key = str(old_key or "").strip()
        new_key = str(new_key or "").strip()
        if not old_key or not new_key or old_key == new_key:
            return False
        with self._lock:
            member = self._members.pop(old_key, None)
            if member is None:
                return False
            existing = self._members.get(new_key)
            if existing is None:
                self._members[new_key] = member
            else:
                existing["added_at"] = min(
                    _num(existing.get("added_at")) or _num(member.get("added_at")),
                    _num(member.get("added_at")) or _num(existing.get("added_at")))
                if member.get("done_at") is None:
                    existing["done_at"] = None
                if not existing.get("job_id"):
                    existing["job_id"] = member.get("job_id") or ""
            # A title set while the card was job-keyed follows it over.
            title = self._titles.pop(old_key, None)
            if title is not None and new_key not in self._titles:
                self._titles[new_key] = title
            self._save_locked()
        return True

    def is_member(self, key: str) -> bool:
        with self._lock:
            member = self._members.get(str(key or ""))
            return bool(member and member.get("done_at") is None)

    def member(self, key: str):
        with self._lock:
            member = self._members.get(str(key or ""))
            return dict(member) if member else None

    def active_keys(self) -> set:
        with self._lock:
            return {k for k, m in self._members.items()
                    if m.get("done_at") is None}

    def mark_done(self, key: str) -> bool:
        """Take a card off the focus list. The session itself is untouched — it
        stays in the full session list, which is the only durable record."""
        key = str(key or "").strip()
        with self._lock:
            member = self._members.get(key)
            if member is None or member.get("done_at") is not None:
                return False
            member["done_at"] = time.time()
            self._save_locked()
        return True

    def restore(self, key: str) -> bool:
        """Undo :meth:`mark_done` while the card is still within the grace
        window."""
        key = str(key or "").strip()
        with self._lock:
            member = self._members.get(key)
            if member is None or member.get("done_at") is None:
                return False
            member["done_at"] = None
            self._save_locked()
        return True

    def mark_seen(self, key: str, ts: float = None) -> bool:
        """Advance the read cursor.

        Cosmetic only: it does **not** change a row's state, it decides whether
        a finished turn is drawn lit (you have not looked) or dim (you have).
        Clients call it when the human opens the session, or when a turn
        finishes while that session's transcript is already on screen.
        """
        key = str(key or "").strip()
        now = time.time() if ts is None else float(ts)
        with self._lock:
            member = self._members.get(key)
            if member is None:
                return False
            if _num(member.get("seen_at")) >= now:
                return False
            member["seen_at"] = now
            self._save_locked()
        return True

    def seen_at(self, key: str) -> float:
        with self._lock:
            member = self._members.get(str(key or ""))
            return _num(member.get("seen_at")) if member else 0.0

    # ---- titles ------------------------------------------------------

    def title(self, key: str) -> str:
        with self._lock:
            entry = self._titles.get(str(key or ""))
            return entry.get("title", "") if entry else ""

    def title_entry(self, key: str):
        with self._lock:
            entry = self._titles.get(str(key or ""))
            return dict(entry) if entry else None

    def set_title(self, key: str, text: str, *, manual: bool = True) -> str:
        """Store a title override. Returns the cleaned title actually stored
        (empty string clears the override instead)."""
        key = str(key or "").strip()
        if not key:
            return ""
        title = clean_title(text)
        with self._lock:
            if not title:
                self._titles.pop(key, None)
            else:
                self._titles[key] = {
                    "title": title,
                    "manual": bool(manual),
                    "at": time.time(),
                }
            self._save_locked()
        return title

    def clear_title(self, key: str) -> bool:
        key = str(key or "").strip()
        with self._lock:
            if self._titles.pop(key, None) is None:
                return False
            self._save_locked()
        return True


def _num(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
