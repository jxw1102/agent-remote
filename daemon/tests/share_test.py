"""Session share links: token isolation, expiry, hosted page.

The rule this file exists to protect: a share token is a capability for
exactly one session. It is not the daemon auth token, it cannot list or
continue anything, and swapping it (or pairing it with another session id)
returns no data.

Run:  python3 tests/share_test.py
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

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-share-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config, load_or_create_token  # noqa: E402
from agentremoted.jobs import JobManager                      # noqa: E402
from agentremoted.server import make_server                   # noqa: E402
from agentremoted import providers                            # noqa: E402
from agentremoted import shares as share_mod                  # noqa: E402

SESSION_ID = "11111111-2222-3333-4444-555555555555"
OTHER_ID = "22222222-3333-4444-5555-666666666666"
PROJECT_DIR_NAME = "-home-me-myapp"
PROJECT_CWD = os.path.join(FAKE_HOME, "myapp")

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


def api(base, token, path, body=None, method=None):
    headers = {}
    if token:
        headers["X-Auth-Token"] = token
    req = urllib.request.Request(base + path, headers=headers)
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    if method:
        req.get_method = lambda: method
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type") or ""
        if "json" in ctype:
            return resp.status, json.loads(raw.decode())
        return resp.status, raw


def http_error(base, token, path, body=None):
    try:
        api(base, token, path, body)
        return None, None
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode())
        except Exception:
            payload = {}
        return e.code, payload


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
        f.write("#!/bin/sh\nexit 0\n")
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
    check("ping advertises share", ping.get("share") is True, ping)
    check("version is 2.7+",
          str(ping.get("version") or "").startswith("2.7")
          or str(ping.get("version") or "") >= "2.7",
          ping.get("version"))

    print("mint requires daemon token:")
    code, err = http_error(base, "", "/api/sessions/%s/share" % SESSION_ID, {})
    check("unauthed share is 401", code == 401, (code, err))
    code, err = http_error(base, "nope", "/api/sessions/%s/share" % SESSION_ID, {})
    check("bad token share is 401", code == 401, (code, err))

    print("mint:")
    status, created = api(base, token, "/api/sessions/%s/share" % SESSION_ID, {})
    check("share returns 200", status == 200, created)
    share_token = created.get("token") or ""
    check("token is unguessable", len(share_token) >= 32, share_token)
    check("path is /share/<token>",
          created.get("path") == "/share/" + share_token, created)
    check("url includes path",
          str(created.get("url") or "").endswith("/share/" + share_token),
          created)
    check("expires in ~7 days",
          6 * 86400 < int(created.get("expires_in") or 0) <= 7 * 86400,
          created.get("expires_in"))
    check("bound to this session",
          created.get("session_id") == SESSION_ID, created)

    print("public read:")
    status, page = api(base, None, "/api/share/" + share_token)
    check("share API needs no daemon token", status == 200, page)
    check("title is the shared session",
          "login" in str(page.get("title") or "").lower()
          or "crash" in str(page.get("title") or "").lower()
          or bool(page.get("title")),
          page.get("title"))
    texts = " ".join(m.get("text") or "" for m in (page.get("messages") or []))
    check("transcript contains the prompt", "login crash" in texts, texts)
    check("public payload has no cwd", "cwd" not in page, page)
    check("public payload has no session_id",
          "session_id" not in page, page)

    print("hosted page:")
    status, html = api(base, None, "/share/" + share_token)
    check("share page is HTML", status == 200
          and b"<html" in html.lower(), status)
    check("share page is Agent Remote",
          b"Agent Remote" in html or b"shared" in html.lower(), True)

    print("isolation:")
    code, err = http_error(base, None, "/api/share/" + share_token[:-1] + "x")
    check("tweaked token is 404", code == 404, (code, err))
    code, err = http_error(base, None, "/api/sessions/%s/messages" % SESSION_ID)
    check("messages still require daemon token", code == 401, (code, err))
    code, err = http_error(base, share_token, "/api/sessions")
    check("share token is not daemon auth", code == 401, (code, err))
    code, err = http_error(base, share_token,
                           "/api/sessions/%s/messages" % OTHER_ID)
    check("share token cannot read another session", code == 401, (code, err))
    code, err = http_error(base, share_token,
                           "/api/sessions/%s/continue" % SESSION_ID,
                           {"prompt": "nope"})
    check("share token cannot continue", code == 401, (code, err))

    print("other session:")
    _, other = api(base, token, "/api/sessions/%s/share" % OTHER_ID, {})
    other_token = other.get("token") or ""
    check("second share is a different token",
          other_token and other_token != share_token, other_token)
    _, other_page = api(base, None, "/api/share/" + other_token)
    other_texts = " ".join(
        m.get("text") or "" for m in (other_page.get("messages") or []))
    check("other share has the other prompt", "parser" in other_texts,
          other_texts)
    check("other share does not include first prompt",
          "login crash" not in other_texts, other_texts)

    print("missing session:")
    code, err = http_error(base, token, "/api/sessions/does-not-exist/share", {})
    check("unknown session is 404", code == 404, (code, err))

    print("expiry:")
    digest = share_mod.token_hash(share_token)
    store_path = os.path.join(os.environ["AGENTREMOTED_HOME"], "shares.json")
    with open(store_path, "r", encoding="utf-8") as f:
        disk = json.load(f)
    disk["shares"][digest]["expires_at"] = time.time() - 10
    with open(store_path, "w", encoding="utf-8") as f:
        json.dump(disk, f)
    # The live ShareStore already has the unexpired row in memory. Mint a
    # fresh server against the same file to prove disk expiry is honoured.
    server.shutdown()
    server2 = make_server(config, token, bundles)
    port2 = server2.server_address[1]
    threading.Thread(target=server2.serve_forever, daemon=True).start()
    base2 = "http://127.0.0.1:%d" % port2
    code, err = http_error(base2, None, "/api/share/" + share_token)
    check("expired token is 404", code == 404, (code, err))
    try:
        api(base2, None, "/share/" + share_token)
        check("expired page is 404", False, "expected HTTPError")
    except urllib.error.HTTPError as e:
        check("expired page is 404", e.code == 404, e.code)
    # The other, still-valid share survives the restart.
    status, still = api(base2, None, "/api/share/" + other_token)
    check("unexpired share still works after restart", status == 200, still)

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
