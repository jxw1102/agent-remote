"""Messages typed into a running codex turn must not keep the job alive.

Codex queues some typed messages as a turn of their own and steers others
into the turn already running. The job used to wait for one more
task_complete per typed message, so a steered one left it "working" after
the thread had gone idle (session 01a1b4c3-…: four messages typed, two
extra turns run, job running for ever).

Run:  python3 tests/codex_typed_ahead_test.py
"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-codex-typed-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config                           # noqa: E402
from agentremoted.jobs import Job                                # noqa: E402
from agentremoted.providers import codex_interactive as ci       # noqa: E402

ci._TYPED_GRACE_S = 1.5         # keep the test quick

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def ev(name, **extra):
    payload = {"type": name}
    payload.update(extra)
    return {"type": "event_msg", "timestamp": "2026-11-13T01:10:00.000Z",
            "payload": payload}


def say(text):
    return {"type": "event_msg", "timestamp": "2026-11-13T01:10:00.000Z",
            "payload": {"type": "item_completed",
                        "item": {"type": "AgentMessage", "text": text}}}


SID = "01a1b4c3-2266-7540-ba38-f197069aeee5"
cfg = Config({"providers": ["codex"], "codex_home": os.path.join(FAKE_HOME, ".codex")})


def harness(name):
    mgr = ci.CodexInteractiveManager(cfg, None)
    mgr._tmux_alive = lambda n: True
    mgr._send_prompt = lambda tui, text: ""
    mgr._confirm_submit = lambda tui, before: None
    mgr._save_state = lambda: None
    tui = ci._Tui("cdx-" + name, FAKE_HOME)
    tui.spawned = True
    tui.session_id = SID
    tui.rollout_path = str(Path(FAKE_HOME) / ("%s.jsonl" % name))
    Path(tui.rollout_path).write_text("", encoding="utf-8")
    mgr._tuis[tui.name] = tui
    job = Job("job-" + name, SID, "do you need to improve anything?", FAKE_HOME)
    job.status = "running"
    t = threading.Thread(target=mgr._run_turn, args=(job, tui, time.time()),
                         daemon=True)
    t.start()
    time.sleep(0.5)             # past the initial EOF snapshot

    def write(*rows):
        with open(tui.rollout_path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    return mgr, tui, job, t, write


print("a typed message codex steers into the running turn")
mgr, tui, job, t, write = harness("steered")
write(ev("task_started"), say("Looking at the installer."))
time.sleep(0.4)
check("typing into the pane is accepted", mgr.type_text(SID, "stop") == "")
write(say("Understood. I've stopped."), ev("task_complete"))
t.join(timeout=ci._TYPED_GRACE_S + 3)
check("the job ends once no new turn starts", job.status == "done", job.status)
check("with the reply that answered it", "stopped" in job.result_text,
      job.result_text)

print("\na typed message codex runs as a turn of its own")
mgr, tui, job, t, write = harness("queued")
write(ev("task_started"), say("First answer."))
time.sleep(0.4)
mgr.type_text(SID, "and the version?")
write(ev("task_complete"))
time.sleep(0.4)
write(ev("task_started"))
time.sleep(ci._TYPED_GRACE_S + 1)  # a quiet turn outlives the grace window
check("the job keeps watching the follow-up turn", job.status == "running",
      job.status)
write(say("Next build is 1.2.0."), ev("task_complete"))
t.join(timeout=3)
check("and ends when it completes", job.status == "done", job.status)
check("having streamed its reply",
      any("1.2.0" in (e.get("text") or "") for e in job.events))

print("\nno typing: the first completion ends the job")
mgr, tui, job, t, write = harness("plain")
write(ev("task_started"), say("Done."), ev("task_complete"))
t.join(timeout=2)
check("job done without waiting out the grace", job.status == "done", job.status)

print("\na fresh TUI that dies before it takes the prompt")
ci.tmux_available = lambda: True
mgr = ci.CodexInteractiveManager(cfg, None)
mgr._send_prompt = lambda tui, text: ""
mgr._confirm_submit = lambda tui, before: None
mgr._save_state = lambda: None
mgr._discover_session = lambda tui, at, timeout_s=0: False
alive = set()
mgr._tmux_alive = lambda n: n in alive
dead = ci._Tui("cdx-dead", FAKE_HOME)
dead.spawned = True
second = ci._Tui("cdx-second", FAKE_HOME)
second.spawned = True
second.session_id = "01a1d2cd-0000-7000-8000-000000000001"
second.rollout_path = str(Path(FAKE_HOME) / "second.jsonl")
Path(second.rollout_path).write_text("", encoding="utf-8")
launches = []


def fake_ensure(cwd, sid, model, effort="", isolate_root=""):
    t = dead if not launches else second
    launches.append(t.name)
    if t is second:
        alive.add(t.name)
    return t, ""


mgr._ensure_tui = fake_ensure
job = Job("job-dead", "", "a long prompt about the EV fix", FAKE_HOME)
t = threading.Thread(target=mgr.run, args=(job,), daemon=True)
t.start()
for _ in range(50):
    if len(launches) == 2:
        break
    time.sleep(0.1)
time.sleep(0.6)
with open(second.rollout_path, "a", encoding="utf-8") as f:
    for r in (ev("task_started"), say("Looking at the EV fix."), ev("task_complete")):
        f.write(json.dumps(r) + "\n")
t.join(timeout=5)
check("the prompt is sent to a second TUI", launches == ["cdx-dead", "cdx-second"],
      launches)
check("and the job finishes there", job.status == "done", (job.status, job.error))

print("\n%s" % ("all ok" if not failures else "FAILED: %s" % ", ".join(failures)))
sys.exit(1 if failures else 0)
