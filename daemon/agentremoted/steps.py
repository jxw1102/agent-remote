"""Process-view steps, shared by every harness.

The default transcript is the *result*: what the human asked, what the agent
answered. The work in between — tool calls, their output, the thinking — is
dropped, and in a working session that is most of the record (83% of the
user/assistant lines in one measured Claude session).

`?detail=steps` attaches that material to the messages it happened between.
Each harness reads its own journal (Claude's JSONL, Grok's updates.jsonl,
Codex's rollout), but they all emit the shape defined here so one client
renderer serves all three.

Two rules the shape exists to enforce:

  * **Steps are children of a message, never messages.** A Claude tool_result
    is a `type: "user"` record; promoting one to a top-level user item would
    corrupt every client that counts user messages — the web's "Rewind to
    here" computes `/rewind N` that way and would cut at the wrong point.
  * **Previews are capped.** A single tool result can be hundreds of KB. Only
    the head travels with the window; `truncated` tells the client to fetch
    the rest from the step endpoint when (and only when) it is expanded.

Bodies are *human-first*: tool inputs render as command / path / diff, not
raw `json.dumps`; known structured results (Grok SearchReplace, Bash
envelopes, …) unwrap to the success line or shell output. Unknown shapes
still fall back to indented JSON so nothing is invented.
"""

from __future__ import annotations

import difflib
import json

PREVIEW_B = 512     # per step; the rest is fetched on expand
_DIFF_LINE_CAP = 800


def clip(text: str, cap: int = PREVIEW_B):
    """(preview, full_len, truncated)."""
    text = text or ""
    if len(text) <= cap:
        return text, len(text), False
    return text[:cap], len(text), True


def tool_use(ref, ts, name, detail, full):
    preview, n, cut = clip(full or "")
    return {"kind": "tool_use", "ref": ref, "ts": ts or "",
            "name": name or "tool", "detail": detail or "",
            "preview": preview, "bytes": n, "truncated": cut}


def tool_result(ref, ts, ok, full):
    preview, n, cut = clip(full or "")
    return {"kind": "tool_result", "ref": ref, "ts": ts or "",
            "ok": bool(ok), "preview": preview, "bytes": n, "truncated": cut}


def thinking(ref, ts, text):
    """A thinking step, or a marker when the harness recorded no plaintext.

    Claude Code stores thinking as signature-only ciphertext on some CLI
    versions (≤2.1.205 and ≥2.1.223 in the transcripts measured here), so an
    empty block is normal rather than a bug — say so instead of rendering a
    blank bubble the reader cannot explain.
    """
    text = (text or "").strip()
    if not text:
        return {"kind": "thinking", "ref": ref, "ts": ts or "",
                "recorded": False}
    preview, n, cut = clip(text)
    return {"kind": "thinking", "ref": ref, "ts": ts or "", "recorded": True,
            "preview": preview, "bytes": n, "truncated": cut}


def attach(window, step_rows):
    """Hang each message's steps off it (attach-*after*).

    A message owns every step from its own position up to — but not including
    — the next message in the window. Attaching *after* rather than before
    means an interrupted turn's trailing steps still have a home instead of
    being orphaned, and the rows read in the order they happened when drawn
    under the bubble.

    `window` messages carry `_pos`; `step_rows` are (pos, step) pairs. Both
    positions only have to be comparable within one harness.
    """
    if not window:
        return
    for i, msg in enumerate(window):
        start = msg.get("_pos", 0)
        end = window[i + 1].get("_pos") if i + 1 < len(window) else None
        msg["steps"] = [st for pos, st in step_rows
                        if pos >= start and (end is None or pos < end)]


# ---------------------------------------------------------------------------
# Smart body formatting (tool_use input + tool_result output)
# ---------------------------------------------------------------------------

def format_tool_use(name, raw) -> str:
    """Human body for a tool_use / tool_call input.

    Recognises Bash, Edit/search_replace, Write, Read, Grep-style inputs.
    Falls back to indented JSON for unknown dicts, or the raw string.
    """
    obj = _as_obj(raw)
    if obj is None:
        return _as_text(raw)
    if not isinstance(obj, dict):
        return _pretty(obj)

    # Edit / MultiEdit / search_replace — show a unified diff, not escaped JSON.
    if _has_any(obj, "old_string", "oldString", "new_string", "newString"):
        path = _first_str(obj, "file_path", "target_file", "path",
                          "absolute_path")
        old = _str(obj.get("old_string") if "old_string" in obj
                   else obj.get("oldString"))
        new = _str(obj.get("new_string") if "new_string" in obj
                   else obj.get("newString"))
        return _format_edit(path, old, new, bool(obj.get("replace_all")
                                                 or obj.get("replaceAll")))

    # MultiEdit: edits: [{file_path, old_string, new_string}, …]
    edits_list = obj.get("edits")
    if isinstance(edits_list, list) and edits_list:
        chunks = []
        for e in edits_list:
            if not isinstance(e, dict):
                continue
            if not _has_any(e, "old_string", "oldString", "new_string",
                            "newString"):
                continue
            path = _first_str(e, "file_path", "target_file", "path")
            old = _str(e.get("old_string") if "old_string" in e
                       else e.get("oldString"))
            new = _str(e.get("new_string") if "new_string" in e
                       else e.get("newString"))
            chunks.append(_format_edit(path, old, new,
                                       bool(e.get("replace_all"))))
        if chunks:
            return "\n\n".join(chunks)

    # Write — path + file body.
    path = _first_str(obj, "file_path", "target_file", "path")
    if path and "content" in obj and isinstance(obj.get("content"), str):
        return _format_write(path, obj.get("content") or "")

    # Shell — description (optional) + command.
    cmd = _first_str(obj, "command", "cmd")
    if cmd is not None:
        desc = (_str(obj.get("description")) or "").strip()
        if desc and desc != cmd:
            return "%s\n$ %s" % (desc, cmd)
        return cmd

    # Grep / search.
    pattern = _first_str(obj, "pattern", "query", "search_query")
    if pattern is not None and _looks_like_search(name, obj):
        bits = [pattern]
        where = _first_str(obj, "path", "glob", "target_directory", "file")
        if where:
            bits.append("in %s" % where)
        return " ".join(bits)

    # Read / list — path (+ range).
    if path or _first_str(obj, "target_directory"):
        target = path or _first_str(obj, "target_directory")
        extra = []
        if obj.get("offset") is not None:
            extra.append("offset=%s" % obj.get("offset"))
        if obj.get("limit") is not None:
            extra.append("limit=%s" % obj.get("limit"))
        if obj.get("pages") is not None:
            extra.append("pages=%s" % obj.get("pages"))
        return target + ((" (%s)" % ", ".join(extra)) if extra else "")

    # Description alone is better than a wall of JSON for odd tools.
    desc = (_str(obj.get("description")) or "").strip()
    if desc and len(obj) <= 4:
        return desc

    return _pretty(obj)


def format_tool_result(text, name: str = "") -> str:
    """Human body for a tool_result / tool_call_update output.

    Unwraps known envelopes (Grok SearchReplace / Bash / ListDir, …). Plain
    text and unrecognised JSON pass through (JSON re-pretty-printed).
    """
    text = text if isinstance(text, str) else _as_text(text)
    stripped = (text or "").strip()
    if not stripped:
        return ""
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return text

    try:
        obj = json.loads(stripped)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text

    if not isinstance(obj, dict):
        return _pretty(obj)

    kind = str(obj.get("type") or obj.get("Type") or "")

    # --- SearchReplace / Edit success envelope (Grok + similar) -----------
    edits = (obj.get("EditsApplied") or obj.get("editsApplied")
             or obj.get("edits_applied"))
    if kind == "SearchReplace" or isinstance(edits, dict):
        msg = _edit_result_message(obj, edits if isinstance(edits, dict)
                                   else None)
        if msg:
            return msg

    # --- Bash / shell envelope --------------------------------------------
    if kind == "Bash" or _looks_like_bash_result(obj):
        return _format_bash_result(obj)

    # --- ListDir ----------------------------------------------------------
    if kind == "ListDir":
        content = obj.get("Content") or obj.get("content")
        if isinstance(content, dict) and content.get("content") is not None:
            return _str(content.get("content"))
        if isinstance(content, str):
            return content

    # --- Generic prompt-facing strings ------------------------------------
    for key in ("tool_output_for_prompt_concise", "tool_output_for_prompt",
                "output_for_prompt", "message"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Nested Result / Content blobs (TaskOutput, KillTask, …).
    for key in ("Result", "result", "Content", "content", "ImageContent"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        if isinstance(val, dict):
            inner = val.get("content") or val.get("text") or val.get("message")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
            # Prefer a short pretty dict over the whole envelope.
            if kind and len(val) <= 8:
                return _pretty(val)

    # TodosUpdated etc. — compact one-liner when possible.
    if kind == "Todo" or "TodosUpdated" in obj:
        todos = obj.get("TodosUpdated") or obj.get("todos")
        if isinstance(todos, list):
            return "%d todos" % len(todos)

    return _pretty(obj)


# -- helpers ---------------------------------------------------------------

def _as_obj(raw):
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except (TypeError, ValueError, json.JSONDecodeError):
                return None
        return None
    return raw if raw is not None else None


def _as_text(raw) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return _pretty(raw)


def _pretty(obj) -> str:
    try:
        return json.dumps(obj, indent=1, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


def _str(val) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return str(val)


def _first_str(obj: dict, *keys):
    for k in keys:
        if k not in obj:
            continue
        val = obj.get(k)
        if isinstance(val, str) and val.strip():
            return val
        if val is not None and not isinstance(val, (dict, list)):
            s = str(val).strip()
            if s:
                return s
    return None


def _has_any(obj: dict, *keys) -> bool:
    return any(k in obj for k in keys)


def _looks_like_search(name, obj: dict) -> bool:
    n = (name or "").lower()
    if any(s in n for s in ("grep", "search", "glob", "find")):
        return True
    # pattern + path-ish without being an edit
    return "pattern" in obj and not _has_any(obj, "old_string", "new_string",
                                             "command", "cmd")


def _looks_like_bash_result(obj: dict) -> bool:
    if "exit_code" in obj and ("output" in obj or "output_for_prompt" in obj
                               or "command" in obj):
        return True
    return False


def _decode_output_blob(val) -> str:
    """Grok sometimes stores Bash `output` as a list of byte ordinals."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list) and val and all(isinstance(x, int) for x in val):
        try:
            return bytes(val).decode("utf-8", "replace")
        except (TypeError, ValueError, OverflowError):
            pass
    if isinstance(val, (dict, list)):
        return _pretty(val)
    return str(val)


def _format_bash_result(obj: dict) -> str:
    body = _decode_output_blob(obj.get("output_for_prompt"))
    if not body.strip():
        body = _decode_output_blob(obj.get("output"))
    body = body.rstrip()
    code = obj.get("exit_code")
    failed = code not in (None, 0, "0")
    if failed:
        head = "exit %s" % code
        return (head + "\n" + body) if body else head
    return body if body else "(no output)"


def _edit_result_message(obj: dict, edits) -> str:
    for src in (edits, obj):
        if not isinstance(src, dict):
            continue
        for key in ("tool_output_for_prompt_concise",
                    "tool_output_for_prompt"):
            val = src.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        path = _first_str(src, "absolute_path", "file_path", "path")
        if path:
            return "Updated %s" % path
    return ""


def _format_write(path: str, content: str) -> str:
    content = content or ""
    lines = content.splitlines()
    head = path or "(new file)"
    if not content:
        return head + "\n(empty)"
    # Cap absurd single-file dumps in the full step text; expand still
    # shows enough to audit without shipping multi-MB bodies.
    cap = 400
    body_lines = lines[:cap]
    body = "\n".join(body_lines)
    if len(lines) > cap:
        body += "\n… (%d more lines)" % (len(lines) - cap)
    return "%s\n\n%s" % (head, body)


def _format_edit(path, old: str, new: str, replace_all: bool = False) -> str:
    old = old or ""
    new = new or ""
    parts = []
    if path:
        parts.append(path)
    if replace_all:
        parts.append("replace_all: true")
    if parts:
        parts.append("")

    old_lines = old.splitlines()
    new_lines = new.splitlines()

    if not old and new:
        parts.append("--- /dev/null")
        parts.append("+++ %s" % (path or "new"))
        shown = new_lines[:_DIFF_LINE_CAP]
        parts.extend("+" + ln for ln in shown)
        if len(new_lines) > _DIFF_LINE_CAP:
            parts.append("… (%d more lines)" % (len(new_lines) - _DIFF_LINE_CAP))
        return "\n".join(parts)

    if old == new:
        parts.append("(no change)")
        return "\n".join(parts)

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=path or "old",
        tofile=path or "new",
        lineterm="",
        n=3,
    ))
    if not diff:
        parts.append("(no change)")
        return "\n".join(parts)
    if len(diff) > _DIFF_LINE_CAP:
        diff = diff[:_DIFF_LINE_CAP] + [
            "… (%d more diff lines)" % (len(diff) - _DIFF_LINE_CAP)
        ]
    parts.extend(diff)
    return "\n".join(parts)
