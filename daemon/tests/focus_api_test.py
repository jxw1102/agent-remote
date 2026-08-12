"""Focus list over HTTP: enrolment rules, state tags, rename, done/restore.

The rule this file exists to protect: a card lands on the list only when the
human drives a turn *through the daemon*. Agent-initiated traffic arrives on
/internal/* — before the auth gate — and must never enrol anything, or the
list fills up with subagent and hook noise and stops being a to-do list.

Run:  python3 tests/focus_api_test.py
"""

import json
import os
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-focusapi-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config, load_or_create_token  # noqa: E402
from agentremoted.jobs import JobManager                      # noqa: E402
from agentremoted.server import make_server                   # noqa: E402
from agentremoted import providers                            # noqa: E402

SESSION_ID = "11111111-2222-3333-4444-555555555555"
OTHER_ID = "22222222-3333-4444-5555-666666666666"
PROJECT_DIR_NAME = "-home-me-myapp"
PROJECT_CWD = os.path.join(FAKE_HOME, "myapp")
NEW_SID = "99999999-8888-7777-6666-555555555555"
MIGRATED_SID = "77777777-6666-5555-4444-333333333333"

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def transcript(sid, text):
    return [
        {"type": "user", "uuid": sid + "-1",
         "timestamp": "2026-07-19T10:00:00Z",
         "sessionId": sid, "cwd": PROJECT_CWD, "gitBranch": "main",
         "message": {"role": "user", "content": text}},
        {"type": "assistant", "uuid": sid + "-2",
         "timestamp": "2026-07-19T10:00:05Z", "sessionId": sid,
         "message": {"role": "assistant", "model": "claude-opus-4-8",
                     "content": [{"type": "text", "text": "Done."}]}},
    ]


FAKE_CLAUDE_TMPL = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
resumed = args[args.index("--resume") + 1] if "--resume" in args else ""
PROJECT_DIR = %(project_dir)r
NEW_SID = %(new_sid)r
MIGRATED_SID = %(migrated_sid)r

if "MIGRATE" in prompt:
    sid = MIGRATED_SID    # a provider that mints a fresh id on resume (grok)
elif resumed:
    sid = resumed         # claude resumes into the same session id
else:
    sid = NEW_SID

print(json.dumps({"type": "system", "subtype": "init",
                  "session_id": sid, "model": "fake-model"}), flush=True)
if "SLEEP" in prompt:
    time.sleep(30)
    sys.exit(0)
if "FAILTURN" in prompt:
    sys.stderr.write("boom\n")
    sys.exit(1)

# Real CLIs append the turn to the transcript, and the daemon reads both the
# session row and last_active from there — so the fake has to write it, with a
# real clock, or every finished turn looks older than the read cursor.
ts = time.strftime("%%Y-%%m-%%dT%%H:%%M:%%S.000Z", time.gmtime())
with open(os.path.join(PROJECT_DIR, sid + ".jsonl"), "a") as f:
    f.write(json.dumps({"type": "user", "uuid": sid + "-p" + ts,
                        "timestamp": ts, "sessionId": sid, "cwd": os.getcwd(),
                        "gitBranch": "main",
                        "message": {"role": "user", "content": prompt}}) + "\n")
    f.write(json.dumps({"type": "assistant", "uuid": sid + "-a" + ts,
                        "timestamp": ts, "sessionId": sid,
                        "message": {"role": "assistant",
                                    "model": "claude-opus-4-8",
                                    "content": [{"type": "text",
                                                 "text": "echo: " + prompt}]}})
            + "\n")

print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "echo: " + prompt}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success",
                  "session_id": sid, "is_error": False}), flush=True)
'''


def api(base, token, path, body=None):
    req = urllib.request.Request(base + path, headers={"X-Auth-Token": token})
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode())


def wait_job(base, token, job_id, timeout=15):
    deadline = time.time() + timeout
    snap = {}
    while time.time() < deadline:
        _, snap = api(base, token, "/api/jobs/" + job_id)
        if snap.get("status") in ("done", "error", "stopped"):
            return snap
        time.sleep(0.1)
    return snap


def row_for(rows, sid):
    for row in rows:
        if row.get("id") == sid:
            return row
    return {}


def main():
    projects = os.path.join(FAKE_HOME, ".claude", "projects", PROJECT_DIR_NAME)
    os.makedirs(projects)
    os.makedirs(PROJECT_CWD)
    for sid, text in ((SESSION_ID, "fix the login crash"),
                      (OTHER_ID, "rewrite the parser")):
        path = os.path.join(projects, sid + ".jsonl")
        with open(path, "w") as f:
            for line in transcript(sid, text):
                f.write(json.dumps(line) + "\n")
        old = time.time() - 3600
        os.utime(path, (old, old))

    fake_bin = os.path.join(FAKE_HOME, "claude")
    with open(fake_bin, "w") as f:
        f.write(FAKE_CLAUDE_TMPL % {
            "project_dir": projects,
            "new_sid": NEW_SID,
            "migrated_sid": MIGRATED_SID,
        })
    os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IEXEC)

    token = load_or_create_token()
    config = Config({
        "provider": "claude",
        "bind": "127.0.0.1", "port": 0,
        "projects_dir": os.path.join(FAKE_HOME, ".claude", "projects"),
        "claude_bin": fake_bin,
        "permission_timeout": 5,
        "drop_dir": os.path.join(os.environ["AGENTREMOTED_HOME"], "drop"),
    })
    bundles = providers.build_all(config, JobManager)
    server = make_server(config, token, bundles)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % port

    print("capability:")
    with urllib.request.urlopen(base + "/api/ping", timeout=5) as resp:
        ping = json.loads(resp.read().decode())
    check("ping advertises focus", ping.get("focus") is True, ping)
    check("ping lists focus states",
          "needs_answer" in (ping.get("focus_states") or []), ping)

    print("empty focus list:")
    _, data = api(base, token, "/api/focus")
    check("focus list starts empty", data.get("total") == 0, data)
    check("focus reports counts", isinstance(data.get("counts"), dict), data)
    _, data = api(base, token, "/api/sessions")
    rows = data.get("sessions") or []
    check("sessions still listed", len(rows) >= 2, len(rows))
    check("existing session is not a member",
          row_for(rows, SESSION_ID).get("focus") is False,
          row_for(rows, SESSION_ID))
    check("non-member carries no state tag",
          "focus_state" not in row_for(rows, SESSION_ID))

    print("enrolment by continuing a session:")
    _, res = api(base, token, "/api/sessions/%s/continue" % SESSION_ID,
                 {"prompt": "keep going"})
    job_id = res["job_id"]
    wait_job(base, token, job_id)
    _, data = api(base, token, "/api/focus")
    check("continued session is on the list", data.get("total") == 1, data)
    card = row_for(data.get("sessions") or [], SESSION_ID)
    check("card is the right session", card.get("id") == SESSION_ID, data)
    check("card is flagged focus", card.get("focus") is True, card)
    check("untouched session stayed off",
          not row_for(data.get("sessions") or [], OTHER_ID), data)

    print("state tag:")
    check("finished turn tagged turn_finished",
          card.get("focus_state") == "turn_finished", card)
    check("counts follow the tag",
          (data.get("counts") or {}).get("turn_finished") == 1, data)

    _, res = api(base, token, "/api/sessions/%s/continue" % SESSION_ID,
                 {"prompt": "SLEEP please"})
    sleep_job = res["job_id"]
    time.sleep(0.6)
    _, data = api(base, token, "/api/focus")
    live = [r for r in (data.get("sessions") or [])
            if r.get("focus_state") == "working"]
    check("in-flight turn tagged working", len(live) == 1, data)
    api(base, token, "/api/jobs/%s/stop" % sleep_job, {})
    wait_job(base, token, sleep_job)

    print("unread styling flag:")
    # Must be a turn that actually produced output: enrolling stamps the cursor,
    # so a session whose last event is your own prompt is legitimately "read".
    _, res = api(base, token, "/api/sessions/%s/continue" % SESSION_ID,
                 {"prompt": "one more please"})
    wait_job(base, token, res["job_id"])
    _, data = api(base, token, "/api/focus")
    card = row_for(data.get("sessions") or [], SESSION_ID)
    check("a finished turn starts unread", card.get("focus_unread") is True, card)
    api(base, token, "/api/focus/%s/seen" % SESSION_ID, {})
    _, data = api(base, token, "/api/focus")
    card = row_for(data.get("sessions") or [], SESSION_ID)
    check("opening it clears unread", card.get("focus_unread") is False, card)
    check("but the state is unchanged",
          card.get("focus_state") == "turn_finished", card)

    print("a running turn does not rename the session:")
    _, one = api(base, token, "/api/sessions/%s" % SESSION_ID)
    idle_title = one.get("title") or ""
    # SLEEP keeps the turn genuinely in flight; a fast fake turn would finish
    # before the assertion and test nothing.
    _, res = api(base, token, "/api/sessions/%s/continue" % SESSION_ID,
                 {"prompt": "continue SLEEP"})
    hold = res["job_id"]
    time.sleep(0.8)
    _, data = api(base, token, "/api/sessions")
    row = row_for(data.get("sessions") or [], SESSION_ID)
    check("title survives an in-flight turn",
          row.get("title") == idle_title, row)
    check("the prompt is not the title",
          row.get("title") != "continue SLEEP", row)
    check("but the row is marked running", row.get("running") is True, row)
    api(base, token, "/api/jobs/%s/stop" % hold, {})
    wait_job(base, token, hold)

    print("rename:")
    _, res = api(base, token, "/api/sessions/%s/title" % SESSION_ID,
                 {"title": "  BB10 pager chime  "})
    check("rename collapses whitespace",
          res.get("title") == "BB10 pager chime", res)
    _, data = api(base, token, "/api/sessions")
    row = row_for(data.get("sessions") or [], SESSION_ID)
    check("session list shows the rename",
          row.get("title") == "BB10 pager chime", row)
    check("rename marked manual", row.get("title_manual") is True, row)
    _, one = api(base, token, "/api/sessions/%s" % SESSION_ID)
    check("single session shows the rename",
          one.get("title") == "BB10 pager chime", one)
    _, data = api(base, token, "/api/sessions/search?q=pager")
    check("search shows the rename",
          row_for(data.get("results") or [], SESSION_ID).get("title")
          == "BB10 pager chime" or not data.get("results"), data)

    _, res = api(base, token, "/api/sessions/%s/title" % SESSION_ID,
                 {"title": ""})
    check("empty title clears the override", res.get("title") == "", res)
    _, data = api(base, token, "/api/sessions")
    row = row_for(data.get("sessions") or [], SESSION_ID)
    check("provider title is back",
          row.get("title") != "BB10 pager chime", row)
    try:
        api(base, token, "/api/sessions/%s/title" % SESSION_ID, {"nope": 1})
        check("rename requires a title field", False)
    except urllib.error.HTTPError as e:
        check("rename requires a title field", e.code == 400)

    print("done and restore:")
    _, res = api(base, token, "/api/focus/%s/done" % SESSION_ID, {})
    check("done reports the change", res.get("changed") is True, res)
    check("done clears membership", res.get("focus") is False, res)
    _, data = api(base, token, "/api/focus")
    check("list is empty again", data.get("total") == 0, data)
    _, data = api(base, token, "/api/sessions")
    check("session survives being marked done",
          row_for(data.get("sessions") or [], SESSION_ID).get("id") == SESSION_ID,
          data)
    check("done session flagged off-list",
          row_for(data.get("sessions") or [], SESSION_ID).get("focus") is False)
    _, res = api(base, token, "/api/focus/%s/done" % SESSION_ID, {})
    check("done twice is idempotent", res.get("changed") is False, res)
    _, res = api(base, token, "/api/focus/%s/restore" % SESSION_ID, {})
    check("restore puts it back", res.get("focus") is True, res)
    _, data = api(base, token, "/api/focus")
    check("restored card is on the list", data.get("total") == 1, data)

    print("a new session enrols and rekeys:")
    api(base, token, "/api/focus/%s/done" % SESSION_ID, {})
    _, res = api(base, token, "/api/sessions/new",
                 {"cwd": PROJECT_CWD, "prompt": "start something new"})
    new_job = res["job_id"]
    _, data = api(base, token, "/api/focus")
    check("new session is enrolled before its id exists",
          data.get("total") == 1, data)
    wait_job(base, token, new_job)
    # The listing path is where both ids are known, so it migrates the card
    # off its job:<id> placeholder.
    api(base, token, "/api/sessions")
    _, data = api(base, token, "/api/focus")
    cards = data.get("sessions") or []
    check("still exactly one card after rekey", len(cards) == 1, data)
    check("card now keyed by the real session id",
          cards and cards[0].get("id") == NEW_SID, cards)
    check("no job: placeholder left behind",
          not any(str(c.get("id", "")).startswith("job:") for c in cards), cards)

    print("a failed turn is visible:")
    _, res = api(base, token, "/api/sessions/%s/continue" % SESSION_ID,
                 {"prompt": "FAILTURN please"})
    snap = wait_job(base, token, res["job_id"])
    check("job really errored", snap.get("status") == "error", snap)
    _, data = api(base, token, "/api/focus")
    card = row_for(data.get("sessions") or [], SESSION_ID)
    check("failed turn tagged failed",
          card.get("focus_state") == "failed", card)
    check("failed counts separately",
          (data.get("counts") or {}).get("failed") == 1, data)
    # A good turn afterwards must clear it: only the LATEST job decides.
    _, res = api(base, token, "/api/sessions/%s/continue" % SESSION_ID,
                 {"prompt": "all better now"})
    wait_job(base, token, res["job_id"])
    _, data = api(base, token, "/api/focus")
    card = row_for(data.get("sessions") or [], SESSION_ID)
    check("a later good turn clears failed",
          card.get("focus_state") == "turn_finished", card)

    print("a card follows a resumed session onto its new id:")
    api(base, token, "/api/focus/%s/done" % NEW_SID, {})
    _, res = api(base, token, "/api/sessions/%s/continue" % SESSION_ID,
                 {"prompt": "MIGRATE this one"})
    wait_job(base, token, res["job_id"])
    _, data = api(base, token, "/api/focus")
    cards = data.get("sessions") or []
    check("exactly one card after the id change", len(cards) == 1, data)
    check("card moved to the new session id",
          cards and cards[0].get("id") == MIGRATED_SID, cards)
    check("card is not stranded on the old id",
          not row_for(cards, SESSION_ID), cards)
    api(base, token, "/api/focus/%s/done" % MIGRATED_SID, {})

    print("agent traffic never enrols:")
    api(base, token, "/api/focus/%s/done" % NEW_SID, {})
    _, before = api(base, token, "/api/focus")
    # /internal/hook sits before the auth gate; a bad secret is the normal
    # outcome here. What matters is that it cannot put a card on the list.
    try:
        req = urllib.request.Request(
            base + "/internal/hook?secret=wrong&tui=claude",
            data=json.dumps({"hook_event_name": "Stop",
                             "session_id": OTHER_ID}).encode(),
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError:
        pass
    _, after = api(base, token, "/api/focus")
    check("hook post did not enrol",
          after.get("total") == before.get("total"), after)
    check("hooked session is still off-list",
          not row_for(after.get("sessions") or [], OTHER_ID), after)

    print("auth:")
    try:
        urllib.request.urlopen(base + "/api/focus", timeout=5)
        check("focus needs a token", False)
    except urllib.error.HTTPError as e:
        check("focus needs a token", e.code == 401)
    try:
        req = urllib.request.Request(
            base + "/api/focus/%s/done" % SESSION_ID,
            data=b"{}", headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        check("focus writes need a token", False)
    except urllib.error.HTTPError as e:
        check("focus writes need a token", e.code == 401)

    server.shutdown()
    print()
    if failures:
        print("FAILED: %d" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
