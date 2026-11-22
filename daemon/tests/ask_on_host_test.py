"""A question answered in the terminal leaves the phone alone.

The card for an AskUserQuestion panel used to stay up after the panel was
answered where it was (one sat for more than a day). Answering or cancelling
that stale card then typed into the pane: Escape interrupts a running turn,
Down and Enter send whatever was half typed.

Run:  python3 tests/ask_on_host_test.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.jobs import Job  # noqa: E402
from agentremoted.providers.claude_interactive import InteractiveManager  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    print("  [%s] %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        failed += 1
        if detail != "":
            print("       %r" % (detail,))


class Tui:
    name = "t1"


class Cfg:
    question_timeout = 0


QUESTIONS = [{"question": "Which?", "header": "Pick", "multi_select": False,
              "options": [{"label": "A", "description": ""},
                          {"label": "B", "description": ""}]}]


def manager(live):
    """An InteractiveManager whose pane is `live()` and whose keys are logged."""
    m = InteractiveManager.__new__(InteractiveManager)
    m.config = Cfg()
    m.keys = []
    m._await_pane = lambda tui, needle, timeout: True
    m._ask_panel_live = lambda name: live()
    m._tmux = lambda *a, **k: m.keys.append(a[-1])
    m._pane_text = lambda name: ""          # the panel closes after the pick
    return m


def run(m, job):
    t = threading.Thread(target=m._answer_questions, args=(job, Tui(), QUESTIONS),
                         daemon=True)
    t.start()
    return t


def wait_pending(job, want, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if bool(job.pending_question) == want:
            return True
        time.sleep(0.05)
    return False


# 1. The panel goes away by itself: the card is withdrawn, nothing is typed.
state = {"live": True}
m = manager(lambda: state["live"])
job = Job("j1", "s1", "", "/tmp")
t = run(m, job)
check("the question is published", wait_pending(job, True))
state["live"] = False
check("answered on the host: the card is withdrawn", wait_pending(job, False))
t.join(5)
check("answered on the host: no key reaches the pane", m.keys == [], m.keys)

# 2. One bad reading of the pane does not withdraw a live question.
state = {"live": True, "blip": 0}


def blip():
    if state["blip"] > 0:
        state["blip"] -= 1
        return False
    return state["live"]


m = manager(blip)
job = Job("j2", "s1", "", "/tmp")
t = run(m, job)
wait_pending(job, True)
state["blip"] = 2
time.sleep(1.5)
check("a repaint does not withdraw the question", bool(job.pending_question))

# 3. Answered from the phone while the panel is up: the pick is typed.
rid = job.pending_question["request_id"]
job.resolve_question(rid, [["B"]])
t.join(5)
check("answered on the phone: Down then Enter", m.keys == ["Down", "Enter"], m.keys)

# 4. Cancelled from the phone while the panel is up: Escape.
m = manager(lambda: True)
job = Job("j3", "s1", "", "/tmp")
t = run(m, job)
wait_pending(job, True)
job.cancel_question()
t.join(5)
check("cancelled on the phone: Escape", m.keys == ["Escape"], m.keys)

# 5. A stale card answered late (the panel went between polls): no keys.
state = {"live": True}
m = manager(lambda: state["live"])
job = Job("j4", "s1", "", "/tmp")
t = run(m, job)
wait_pending(job, True)
time.sleep(0.5)                 # the panel has been seen
rid = job.pending_question["request_id"]
state["live"] = False
job.resolve_question(rid, [["B"]])
t.join(5)
check("a late answer to a closed panel types nothing", m.keys == [], m.keys)

print("FAILED: %d" % failed if failed else "all passed")
sys.exit(1 if failed else 0)
