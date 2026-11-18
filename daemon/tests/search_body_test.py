"""Search reads the whole transcript, not a head + tail window.

The reported miss: a 99 MB Claude session about the Momenta "madapter" could
not be found by that word, because every mention sat between 6 MB and 94 MB
and only the first 384 KB and last 192 KB were read.

Run:  python3 tests/search_body_test.py
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted import search_util as su  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


d = tempfile.mkdtemp(prefix="agentremoted-search-")
p = os.path.join(d, "t.jsonl")

print("candidate lines")
su._SCAN_CHUNK = 16          # force hits across chunk boundaries
with open(p, "wb") as f:
    f.write(b"aaaa\n" * 10 + b"xx MadApter yy\n"
            + "b中文c\n".encode("utf-8") + b"q" * 40 + b"madapter")
lines = list(su.iter_matching_lines(p, "madapter"))
check("any case, across chunks, last line without newline",
      lines == ["xx MadApter yy", "q" * 40 + "madapter"], lines)
check("CJK matches", list(su.iter_matching_lines(p, "中文")) == ["b中文c"])
check("a miss yields nothing", list(su.iter_matching_lines(p, "nothere")) == [])
open(p, "wb").close()
check("an empty file is fine", list(su.iter_matching_lines(p, "x")) == [])
su._SCAN_CHUNK = 8 << 20

print("the middle of a big Claude transcript")
os.environ["HOME"] = d
from agentremoted.providers.claude import ClaudeStore  # noqa: E402

proj = os.path.join(d, "projects", "-tmp-proj")
os.makedirs(proj)
sid = "98ce7bd7-02de-4145-b4f8-1229d9647085"


def user(text, n):
    return json.dumps({"type": "user", "uuid": "u%d" % n, "sessionId": sid,
                       "cwd": "/tmp/proj", "timestamp": "2026-11-10T07:29:09Z",
                       "message": {"role": "user", "content": text}})


filler = json.dumps({"type": "user", "uuid": "f", "sessionId": sid,
                     "toolUseResult": "x" * 2000,
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "content": "y" * 2000}]}})
with open(os.path.join(proj, sid + ".jsonl"), "w") as f:
    f.write(user("start the work", 0) + "\n")
    for _ in range(600):                       # ~2.4 MB of tool output
        f.write(filler + "\n")
    f.write(user("plug the PR's work into madapter", 1) + "\n")
    for _ in range(600):
        f.write(filler + "\n")
    f.write(user("the end", 2) + "\n")
from pathlib import Path  # noqa: E402
store = ClaudeStore(Path(d) / "projects")
hits = store.search_sessions("MADAPTER", None, 10, user_only=False)
check("a word only in the middle is found", len(hits) == 1, hits)
if hits:
    check("with the message as snippet", "madapter" in hits[0].get("snippet", ""),
          hits[0].get("snippet"))

print("\n%s" % ("all ok" if not failures else "FAILED: %s" % ", ".join(failures)))
sys.exit(1 if failures else 0)
