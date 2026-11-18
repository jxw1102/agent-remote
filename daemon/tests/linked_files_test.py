"""GET /api/sessions/<id>/file: a file the agent linked, and nothing else.

Codex answers with links like [findings.txt](/abs/path/findings.txt:12).
Clients now open them through the daemon. The rule this file protects: the
endpoint is a link follower, not a file browser. It serves a path only when
the session's assistant text links it AND it resolves inside the session's
working folder AND it crosses no credential folder. Every refusal is the same
404, so the endpoint cannot probe what exists on the host.

Run:  python3 tests/linked_files_test.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = os.path.realpath(tempfile.mkdtemp(prefix="agentremoted-files-"))
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted import linked_files                          # noqa: E402
from agentremoted.config import Config, load_or_create_token  # noqa: E402
from agentremoted.providers import ProviderBundle             # noqa: E402
from agentremoted.server import make_server                   # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


PROJECT = os.path.join(FAKE_HOME, "work", "proj")
OUTSIDE = os.path.join(FAKE_HOME, "work", "other")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


FINDINGS = os.path.join(PROJECT, "audit", "findings.txt")
UNLINKED = os.path.join(PROJECT, "audit", "private.txt")
SECRET = os.path.join(OUTSIDE, "secret.txt")
ESCAPE = os.path.join(PROJECT, "escape.txt")            # symlink -> SECRET
SSH_KEY = os.path.join(PROJECT, ".ssh", "config")
PAGE = os.path.join(PROJECT, "report.html")
PIC = os.path.join(PROJECT, "shot.png")
SPACED = os.path.join(PROJECT, "my notes", "a b.md")
BIG = os.path.join(PROJECT, "big.log")
PUBLIC_PIC = os.path.join(FAKE_HOME, "Public", "arch.png")
PUBLIC_UNLINKED = os.path.join(FAKE_HOME, "Public", "other.bar")
PUBLIC_SSH = os.path.join(FAKE_HOME, "Public", ".ssh", "id")


def fixtures():
    write(FINDINGS, b"DES-EDE3 registration audit\n")
    write(UNLINKED, b"not linked anywhere\n")
    write(SECRET, b"outside the project\n")
    os.symlink(SECRET, ESCAPE)
    write(SSH_KEY, b"Host *\n")
    write(PAGE, b"<script>alert(1)</script>")
    write(PIC, b"\x89PNG\r\n\x1a\n\x00\x00")
    write(SPACED, b"# notes\n")
    write(BIG, b"x" * (linked_files.MAX_BYTES + 1))
    write(PUBLIC_PIC, b"\x89PNG\r\n\x1a\n\x00\x00")
    write(PUBLIC_UNLINKED, b"bar")
    write(PUBLIC_SSH, b"key")


class Store:
    def __init__(self, sessions):
        self.sessions = sessions

    def get_session(self, sid):
        s = self.sessions.get(sid)
        return dict(s["meta"]) if s else None

    def get_messages(self, sid, offset=None, limit=50, steps=False):
        msgs = self.sessions[sid]["messages"]
        off = offset or 0
        return {"session_id": sid, "total": len(msgs), "offset": off,
                "messages": msgs[off:off + limit]}


class Runner:
    def __init__(self, name):
        self.name = name

    def capabilities(self):
        return {}

    def slash_commands(self):
        return []

    def check_available(self):
        return True, ""


def assistant(text):
    return {"role": "assistant", "text": text}


def main():
    fixtures()
    linked = "\n".join([
        "The audit is saved [here](%s)." % FINDINGS,
        "Line 12 of [findings](%s:12) is the bug." % FINDINGS,
        "It escapes via [escape](%s) and [outside](%s)." % (ESCAPE, SECRET),
        "Keys at [ssh](%s), page [report](%s), shot [png](%s)." % (
            SSH_KEY, PAGE, PIC),
        "Notes in [notes](%s) and [log](%s)." % (
            urllib.parse.quote(SPACED), BIG),
        "Diagram in [Public](%s), key [k](%s)." % (PUBLIC_PIC, PUBLIC_SSH),
    ])
    # Many filler messages so the link sits past the first 500-row page.
    msgs = [assistant("step %d" % i) for i in range(620)] + [assistant(linked)]
    msgs.insert(3, {"role": "user", "text": "show me %s" % UNLINKED})
    store = Store({
        "s1": {"meta": {"id": "s1", "cwd": PROJECT}, "messages": msgs},
        "root": {"meta": {"id": "root", "cwd": "/"},
                 "messages": [assistant("[x](%s)" % FINDINGS)]},
    })
    bundles = {
        "codex": ProviderBundle("codex", store, Runner("codex"), None),
        "claude": ProviderBundle("claude", Store({}), Runner("claude"), None),
    }
    token = load_or_create_token()
    config = Config({"provider": "codex", "bind": "127.0.0.1", "port": 0,
                     "projects_dir": os.path.join(FAKE_HOME, "projects")})
    server = make_server(config, token, bundles)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]

    def fetch(path, sid="s1", auth=True, prefix=""):
        url = "%s%s/api/sessions/%s/file?path=%s" % (
            base, prefix, sid, urllib.parse.quote(path, safe=""))
        headers = {"X-Auth-Token": token} if auth else {}
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=headers),
                    timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    try:
        print("linked files are served:")
        st, hd, body = fetch(FINDINGS)
        check("linked file is served", st == 200 and body.startswith(b"DES"),
              (st, body[:60]))
        check("as plain text", hd.get("Content-Type", "").startswith("text/plain"),
              hd.get("Content-Type"))
        check("never sniffed", hd.get("X-Content-Type-Options") == "nosniff")
        check("CORS allows the web client", "Access-Control-Allow-Origin" in hd)
        st, _h, _b = fetch(FINDINGS + ":12")
        check("a :line suffix still opens the file", st == 200, st)
        st, _h, _b = fetch(FINDINGS + "#L12")
        check("a #L line anchor still opens the file", st == 200, st)
        st, _h, _b = fetch(FINDINGS, prefix="/codex")
        check("the per-harness path works too", st == 200, st)
        st, _h, body = fetch(SPACED)
        check("a percent-encoded link opens", st == 200 and body == b"# notes\n",
              (st, body[:40]))
        st, hd, _b = fetch(PIC)
        check("an image keeps its type", st == 200
              and hd.get("Content-Type") == "image/png", hd.get("Content-Type"))

        st, hd, _b = fetch(PUBLIC_PIC)
        check("a linked file in ~/Public is served", st == 200
              and hd.get("Content-Type") == "image/png", st)

        print("everything else is refused:")
        st, _h, _b = fetch(FINDINGS, auth=False)
        check("no token, no file", st == 401, st)
        refusals = [
            ("a file the agent never linked", UNLINKED),
            ("a linked file outside the working folder", SECRET),
            ("a linked symlink that leaves the working folder", ESCAPE),
            ("a linked file under .ssh", SSH_KEY),
            ("a path the transcript does not contain", "/etc/passwd"),
            ("a traversal spelling", PROJECT + "/audit/../../other/secret.txt"),
            ("a relative path", "audit/findings.txt"),
            ("the working folder itself", PROJECT),
            ("an unlinked file in ~/Public", PUBLIC_UNLINKED),
            ("a linked file under ~/Public/.ssh", PUBLIC_SSH),
            ("~/Public itself", os.path.join(FAKE_HOME, "Public")),
        ]
        bodies = set()
        for label, path in refusals:
            st, _h, body = fetch(path)
            check(label + " is 404", st == 404, st)
            bodies.add(body)
        check("every refusal reads the same", len(bodies) == 1, bodies)
        st, _h, _b = fetch(FINDINGS, sid="root")
        check("a session rooted at / serves nothing", st == 404, st)
        st, _h, _b = fetch(FINDINGS, sid="nope")
        check("an unknown session is 404", st == 404, st)
        st, _h, _b = fetch(BIG)
        check("a file over the cap is 413", st == 413, st)

        print("nothing runs in the client's origin:")
        st, hd, _b = fetch(PAGE)
        check("an .html link is served as text, not a page",
              st == 200 and hd.get("Content-Type", "").startswith("text/plain"),
              (st, hd.get("Content-Type")))
        check("and sandboxed", "sandbox" in hd.get("Content-Security-Policy", ""))

        print("ping advertises it:")
        with urllib.request.urlopen(base + "/api/ping", timeout=10) as resp:
            ping = json.loads(resp.read().decode())
        check("ping says linked_files", ping.get("linked_files") is True, ping)
    finally:
        server.shutdown()

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1
    print("all linked-file checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
