"""The request log does not carry the API token or the hook secret.

Run:  python3 tests/log_redact_test.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.server import redact_secrets  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("request log redaction")
line = '"POST /internal/hook?secret=abc123def&tui=cld-1-2 HTTP/1.1" 200 -'
out = redact_secrets(line)
check("the hook secret is gone", "abc123def" not in out, out)
check("the rest of the line stays",
      out == '"POST /internal/hook?secret=<redacted>&tui=cld-1-2 HTTP/1.1" 200 -', out)
line = '"GET /api/status/stream?since=4&token=TOK-9_x.y HTTP/1.1" 200 -'
out = redact_secrets(line)
check("the API token is gone", "TOK-9_x.y" not in out, out)
check("other parameters stay", "since=4&token=<redacted> HTTP/1.1" in out, out)
check("a token last in the query stops at the space",
      redact_secrets("GET /x?token=abc HTTP/1.1") == "GET /x?token=<redacted> HTTP/1.1")
check("case does not matter", "S3" not in redact_secrets("/x?Token=S3&a=1"))
check("a path that merely contains the word is left alone",
      redact_secrets("GET /api/tokens/list?a=1") == "GET /api/tokens/list?a=1")
check("a parameter that only ends with the name is left alone",
      redact_secrets("/x?mytoken=1") == "/x?mytoken=1")
check("nothing to redact, nothing changed",
      redact_secrets('"GET /api/ping HTTP/1.1" 200 -') == '"GET /api/ping HTTP/1.1" 200 -')
check("empty is empty", redact_secrets("") == "" and redact_secrets(None) == "")

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all ok")
