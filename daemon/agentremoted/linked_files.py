"""Files an agent linked in its own transcript, served to the session's owner.

Codex (and sometimes Claude) answers with links such as
``[findings.txt](/Users/me/project/out/findings.txt:12)``: a bare absolute
path on the host. A client page resolves that against its own origin — the
static web host, the phone — where no such file exists, so the link 404s.

``GET /api/sessions/<id>/file?path=<abs path>`` lets a client fetch the file
through the daemon instead. It is NOT a file browser. A path is served only
when every rule below holds:

  * the caller is authenticated and the session is in their scope (the
    route checks this before calling in here);
  * the session's own transcript links the path in an assistant message —
    the agent put it on screen, so the client is only following a link the
    reader can already see;
  * after symlinks are resolved, the file sits inside the session's working
    folder, so a link (or a symlink) cannot reach outside the project;
  * the path crosses no credential directory (``.ssh``, the daemon's own
    ``.agentremoted``, …), even when the working folder is the home folder;
  * it is a regular file no larger than ``MAX_BYTES``.

Everything else is 404, with no hint of whether the file exists.
"""

from __future__ import annotations

import os
import re
import stat
from urllib.parse import quote, unquote

MAX_BYTES = 10 * 1024 * 1024

# Never served, wherever the working folder is. Matched against each path
# component below the working folder.
_DENY_DIRS = frozenset({
    ".ssh", ".gnupg", ".agentremoted", ".aws", ".azure", ".kube",
    ".docker", ".password-store", "gcloud", "Keychains",
})
_DENY_NAMES = frozenset({".env", ".netrc", ".pgpass", ".git-credentials", ".npmrc",
                         ".pypirc", "id_rsa", "id_ed25519", "id_ecdsa"})

# Editor-style location suffixes the agent appends: file.py:12, file.py:12:3,
# file.py#L12, file.py#L12-L20.
_LOC_SUFFIX = re.compile(r"(?:#L\d+(?:-L?\d+)?|:\d+(?::\d+)?)$")

# Types a browser may render inline. Everything else goes out as text/plain
# (when it decodes) or as a download — never text/html or SVG, which would
# run script in whatever origin opens it.
_INLINE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".pdf": "application/pdf",
}


def normalize(raw: str) -> str:
    """The host path a link points at: decoded, location suffix dropped."""
    p = unquote(str(raw or "")).strip()
    if p.startswith("file://"):
        p = p[len("file://"):]
    p = _LOC_SUFFIX.sub("", p)
    return p


def link_forms(path: str) -> tuple:
    """Spellings of *path* that count as the transcript linking it."""
    forms = {path, quote(path), quote(path, safe="/:")}
    return tuple(f for f in forms if f)


def mentioned(path: str, texts) -> bool:
    """True when any assistant text contains the path (any spelling)."""
    forms = link_forms(path)
    for text in texts:
        if not text:
            continue
        if any(f in text for f in forms):
            return True
    return False


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:
        return False


def resolve(path: str, cwd: str) -> str:
    """Real path of an allowed file, or "" when the rules refuse it."""
    if not path or not os.path.isabs(path) or "\x00" in path:
        return ""
    root = os.path.realpath(os.path.expanduser(str(cwd or "")))
    if not cwd or root == os.path.sep:
        return ""  # no working folder, or "/" — nothing to scope to
    real = os.path.realpath(path)
    if not _inside(real, root) or real == root:
        return ""
    rel = os.path.relpath(real, root)
    parts = rel.split(os.sep)
    if _DENY_DIRS.intersection(parts[:-1]) or parts[-1] in _DENY_NAMES:
        return ""
    # The home's credential folders when the working folder sits above home.
    home = os.path.realpath(os.path.expanduser("~"))
    if _inside(real, home) and real != home:
        hparts = os.path.relpath(real, home).split(os.sep)
        if _DENY_DIRS.intersection(hparts[:-1]) or hparts[-1] in _DENY_NAMES:
            return ""
    try:
        st = os.stat(real)
    except OSError:
        return ""
    if not stat.S_ISREG(st.st_mode):
        return ""
    return real


def content_type(real: str, head: bytes) -> tuple:
    """(Content-Type, inline) for the file at *real* given its first bytes."""
    ext = os.path.splitext(real)[1].lower()
    if ext in _INLINE_TYPES:
        return _INLINE_TYPES[ext], True
    if b"\x00" not in head:
        try:
            head.decode("utf-8")
            return "text/plain; charset=utf-8", True
        except UnicodeDecodeError as e:
            # A multibyte character cut by the sniff window is still text.
            if e.start >= len(head) - 3:
                return "text/plain; charset=utf-8", True
    return "application/octet-stream", False
