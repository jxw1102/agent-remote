"""SSE (Server-Sent Events) status stream — stdlib only.

GET /sse/status streams the same active-job payload as /ws/status but over
plain HTTP (text/event-stream), so it works through HTTPS and proxies that
do not support WebSocket upgrades.

    data: {"type": "status", "active": [...]}

Push granularity and payload shape match wstream.py exactly.
"""

import json
import time

_PUSH_INTERVAL = 1.0
_KEEPALIVE_INTERVAL = 15.0


def serve_status(handler, jobs, active_fn=None):
    """Write SSE events until the client disconnects.
    Runs on the request's own thread (ThreadingHTTPServer).

    active_fn: optional callable → list of active job dicts (multi-provider
    root stream). When set, jobs may be None.
    """
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")
    # The web client's EventSource is cross-origin for every daemon but the
    # one that served the page. (EventSource cannot send headers, which is
    # why the token rides in the query string for this endpoint.)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Vary", "Origin")
    handler.end_headers()

    last_sent = None
    last_write = time.time()
    try:
        while True:
            active = active_fn() if active_fn is not None else jobs.active_status()
            payload = json.dumps({"type": "status", "active": active})
            if payload != last_sent:
                handler.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
                handler.wfile.flush()
                last_sent = payload
                last_write = time.time()
            else:
                now = time.time()
                if now - last_write >= _KEEPALIVE_INTERVAL:
                    handler.wfile.write(b":\n\n")
                    handler.wfile.flush()
                    last_write = now
            time.sleep(_PUSH_INTERVAL)
    except (OSError, BrokenPipeError):
        pass
