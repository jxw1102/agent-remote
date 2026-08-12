"""AI session titles, shared by every harness.

A session's own name is whatever its CLI happened to store: Claude keeps an
``ai-title`` line, Grok sometimes writes ``generated_title``, Codex often has
nothing and the list falls back to the first user message ("update
prepare_upload.py . if the item i…"). Across a dozen parallel projects those
raw openings are what make a list unreadable, so the daemon derives a short
title of its own and caches it.

**Each harness names its own sessions.** A Grok session is titled by Grok, a
Codex session by Codex, a Claude session by Claude — the runner supplies a
``title_for`` callable and this module only decides *when* to ask and where to
keep the answer. Claude's is a cheap Haiku request over the subscription token
the daemon already refreshes; the other two have no usable API path, so they
run their own CLI one-shot (see each runner's ``title_for``).

Two costs come with the CLI route, and they are why titles are cached hard and
generated at most once per source text:

  * roughly ten seconds per title, so generation is always in the background
    and the raw opening message shows once while it is in flight;
  * real tokens on that harness's subscription (Codex measured ~11.5k for one
    title, since it loads its instructions first).

The one-shot also *creates a session* in the CLI's own store. Those turns run
in :func:`titler_cwd`, and both stores treat that directory as non-human, so a
title never adds a row to the session list.

The cache is keyed by session id, which is unique across harnesses, so one file
serves all of them.

Titles are cached against a *signature* of the text they were derived from, so
a title is generated once and only regenerates when its source really changes
(see :func:`sig_for` and Claude's compaction-aware ``_title_source``).
"""

import hashlib
import json
import os
import queue
import threading
import urllib.error
import urllib.request

from .config import CONFIG_DIR

MESSAGES_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"
MAX_CHARS = 42
# Bump to invalidate every cached title after a logic change.
SIG_VERSION = "v3"
INPUT_CHARS = 4000  # hard cap on the API input
SYSTEM = (
    "You name coding sessions. From the text below, reply with ONLY a short "
    "topic title of at most 5 words. No markdown, no quotes, no trailing "
    "punctuation, no leading 'Title:'."
)

# Titles a CLI writes that carry no information — treat as "no title at all".
BLANK = ("", "new session", "untitled", "untitled session", "session")


def titler_cwd():
    """Scratch directory the CLI one-shots run in.

    Their own home (and therefore their login) is left alone — only the working
    directory is redirected, which is what makes the throwaway session
    identifiable and filterable.
    """
    path = CONFIG_DIR / "titler"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def is_titler_cwd(cwd) -> bool:
    """True for a session the titler created. Stores drop these from
    `user_only` listings so naming a session never adds a row to the list."""
    text = str(cwd or "").strip().rstrip("/")
    if not text:
        return False
    return text == str(CONFIG_DIR / "titler").rstrip("/")


def prompt_for(text: str) -> str:
    """The one-shot prompt handed to a harness CLI."""
    return (SYSTEM + "\n\nCoding session to name:\n\n"
            + (text or "").strip()[:INPUT_CHARS])


def title_from_output(out: str) -> str:
    """Last non-empty line of a CLI's stdout, cleaned.

    Grok prints the reply alone; Codex frames it with a `codex` header and a
    token-usage footer but still ends with the answer, so the tail is the one
    rule that fits both.
    """
    lines = [l.strip() for l in str(out or "").splitlines() if l.strip()]
    for line in reversed(lines):
        # Skip Codex's trailing counters ("tokens used", "11,476").
        low = line.lower()
        if low.startswith("tokens used") or low.replace(",", "").isdigit():
            continue
        cleaned = clean_title(line)
        if cleaned:
            return cleaned
    return ""


def clean_title(text: str) -> str:
    """Normalize a model reply into a bare title: drop markdown/quotes/leading
    '#', collapse whitespace, trim trailing punctuation, clamp for mobile."""
    t = " ".join((text or "").split())
    t = t.lstrip("#").strip()
    t = t.strip("*_`\"'").strip()
    t = t.rstrip(".:;,").strip()
    if len(t) > MAX_CHARS:
        t = t[: MAX_CHARS - 1].rstrip() + "…"
    return t


def looks_blank(title: str) -> bool:
    """True when a CLI's own title is missing or a placeholder."""
    return " ".join(str(title or "").split()).lower() in BLANK


def sig_for(text: str) -> str:
    """Signature for a title derived from immutable text (a first message).

    Hashing the source means the title is generated once and never churns while
    that text stays put — which is the common case for Grok and Codex, neither
    of which has Claude's compaction cycle.
    """
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]
    return "%s:0:%s" % (SIG_VERSION, digest)


def _subscription_token(config) -> str:
    """Bearer token for the title call.

    Resolved lazily and from the Claude provider on purpose: it owns the
    credential store and the refresh grant, and every harness borrows it rather
    than each carrying its own titling credential. Kept out of the import graph
    at module level so `providers.grok` / `providers.codex` never import
    `providers.claude`.
    """
    try:
        from .providers.claude import _oauth_token
    except Exception:
        return ""
    try:
        return _oauth_token(config) or ""
    except Exception:
        return ""


def summarize(config, text: str, token_fn=None) -> str:
    """One short title from Haiku, or "" on any failure.

    Never raises: the caller always has a heuristic title to fall back on, and
    a listing must not fail because a title could not be generated.
    """
    text = (text or "").strip()[:INPUT_CHARS]
    if not text:
        return ""
    token = (token_fn or _subscription_token)(config)
    if not token:
        return ""
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 32,
        "system": SYSTEM,
        "messages": [{"role": "user",
                      "content": "Coding session to name:\n\n" + text}],
    }).encode("utf-8")
    req = urllib.request.Request(MESSAGES_URL, data=body, headers={
        "Authorization": "Bearer " + token,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
        "User-Agent": "agentremoted",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            json.JSONDecodeError, ValueError):
        return ""
    out = "".join(b.get("text", "") for b in raw.get("content", [])
                  if isinstance(b, dict) and b.get("type") == "text")
    return clean_title(out)


class TitleCache:
    """Persistent session_id -> {title, sig} map, filled lazily by a single
    background worker so the sessions list never blocks on the model call.

    Shared across harnesses: session ids are unique, and one worker means three
    stores listing at once cannot open three connections per session.
    """

    def __init__(self, config, token_fn=None, path=None):
        self._config = config
        self._token_fn = token_fn
        self._path = path or (CONFIG_DIR / "session_titles.json")
        self._lock = threading.Lock()
        self._map = {}
        self._queue = queue.Queue()
        self._pending = set()
        self._worker = None
        self._load()

    def _load(self):
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            self._map = {k: v for k, v in data.items()
                         if isinstance(v, dict) and v.get("title")}

    def _save(self):
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.parent / (self._path.name + ".tmp")
            tmp.write_text(json.dumps(self._map), encoding="utf-8")
            os.replace(str(tmp), str(self._path))
        except OSError:
            pass

    def get(self, session_id: str, sig: str) -> str:
        with self._lock:
            entry = self._map.get(session_id)
            if entry and entry.get("sig") == sig:
                return entry.get("title", "")
        return ""

    def request(self, session_id: str, sig: str, text: str,
                titler=None) -> None:
        """Queue a title generation unless one is cached (same sig) or already
        in flight for this session.

        `titler` is the owning harness's generator; without one the Claude
        subscription is used, which is the only sensible fallback.
        """
        if not text:
            return
        with self._lock:
            entry = self._map.get(session_id)
            if entry and entry.get("sig") == sig:
                return
            if session_id in self._pending:
                return
            self._pending.add(session_id)
            if self._worker is None:
                self._worker = threading.Thread(target=self._run, daemon=True)
                self._worker.start()
        self._queue.put((session_id, sig, text, titler))

    def _run(self):
        while True:
            session_id, sig, text, titler = self._queue.get()
            try:
                if titler is not None:
                    try:
                        title = clean_title(titler(text))
                    except Exception:
                        title = ""
                else:
                    title = summarize(self._config, text, self._token_fn)
                if title:
                    with self._lock:
                        self._map[session_id] = {"title": title, "sig": sig}
                        self._save()
            finally:
                with self._lock:
                    self._pending.discard(session_id)
                self._queue.task_done()


_shared = {}
_shared_lock = threading.Lock()


def shared_cache(config) -> TitleCache:
    """One cache per process, so all three stores share the worker and file."""
    with _shared_lock:
        cache = _shared.get("cache")
        if cache is None:
            cache = TitleCache(config)
            _shared["cache"] = cache
        return cache
