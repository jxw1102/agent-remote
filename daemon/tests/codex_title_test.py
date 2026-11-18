"""What a codex session is called in the list.

Codex summarises a thread into ``threads.name`` ("Diagnose Android app
crashes") a turn or two in. ``threads.title`` is not a second opinion: it
holds the raw opening message, byte for byte the same as ``preview``. So the
name wins wherever there is one — in the list and in search — and a thread
Codex never named still falls back to the opening message.

Run:  python3 tests/codex_title_test.py
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-codex-title-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.providers.codex import CodexStore              # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


NAMED = "01a1ee30-aed8-7bb3-bb58-fa486b955fbb"
UNNAMED = "01a1ee26-308f-7061-810b-48c27eaa945c"
OPENING = ("Android runtime. Continue the claude session 46c2870a and get "
           "the app to start")
OLD_OPENING = "I need a tool to download apk for arm32 Android devices"

_COLS = ("id TEXT, title TEXT, cwd TEXT, model TEXT, git_branch TEXT, "
         "preview TEXT, rollout_path TEXT, created_at INTEGER, "
         "updated_at INTEGER, archived INTEGER, first_user_message TEXT, "
         "has_user_event INTEGER")


def write_threads(home, rows, with_name=True):
    """A codex state db. `with_name=False` is a CLI from before names."""
    home.mkdir(parents=True, exist_ok=True)
    db = home / "state_5.sqlite"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(str(db))
    cols = _COLS + (", name TEXT" if with_name else "")
    con.execute("CREATE TABLE threads (%s)" % cols)
    width = 12 + (1 if with_name else 0)
    for r in rows:
        r = list(r)[:width]
        con.execute("INSERT INTO threads VALUES (%s)"
                    % ",".join("?" * width), r)
    con.commit()
    con.close()


def row(sid, opening, name, cwd="/Users/x/Developer"):
    # Codex writes the opening message into BOTH title and preview.
    return (sid, opening, cwd, "gpt-5", "main", opening, "", 1763000000,
            1763000900, 0, opening, 1, name)


print("the name codex gave the thread")

home = Path(FAKE_HOME) / ".codex"
write_threads(home, [
    row(NAMED, OPENING, "Diagnose Android app crashes"),
    row(UNNAMED, OLD_OPENING, ""),
])
store = CodexStore(home)
by_id = {s["id"]: s for s in store.list_sessions(limit=25)}

check("both threads are listed", set(by_id) == {NAMED, UNNAMED}, sorted(by_id))
check("a named thread is listed under codex's own name",
      by_id[NAMED]["title"] == "Diagnose Android app crashes",
      by_id[NAMED]["title"])
check("the raw opening message in `title` never wins over the name",
      OPENING[:20] not in by_id[NAMED]["title"], by_id[NAMED]["title"])
check("a thread codex never named falls back to its opening message",
      by_id[UNNAMED]["title"].startswith("I need a tool"),
      by_id[UNNAMED]["title"])
check("get_session agrees with the list",
      store.get_session(NAMED)["title"] == "Diagnose Android app crashes",
      store.get_session(NAMED))

print("\nsearching for that name")

hits = store.search_sessions("android app crashes")
check("the name is searched, not only the transcript",
      [h["id"] for h in hits] == [NAMED], [h["id"] for h in hits])
check("and a hit on the name snippets the name",
      hits and "Diagnose Android app crashes" in hits[0]["snippet"],
      hits[0]["snippet"] if hits else None)
check("the opening message is still searchable",
      [h["id"] for h in store.search_sessions("arm32")] == [UNNAMED])
check("a miss is a miss",
      store.search_sessions("nothing here at all") == [])

print("\na codex too old to name its threads")

old = Path(FAKE_HOME) / ".codex-old"
write_threads(old, [row(UNNAMED, OLD_OPENING, None)], with_name=False)
old_store = CodexStore(old)
rows = old_store.list_sessions(limit=25)
check("a threads table with no `name` column still lists",
      [r["id"] for r in rows] == [UNNAMED], rows)
check("and falls back to the opening message",
      rows and rows[0]["title"].startswith("I need a tool"),
      rows[0]["title"] if rows else None)
check("search still works without the column",
      [h["id"] for h in old_store.search_sessions("arm32")] == [UNNAMED])

print("\na blank name is not a name")

blank = Path(FAKE_HOME) / ".codex-blank"
write_threads(blank, [row(NAMED, OPENING, "   ")])
check("whitespace falls through to the opening message",
      CodexStore(blank).list_sessions(limit=25)[0]["title"]
      .startswith("Android runtime"))

print("\n%s" % ("FAILURES: " + ", ".join(failures) if failures
                else "all checks passed"))
sys.exit(1 if failures else 0)
