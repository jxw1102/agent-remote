"""Per-session turn options: the model (and effort) one session runs on.

Model used to be a property of the *client*: one pick per daemon, applied to
whatever session you happened to have open. That is wrong twice over — the
picker showed a model the open session was not running on, and changing it
silently moved every other session too.

The model is a property of the session. Most of the time it needs no storage,
because the transcript already records it: the session list reports the model
each session last ran on (`model`), read back from the harness's own files.
This module stores the one thing a transcript cannot answer — a model the
human has chosen but which has not run yet — and keeps it until it does:

    POST /api/sessions/<id>/options {"model": "claude-opus-5"}

The pin then rides every turn of that session, from any client, and appears on
the session row as ``model_pinned`` so the other clients show it immediately
rather than after the next turn. An empty string clears it, putting the
session back on whatever its harness would pick.

Pins are tiny and few (one short string per session you have deliberately
re-pointed), but nothing prunes sessions, so the store is capped and evicts
oldest-first.
"""

import json
import os
import threading
import time

from .config import CONFIG_DIR

# Hard cap on stored pins, oldest-first eviction. A pin is only created by a
# deliberate pick, so this is generous: it exists so a runaway client cannot
# grow the file without bound.
_MAX_PINS = 500

# Longest model / effort id accepted. Real ids are short
# ("claude-opus-5-5[1m]", "gpt-6.2-sol", "high"); anything longer is a client
# bug or an attempt to use the store as scratch space.
_VALUE_MAX = 120

# What every client already means by "let the harness decide". Storing it
# would pin a session to a non-choice, so it clears the pin instead.
_NEUTRAL = ("", "default", "auto")


def normalize(value) -> str:
    """A pin value, or "" for "no pin" (including the neutral choices)."""
    text = str(value or "").strip()
    if len(text) > _VALUE_MAX:
        return ""
    return "" if text.lower() in _NEUTRAL else text


class SessionPrefs:
    """Model / effort pins, keyed by session id, persisted as one JSON file."""

    def __init__(self, path=None):
        self.path = path or (CONFIG_DIR / "session-prefs.json")
        self._lock = threading.Lock()
        self._pins = {}
        self._load()

    # -- persistence ----------------------------------------------------

    def _load(self):
        try:
            with open(str(self.path), encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return
        pins = data.get("sessions") if isinstance(data, dict) else None
        if not isinstance(pins, dict):
            return
        clean = {}
        for key, entry in pins.items():
            if not isinstance(key, str) or not isinstance(entry, dict):
                continue
            row = self._row(entry)
            if row["model"] or row["effort"]:
                clean[key] = row
        with self._lock:
            self._pins = clean

    @staticmethod
    def _row(entry: dict) -> dict:
        return {
            "model": normalize(entry.get("model")),
            "effort": normalize(entry.get("effort")),
            "provider": str(entry.get("provider") or "")[:40],
            "at": float(entry.get("at") or 0.0),
        }

    def _save_locked(self):
        payload = {"version": 1, "sessions": self._pins}
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, str(self.path))
        except OSError:
            pass

    def _prune_locked(self):
        if len(self._pins) <= _MAX_PINS:
            return
        oldest = sorted(self._pins.items(), key=lambda kv: kv[1].get("at", 0.0))
        for key, _entry in oldest[:len(self._pins) - _MAX_PINS]:
            self._pins.pop(key, None)

    # -- reads ----------------------------------------------------------

    def get(self, key: str) -> dict:
        """{"model", "effort"} pinned to this session; empty strings if none."""
        k = str(key or "").strip()
        if not k:
            return {"model": "", "effort": ""}
        with self._lock:
            entry = self._pins.get(k)
            if entry is None:
                return {"model": "", "effort": ""}
            return {"model": entry.get("model", ""),
                    "effort": entry.get("effort", "")}

    def model(self, key: str) -> str:
        return self.get(key)["model"]

    def effort(self, key: str) -> str:
        return self.get(key)["effort"]

    # -- writes ---------------------------------------------------------

    def set(self, key: str, *, model=None, effort=None, provider: str = "") -> dict:
        """Pin model and/or effort. `None` leaves a field alone, "" clears it.

        Returns the pins now in force for this session.
        """
        k = str(key or "").strip()
        if not k:
            return {"model": "", "effort": ""}
        with self._lock:
            entry = dict(self._pins.get(k) or
                         {"model": "", "effort": "", "provider": "", "at": 0.0})
            if model is not None:
                entry["model"] = normalize(model)
            if effort is not None:
                entry["effort"] = normalize(effort)
            if provider:
                entry["provider"] = str(provider)[:40]
            entry["at"] = time.time()
            if entry["model"] or entry["effort"]:
                self._pins[k] = entry
            else:
                # Back to "whatever the harness picks": no row at all, so the
                # store does not fill with sessions that pin nothing.
                self._pins.pop(k, None)
            self._prune_locked()
            self._save_locked()
            return {"model": entry["model"], "effort": entry["effort"]}

    def clear(self, key: str) -> bool:
        k = str(key or "").strip()
        with self._lock:
            if k not in self._pins:
                return False
            del self._pins[k]
            self._save_locked()
            return True

    def rekey(self, old_key: str, new_key: str) -> bool:
        """Move a pin to the session id a provider minted on resume (grok)."""
        old, new = str(old_key or "").strip(), str(new_key or "").strip()
        if not old or not new or old == new:
            return False
        with self._lock:
            entry = self._pins.get(old)
            if entry is None or new in self._pins:
                return False
            self._pins[new] = entry
            del self._pins[old]
            self._save_locked()
            return True
