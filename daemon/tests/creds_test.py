"""Checks for the Claude credential-store resolution (file + macOS Keychain).

On macOS the Claude CLI keeps its OAuth credentials in the login Keychain
(service "Claude Code-credentials") and writes no ~/.claude/.credentials.json
at all; on Linux/WSL it writes the file. The daemon must read either, prefer
whichever holds the fresher token, and write refreshed tokens back to the
store they came from.

Uses a fake `security` binary on PATH backed by a plain file, so it runs on
any platform and never touches a real Keychain.

Run:  python3 tests/creds_test.py
"""

import json
import os
import stat
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-creds-test-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
os.environ.pop("AGENTREMOTED_NO_KEYCHAIN", None)

# Fake `security` first on PATH: find-generic-password prints the store file,
# add-generic-password -U rewrites it with the last argument (the -w payload).
KEYCHAIN_STORE = os.path.join(FAKE_HOME, "keychain-store")
os.environ["KEYCHAIN_STORE"] = KEYCHAIN_STORE
FAKE_BIN = os.path.join(FAKE_HOME, "bin")
os.makedirs(FAKE_BIN)
_security = os.path.join(FAKE_BIN, "security")
with open(_security, "w") as f:
    f.write("""#!/bin/sh
if [ "$1" = "find-generic-password" ]; then
    [ -f "$KEYCHAIN_STORE" ] || exit 44
    cat "$KEYCHAIN_STORE"
    exit 0
fi
if [ "$1" = "add-generic-password" ]; then
    for last in "$@"; do :; done
    printf '%s' "$last" > "$KEYCHAIN_STORE"
    exit 0
fi
exit 1
""")
os.chmod(_security, os.stat(_security).st_mode | stat.S_IEXEC)
os.environ["PATH"] = FAKE_BIN + os.pathsep + os.environ["PATH"]

from agentremoted.providers import claude  # noqa: E402

CONFIG = types.SimpleNamespace(claude_env={})
NOW_MS = time.time() * 1000

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def creds_file():
    return os.path.join(FAKE_HOME, ".claude", ".credentials.json")


def write_file_store(blob):
    os.makedirs(os.path.dirname(creds_file()), exist_ok=True)
    with open(creds_file(), "w") as f:
        json.dump(blob, f)


def clear_stores():
    for p in (creds_file(), KEYCHAIN_STORE):
        try:
            os.unlink(p)
        except OSError:
            pass


def oauth_blob(token, expires_ms, refresh="refresh-1"):
    return {"claudeAiOauth": {"accessToken": token, "refreshToken": refresh,
                              "expiresAt": expires_ms}}


def main():
    # The platform gate is real code on macOS; force it on so every flow
    # below also runs on Linux CI (the fake `security` does the rest).
    orig_enabled = claude._keychain_enabled
    claude._keychain_enabled = lambda: True

    print("resolution:")
    clear_stores()
    data, store = claude._read_creds()
    check("no store anywhere", (data, store) == ({}, ""), (data, store))

    with open(KEYCHAIN_STORE, "w") as f:
        json.dump(oauth_blob("kc-token", NOW_MS + 3_600_000), f)
    data, store = claude._read_creds()
    check("keychain only -> keychain", store == "keychain", store)
    check("keychain blob parsed",
          data.get("claudeAiOauth", {}).get("accessToken") == "kc-token", data)

    clear_stores()
    write_file_store(oauth_blob("file-token", NOW_MS + 3_600_000))
    data, store = claude._read_creds()
    check("file only -> file", store == "file", store)

    with open(KEYCHAIN_STORE, "w") as f:
        json.dump(oauth_blob("kc-fresher", NOW_MS + 7_200_000), f)
    data, store = claude._read_creds()
    check("fresher keychain beats stale file", store == "keychain", store)
    write_file_store(oauth_blob("file-fresher", NOW_MS + 10_800_000))
    data, store = claude._read_creds()
    check("fresher file beats stale keychain", store == "file", store)

    print("token flows:")
    clear_stores()
    with open(KEYCHAIN_STORE, "w") as f:
        json.dump(oauth_blob("kc-fresh", NOW_MS + 3_600_000), f)
    check("fresh keychain token returned",
          claude._oauth_token(CONFIG) == "kc-fresh")

    # Expired keychain token: refresh (stubbed) and write back to the keychain.
    with open(KEYCHAIN_STORE, "w") as f:
        json.dump(oauth_blob("kc-expired", NOW_MS - 1000), f)
    orig_refresh = claude._refresh_oauth
    claude._refresh_oauth = lambda rt: {
        "accessToken": "kc-refreshed", "refreshToken": "refresh-2",
        "expiresAt": int(NOW_MS + 3_600_000)}
    try:
        tok = claude._oauth_token(CONFIG)
    finally:
        claude._refresh_oauth = orig_refresh
    check("expired keychain token refreshed", tok == "kc-refreshed", tok)
    with open(KEYCHAIN_STORE) as f:
        stored = json.load(f)
    check("rotated tokens written back to keychain",
          stored.get("claudeAiOauth", {}).get("accessToken") == "kc-refreshed"
          and stored["claudeAiOauth"].get("refreshToken") == "refresh-2",
          stored)
    check("file store not created by keychain write-back",
          not os.path.exists(creds_file()))

    print("placeholder token guard:")
    # A template placeholder in claude_env must not shadow a valid sign-in.
    clear_stores()
    with open(KEYCHAIN_STORE, "w") as f:
        json.dump(oauth_blob("kc-good", NOW_MS + 3_600_000), f)
    bad_cfg = types.SimpleNamespace(
        claude_env={"CLAUDE_CODE_OAUTH_TOKEN": "PASTE-TOKEN-HERE"})
    check("placeholder ignored, keychain used",
          claude._oauth_token(bad_cfg) == "kc-good")
    good_cfg = types.SimpleNamespace(
        claude_env={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-real"})
    check("real setup-token still wins",
          claude._oauth_token(good_cfg) == "sk-ant-oat01-real")
    check("reject helper drops garbage",
          claude._reject_bad_env_token("PASTE-TOKEN-HERE") == "")
    check("reject helper keeps real tokens",
          claude._reject_bad_env_token(" sk-ant-oat01-x ") == "sk-ant-oat01-x")

    print("mcp oauth from keychain:")
    blob = oauth_blob("kc-fresh", NOW_MS + 3_600_000)
    blob["mcpOAuth"] = {"claude.ai Foo|abc": {
        "serverName": "claude.ai Foo", "serverUrl": "https://mcp.example/sse",
        "accessToken": "mcp-token", "refreshToken": "mcp-refresh",
        "expiresAt": int(NOW_MS + 3_600_000)}}
    with open(KEYCHAIN_STORE, "w") as f:
        json.dump(blob, f)
    servers = claude._mcp_oauth_servers()
    srv = servers.get("claude_ai_Foo") or {}
    check("mcp server surfaced from keychain",
          srv.get("headers", {}).get("Authorization") == "Bearer mcp-token",
          servers)

    print("kill switch:")
    claude._keychain_enabled = orig_enabled
    os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"
    check("AGENTREMOTED_NO_KEYCHAIN disables keychain",
          not claude._keychain_enabled())
    del os.environ["AGENTREMOTED_NO_KEYCHAIN"]
    if sys.platform == "darwin":
        check("keychain enabled by default on macOS",
              claude._keychain_enabled())
    else:
        check("keychain never used off macOS", not claude._keychain_enabled())

    if failures:
        print("\n%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
