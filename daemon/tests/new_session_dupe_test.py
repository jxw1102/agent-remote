"""One prompt, one session: a repeated POST /api/sessions/new is not a second task.

The reported bug: one "Start" in the web client produced two codex sessions
(01a1e3ad-4265 and 01a1e3ad-468a, 1.03s apart) from one prompt. The modal
stayed on screen and its button stayed live while the first request was in
flight, so a second tap ran the whole action again. The daemon is the last
line of defence — it also covers a client retrying a request whose reply was
lost over the tunnel.

Run:  python3 tests/new_session_dupe_test.py
"""

import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-dupe-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.server import ApiHandler        # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Runner:
    name = "codex"


def handler():
    """An ApiHandler with the HTTP machinery left out."""
    h = ApiHandler.__new__(ApiHandler)
    h.principal = None
    h.runner = Runner()
    return h


def reset():
    ApiHandler._new_recent = {}


BODY = {"prompt": "Please continue the grok session",
        "provider": "codex"}
CWD = "/Users/me/Developer"

print("the reported bug: Start tapped twice")
reset()
h1 = handler()
check("the first POST is not a repeat", h1._new_session_dupe(BODY, CWD) == "")
h1._new_session_served("job-first")
h2 = handler()
check("the second POST reuses that job",
      h2._new_session_dupe(BODY, CWD) == "job-first")

print("\na different task is never a repeat")
check("another prompt starts its own job",
      handler()._new_session_dupe(dict(BODY, prompt="something else"), CWD) == "")
check("the same prompt in another folder too",
      handler()._new_session_dupe(BODY, "/Users/me/Other") == "")
check("and on another harness",
      handler()._new_session_dupe(dict(BODY, provider="grok"), CWD) == "")

print("\nthe same prompt again, later, is a real second run")
reset()
h = handler()
h._new_session_dupe(BODY, CWD)
h._new_session_served("job-old")
ApiHandler._new_recent[list(ApiHandler._new_recent)[0]] = (
    time.time() - ApiHandler._NEW_DEDUPE_S - 1, "job-old")
check("past the window it starts a new job",
      handler()._new_session_dupe(BODY, CWD) == "")

print("\na turn that never started does not block the retry")
reset()
h = handler()
h._new_session_dupe(BODY, CWD)
h._new_session_failed()
check("the failed claim is released",
      handler()._new_session_dupe(BODY, CWD) == "")

print("\ntwo taps at once (both in flight)")
reset()
ApiHandler._NEW_INFLIGHT_WAIT_S = 5.0
first = handler()
check("the first claims the key", first._new_session_dupe(BODY, CWD) == "")
got = []


def second():
    got.append(handler()._new_session_dupe(BODY, CWD))


t = threading.Thread(target=second, daemon=True)
t.start()
time.sleep(0.3)
check("the second waits instead of starting a job", not got, got)
first._new_session_served("job-race")
t.join(timeout=3)
check("and takes the first one's job once it exists", got == ["job-race"], got)

print("\nthe first request dies while the second waits")
reset()
ApiHandler._NEW_INFLIGHT_WAIT_S = 0.5
first = handler()
first._new_session_dupe(BODY, CWD)
started = time.time()
check("the waiter gives up and starts its own job",
      handler()._new_session_dupe(BODY, CWD) == "")
check("after the wait, not instantly", time.time() - started >= 0.4)

print("\n%s" % ("all ok" if not failures else "FAILED: %s" % ", ".join(failures)))
sys.exit(1 if failures else 0)
