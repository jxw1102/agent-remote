"""Inbox drop names: CJK filenames must download.

HTTP/1 headers are latin-1. A raw Chinese X-Drop-Name used to raise
UnicodeEncodeError in send_header and 500 the whole transfer.

Run:  python3 tests/drop_name_test.py
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

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-drop-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

DROP_DIR = os.path.join(os.environ["AGENTREMOTED_HOME"], "drop")
os.makedirs(DROP_DIR, exist_ok=True)
os.makedirs(os.environ["AGENTREMOTED_HOME"], exist_ok=True)
with open(os.path.join(os.environ["AGENTREMOTED_HOME"], "config.json"), "w") as f:
    json.dump({"drop_dir": DROP_DIR}, f)

from agentremoted.config import Config, load_or_create_token  # noqa: E402
from agentremoted.jobs import JobManager                      # noqa: E402
from agentremoted.server import (                             # noqa: E402
    _content_disposition, _header_filename, make_server)
from agentremoted import providers                            # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def test_helpers():
    print("helpers:")
    ascii_name = "hello drop.txt"
    check("ascii X-Drop-Name unchanged",
          _header_filename(ascii_name) == ascii_name)
    zh = "中文 测试.txt"
    encoded = _header_filename(zh)
    try:
        encoded.encode("latin-1")
        check("CJK X-Drop-Name is latin-1", True)
    except UnicodeEncodeError as e:
        check("CJK X-Drop-Name is latin-1", False, e)
    check("CJK X-Drop-Name round-trips",
          urllib.parse.unquote(encoded) == zh, encoded)
    cd = _content_disposition(zh)
    try:
        cd.encode("latin-1")
        check("CJK Content-Disposition is latin-1", True)
    except UnicodeEncodeError as e:
        check("CJK Content-Disposition is latin-1", False, e)
    check("CJK Content-Disposition has filename*",
          "filename*=UTF-8''" in cd, cd)
    check("CJK not in filename= fallback",
          "中" not in cd.split("filename*=", 1)[0], cd)


def start_drop_server(token):
    config = Config({
        "provider": "claude",
        "bind": "127.0.0.1",
        "port": 0,
        "drop_dir": DROP_DIR,
        "claude_bin": "/usr/bin/true",
    })
    bundles = providers.build_all(config, JobManager)
    server = make_server(config, token, bundles)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    return server, "http://127.0.0.1:%d" % port


def test_http(token):
    print("http:")
    server, base = start_drop_server(token)
    try:
        zh_name = "中文 测试.txt"
        zh_body = "你好 inbox\n".encode("utf-8")
        with open(os.path.join(DROP_DIR, zh_name), "wb") as f:
            f.write(zh_body)
        req = urllib.request.Request(
            base + "/api/drop", headers={"X-Auth-Token": token})
        with urllib.request.urlopen(req, timeout=10) as resp:
            listing = json.loads(resp.read().decode())
        names = [f["name"] for f in listing.get("files", [])]
        check("lists CJK name", zh_name in names, names)

        req = urllib.request.Request(
            base + "/api/drop/" + urllib.request.quote(zh_name),
            headers={"X-Auth-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                got = resp.read()
                hdr = resp.headers.get("X-Drop-Name") or ""
                cd = resp.headers.get("Content-Disposition") or ""
                check("CJK download bytes match", got == zh_body, got[:40])
                hdr.encode("latin-1")
                check("response X-Drop-Name is latin-1", True, hdr)
                check("response X-Drop-Name round-trips",
                      urllib.parse.unquote(hdr) == zh_name, hdr)
                check("response has filename*",
                      "filename*=UTF-8''" in cd, cd)
        except urllib.error.HTTPError as e:
            check("CJK download bytes match", False,
                  "HTTP %s %s" % (e.code, e.read()[:200]))

        folder = os.path.join(DROP_DIR, "资料")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "inside.txt"), "w") as f:
            f.write("nested\n")
        req = urllib.request.Request(
            base + "/api/drop/" + urllib.request.quote("资料"),
            headers={"X-Auth-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                zdata = resp.read()
                zhdr = resp.headers.get("X-Drop-Name") or ""
                check("CJK folder zips", zdata[:2] == b"PK", zdata[:8])
                check("CJK zip name round-trips",
                      urllib.parse.unquote(zhdr) == "资料.zip", zhdr)
        except urllib.error.HTTPError as e:
            check("CJK folder zips", False,
                  "HTTP %s %s" % (e.code, e.read()[:200]))
    finally:
        server.shutdown()


def main():
    test_helpers()
    token = load_or_create_token()
    test_http(token)
    if failures:
        print("\n%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
