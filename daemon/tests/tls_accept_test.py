"""A stalled TLS peer must not take the whole daemon down with it.

The public host died this way on 2026-09-23: the listening socket was
TLS-wrapped, so ``accept()`` ran the handshake on ``serve_forever``'s own
thread. A scanner opened a connection, never sent a ClientHello, and every
later connection queued behind it for three hours while systemd still
reported the unit active.

So the test is the outage: connect, say nothing, and then check that a real
client is still served.

Run:  python3 tests/tls_accept_test.py
"""

import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-tls-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config        # noqa: E402
from agentremoted.server import make_server   # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def self_signed(dirpath):
    """(cert, key) via openssl, or (None, None) when it is unavailable."""
    cert = os.path.join(dirpath, "cert.pem")
    key = os.path.join(dirpath, "key.pem")
    try:
        proc = subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", key, "-out", cert, "-days", "1",
             "-subj", "/CN=localhost"],
            capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if proc.returncode != 0 or not os.path.isfile(cert):
        return None, None
    return cert, key


def http_over_tls(port, timeout=8.0):
    """One real HTTPS request. Returns the status line, or '' on failure."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname="localhost") as tls:
                tls.settimeout(timeout)
                tls.sendall(b"GET /api/ping HTTP/1.1\r\n"
                            b"Host: localhost\r\nConnection: close\r\n\r\n")
                data = b""
                while b"\r\n" not in data and len(data) < 4096:
                    chunk = tls.recv(1024)
                    if not chunk:
                        break
                    data += chunk
        return data.split(b"\r\n", 1)[0].decode("latin-1")
    except (OSError, ssl.SSLError) as e:
        return "error: %s" % e


def main():
    print("\n=== tls accept loop ===")
    cert, key = self_signed(FAKE_HOME)
    if not cert:
        print("  [skip] openssl unavailable — cannot mint a test certificate")
        return 0

    config = Config({
        "provider": "claude", "bind": "127.0.0.1", "port": 0,
        "tls_cert": cert, "tls_key": key,
    })
    server = make_server(config, "test-token", {})
    port = server.server_address[1]

    # Structural: the LISTENING socket must stay plain. If this ever goes
    # back to ssl.wrap_socket(server.socket) the handshake moves back onto
    # the accept loop and the rest of this test only fails by luck/timing.
    check("listening socket is not TLS-wrapped",
          not isinstance(server.socket, ssl.SSLSocket), type(server.socket))
    check("server carries the context instead",
          isinstance(getattr(server, "ssl_context", None), ssl.SSLContext))

    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.2)
    try:
        check("serves HTTPS normally",
              http_over_tls(port).startswith("HTTP/1."), http_over_tls(port))

        # The outage: a peer that connects and then says nothing at all.
        stalled = []
        for _ in range(3):
            s = socket.create_connection(("127.0.0.1", port), timeout=5)
            stalled.append(s)          # no ClientHello, no close
        time.sleep(0.3)

        t0 = time.time()
        status = http_over_tls(port)
        elapsed = time.time() - t0
        check("a silent peer does not block the accept loop",
              status.startswith("HTTP/1."), status)
        check("and does not even slow it down", elapsed < 5.0,
              "%.1fs" % elapsed)

        for s in stalled:
            try:
                s.close()
            except OSError:
                pass

        # A peer that sends garbage instead of TLS must also be survivable.
        junk = socket.create_connection(("127.0.0.1", port), timeout=5)
        junk.sendall(b"GET / HTTP/1.1\r\n\r\n")   # plaintext to a TLS port
        time.sleep(0.3)
        check("plaintext to the TLS port does not wedge it",
              http_over_tls(port).startswith("HTTP/1."))
        junk.close()
    finally:
        server.shutdown()
        server.server_close()

    print()
    if failures:
        print("%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
