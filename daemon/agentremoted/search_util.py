"""Full-text session search helpers shared by every provider store.

Search is deliberately simple: case-insensitive substring over titles and
human-visible message text. No inverted index — a phone-initiated scan of
the most recent sessions is fast enough and needs no extra on-disk state.

Body scans only read a head + tail window of each transcript so multi-MB
sessions do not dominate latency.
"""

from __future__ import annotations

# Cap how many sessions we open when searching (mtime order, newest first).
MAX_SCAN = 120
# Bytes of each transcript body to scan when titles miss (head + tail).
# Large enough for recent context; keeps a full miss under ~100–200ms locally.
SEARCH_HEAD_BYTES = 384 * 1024
SEARCH_TAIL_BYTES = 192 * 1024
# Snippet window around the first hit (characters, after whitespace collapse).
SNIPPET_LEN = 140
# Reject absurd queries early.
MAX_QUERY_LEN = 120


def normalize_query(raw) -> str:
    """Strip and clamp a user query. Empty string means "do not search"."""
    q = " ".join(str(raw or "").split())
    if not q:
        return ""
    if len(q) > MAX_QUERY_LEN:
        q = q[:MAX_QUERY_LEN]
    return q


def contains_ci(haystack: str, needle: str) -> bool:
    if not needle or not haystack:
        return False
    # ASCII queries dominate; str.lower is cheaper than casefold on long lines.
    if needle.isascii() and haystack.isascii():
        return needle.lower() in haystack.lower()
    return needle.casefold() in haystack.casefold()


def line_may_match(line: str, needle_folded: str, *, ascii_needle: bool) -> bool:
    """Fast reject for raw JSONL lines before JSON parse."""
    if not needle_folded or not line:
        return False
    if ascii_needle:
        return needle_folded in line.lower()
    return needle_folded in line.casefold()


def make_snippet(text: str, query: str, max_len: int = SNIPPET_LEN) -> str:
    """Collapse whitespace and return a window centered on the first hit.

    The phone highlights the query client-side; we just pick the readable
    context. If the query is missing, return the head of the text.
    """
    text = " ".join((text or "").split())
    if not text:
        return ""
    q = (query or "").strip()
    if not q:
        return text[:max_len] + ("…" if len(text) > max_len else "")

    lower = text.casefold()
    q_lower = q.casefold()
    idx = lower.find(q_lower)
    if idx < 0:
        return text[:max_len] + ("…" if len(text) > max_len else "")

    # Prefer keeping the match near the middle of the window.
    half = max(0, (max_len - len(q)) // 2)
    start = max(0, idx - half)
    end = min(len(text), start + max_len)
    start = max(0, end - max_len)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet.lstrip()
    if end < len(text):
        snippet = snippet.rstrip() + "…"
    return snippet


def rank_key(row: dict) -> str:
    """Sort key: most recently active first (ISO strings sort lexicographically)."""
    return row.get("last_active") or row.get("started") or ""
