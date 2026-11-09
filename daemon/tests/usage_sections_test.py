"""/api/usage on a multi-harness daemon: only harnesses with a quota appear.

The rule this file exists to protect: a harness that publishes no
subscription quota (DeepSeek, CodeBuddy) contributes nothing to the Usage
sheet. It used to contribute a section reading "not supported", which every
client faithfully drew as a row, so the sheet filled with harnesses that had
nothing to say. Absence is the signal; the clients already render an empty
sheet when no harness reports.

Run:  python3 tests/usage_sections_test.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-usage-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

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


class Quota:
    """A harness that does report a subscription quota (Claude, Codex, …)."""

    def __init__(self, name):
        self.name = name

    def usage(self):
        return {"ok": True, "account": "me@example.com",
                "account_id": "acct-1",
                "buckets": [{"title": "Weekly limit", "percent": 42,
                             "severity": "normal",
                             "resets_text": "resets Tuesday"}]}


class NoQuota:
    """A harness with nothing to report: no usage() at all."""

    def __init__(self, name):
        self.name = name


def bundles_for(*runners):
    return {r.name: ProviderBundle(r.name, None, r, None) for r in runners}


def serve(bundles, token, config):
    server = make_server(config, token, bundles)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def get(base, token, path):
    req = urllib.request.Request(base + path, headers={"X-Auth-Token": token})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def main():
    token = load_or_create_token()
    config = Config({"provider": "claude", "bind": "127.0.0.1", "port": 0,
                     "projects_dir": os.path.join(FAKE_HOME, "projects")})

    print("mixed harnesses:")
    server, base = serve(
        bundles_for(Quota("claude"), NoQuota("codebuddy"), NoQuota("deepseek")),
        token, config)
    try:
        data = get(base, token, "/api/usage")
    finally:
        server.shutdown()
    names = [s.get("provider") for s in (data.get("sections") or [])]
    check("the harness with a quota is there", names == ["claude"], names)
    check("codebuddy is left out", "codebuddy" not in names, names)
    check("deepseek is left out", "deepseek" not in names, names)
    check("no 'not supported' row survives",
          not any("not supported" in str(s.get("error") or "")
                  for s in (data.get("sections") or [])), data.get("sections"))
    check("the sheet is ok", data.get("ok") is True, data)
    titles = [b.get("title") for b in (data.get("buckets") or [])]
    check("flat buckets are only the quota harness's",
          titles == ["Claude · Weekly limit"], titles)

    print("no harness reports usage:")
    server, base = serve(
        bundles_for(NoQuota("codebuddy"), NoQuota("deepseek")), token, config)
    try:
        data = get(base, token, "/api/usage")
    finally:
        server.shutdown()
    check("no sections at all", data.get("sections") == [], data.get("sections"))
    check("no buckets at all", data.get("buckets") == [], data.get("buckets"))
    check("clients see the empty state, not a list of excuses",
          data.get("ok") is False and data.get("error") == "no usage data", data)

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1
    print("all usage-section checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
