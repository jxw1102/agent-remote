"""Inbox drop downloads past the old 64 MB cap, streamed and resumable.

2.14.2: the daemon used to read the whole file into memory and refuse
anything over max_drop_mb (64). Now the body streams, there is no cap by
default, and a file honours Range / If-Range so a client resumes.

Run:  python3 tests/drop_large_test.py
"""

import hashlib
import json
import os
import resource
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-droplarge-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

DROP_DIR = os.path.join(os.environ["AGENTREMOTED_HOME"], "drop")
os.makedirs(DROP_DIR, exist_ok=True)
with open(os.path.join(os.environ["AGENTREMOTED_HOME"], "config.json"), "w") as f:
    json.dump({"drop_dir": DROP_DIR}, f)

from agentremoted.config import Config, load_or_create_token  # noqa: E402
from agentremoted.jobs import JobManager                      # noqa: E402
from agentremoted.server import _parse_byte_range, make_server  # noqa: E402
from agentremoted import providers                            # noqa: E402

BIG_MB = 150
failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def test_parse():
    print("range parsing:")
    check("absent", _parse_byte_range(None, 100) is None)
    check("open end", _parse_byte_range("bytes=10-", 100) == (10, 99))
    check("closed", _parse_byte_range("bytes=10-19", 100) == (10, 19))
    check("end clamps", _parse_byte_range("bytes=90-500", 100) == (90, 99))
    check("suffix", _parse_byte_range("bytes=-10", 100) == (90, 99))
    check("past end", _parse_byte_range("bytes=100-", 100) == "unsatisfiable")
    check("multi-range ignored",
          _parse_byte_range("bytes=0-1,5-6", 100) is None)
    check("garbage ignored", _parse_byte_range("items=1-2", 100) is None)


def start(extra=None):
    cfg = {"provider": "claude", "bind": "127.0.0.1", "port": 0,
           "drop_dir": DROP_DIR, "claude_bin": "/usr/bin/true"}
    cfg.update(extra or {})
    config = Config(cfg)
    server = make_server(config, TOKEN, providers.build_all(config, JobManager))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def get(base, path, headers=None):
    h = {"X-Auth-Token": TOKEN}
    h.update(headers or {})
    return urllib.request.urlopen(
        urllib.request.Request(base + path, headers=h), timeout=60)


def sha_stream(resp, limit=None):
    """Hash a body piecewise (the test must not hold 150 MB either)."""
    d = hashlib.sha256()
    n = 0
    while True:
        want = 1 << 20
        if limit is not None:
            want = min(want, limit - n)
            if want <= 0:
                break
        b = resp.read(want)
        if not b:
            break
        d.update(b)
        n += len(b)
    return d, n


def test_http():
    print("http:")
    big = os.path.join(DROP_DIR, "big.bin")
    whole = hashlib.sha256()
    with open(big, "wb") as f:
        block = os.urandom(1 << 20)
        for i in range(BIG_MB):
            piece = bytes([i & 0xFF]) + block[1:]
            f.write(piece)
            whole.update(piece)
    size = BIG_MB << 20
    server, base = start()
    try:
        with get(base, "/api/ping") as r:
            ping = json.loads(r.read().decode())
        check("ping advertises drop_range", ping.get("drop_range") is True, ping)
        check("ping max_drop_mb 0 (uncapped)", ping.get("max_drop_mb") == 0)

        rss0 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with get(base, "/api/drop/big.bin") as r:
            check("200 for %d MB" % BIG_MB, r.status == 200, r.status)
            check("Content-Length", r.headers.get("Content-Length") == str(size))
            check("Accept-Ranges bytes", r.headers.get("Accept-Ranges") == "bytes")
            etag = r.headers.get("ETag") or ""
            check("ETag present", bool(etag))
            d, n = sha_stream(r)
        check("whole body intact", n == size and d.digest() == whole.digest(), n)
        rss1 = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # ru_maxrss is bytes on macOS, KB on Linux.
        grew = (rss1 - rss0) / ((1 << 20) if sys.platform == "darwin" else 1024)
        check("daemon did not buffer the file (peak RSS grew %.0f MB)" % grew,
              grew < BIG_MB / 2, grew)

        # Resume: first 70 MB, "drop", then Range from there with If-Range.
        cut = 70 << 20
        with get(base, "/api/drop/big.bin") as r:
            head, _ = sha_stream(r, limit=cut)
        with get(base, "/api/drop/big.bin",
                 {"Range": "bytes=%d-" % cut, "If-Range": etag}) as r:
            check("206 on resume", r.status == 206, r.status)
            check("Content-Range",
                  r.headers.get("Content-Range")
                  == "bytes %d-%d/%d" % (cut, size - 1, size),
                  r.headers.get("Content-Range"))
            check("X-Drop-Size is the whole size",
                  r.headers.get("X-Drop-Size") == str(size))
            # Feed the tail into the head's hash state.
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                head.update(b)
        check("head + resumed tail == file", head.digest() == whole.digest())

        with get(base, "/api/drop/big.bin",
                 {"Range": "bytes=%d-" % cut, "If-Range": '"stale"'}) as r:
            check("stale If-Range sends whole file (200)", r.status == 200, r.status)
        try:
            get(base, "/api/drop/big.bin", {"Range": "bytes=%d-" % size})
            check("416 past end", False, "no error")
        except urllib.error.HTTPError as e:
            check("416 past end", e.code == 416, e.code)

        os.makedirs(os.path.join(DROP_DIR, "dir"), exist_ok=True)
        with open(os.path.join(DROP_DIR, "dir", "a.txt"), "w") as f:
            f.write("a\n")
        with get(base, "/api/drop/dir", {"Range": "bytes=5-"}) as r:
            body = r.read()
            check("folder zip ignores Range (200, whole zip)",
                  r.status == 200 and body[:2] == b"PK", r.status)
            check("folder zip says Accept-Ranges none",
                  r.headers.get("Accept-Ranges") == "none")
    finally:
        server.shutdown()

    server, base = start({"max_drop_mb": 100})
    try:
        try:
            get(base, "/api/drop/big.bin")
            check("explicit max_drop_mb still caps", False, "no error")
        except urllib.error.HTTPError as e:
            check("explicit max_drop_mb still caps", e.code == 413, e.code)
    finally:
        server.shutdown()


TOKEN = load_or_create_token()


def main():
    test_parse()
    test_http()
    if failures:
        print("\n%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
