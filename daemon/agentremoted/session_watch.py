"""Detect newly created sessions and expose a revision the status streams push.

A client that only polls `/api/sessions` learns about a session started
elsewhere — the host's own TUI, another phone, a headless turn — only on its
next manual refresh. This watcher gives the live status stream a cheap signal
for "the set of sessions changed": a monotonic `sessions_rev` that bumps
whenever a new session appears (or one goes away). The stream already pushes
only when its JSON payload changes, so a bumped rev rides out to every
connected client for free, and the client re-lists.

Detection reuses each store's existing `list_projects()` — the same
memoized, head-only scan the projects endpoint runs — and fingerprints the
per-project session counts. A new session raises a project's count (or adds a
project); activity *within* an existing session leaves the counts untouched,
so a running turn does not churn the rev. One shared instance serves every
stream connection, and the scan is throttled so N clients cause one scan per
interval, not N.
"""

import threading
import time


class SessionWatcher:
    """Shared, thread-safe, throttled fingerprint of the session set."""

    def __init__(self, stores, interval: float = 2.0):
        # provider name -> session store (anything with list_projects()).
        self._stores = dict(stores or {})
        self._interval = interval
        self._lock = threading.Lock()
        self._last_scan = 0.0
        self._fingerprint = None   # provider -> {project_id: session_count}
        self._rev = 0

    def _compute(self) -> dict:
        fp = {}
        for name, store in self._stores.items():
            if store is None:
                continue
            counts = {}
            try:
                for proj in store.list_projects():
                    counts[str(proj.get("id"))] = int(proj.get("session_count") or 0)
            except Exception:
                # A flaky provider must never take down the stream; keep the
                # previous slice for it by carrying nothing new this pass.
                if self._fingerprint is not None:
                    counts = self._fingerprint.get(name, {})
            fp[name] = counts
        return fp

    def rev(self, now: float = None) -> int:
        """Current session revision, recomputing at most once per interval."""
        now = time.time() if now is None else now
        with self._lock:
            if self._fingerprint is not None and now - self._last_scan < self._interval:
                return self._rev
            self._last_scan = now
            fp = self._compute()
            if self._fingerprint is None:
                # First scan establishes the baseline without a phantom bump.
                self._fingerprint = fp
            elif fp != self._fingerprint:
                self._fingerprint = fp
                self._rev += 1
            return self._rev
