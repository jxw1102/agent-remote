"""HTTP client for DeepSeek Harness (`dsh web`) localhost /api.

The official UI is a browser on http://127.0.0.1:3080. Agent Remote talks to
the same RPC the page uses — never expose that port to phones. There is no
auth token; the Host-header trust fence allows loopback.

Wire (packages/host/apiproxy):

    POST /api/<method>
    {"type":"client-request","rpcId":"...","method":"<method>","payload":{...}}

    → {"type":"server-response","rpcId":"...","result":{"ok":true,"value":...}}
"""

from __future__ import annotations

import json
import logging
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:3080"


class DshError(Exception):
    """A dsh /api call failed (transport or business)."""

    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code = str(code or "")
        self.status = int(status or 0)


class DshClient:
    def __init__(self, base_url: str = "", timeout: float = 30.0):
        raw = (base_url or DEFAULT_URL).strip() or DEFAULT_URL
        if "://" not in raw:
            raw = "http://" + raw
        self.base = raw.rstrip("/")
        self.timeout = float(timeout)
        parsed = urlparse(self.base)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        self.host_header = ("%s:%s" % (host, port)) if port else host

    def call(self, method: str, payload=None, timeout=None):
        """POST one unary RPC. Returns the business value or raises DshError."""
        method = str(method or "").strip()
        if not method:
            raise DshError("rpc method required")
        rpc_id = uuid.uuid4().hex
        body = {
            "type": "client-request",
            "rpcId": rpc_id,
            "method": method,
            "payload": payload if isinstance(payload, dict) else {},
        }
        data = json.dumps(body, allow_nan=False).encode("utf-8")
        req = Request(
            self.base + "/api/" + method,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Host": self.host_header,
            },
        )
        wait = self.timeout if timeout is None else float(timeout)
        try:
            with urlopen(req, timeout=max(wait, 1.0)) as resp:
                raw = resp.read()
                status = getattr(resp, "status", 200) or 200
        except HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            raise DshError(
                "dsh HTTP %s: %s" % (e.code, err_body or e.reason),
                status=int(e.code or 0),
            ) from e
        except URLError as e:
            reason = getattr(e, "reason", e)
            raise DshError(
                "dsh web is not reachable at %s (%s)" % (self.base, reason),
                status=0,
            ) from e
        except TimeoutError as e:
            raise DshError("dsh web timed out", status=0) from e
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise DshError("dsh returned non-JSON (HTTP %s)" % status) from e
        if not isinstance(msg, dict):
            raise DshError("dsh returned a non-object")
        result = msg.get("result")
        if not isinstance(result, dict):
            # Some older snapshots may unwrap the value.
            if "ok" in msg:
                result = msg
            else:
                raise DshError("dsh response missing result")
        if result.get("ok") is True:
            return result.get("value")
        err = result.get("error") if isinstance(result.get("error"), dict) else {}
        code = str(err.get("code") or "internal")
        message = str(err.get("message") or code)
        raise DshError(message, code=code, status=status)

    def reachable(self) -> bool:
        try:
            self.call("session.list", {}, timeout=4)
            return True
        except DshError:
            return False
