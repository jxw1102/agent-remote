"""A codex session that Codex forked is still one session.

Codex does not grow a thread forever. Its own rewind, and the fresh thread
it opens when a conversation outgrows the context window, both start a NEW
thread id whose rollout head records ``forked_from_id`` and the ordinal it
forked at. The tmux pane keeps running and the person keeps typing into the
same window — so the daemon has to follow, or the transcript freezes at the
fork while the Live TUI goes on showing work.

Not every fork continues, though. Codex forks to BRANCH as well, and a
thread that kept talking after the fork is a conversation of its own.

Run:  python3 tests/codex_fork_chain_test.py
"""

import calendar
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-codex-fork-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.providers.codex import CodexStore                # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


HOME = Path(FAKE_HOME) / ".codex"
SESS = HOME / "sessions"

# Thread ids are UUIDs — hex only, as the real ones are.
ROOT = "01a10000-0000-7000-8000-000000000001"
CONT = "01a10000-0000-7000-8000-000000000002"
BRANCH = "01a10000-0000-7000-8000-000000000003"
SUB = "01a10000-0000-7000-8000-000000000004"
LONE = "01a10000-0000-7000-8000-000000000005"


def when(iso):
    """Unix seconds for an ISO instant, so the fixtures agree with the
    timestamps inside the rollouts."""
    return calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))

_COLS = ("id TEXT, title TEXT, cwd TEXT, model TEXT, git_branch TEXT, "
         "preview TEXT, rollout_path TEXT, created_at INTEGER, "
         "updated_at INTEGER, archived INTEGER, first_user_message TEXT, "
         "has_user_event INTEGER, name TEXT")


def chat(ts, role, text):
    kind = "user_message" if role == "user" else "agent_message"
    return {"timestamp": ts, "type": "event_msg",
            "payload": {"type": kind, "message": text}}


def call(ts, name, args):
    return {"timestamp": ts, "type": "response_item",
            "payload": {"type": "function_call", "name": name,
                        "arguments": args, "call_id": name + ts}}


def head(ts, sid, forked_from="", ordinal=0):
    payload = {"id": sid, "session_id": sid}
    if forked_from:
        payload["forked_from_id"] = forked_from
        payload["forked_from_ordinal_exclusive"] = ordinal
    payload["timestamp"] = ts
    payload["cwd"] = "/Users/x/Developer"
    # The real head carries a ~20 kB instructions blob after these fields.
    payload["base_instructions"] = {"text": "x" * 4000}
    return {"timestamp": ts, "type": "session_meta", "payload": payload}


def write_rollout(sid, events):
    SESS.mkdir(parents=True, exist_ok=True)
    path = SESS / ("rollout-%s.jsonl" % sid)
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return str(path)


def write_threads(rows):
    HOME.mkdir(parents=True, exist_ok=True)
    db = HOME / "state_5.sqlite"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE threads (%s)" % _COLS)
    for r in rows:
        con.execute("INSERT INTO threads VALUES (%s)" % ",".join("?" * 13), r)
    con.commit()
    con.close()


def row(sid, path, created, updated, name, opening="the opening message"):
    return (sid, opening, "/Users/x/Developer", "gpt-5", "main", opening,
            path, created, updated, 0, opening, 1, name)


# ---------------------------------------------------------------- fixtures
#
# ROOT   talks, then is forked at line 6 and never speaks again  -> continued
#        (its lines 6.. are the branch the fork rewound away)
# BRANCH forked off ROOT earlier, at line 3, while ROOT talked on -> its own
# SUB    forked off ROOT at ordinal 0: a spawned sub-agent, never a session
# LONE   never forked

root_path = write_rollout(ROOT, [
    head("2026-11-18T10:00:00.000Z", ROOT),                           # 0
    chat("2026-11-18T10:00:10.000Z", "user", "start the engine"),     # 1
    call("2026-11-18T10:00:11.000Z", "shell", '{"command":["ls"]}'),  # 2
    chat("2026-11-18T10:00:12.000Z", "assistant", "engine started"),  # 3
    chat("2026-11-18T10:01:00.000Z", "user", "now the gearbox"),      # 4
    chat("2026-11-18T10:01:30.000Z", "assistant", "gearbox done"),    # 5
    # Everything from here was rewound away by the fork at ordinal 6.
    chat("2026-11-18T10:02:00.000Z", "user", "ABANDONED PROMPT"),     # 6
    chat("2026-11-18T10:02:30.000Z", "assistant", "ABANDONED REPLY"),  # 7
])
cont_path = write_rollout(CONT, [
    head("2026-11-18T11:00:00.000Z", CONT, ROOT, 6),                  # 0
    chat("2026-11-18T11:00:10.000Z", "user", "carry on with the axle"),
    call("2026-11-18T11:00:11.000Z", "grep", '{"pattern":"axle"}'),
    chat("2026-11-18T11:00:20.000Z", "assistant", "the axle is bolted on"),
])
branch_path = write_rollout(BRANCH, [
    head("2026-11-18T10:00:30.000Z", BRANCH, ROOT, 3),
    chat("2026-11-18T10:00:40.000Z", "user", "a side question"),
    chat("2026-11-18T10:00:50.000Z", "assistant", "a side answer"),
])
sub_path = write_rollout(SUB, [
    head("2026-11-18T10:00:35.000Z", SUB, ROOT, 0),
    chat("2026-11-18T10:00:36.000Z", "assistant", "sub-agent output"),
])
lone_path = write_rollout(LONE, [
    head("2026-11-18T09:00:00.000Z", LONE),
    chat("2026-11-18T09:00:10.000Z", "user", "unrelated session"),
])

TIP_ACTIVE = "2026-11-18T11:00:20Z"

write_threads([
    # ROOT's own updated_at runs past the fork, as a real one does: a turn
    # already in flight keeps writing to the old file for a while.
    row(ROOT, root_path, when("2026-11-18T10:00:00Z"),
        when("2026-11-18T11:30:00Z"), "Build the engine"),
    row(CONT, cont_path, when("2026-11-18T11:00:00Z"),
        when(TIP_ACTIVE), "Build the engine"),
    row(BRANCH, branch_path, when("2026-11-18T10:00:30Z"),
        when("2026-11-18T10:00:50Z"), "A side question"),
    row(SUB, sub_path, when("2026-11-18T10:00:35Z"),
        when("2026-11-18T10:00:36Z"), ""),
    row(LONE, lone_path, when("2026-11-18T09:00:00Z"),
        when("2026-11-18T09:00:10Z"), "Something else"),
])

store = CodexStore(HOME)

print("the chain")

chain = store.chain(ROOT)
check("a continued session is two rollouts, oldest first",
      [c[0] for c in chain] == [ROOT, CONT], [c[0] for c in chain])
check("the first segment stops where the fork took it",
      chain and chain[0][2] == 6, chain[0][2] if chain else None)
check("the last segment runs to the end",
      chain and chain[-1][2] is None, chain[-1][2] if chain else None)
check("a session nobody forked is one segment",
      [c[0] for c in store.chain(LONE)] == [LONE])
check("a branch stands on its own",
      [c[0] for c in store.chain(BRANCH)] == [BRANCH])
check("a thread that kept talking after a fork is NOT continued by it",
      BRANCH not in [c[0] for c in chain], chain)
check("a fork at ordinal 0 is a spawned sub-agent, never a continuation",
      SUB not in [c[0] for c in chain], chain)

print("\nthe list")

rows = {s["id"]: s for s in store.list_sessions(limit=50)}
check("the continuation gets no row of its own", CONT not in rows, sorted(rows))
check("the session it continues keeps its row", ROOT in rows, sorted(rows))
check("a branch keeps its own row", BRANCH in rows, sorted(rows))
check("an unforked session is untouched", LONE in rows, sorted(rows))
check("the row shows what the session said LAST, across the fork",
      rows[ROOT]["last_text"].startswith("the axle is bolted on"),
      rows[ROOT]["last_text"])
check("and when it last spoke, across the fork",
      rows[ROOT]["last_active"] == TIP_ACTIVE,
      rows[ROOT]["last_active"])
check("its size counts every rollout it is spread over",
      rows[ROOT]["size_bytes"] == os.path.getsize(root_path)
      + os.path.getsize(cont_path), rows[ROOT]["size_bytes"])
check("opening the continuation by id still works",
      (store.get_session(CONT) or {}).get("id") == CONT)

print("\nthe transcript")

msgs = store.get_messages(ROOT, offset=0, limit=50)
texts = [m["text"] for m in msgs["messages"]]
check("it runs from the first prompt into the fork",
      texts[0].startswith("start the engine")
      and texts[-1].startswith("the axle is bolted on"), texts)
check("the work the fork rewound away is gone",
      not any("ABANDONED" in t for t in texts), texts)
check("every message is there once",
      len(texts) == len(set(texts)), texts)
check("message ids stay unique across the fork",
      len({m["uuid"] for m in msgs["messages"]}) == len(msgs["messages"]))
check("an unforked session reads as before",
      [m["text"] for m in
       store.get_messages(LONE, offset=0, limit=10)["messages"]]
      == ["unrelated session"])

print("\nsteps across the fork")

det = store.get_messages(ROOT, offset=0, limit=50, steps=True)
refs = []
for m in det["messages"]:
    for st in (m.get("steps") or []):
        refs.append(st.get("id") or st.get("ref") or "")
check("both rollouts contribute steps", len(refs) >= 2, refs)
tool_refs = [r for r in refs if r.startswith("cu")]
check("a step from the second rollout resolves",
      tool_refs and any(
          (store.get_step(ROOT, r) or {}).get("text", "").find("axle") >= 0
          for r in tool_refs), tool_refs)
check("a step from the first rollout still resolves",
      any((store.get_step(ROOT, r) or {}).get("text", "").find("ls") >= 0
          for r in tool_refs), tool_refs)

print("\nwhere a rewind would cut")

check("rollout_path is the file the session writes to NOW",
      store.rollout_path(ROOT) == cont_path, store.rollout_path(ROOT))
check("an unforked session still points at its own file",
      store.rollout_path(LONE) == lone_path)

print("\nsearch")

hits = {h["id"]: h for h in store.search_sessions("axle")}
check("a hit inside the continuation is reported against the open-able row",
      list(hits) == [ROOT], list(hits))
check("the hit still carries its snippet",
      "axle" in (hits.get(ROOT, {}).get("snippet") or "").lower(),
      hits.get(ROOT, {}).get("snippet"))
check("a hit in a branch stays with the branch",
      [h["id"] for h in store.search_sessions("side answer")] == [BRANCH])

print("\n%s" % ("FAILURES: " + ", ".join(failures) if failures
                else "all checks passed"))
sys.exit(1 if failures else 0)
