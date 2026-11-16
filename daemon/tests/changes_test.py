"""The Changes view's data: a session folder's working tree, read through git.

Run:  python3 tests/changes_test.py
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted import changes  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                   capture_output=True)


def write(root, name, text):
    path = os.path.join(root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def main():
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
    os.environ["GIT_CONFIG_SYSTEM"] = os.devnull
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="agentremoted-changes-"))

    print("not a repository:")
    plain = os.path.join(tmp, "plain")
    os.makedirs(plain)
    check("says so", changes.collect(plain) == {"repo": False})

    print("no commit yet:")
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    git(repo, "init", "-q", "-b", "main")
    write(repo, "a.txt", "one\n")
    got = changes.collect(repo)
    check("is a repo on main", got.get("repo") and got.get("branch") == "main", got)
    check("the new file is untracked",
          [(f["path"], f["status"]) for f in got["files"]] == [("a.txt", "untracked")])

    git(repo, "add", "a.txt")
    write(repo, "src/keep.py", "x = 1\ny = 2\n")
    write(repo, "gone.txt", "bye\n")
    write(repo, "old name.txt", "same\ncontent\nhere\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "first")

    print("clean tree:")
    got = changes.collect(repo)
    check("no files, empty diff", got["files"] == [] and got["diff"] == "", got)

    print("a mix of changes:")
    write(repo, "src/keep.py", "x = 1\ny = 3\nz = 4\n")           # modified
    os.remove(os.path.join(repo, "gone.txt"))                      # deleted
    git(repo, "mv", "old name.txt", "new name.txt")                # renamed, staged
    write(repo, "notes/new file.md", "# hi\n")                     # untracked, in a new folder
    with open(os.path.join(repo, "blob.bin"), "wb") as fh:
        fh.write(bytes(range(256)) * 4)
    git(repo, "add", "blob.bin")                                   # binary, staged
    got = changes.collect(repo)
    by = {f["path"]: f for f in got["files"]}
    check("modified file with line counts",
          by.get("src/keep.py", {}).get("status") == "modified"
          and (by["src/keep.py"]["added"], by["src/keep.py"]["removed"]) == (2, 1),
          by.get("src/keep.py"))
    check("deleted file", by.get("gone.txt", {}).get("status") == "deleted", by.get("gone.txt"))
    check("rename keeps both names, spaces intact",
          by.get("new name.txt", {}).get("status") == "renamed"
          and by["new name.txt"].get("from") == "old name.txt"
          and by["new name.txt"]["staged"] is True, by.get("new name.txt"))
    check("untracked file inside a new folder is listed by path",
          by.get("notes/new file.md", {}).get("status") == "untracked", sorted(by))
    check("binary file is flagged, not counted",
          by.get("blob.bin", {}).get("binary") is True and by["blob.bin"]["added"] == 0,
          by.get("blob.bin"))
    check("totals add up, the new file's line included",
          (got["added"], got["removed"]) == (3, 2), (got["added"], got["removed"]))
    check("a new file is shown as a diff against nothing",
          by["notes/new file.md"]["added"] == 1
          and "diff --git a/notes/new file.md b/notes/new file.md" in got["diff"]
          and "+# hi" in got["diff"], got["diff"][-300:])
    check("nothing to publish to without an origin", got["can_publish"] is False)
    check("diff covers staged and unstaged together",
          "+z = 4" in got["diff"] and "-bye" in got["diff"]
          and "rename to new name.txt" in got["diff"], got["diff"][:400])
    check("not truncated", got["truncated"] is False)

    print("from a subfolder:")
    sub = changes.collect(os.path.join(repo, "src"))
    check("same repository root", sub.get("root") == repo, sub.get("root"))
    check("paths stay relative to the root",
          "src/keep.py" in [f["path"] for f in sub["files"]])

    print("the repository cannot run programs through this:")
    marker = os.path.join(tmp, "ran")
    hook = os.path.join(tmp, "hook.sh")
    with open(hook, "w") as fh:
        fh.write("#!/bin/sh\ntouch '%s'\nexit 0\n" % marker)
    os.chmod(hook, 0o755)
    git(repo, "config", "diff.external", hook)
    git(repo, "config", "core.fsmonitor", hook)
    git(repo, "config", "diff.conv.textconv", hook)
    write(repo, ".gitattributes", "*.py diff=conv\n")
    changes.collect(repo)
    check("external diff, textconv and fsmonitor were not run",
          not os.path.exists(marker))

    print("a large diff is cut at a line boundary:")
    write(repo, "big.txt", "start\n")
    git(repo, "add", "big.txt")
    git(repo, "commit", "-q", "-m", "big", "--", "big.txt")
    write(repo, "big.txt", "".join("line %06d of a long file\n" % n for n in range(40000)))
    got = changes.collect(repo)
    check("flagged as truncated", got["truncated"] is True)
    check("within the cap, ends on a whole line",
          len(got["diff"].encode()) <= changes.MAX_DIFF_BYTES
          and got["diff"].splitlines()[-1].startswith(("+line", "+", "-", " ", "@")),
          len(got["diff"]))
    check("file list still complete",
          any(f["path"] == "big.txt" and f["added"] == 40000 for f in got["files"]))

    print("publishing a branch:")
    bare = os.path.join(tmp, "origin.git")
    git(tmp, "init", "-q", "--bare", bare)
    pub = os.path.join(tmp, "pub")
    os.makedirs(pub)
    git(pub, "init", "-q", "-b", "main")
    write(pub, "a.txt", "one\n")
    check("no commit yet: nothing to publish",
          changes.collect(pub)["can_publish"] is False)
    git(pub, "add", "-A")
    git(pub, "commit", "-q", "-m", "first")
    check("a branch with commits but no origin cannot be published",
          changes.collect(pub)["can_publish"] is False)
    git(pub, "remote", "add", "origin", bare)
    check("with an origin and no upstream it can",
          changes.collect(pub)["can_publish"] is True)
    git(pub, "push", "-q", "-u", "origin", "main")
    got = changes.collect(pub)
    check("once it has an upstream there is nothing to publish",
          got["can_publish"] is False and got["upstream"] == "origin/main", got)
    git(pub, "checkout", "-q", "--detach")
    got = changes.collect(pub)
    check("a detached HEAD cannot be published",
          got["can_publish"] is False and got["branch"] == "HEAD", got)

    print("git that cannot answer:")
    real = changes._git

    def slow(cwd, args, timeout=changes.GIT_TIMEOUT):
        return (-2, b"") if args and args[0] == "status" else real(cwd, args, timeout)

    changes._git = slow
    try:
        try:
            changes.collect(repo)
            check("a slow status is an error, not 'not a repository'", False)
        except changes.ChangesError as e:
            check("a slow status is an error, not 'not a repository'", "status" in str(e), e)
    finally:
        changes._git = real

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        for name in failures:
            print("  - " + name)
        sys.exit(1)
    print("all ok")


if __name__ == "__main__":
    main()
