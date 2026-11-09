"""The goal watchdog: when the daemon types /goal resume, and when it stops.

A codex thread with a goal restarts itself after every turn - until it
doesn't, and then it sits at an idle prompt with work left to do. These are
the rules for noticing that and nudging it, and for giving up rather than
typing into a dead prompt for ever.

Run:  python3 tests/codex_goal_test.py
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-codex-goal-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted import goalwatch                               # noqa: E402
from agentremoted.config import Config                           # noqa: E402
from agentremoted.providers.codex import CodexStore              # noqa: E402
from agentremoted.providers.codex_interactive import (            # noqa: E402
    CodexInteractiveManager, _Tui)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


SID = "01a1694f-0750-75b0-9c69-fb3db54d70fc"
CODEX_HOME = Path(FAKE_HOME) / ".codex"
CODEX_HOME.mkdir(parents=True, exist_ok=True)


def write_goals(rows):
    db = CODEX_HOME / "goals_1.sqlite"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE thread_goals (thread_id TEXT, goal_id TEXT, "
                "objective TEXT, status TEXT, token_budget TEXT, "
                "tokens_used TEXT, time_used_seconds TEXT, "
                "created_at_ms TEXT, updated_at_ms TEXT)")
    for r in rows:
        con.execute("INSERT INTO thread_goals VALUES (?,?,?,?,?,?,?,?,?)", r)
    con.commit()
    con.close()


print("reading codex's goals database")

write_goals([
    (SID, "g1", "have android 7 running", "active", None, "27531898",
     "223718", "1792769909149", "1793232315939"),
    ("other", "g2", "something finished", "completed", None, "10", "20",
     "1792769909149", "1793232315939"),
])
store = CodexStore(CODEX_HOME)
goals = store.goals()
check("both goals are read", set(goals) == {SID, "other"}, sorted(goals))
check("the counters are numbers, not the strings sqlite holds",
      goals[SID]["tokens_used"] == 27531898.0
      and goals[SID]["time_used_s"] == 223718.0, goals[SID])
check("updated_at comes back in seconds",
      abs(goals[SID]["updated_at"] - 1793232315.939) < 0.01,
      goals[SID]["updated_at"])
check("a home with no goals database is simply empty",
      CodexStore(Path(FAKE_HOME) / "nowhere").goals() == {})

print("\nthe watch list")

path = Path(FAKE_HOME) / "goalwatch.json"
w = goalwatch.GoalWatch(path)
check("nothing is watched until a human asks", w.watching() == [])
w.set(SID, True)
check("enrolling makes it watched", w.watching() == [SID], w.watching())
check("it survives a daemon restart",
      goalwatch.GoalWatch(path).watching() == [SID])

w.note_resume(SID, tokens_used=100)
row = w.get(SID)
check("a resume is counted", row["resumes"] == 1, row)
check("the first resume is not called stale", row["stale_resumes"] == 0, row)
w.note_resume(SID, tokens_used=100)
check("a resume that moved no tokens is stale",
      w.get(SID)["stale_resumes"] == 1, w.get(SID))
w.note_resume(SID, tokens_used=250)
check("a resume that moved the goal clears the count",
      w.get(SID)["stale_resumes"] == 0, w.get(SID))

w.give_up(SID, "three resumes in a row moved nothing")
check("a thread given up on is no longer watched", w.watching() == [],
      w.watching())
check("and the reason is kept for the client",
      w.get(SID)["stopped_why"].startswith("three resumes"))
w.set(SID, True)
check("switching it on again clears the give-up",
      w.watching() == [SID] and not w.get(SID)["stopped_why"], w.get(SID))

print("\nwhen the watchdog types /goal resume")

goalwatch._shared = w           # the manager asks for the shared list
cfg = Config({"providers": ["codex"], "codex_home": str(CODEX_HOME)})


class FakeRunner:
    def __init__(self, store):
        self.store = store


mgr = CodexInteractiveManager(cfg, FakeRunner(store))
tui = _Tui("cdx-test", str(FAKE_HOME))
tui.spawned = True
tui.session_id = SID
tui.rollout_path = str(CODEX_HOME / "rollout.jsonl")
Path(tui.rollout_path).write_text("{}\n", encoding="utf-8")
mgr._tuis[tui.name] = tui

sent = []
state = {"tui": True, "busy": False, "job": False,
         "idle_s": goalwatch.DEFAULT_IDLE_S + 60}
mgr._tmux_alive = lambda name: True
mgr.goal_state = lambda sid: dict(state)
mgr._send_prompt = lambda t, text: sent.append(text) or ""

w.set(SID, True)
w._rows[SID]["last_resume_at"] = 0.0
check("a stalled goal gets the nudge",
      mgr.goal_tick() == [SID] and sent == [goalwatch.RESUME_COMMAND], sent)

sent[:] = []
check("and not a second one inside the cooldown", mgr.goal_tick() == [] and not sent)

w._rows[SID]["last_resume_at"] = time.time() - goalwatch.DEFAULT_COOLDOWN_S - 1
state["busy"] = True
check("a thread that is working is left alone", mgr.goal_tick() == [] and not sent)
state["busy"] = False
state["job"] = True
check("so is one running a turn of ours", mgr.goal_tick() == [] and not sent)
state["job"] = False
state["idle_s"] = goalwatch.DEFAULT_IDLE_S - 30
check("so is one that only just went quiet",
      mgr.goal_tick() == [] and not sent)
state["idle_s"] = goalwatch.DEFAULT_IDLE_S + 60
state["tui"] = False
check("with no live TUI there is nowhere to type",
      mgr.goal_tick() == [] and not sent)
state["tui"] = True

w._rows[SID]["stale_resumes"] = goalwatch.DEFAULT_MAX_STALE_RESUMES
check("after enough resumes that moved nothing it gives up",
      mgr.goal_tick() == [] and not sent and w.get(SID)["stopped_why"],
      w.get(SID))

w.set(SID, True)
w._rows[SID]["last_resume_at"] = 0.0
write_goals([(SID, "g1", "done now", "completed", None, "1", "1", "1", "2")])
check("a goal that is no longer active stops the watch",
      mgr.goal_tick() == [] and not sent
      and "completed" in w.get(SID)["stopped_why"], w.get(SID))

print("\n%s" % ("FAILED: " + ", ".join(failures) if failures else "all ok"))
sys.exit(1 if failures else 0)
