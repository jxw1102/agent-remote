"""Where a session's project really sits, for the Changes view.

A session's folder is whatever the person picked when it started, and that
is often a level or two *above* the project: ``~/Developer`` with the work
in ``~/Developer/app``. Measuring the outer folder fails both ways:

* "Since last commit" asks git for the repository that contains the
  folder, and a parent of the repositories is in none.
* "This turn" snapshots the whole folder at the start of every turn, and a
  workspace full of checkouts outruns the snapshot budget, so no mark is
  ever written ("No turn has run in this folder yet").

The agent's own transcript says where the work is: every edit names the
file it writes (``steps.written_paths``). Each written path maps to the git
repository that holds it; the repositories *below* the session folder are
the session's projects, newest edit first. A folder that is itself inside a
repository keeps today's behaviour: its project is the folder.

Known projects are remembered per session (and per folder, for a turn that
starts before the session has an id), so the next turn's snapshot is taken
of the project before the agent touches it.
"""

import json
import logging
import os
import subprocess
import threading
import time

from . import config
from . import shadow

log = logging.getLogger("agentremoted.projects")

MAX_ROOTS = 5
# How much of the transcript to read for written paths: the tail, where the
# current work is. Older edits in another project fall out on their own.
TRANSCRIPT_TAIL = 200
# A folder with nothing edited yet: look this deep for a single repository.
SCAN_DEPTH = 2
SCAN_MAX_DIRS = 400
_SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "build",
              "dist", "target", ".gradle", "Pods", "DerivedData"}

_lock = threading.Lock()
_toplevel_cache = {}        # directory -> (toplevel or "", checked at)
_TOPLEVEL_TTL = 300.0


def _state_path():
    return os.path.join(str(config.CONFIG_DIR), "shadow", "projects.json")


def _load():
    try:
        with open(_state_path()) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("sessions", {})
            data.setdefault("folders", {})
            return data
    except (OSError, ValueError):
        pass
    return {"sessions": {}, "folders": {}}


def _save(data):
    path = _state_path()
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _real(path):
    return os.path.realpath(os.path.expanduser(path))


def _under(path, folder):
    """Whether `path` is `folder` or inside it (both real paths)."""
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


def toplevel(directory):
    """The git work tree that holds `directory`, or "" (cached briefly)."""
    now = time.time()
    with _lock:
        hit = _toplevel_cache.get(directory)
        if hit and now - hit[1] < _TOPLEVEL_TTL:
            return hit[0]
    top = ""
    try:
        out = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "rev-parse",
             "--show-toplevel"],
            cwd=directory, capture_output=True, timeout=5,
            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0",
                     GIT_TERMINAL_PROMPT="0", LC_ALL="C"))
        if out.returncode == 0:
            top = _real(out.stdout.decode("utf-8", "replace").strip())
    except (OSError, subprocess.SubprocessError):
        top = ""
    with _lock:
        if len(_toplevel_cache) > 2000:
            _toplevel_cache.clear()
        _toplevel_cache[directory] = (top, now)
    return top


def _existing_dir(path):
    """`path` if it is a directory, else its nearest existing ancestor."""
    p = path
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            return ""
        p = parent
    return p


def root_for_path(cwd, path):
    """The project folder a written `path` belongs to, seen from `cwd`.

    "" when the path is outside the session folder (an agent's memory file,
    a scratch directory): it says nothing about this session's project.
    """
    cwd = _real(cwd)
    p = path if os.path.isabs(path) else os.path.join(cwd, path)
    p = _real(p)
    if not _under(p, cwd):
        return ""
    d = _existing_dir(p)
    if not d:
        return ""
    top = toplevel(d)
    if top:
        if _under(cwd, top):
            # The session folder is inside this repository already: measure
            # the folder, exactly as before.
            return cwd
        if _under(top, cwd):
            return top
    # No repository. The folder itself, unless it is a workspace of
    # projects (repositories directly under it) or the kind no snapshot can
    # walk; then the first level under it, where a project would sit.
    if not shadow.unwalkable(cwd) and not is_workspace(cwd):
        return cwd
    # From the written path, not the nearest existing folder: a file the
    # turn created (or deleted) still names the folder it belongs to.
    parts = os.path.relpath(p, cwd).split(os.sep)
    if len(parts) < 2 or parts[0] in (".", ""):
        return cwd          # a file directly in the workspace folder
    return os.path.join(cwd, parts[0])


_workspace_cache = {}       # folder -> (bool, checked at)


def is_workspace(cwd):
    """Whether `cwd` holds repositories directly under it (``~/Developer``)."""
    now = time.time()
    with _lock:
        hit = _workspace_cache.get(cwd)
        if hit and now - hit[1] < _TOPLEVEL_TTL:
            return hit[0]
    found = False
    try:
        with os.scandir(cwd) as it:
            for i, e in enumerate(it):
                if i > SCAN_MAX_DIRS:
                    break
                if e.name.startswith(".") or e.name in _SKIP_DIRS:
                    continue
                if e.is_dir(follow_symlinks=False) and \
                        os.path.exists(os.path.join(e.path, ".git")):
                    found = True
                    break
    except OSError:
        found = False
    with _lock:
        _workspace_cache[cwd] = (found, now)
    return found


def written_from_transcript(store, session_id, limit=TRANSCRIPT_TAIL):
    """Written paths in the tail of a session's transcript, oldest first."""
    getter = getattr(store, "get_messages", None)
    if getter is None or not session_id:
        return []
    try:
        res = getter(session_id, None, limit, steps=True)
    except TypeError:
        try:
            res = getter(session_id, offset=None, limit=limit, steps=True)
        except Exception:  # noqa: BLE001
            return []
    except Exception:  # noqa: BLE001 — a provider hiccup costs the hint only
        log.debug("transcript read failed for %s", session_id, exc_info=True)
        return []
    out = []
    for msg in (res or {}).get("messages") or []:
        for st in msg.get("steps") or []:
            for p in st.get("writes") or []:
                if isinstance(p, str):
                    out.append(p)
    return out


def roots_from_paths(cwd, paths):
    """Project folders for `paths` (oldest first), newest edit first."""
    roots = []
    for p in reversed(list(paths)):
        r = root_for_path(cwd, p)
        if r and r not in roots:
            roots.append(r)
            if len(roots) >= MAX_ROOTS:
                break
    return roots


def scan_single_repo(cwd):
    """The one repository within SCAN_DEPTH levels under `cwd`, or "".

    Only for a folder that is not a repository itself and has no edits to go
    by. Two or more repositories is no answer: picking one would be a guess.
    """
    cwd = _real(cwd)
    if shadow.too_big(cwd):
        return ""
    found = []
    seen = 0
    frontier = [(cwd, 0)]
    while frontier:
        d, depth = frontier.pop(0)
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.name in _SKIP_DIRS:
                continue
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            seen += 1
            if seen > SCAN_MAX_DIRS:
                return ""
            if os.path.exists(os.path.join(e.path, ".git")):
                found.append(e.path)
                if len(found) > 1:
                    return ""
            elif depth + 1 < SCAN_DEPTH:
                frontier.append((e.path, depth + 1))
    return found[0] if found else ""


def _cwd_is_project(cwd):
    """A folder inside a repository is its own project (today's behaviour)."""
    return bool(toplevel(cwd))


def remembered(cwd, session_id=""):
    """Project folders remembered for this session (or folder), or []."""
    cwd = _real(cwd)
    data = _load()
    entry = None
    if session_id:
        entry = data["sessions"].get(session_id)
    if not entry:
        entry = data["folders"].get(cwd)
    if not isinstance(entry, dict) or entry.get("cwd", cwd) != cwd:
        return []
    return [r for r in entry.get("roots") or []
            if isinstance(r, str) and os.path.isdir(r) and _under(r, cwd)]


def remember(cwd, session_id, roots):
    cwd = _real(cwd)
    if not roots:
        return
    entry = {"cwd": cwd, "roots": list(roots)[:MAX_ROOTS], "at": time.time()}
    with _lock:
        data = _load()
        if session_id:
            data["sessions"][session_id] = entry
            # Bounded: drop the oldest sessions past a few hundred.
            if len(data["sessions"]) > 300:
                oldest = sorted(data["sessions"].items(),
                                key=lambda kv: kv[1].get("at", 0))
                for k, _v in oldest[:len(data["sessions"]) - 300]:
                    data["sessions"].pop(k, None)
        data["folders"][cwd] = entry
        if len(data["folders"]) > 300:
            oldest = sorted(data["folders"].items(),
                            key=lambda kv: kv[1].get("at", 0))
            for k, _v in oldest[:len(data["folders"]) - 300]:
                data["folders"].pop(k, None)
        _save(data)


def resolve(cwd, session_id="", store=None):
    """The session's project folders, best first. Never empty for a folder.

    Learns from the transcript when a store is given, and remembers what it
    learned so the next turn can snapshot the right folder up front.
    """
    cwd = _real(cwd)
    if _cwd_is_project(cwd):
        return [cwd]
    roots = []
    if store is not None and session_id:
        roots = roots_from_paths(cwd, written_from_transcript(store, session_id))
        if roots:
            remember(cwd, session_id, roots)
    if not roots:
        roots = remembered(cwd, session_id)
    if not roots:
        single = scan_single_repo(cwd)
        if single:
            roots = [single]
    return roots or [cwd]


def learn(cwd, session_id, store):
    """After a turn: re-read where it wrote, for the next turn's snapshot."""
    if not cwd or not session_id:
        return
    try:
        resolve(cwd, session_id, store)
    except Exception:  # noqa: BLE001 — a hint, never a failure
        log.debug("project learn failed for %s", cwd, exc_info=True)


def mark_turn(cwd, session_id="", store=None, budget=None):
    """Snapshot each project folder of this session as a turn begins.

    Uses what is already known (remembered roots, or the folder itself when
    it is a repository) and reads the transcript only when nothing is known
    yet. The budget is shared: a slow project costs the ones after it their
    mark, never the turn more than `budget` seconds.
    """
    if not cwd:
        return []
    budget = shadow.TURN_BUDGET if budget is None else budget
    real = _real(cwd)
    if _cwd_is_project(real):
        roots = [real]
    else:
        roots = remembered(real, session_id)
        if not roots:
            roots = resolve(real, session_id, store)
    deadline = time.time() + budget
    marked = []
    for r in roots:
        left = deadline - time.time()
        if left <= 0.5:
            break
        if shadow.mark_turn(r, budget=left):
            marked.append(r)
    return marked
