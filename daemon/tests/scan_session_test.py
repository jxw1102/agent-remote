"""_scan_session reads what was appended, not the whole transcript again."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.providers import claude as C  # noqa: E402

failed = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ((" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failed.append(name)


def user(text, **extra):
    rec = {"type": "user", "message": {"role": "user", "content": text}}
    rec.update(extra)
    return json.dumps(rec) + "\n"


def assistant(text):
    return json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [{"type": "text", "text": text}]}}) + "\n"


class Store(C.ClaudeStore):
    """Only what _scan_session touches; counts the lines it parses."""

    def __init__(self):
        self._scan_memo = {}
        self._scan_locks = {}
        self._scan_guard = threading.Lock()
        self.parsed = 0

    def _rec(self, obj):
        self.parsed += 1
        return obj


with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "11111111-1111-1111-1111-111111111111.jsonl"
    path.write_text(user("first question") + assistant("a" * 2000) * 50
                    + user("summary one", isCompactSummary=True))
    s = Store()
    check("first scan", s._scan_session(path) == (1, "first question", "summary one"),
          s._scan_session(path))
    check("assistant lines are skipped unparsed", s.parsed == 2, s.parsed)

    s.parsed = 0
    s._scan_session(path)
    check("unchanged file is not read again", s.parsed == 0, s.parsed)

    with open(path, "a") as f:
        f.write(assistant("more") + user("second question")
                + user("summary two", isCompactSummary=True))
    s.parsed = 0
    check("appended records are added to what was known",
          s._scan_session(path) == (2, "first question", "summary two"),
          s._scan_session(path))
    check("only the appended records were parsed", s.parsed == 2, s.parsed)

    # A record still being written: not counted until its newline lands.
    half = user("summary three", isCompactSummary=True)
    with open(path, "a") as f:
        f.write(half[:30])
    check("a half-written record waits", s._scan_session(path)[0] == 2)
    with open(path, "a") as f:
        f.write(half[30:])
    check("and is read once whole",
          s._scan_session(path) == (3, "first question", "summary three"),
          s._scan_session(path))

    # Cut short (a rewind): start over rather than trust the old offset.
    path.write_text(user("rewritten"))
    check("a shorter file is scanned from the top",
          s._scan_session(path) == (0, "rewritten", ""), s._scan_session(path))

    # A rewind cuts the file in place (same inode) and the next turn grows it
    # past where the last scan stopped: nothing from before may be kept.
    path.write_text(user("kept question") + user("old summary", isCompactSummary=True)
                    + assistant("z" * 3000))
    check("before the rewind", s._scan_session(path) == (1, "kept question", "old summary"))
    keep = len(user("kept question"))
    with open(path, "r+") as f:
        f.truncate(keep)
        f.seek(keep)
        f.write(assistant("w" * 6000) + user("after the rewind"))
    check("cut in place and regrown: scanned from the top",
          s._scan_session(path) == (0, "kept question", ""), s._scan_session(path))

    # Replaced by a longer file of the same name: a new inode, from the top.
    other = Path(tmp) / "swap.jsonl"
    other.write_text(user("replaced") + assistant("x" * 500))
    os.replace(other, path)
    check("a replaced file is scanned from the top",
          s._scan_session(path) == (0, "replaced", ""), s._scan_session(path))

    # Many callers at once share one read.
    big = Path(tmp) / "22222222-2222-2222-2222-222222222222.jsonl"
    big.write_text((user("q") + assistant("y" * 200)) * 2000)
    s2 = Store()
    out = []
    ts = [threading.Thread(target=lambda: out.append(s2._scan_session(big)))
          for _ in range(16)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("concurrent callers agree", len(set(out)) == 1 and out[0][1] == "q", out[:2])
    check("and the file was parsed once", s2.parsed == 2000, s2.parsed)

    # A store whose _rec folds other shapes parses every line.
    class Folding(Store):
        _scan_marker = b""
    s3 = Folding()
    s3._scan_session(big)
    check("no marker: every line is parsed", s3.parsed == 4000, s3.parsed)

print()
print("ALL PASS" if not failed else "FAILED: %s" % failed)
sys.exit(1 if failed else 0)
