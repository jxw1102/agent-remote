"""A new Codex TUI binds to the thread IT created, not a busy neighbour.

A fresh ``codex -C <dir>`` TUI does not say which thread it made, so the
daemon finds it on disk. It used to take the most recently MODIFIED rollout
in the folder and read ``session_meta.session_id``. On 2026-11-09 that was a
subagent of an older session in the same folder: its file was being written
that second, and its ``session_id`` is its PARENT's id. The new session was
bound to the parent, so the clients showed the parent's frozen transcript
while the live TUI kept writing the real new thread.

The rules this file protects: the bound thread was CREATED after the launch,
is a top-level thread (no subagent, no fork), is identified by its own
``id``, and is not already owned by another TUI.

Run:  python3 tests/codex_bind_test.py
"""

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

HOME = os.path.realpath(tempfile.mkdtemp(prefix="agentremoted-bind-"))
os.environ["AGENTREMOTED_HOME"] = os.path.join(HOME, ".agentremoted")

from agentremoted.providers.codex_interactive import (  # noqa: E402
    CodexInteractiveManager, _Tui)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


CODEX_HOME = Path(HOME) / "codex"
PROJECT = os.path.join(HOME, "Developer")
os.makedirs(PROJECT)

PARENT = "01a1b4c3-2266-7540-ba38-f197069aeee5"
CHILD = "01a1c0d4-cc90-7ae1-824b-f30b6ff90d3d"
NEW = "01a1c0da-db95-7ee2-a143-b43b302e6033"
OTHER = "01a1c0ff-0000-7000-8000-000000000001"


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def rollout(tid, created, *, session_id=None, source="vscode", cwd=PROJECT,
            parent=None):
    day = CODEX_HOME / "sessions" / "2026" / "11" / "09"
    day.mkdir(parents=True, exist_ok=True)
    path = day / ("rollout-2026-11-09T23-00-00-%s.jsonl" % tid)
    payload = {"id": tid, "session_id": session_id or tid, "cwd": cwd,
               "timestamp": iso(created), "source": source}
    if parent:
        payload["forked_from_id"] = parent
        payload["parent_thread_id"] = parent
    path.write_text(json.dumps({"type": "session_meta", "payload": payload})
                    + "\n", encoding="utf-8")
    os.utime(path, (time.time(), time.time()))  # all "busy right now"
    return str(path)


class Cfg:
    codex_home_path = CODEX_HOME


def manager():
    m = object.__new__(CodexInteractiveManager)
    m.config = Cfg()
    m.runner = None
    m._tuis = {}
    m._lock = threading.Lock()
    m._busy_cache = {}
    return m


def main():
    launched = time.time()
    # The old session, still busy, and its subagent spawned before launch.
    rollout(PARENT, launched - 2 * 86400)
    rollout(CHILD, launched - 400, session_id=PARENT, parent=PARENT,
            source={"subagent": {"thread_spawn": {"parent_thread_id": PARENT}}})

    print("before the new thread exists:")
    m = manager()
    tui = _Tui("cdx-test-1", PROJECT)
    tui.spawned = True
    m._tuis[tui.name] = tui
    bound = m._try_bind_session(tui, launched)
    check("nothing is bound yet", not bound and tui.session_id == "",
          tui.session_id)

    print("a subagent spawned AFTER the launch is still not the new thread:")
    late_child = "01a1c0d9-0000-7000-8000-00000000000c"
    rollout(late_child, launched + 0.5, session_id=PARENT, parent=PARENT,
            source={"subagent": {"thread_spawn": {"parent_thread_id": PARENT}}})
    m._try_bind_session(tui, launched)
    check("subagent skipped", tui.session_id not in (late_child, PARENT),
          tui.session_id)

    print("once the TUI writes its thread:")
    new_path = rollout(NEW, launched + 1.2)
    m._try_bind_session(tui, launched)
    check("binds the thread the TUI created", tui.session_id == NEW,
          tui.session_id)
    check("with that thread's own file", tui.rollout_path == new_path,
          tui.rollout_path)
    check("never the subagent's parent", tui.session_id != PARENT)

    print("a second new TUI in the same folder:")
    tui2 = _Tui("cdx-test-2", PROJECT)
    tui2.spawned = True
    m._tuis[tui2.name] = tui2
    m._try_bind_session(tui2, launched)
    check("does not steal the first TUI's thread", tui2.session_id == "",
          tui2.session_id)
    other_path = rollout(OTHER, launched + 3)
    m._try_bind_session(tui2, launched)
    check("binds its own thread", tui2.session_id == OTHER
          and tui2.rollout_path == other_path, (tui2.session_id, tui2.rollout_path))

    print("a resumed TUI keeps its thread:")
    tui3 = _Tui("cdx-test-3", PROJECT)
    tui3.spawned = True
    tui3.session_id = PARENT
    m._tuis[tui3.name] = tui3
    m._try_bind_session(tui3, time.time())
    check("stays on the resumed id", tui3.session_id == PARENT, tui3.session_id)
    check("with the resumed id's own file",
          tui3.rollout_path.endswith("-%s.jsonl" % PARENT), tui3.rollout_path)

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1
    print("all codex bind checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
