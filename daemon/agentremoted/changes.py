"""What the agent changed: the working tree of a session's folder, via git.

Read-only. This is the "review before you answer" view: which files differ
from the last commit, by how much, and the diff itself. Nothing here writes
to the repository; commit and push stay explicit actions the person takes.

Everything goes through `git` with a fixed argument list (no shell), with the
repository's own hooks into diffing switched off: an external diff driver, a
textconv filter or an fsmonitor command are all programs named by the repo's
config, and looking at a diff must not run them.
"""

import os
import subprocess

# One response must stay small enough for a phone on a bad link.
MAX_DIFF_BYTES = 400 * 1024
MAX_FILES = 500
GIT_TIMEOUT = 10

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
    except (OSError, subprocess.SubprocessError):
        return -1, b""
    return out.returncode, out.stdout


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
    missing): the client says so instead of showing an empty diff.
    """
    code, out = _git(cwd, ["rev-parse", "--show-toplevel"], timeout=5)
    if code != 0:
        return {"repo": False}
    root = _text(out).strip()

    code, out = _git(cwd, ["status", "--porcelain=v1", "--branch", "-z",
                           "--untracked-files=all"])
    if code != 0:
        return {"repo": False}
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

    code, out = _git(cwd, ["diff", "--no-color", "-M", "--no-ext-diff",
                           "--no-textconv"] + base)
    diff = out if code == 0 else b""
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
        "files": listed,
        "more_files": max(0, len(order) - MAX_FILES),
        "added": sum(f["added"] for f in files.values()),
        "removed": sum(f["removed"] for f in files.values()),
        "diff": _text(diff),
        "truncated": truncated,
    }
