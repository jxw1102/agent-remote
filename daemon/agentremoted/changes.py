"""What the agent changed: a session's folder, via git.

Read-only. This is the "review before you answer" view: which files differ,
by how much, and the diff itself. Nothing here writes to the repository;
commit and push stay explicit actions the person takes.

Two scopes answer two different questions:

* ``collect`` — **since the last commit**. The working tree against HEAD,
  the way `git status` sees it. Needs a repository.
* ``collect_turn`` — **since this turn started**. A diff between two
  snapshots taken in a shadow repository beside the folder (see
  ``shadow.py``), so it works in a folder that was never `git init`-ed, and
  in a repository it does not drown one turn in ten turns of uncommitted
  work.

Everything goes through `git` with a fixed argument list (no shell), with the
repository's own hooks into diffing switched off: an external diff driver, a
textconv filter or an fsmonitor command are all programs named by the repo's
config, and looking at a diff should not run them.

Not switched off: clean filters (`filter.<name>.clean` / `.process`, which is
how git-lfs and git-crypt work). git runs them to tell whether a file
differs, and without them every filtered file would read as modified. They
are the same programs any `git status` in that folder already runs.
"""

import os
import subprocess
import threading
import time

from . import shadow

# One response must stay small enough for a phone on a bad link.
MAX_DIFF_BYTES = 400 * 1024
MAX_FILES = 500
GIT_TIMEOUT = 10
# New files have no diff against the last commit, so they are read one by
# one; this many at most, and none larger than this.
MAX_UNTRACKED_DIFFS = 40
MAX_UNTRACKED_BYTES = 200 * 1024
UNTRACKED_BUDGET_S = 8.0


class ChangesError(Exception):
    """git could not answer (too slow, or failed): not the same as "not a repo"."""

_SAFE = ["-c", "core.fsmonitor=false", "-c", "core.quotepath=false",
         "-c", "diff.external=", "-c", "color.ui=false"]

_STATUS_WORDS = {
    "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
    "C": "copied", "U": "conflicted", "T": "type changed", "?": "untracked",
}


def _git(cwd, args, timeout=GIT_TIMEOUT):
    """(returncode, stdout bytes). returncode -1 when git could not be run."""
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"      # never take the index lock
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    try:
        out = subprocess.run(["git"] + _SAFE + args, cwd=cwd, env=env,
                             capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -2, b""
    except (OSError, subprocess.SubprocessError):
        return -1, b""
    return out.returncode, out.stdout


def _git_capped(cwd, args, cap, timeout=GIT_TIMEOUT):
    """(returncode, stdout bytes, cut). Reads at most `cap` + one more line.

    A diff can be hundreds of megabytes (a regenerated lockfile, a vendored
    tree). It used to be read whole and then cut down to the cap; now git is
    stopped as soon as there is enough to show.
    """
    env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    try:
        proc = subprocess.Popen(["git"] + _SAFE + args, cwd=cwd, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return -1, b"", False
    return _read_capped(proc, cap, timeout)


def _read_capped(proc, cap, timeout):
    """Drain a diff process, stopping it once there is more than `cap`."""
    timer = threading.Timer(timeout, proc.kill)
    timer.daemon = True
    timer.start()
    chunks, size, cut = [], 0, False
    try:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > cap:
                cut = True
                proc.kill()
                break
    finally:
        timer.cancel()
        proc.stdout.close()
        code = proc.wait()
    return (0 if cut else code), b"".join(chunks), cut


def _untracked_diff(root, path):
    """(diff bytes, added lines, binary) for a file git does not track yet.

    Compared with nothing, the way `git diff` would show it once added. This
    reads the file and runs no filter on it.
    """
    full = os.path.join(root, path)
    try:
        if os.path.islink(full) or not os.path.isfile(full) \
                or os.path.getsize(full) > MAX_UNTRACKED_BYTES:
            return b"", 0, False
    except OSError:
        return b"", 0, False
    # Exit code 1 means "they differ", which is the point.
    code, out = _git(root, ["diff", "--no-index", "--no-color", "--no-ext-diff",
                            "--no-textconv", "--", os.devnull, path], timeout=5)
    if code not in (0, 1) or not out:
        return b"", 0, False
    if b"\nBinary files " in out or out.startswith(b"Binary files "):
        return out, 0, True
    added = 0
    in_hunk = False
    for line in out.split(b"\n"):
        if line.startswith(b"@@"):
            in_hunk = True
        elif in_hunk and line.startswith(b"+"):
            added += 1
    return out, added, False


def _text(raw):
    return raw.decode("utf-8", errors="replace")


def _parse_branch(line):
    """'## main...origin/main [ahead 1, behind 2]' -> (branch, upstream, a, b)."""
    body = line[3:].strip()
    ahead = behind = 0
    if body.endswith("]") and " [" in body:
        body, _, marks = body.rpartition(" [")
        for part in marks.rstrip("]").split(","):
            word, _, num = part.strip().partition(" ")
            if word == "ahead" and num.isdigit():
                ahead = int(num)
            elif word == "behind" and num.isdigit():
                behind = int(num)
    if body.startswith("No commits yet on "):
        return body[len("No commits yet on "):], "", 0, 0
    if body.startswith("HEAD (no branch)"):
        return "HEAD", "", 0, 0
    branch, _, upstream = body.partition("...")
    return branch, upstream, ahead, behind


def collect(cwd):
    """The working-tree changes under `cwd`, as a JSON-ready dict.

    {"repo": False} when the folder is not inside a git work tree (or git is
    missing): the client says so instead of showing an empty diff. Raises
    ChangesError when it is a repository but git could not answer, so a slow
    repository is never reported as "not a repository".
    """
    code, out = _git(cwd, ["rev-parse", "--show-toplevel"], timeout=5)
    if code == -2:
        raise ChangesError("git did not answer in time")
    if code != 0:
        return {"repo": False}
    root = _text(out).strip()

    code, out = _git(cwd, ["status", "--porcelain=v1", "--branch", "-z",
                           "--untracked-files=all"])
    if code == -2:
        raise ChangesError(
            "git status took longer than %d s in this folder" % GIT_TIMEOUT)
    if code != 0:
        raise ChangesError("git status failed in this folder")
    branch, upstream, ahead, behind = "", "", 0, 0
    files = {}
    order = []
    fields = _text(out).split("\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if not entry:
            continue
        if entry.startswith("## "):
            branch, upstream, ahead, behind = _parse_branch(entry)
            continue
        if len(entry) < 4:
            continue
        xy, path = entry[:2], entry[3:]
        old = ""
        if xy[0] in "RC" or xy[1] in "RC":
            # -z puts the original path in the next field.
            old = fields[i] if i < len(fields) else ""
            i += 1
        code_letter = "?" if xy == "??" else (
            "U" if "U" in xy or xy in ("AA", "DD") else
            (xy[0] if xy[0] != " " else xy[1]))
        files[path] = {
            "path": path,
            "status": _STATUS_WORDS.get(code_letter, "changed"),
            "staged": xy[0] not in (" ", "?"),
            "added": 0, "removed": 0, "binary": False,
        }
        if old:
            files[path]["from"] = old
        order.append(path)

    # Compare with the last commit, so staged and unstaged read as one change.
    # A repository with no commit yet has no HEAD to compare with.
    has_head = _git(cwd, ["rev-parse", "--verify", "-q", "HEAD"], timeout=5)[0] == 0
    base = ["HEAD"] if has_head else ["--cached"]
    code, out = _git(cwd, ["diff", "--numstat", "-z", "-M", "--no-ext-diff",
                           "--no-textconv"] + base)
    if code == 0:
        parts = _text(out).split("\0")
        j = 0
        while j < len(parts):
            row = parts[j]
            j += 1
            if not row:
                continue
            cols = row.split("\t")
            if len(cols) < 3:
                continue
            added, removed, path = cols[0], cols[1], cols[2]
            if path == "":
                # Rename: "<a>\t<r>\t" then old path, then new path.
                path = parts[j + 1] if j + 1 < len(parts) else ""
                j += 2
            info = files.get(path)
            if info is None:
                continue
            if added == "-":
                info["binary"] = True
            else:
                info["added"] = int(added) if added.isdigit() else 0
                info["removed"] = int(removed) if removed.isdigit() else 0

    code, out, cut = _git_capped(cwd, ["diff", "--no-color", "-M", "--no-ext-diff",
                                       "--no-textconv"] + base, MAX_DIFF_BYTES)
    diff = out if code == 0 else b""
    # Files git does not track yet are part of what a commit would take
    # (the clients' Commit stages everything), so they are shown too: each
    # one as a diff against nothing, while there is room.
    read = 0
    give_up = time.time() + UNTRACKED_BUDGET_S
    for path in order:
        info = files[path]
        if info["status"] != "untracked":
            continue
        # A git process per file: bounded by count, by the room left in the
        # diff, and by the clock, so the request answers while the client
        # is still waiting for it.
        if (read >= MAX_UNTRACKED_DIFFS or len(diff) > MAX_DIFF_BYTES
                or time.time() > give_up):
            break
        read += 1
        # status lists paths from the top of the work tree.
        part, added, binary = _untracked_diff(root, path)
        info["added"] = added
        info["binary"] = binary
        if part and not binary:
            diff += (b"" if not diff or diff.endswith(b"\n") else b"\n") + part

    # Publishing only makes sense for a real branch with commits, no upstream
    # yet, and an `origin` to publish to.
    can_publish = bool(
        has_head and branch and branch != "HEAD" and not upstream
        and _git(cwd, ["remote", "get-url", "origin"], timeout=5)[0] == 0)

    truncated = len(diff) > MAX_DIFF_BYTES
    if truncated:
        cut = diff.rfind(b"\n", 0, MAX_DIFF_BYTES)
        diff = diff[:cut if cut > 0 else MAX_DIFF_BYTES]

    listed = [files[p] for p in order[:MAX_FILES]]
    return {
        "repo": True,
        "root": root,
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "can_publish": can_publish,
        "files": listed,
        "more_files": max(0, len(order) - MAX_FILES),
        "added": sum(f["added"] for f in files.values()),
        "removed": sum(f["removed"] for f in files.values()),
        "diff": _text(diff),
        "truncated": truncated,
    }


# --------------------------------------------------------------- this turn

def _parse_numstat(raw, files, order):
    """`diff --numstat -z` into the per-file counters `files` already holds."""
    parts = _text(raw).split("\0")
    j = 0
    while j < len(parts):
        row = parts[j]
        j += 1
        if not row:
            continue
        cols = row.split("\t")
        if len(cols) < 3:
            continue
        added, removed, path = cols[0], cols[1], cols[2]
        if path == "":
            # Rename: "<a>\t<r>\t" then the old path, then the new one.
            path = parts[j + 1] if j + 1 < len(parts) else ""
            j += 2
        info = files.get(path)
        if info is None:
            info = {"path": path, "status": "changed", "staged": False,
                    "added": 0, "removed": 0, "binary": False}
            files[path] = info
            order.append(path)
        if added == "-":
            info["binary"] = True
        else:
            info["added"] = int(added) if added.isdigit() else 0
            info["removed"] = int(removed) if removed.isdigit() else 0


def _parse_name_status(raw):
    """`diff --name-status -z` -> ({path: info}, [path]) in git's order."""
    files, order = {}, []
    parts = _text(raw).split("\0")
    i = 0
    while i < len(parts):
        code = parts[i]
        i += 1
        if not code:
            continue
        letter = code[0]
        path = parts[i] if i < len(parts) else ""
        i += 1
        old = ""
        if letter in ("R", "C"):
            # Rename / copy: the score is on the letter, then old, then new.
            old, path = path, (parts[i] if i < len(parts) else "")
            i += 1
        if not path:
            continue
        files[path] = {
            "path": path,
            "status": _STATUS_WORDS.get(letter, "changed"),
            # Nothing is "staged" in this scope: the shadow index is ours,
            # and the project's index is none of our business.
            "staged": False,
            "added": 0, "removed": 0, "binary": False,
        }
        if old:
            files[path]["from"] = old
        order.append(path)
    return files, order


def collect_turn(cwd):
    """What changed under `cwd` since the last turn started, as a dict.

    Same shape as [collect] so a client renders it with the same code, plus
    ``scope`` and ``since``. ``available: False`` (with a ``reason``) when
    there is no mark to measure from — a folder no turn has run in since the
    daemon started, one too large to snapshot, or a machine without git.
    """
    def unavailable(reason):
        return {"repo": False, "scope": "turn", "available": False,
                "reason": reason, "files": [], "diff": "",
                "added": 0, "removed": 0, "more_files": 0,
                "truncated": False, "can_publish": False}

    base = shadow.base_for(cwd)
    if base is None:
        return unavailable(
            "No turn has run in this folder yet. The next one is measured "
            "from where the folder stands when it starts.")
    head = shadow.snapshot(cwd)
    if not head:
        return unavailable(
            "This folder could not be snapshotted — too large, or git "
            "could not read it.")
    base_tree = str(base.get("tree") or "")

    root = os.path.realpath(cwd)
    payload = {
        "repo": True,
        "scope": "turn",
        "available": True,
        "since": float(base.get("at") or 0.0),
        "root": root,
        "branch": "", "upstream": "", "ahead": 0, "behind": 0,
        # Commit and Push act on the project's repository and would take in
        # more than this diff shows, so they are not offered in this scope.
        "can_publish": False,
    }
    if head == base_tree:
        payload.update({"files": [], "more_files": 0, "added": 0,
                        "removed": 0, "diff": "", "truncated": False})
        return payload

    code, out = shadow.diff_args(cwd, base_tree,
                                 ["--name-status", "-z", "-M",
                                  "--no-ext-diff", "--no-textconv",
                                  base_tree, head])
    if code == -2:
        raise ChangesError("the snapshot diff took too long in this folder")
    if code != 0:
        raise ChangesError("could not diff this folder's snapshots")
    files, order = _parse_name_status(out)

    code, out = shadow.diff_args(cwd, base_tree,
                                 ["--numstat", "-z", "-M", "--no-ext-diff",
                                  "--no-textconv", base_tree, head])
    if code == 0:
        _parse_numstat(out, files, order)

    proc = shadow.diff_popen(cwd, ["--no-color", "-M", "--no-ext-diff",
                                   "--no-textconv", base_tree, head])
    diff = b""
    if proc is not None:
        code, out, _cut = _read_capped(proc, MAX_DIFF_BYTES, GIT_TIMEOUT)
        if code == 0:
            diff = out

    truncated = len(diff) > MAX_DIFF_BYTES
    if truncated:
        cut = diff.rfind(b"\n", 0, MAX_DIFF_BYTES)
        diff = diff[:cut if cut > 0 else MAX_DIFF_BYTES]

    payload.update({
        "files": [files[p] for p in order[:MAX_FILES]],
        "more_files": max(0, len(order) - MAX_FILES),
        "added": sum(f["added"] for f in files.values()),
        "removed": sum(f["removed"] for f in files.values()),
        "diff": _text(diff),
        "truncated": truncated,
    })
    return payload
