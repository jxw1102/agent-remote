"""The transcript a client gets back is the conversation that happened.

A Claude Code transcript is a tree, not a log. Two things reshape it:

  * /rewind hangs the next turn off an earlier node and leaves the
    abandoned turns in the file — those must stay hidden;
  * /compact writes its summary as a NEW ROOT, so one file holds several
    unconnected trees — everything before the last compaction must still
    be reachable, or a long session arrives as a handful of messages with
    no "load earlier messages" to press.

Run:  python3 tests/transcript_branch_test.py
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.providers.claude import ClaudeStore  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def eq(name, got, want):
    check(name, got == want, "got %r want %r" % (got, want))


def msg(uuid, parent, role, text):
    return {"type": role, "uuid": uuid, "parentUuid": parent,
            "timestamp": "2026-10-29T00:00:00.000Z",
            "message": {"role": role,
                        "content": [{"type": "text", "text": text}]}}


def plumbing(uuid, parent, kind="system"):
    """A non-message record: the chain threads through these."""
    return {"type": kind, "uuid": uuid, "parentUuid": parent}


def compact_summary(uuid, parent):
    r = msg(uuid, parent, "user", "This session is being continued…")
    r["isCompactSummary"] = True
    return r


def texts(records):
    """What a client would show, in order."""
    d = tempfile.mkdtemp()
    try:
        proj = os.path.join(d, "-proj")
        os.makedirs(proj)
        with open(os.path.join(proj, "s1.jsonl"), "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        out = ClaudeStore(__import__("pathlib").Path(d)).get_messages("s1", limit=99)
        return [m["text"] for m in out["messages"]], out["total"], out["offset"]
    finally:
        shutil.rmtree(d, ignore_errors=True)


print("transcript branches")

# --- a plain conversation -------------------------------------------------
got, total, _ = texts([
    msg("u1", None, "user", "one"),
    msg("u2", "u1", "assistant", "two"),
])
eq("plain conversation is whole", got, ["one", "two"])

# --- /rewind: the abandoned turn stays hidden -----------------------------
got, _, _ = texts([
    msg("u1", None, "user", "one"),
    msg("u2", "u1", "assistant", "abandoned"),
    msg("u3", "u1", "assistant", "kept"),
])
eq("rewind hides the abandoned branch", got, ["one", "kept"])

# --- /compact: the new root must not cut the history off ------------------
# This is the 13 MB session that arrived as 5 messages: compaction starts a
# fresh tree, so walking parentUuid from the newest message dead-ends at the
# compaction root and everything before it became unreachable.
got, total, offset = texts([
    msg("u1", None, "user", "before one"),
    msg("u2", "u1", "assistant", "before two"),
    plumbing("sys1", None),                 # new root: nothing points at it
    compact_summary("cs1", "sys1"),         # not a message, but is in the chain
    msg("u3", "cs1", "user", "after one"),
    msg("u4", "u3", "assistant", "after two"),
])
eq("history survives a compaction", got,
   ["before one", "before two", "after one", "after two"])
eq("and is counted", total, 4)

# --- several compactions in one file --------------------------------------
got, total, _ = texts([
    msg("a1", None, "user", "era one"),
    plumbing("sysA", None),
    compact_summary("csA", "sysA"),
    msg("b1", "csA", "user", "era two"),
    plumbing("sysB", None),
    compact_summary("csB", "sysB"),
    msg("c1", "csB", "user", "era three"),
])
eq("three eras, one transcript", got, ["era one", "era two", "era three"])

# --- compaction AND a rewind before it ------------------------------------
got, _, _ = texts([
    msg("u1", None, "user", "one"),
    msg("u2", "u1", "assistant", "abandoned"),
    msg("u3", "u1", "assistant", "kept"),
    plumbing("sys1", None),
    compact_summary("cs1", "sys1"),
    msg("u4", "cs1", "user", "after"),
])
eq("crossing a compaction still respects rewind", got, ["one", "kept", "after"])

# --- the windowing a client actually pages through ------------------------
records = [msg("u1", None, "user", "oldest")]
for i in range(2, 60):
    records.append(msg("u%d" % i, "u%d" % (i - 1),
                       "assistant" if i % 2 else "user", "m%d" % i))
d = tempfile.mkdtemp()
proj = os.path.join(d, "-proj")
os.makedirs(proj)
with open(os.path.join(proj, "s1.jsonl"), "w", encoding="utf-8") as f:
    for r in records:
        f.write(json.dumps(r) + "\n")
import pathlib  # noqa: E402
store = ClaudeStore(pathlib.Path(d))
page = store.get_messages("s1", limit=10)
eq("default window is the newest page", page["offset"], 49)
check("an earlier page exists to load", page["offset"] > 0, page["offset"])
first = store.get_messages("s1", offset=0, limit=10)
eq("and it starts at the oldest message", first["messages"][0]["text"], "oldest")
shutil.rmtree(d, ignore_errors=True)

# --- a transcript with no uuids at all falls back to the file -------------
got, _, _ = texts([
    {"type": "user", "message": {"role": "user",
                                 "content": [{"type": "text", "text": "a"}]}},
    {"type": "assistant", "message": {"role": "assistant",
                                      "content": [{"type": "text", "text": "b"}]}},
])
eq("no uuids: trust the file", got, ["a", "b"])

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("all ok")
