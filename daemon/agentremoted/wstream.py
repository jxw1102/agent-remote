"""Minimal WebSocket (RFC 6455) status stream — stdlib only.

GET /ws/status upgrades to a WebSocket that pushes the active-job status
whenever it changes (~1 s granularity; elapsed_s ticks while a job runs):

    {"type": "status", "active": [
        {"job_id", "session_id", "new_session_id", "status", "prompt",
         "elapsed_s", "queued_count", "tool", "tool_detail",
         "pending_permission", "pending_question", "next_seq"}, ...]}

next_seq doubles as a doorbell: the app compares it to its event cursor and
only polls /api/jobs/<id>?since=N when there is actually something to fetch.

Only what the BB10 banner needs is implemented: server->client text
frames, ping/pong keepalive, close. No extensions, no fragmentation
(status payloads are far below one frame), no client text frames.
"""

import base64
import hashlib
import json
import struct
import threading
import time

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_PUSH_INTERVAL = 1.0
_PING_INTERVAL = 15.0
_MAX_CLIENT_PAYLOAD = 64 * 1024

_OP_TEXT = 0x1
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA


def is_upgrade(headers) -> bool:
    return "websocket" in (headers.get("Upgrade", "") or "").lower()


def serve_status(handler, jobs, active_fn=None):
    """Handshake on the handler's socket, then stream until the client
    leaves. Runs on the request's own thread (ThreadingHTTPServer).

    active_fn: optional callable → list of active job dicts (multi root).
    """
    key = handler.headers.get("Sec-WebSocket-Key", "")
    if not key:
        handler.send_response(400)
        handler.end_headers()
        return
    accept = base64.b64encode(
        hashlib.sha1((key + _GUID).encode("ascii")).digest()).decode("ascii")
    handler.wfile.write((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode("ascii"))
    handler.wfile.flush()

    closed = threading.Event()
    write_lock = threading.Lock()

    def send(opcode, payload=b""):
        frame = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            frame.append(n)
        elif n < 65536:
            frame.append(126)
            frame += struct.pack(">H", n)
        else:
            frame.append(127)
            frame += struct.pack(">Q", n)
        with write_lock:
            handler.wfile.write(bytes(frame) + payload)
            handler.wfile.flush()

    # Reader: the client only ever sends control frames (close / ping /
    # its pong replies). EOF or a protocol error ends the stream.
    def read_loop():
        try:
            while not closed.is_set():
                frame = _read_frame(handler.rfile)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == _OP_CLOSE:
                    try:
                        send(_OP_CLOSE)
                    except OSError:
                        pass
                    break
                if opcode == _OP_PING:
                    send(_OP_PONG, payload)
        except OSError:
            pass
        closed.set()

    reader = threading.Thread(target=read_loop, daemon=True)
    reader.start()

    last_sent = None
    last_ping = time.time()
    try:
        while not closed.is_set():
            active = active_fn() if active_fn is not None else jobs.active_status()
            payload = json.dumps({"type": "status", "active": active})
            if payload != last_sent:
                send(_OP_TEXT, payload.encode("utf-8"))
                last_sent = payload
            now = time.time()
            if now - last_ping >= _PING_INTERVAL:
                send(_OP_PING)
                last_ping = now
            closed.wait(_PUSH_INTERVAL)
    except OSError:
        pass
    closed.set()


def _read_frame(rfile):
    """One frame from the client: (opcode, unmasked payload) or None."""
    head = rfile.read(2)
    if len(head) < 2:
        return None
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    n = head[1] & 0x7F
    if n == 126:
        ext = rfile.read(2)
        if len(ext) < 2:
            return None
        n = struct.unpack(">H", ext)[0]
    elif n == 127:
        ext = rfile.read(8)
        if len(ext) < 8:
            return None
        n = struct.unpack(">Q", ext)[0]
    if n > _MAX_CLIENT_PAYLOAD:
        return None
    mask = b""
    if masked:
        mask = rfile.read(4)
        if len(mask) < 4:
            return None
    payload = rfile.read(n) if n else b""
    if len(payload) < n:
        return None
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload
