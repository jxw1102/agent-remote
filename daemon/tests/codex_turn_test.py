"""Codex turn boundaries: who is working, and whose turn a message belongs to.

A codex thread with an active goal starts turns nobody submitted. Our job
ends with OUR turn, so the job alone cannot answer "is it still working" -
and a transcript that hides the self-started turns reads as the same
progress report over and over.

Run:  python3 tests/codex_turn_test.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-codex-turn-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.providers.codex import (                        # noqa: E402
    _build_transcript, _goal_continuation, _render_codex_message)
from agentremoted.providers.codex_interactive import _tail_mid_turn  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def ev_msg(role, text):
    return {"type": "response_item", "timestamp": "2026-10-24T04:00:00.000Z",
            "payload": {"type": "message", "role": role,
                        "content": [{"type": "text", "text": text}]}}


def ev_item(kind, text):
    return {"type": "event_msg", "timestamp": "2026-10-24T04:00:01.000Z",
            "payload": {"type": "item_completed",
                        "item": {"type": kind, "text": text}}}


def ev(name):
    return {"type": "event_msg", "timestamp": "2026-10-24T04:00:02.000Z",
            "payload": {"type": name}}


GOAL = ('<codex_internal_context source="goal">\n'
        'Continue working toward the active thread goal.\n'
        '</codex_internal_context>')

ROLLOUT = [
    ev_msg("user", "Work on the Android 7 runtime"),
    ev_item("UserMessage", "Work on the Android 7 runtime"),
    ev("task_started"),
    ev_item("AgentMessage", "First progress report."),
    ev("task_complete"),
    # Nobody typed this: the goal restarted the thread.
    ev_msg("user", GOAL),
    ev("task_started"),
    ev_item("AgentMessage", "More progress, much like the last one."),
    ev("task_complete"),
    ev_msg("user", GOAL),
    ev("task_started"),
    ev_item("AgentMessage", "Still more progress."),
]


def write_rollout(name, rows):
    path = Path(FAKE_HOME) / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


print("goal continuations in the transcript")

check("the injected goal block is recognised", _goal_continuation(ROLLOUT[5]))
check("a real user message is not", not _goal_continuation(ROLLOUT[0]))
check("an assistant message is not",
      not _goal_continuation(ev_msg("assistant", "hello")))

path = write_rollout("rollout.jsonl", ROLLOUT)
msgs = _build_transcript(path)
roles = [m["role"] for m in msgs]
check("the transcript keeps one row per real turn boundary",
      roles == ["user", "meta", "assistant", "meta", "assistant", "assistant"]
      or roles == ["user", "assistant", "meta", "assistant", "meta", "assistant"],
      roles)
check("the injected block never shows as something the user typed",
      not any("codex_internal_context" in (m["text"] or "") for m in msgs),
      [m["text"][:40] for m in msgs])
check("both self-started turns are marked",
      sum(1 for m in msgs if m["role"] == "meta") == 2, roles)

meta = [m for m in msgs if m["role"] == "meta"][0]
_render_codex_message(meta)
block = (meta.get("blocks") or [{}])[0]
check("the marker renders as a meta row, not a message",
      block.get("k") == "meta" and block.get("metaKind") == "goal", block)

print("\nis the thread working right now")

open_turn = write_rollout("open.jsonl", ROLLOUT)          # ends mid-turn
done_turn = write_rollout("done.jsonl", ROLLOUT[:9])      # ends on complete
check("a turn with no completion reads as working",
      _tail_mid_turn(str(open_turn), open_turn.stat().st_size))
check("a finished turn does not",
      not _tail_mid_turn(str(done_turn), done_turn.stat().st_size))

empty = write_rollout("empty.jsonl", [])
check("an empty rollout does not",
      not _tail_mid_turn(str(empty), empty.stat().st_size))
check("a missing rollout does not", not _tail_mid_turn(
    str(Path(FAKE_HOME) / "nope.jsonl"), 10))

# The marker must survive a turn bigger than one read chunk.
big = [ROLLOUT[2]] + [ev_item("Reasoning", "x" * 2000) for _ in range(400)]
huge = write_rollout("huge.jsonl", big)
check("a turn larger than the scan chunk is still seen as working",
      _tail_mid_turn(str(huge), huge.stat().st_size),
      "%.1f KB" % (huge.stat().st_size / 1024))

print("\n%s" % ("FAILED: " + ", ".join(failures) if failures else "all ok"))
sys.exit(1 if failures else 0)
