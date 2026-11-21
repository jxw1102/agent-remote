"""The "This turn" scope: snapshots taken beside a folder, not in it.

Three things have to hold, and each one is checked against a real git:

1. It answers in a folder that is **not** a repository at all.
2. It answers *per turn* in a repository — the turn's own diff, not the
   whole uncommitted pile.
3. It leaves the project's repository byte-identical: same HEAD, same
   index, same `git status`, same stash, nothing added to the work tree.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = tempfile.mkdtemp(prefix="agentremoted-shadow-home-")
os.environ["AGENTREMOTED_HOME"] = HOME

from agentremoted import changes, shadow  # noqa: E402


def git(cwd, *args, **kw):
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "T")
    env.setdefault("GIT_AUTHOR_EMAIL", "t@example.invalid")
    env.setdefault("GIT_COMMITTER_NAME", "T")
    env.setdefault("GIT_COMMITTER_EMAIL", "t@example.invalid")
    out = subprocess.run(["git"] + list(args), cwd=cwd, env=env,
                         capture_output=True, **kw)
    return out.returncode, out.stdout.decode("utf-8", "replace")


def write(root, rel, text):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


class ShadowTurnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentremoted-shadow-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- 1. a folder that is not a repository ---------------------------

    def test_plain_folder_has_no_commit_scope_but_has_a_turn(self):
        work = os.path.join(self.tmp, "plain")
        os.makedirs(work)
        write(work, "a.txt", "one\n")

        # The commit scope cannot answer here: that is the whole problem.
        self.assertEqual(changes.collect(work), {"repo": False})

        # Before any turn there is nothing to measure from, and it says so
        # rather than showing an empty diff.
        first = changes.collect_turn(work)
        self.assertFalse(first["available"])
        self.assertIn("No turn has run", first["reason"])

        shadow.mark_turn(work)
        write(work, "a.txt", "one\ntwo\n")
        write(work, "sub/b.txt", "new file\n")

        got = changes.collect_turn(work)
        self.assertTrue(got["available"], got.get("reason"))
        self.assertEqual(got["scope"], "turn")
        paths = sorted(f["path"] for f in got["files"])
        self.assertEqual(paths, ["a.txt", "sub/b.txt"])
        by_path = {f["path"]: f for f in got["files"]}
        self.assertEqual(by_path["a.txt"]["status"], "modified")
        self.assertEqual(by_path["sub/b.txt"]["status"], "added")
        self.assertEqual(by_path["a.txt"]["added"], 1)
        self.assertIn("+two", got["diff"])
        self.assertIn("+new file", got["diff"])
        # Commit and Push act on a repository this folder does not have.
        self.assertFalse(got["can_publish"])

    def test_deleted_file_is_reported(self):
        work = os.path.join(self.tmp, "del")
        os.makedirs(work)
        write(work, "gone.txt", "bye\n")
        shadow.mark_turn(work)
        os.remove(os.path.join(work, "gone.txt"))
        got = changes.collect_turn(work)
        self.assertEqual([f["status"] for f in got["files"]], ["deleted"])

    def test_nothing_changed_is_an_empty_answer_not_an_error(self):
        work = os.path.join(self.tmp, "quiet")
        os.makedirs(work)
        write(work, "a.txt", "one\n")
        shadow.mark_turn(work)
        got = changes.collect_turn(work)
        self.assertTrue(got["available"])
        self.assertEqual(got["files"], [])
        self.assertEqual(got["diff"], "")

    # -- 2. per turn inside a repository --------------------------------

    def test_turn_scope_is_narrower_than_the_commit_scope(self):
        work = os.path.join(self.tmp, "repo")
        os.makedirs(work)
        git(work, "init", "-q")
        write(work, "tracked.txt", "base\n")
        git(work, "add", "-A")
        git(work, "-c", "commit.gpgsign=false", "commit", "-qm", "base")

        # Work from an earlier turn, already in the tree, not committed.
        write(work, "earlier.txt", "from a previous turn\n")

        shadow.mark_turn(work)                       # this turn starts here
        write(work, "tracked.txt", "base\nthis turn\n")

        turn = changes.collect_turn(work)
        self.assertEqual([f["path"] for f in turn["files"]], ["tracked.txt"])
        self.assertNotIn("earlier.txt", turn["diff"])

        commit_scope = changes.collect(work)
        self.assertEqual(
            sorted(f["path"] for f in commit_scope["files"]),
            ["earlier.txt", "tracked.txt"])

    def test_gitignore_of_the_project_is_honoured(self):
        work = os.path.join(self.tmp, "ignored")
        os.makedirs(work)
        git(work, "init", "-q")
        write(work, ".gitignore", "build/\n")
        write(work, "src.txt", "code\n")
        git(work, "add", "-A")
        git(work, "-c", "commit.gpgsign=false", "commit", "-qm", "base")

        shadow.mark_turn(work)
        write(work, "build/output.bin", "generated\n")
        write(work, "src.txt", "code\nmore\n")

        got = changes.collect_turn(work)
        self.assertEqual([f["path"] for f in got["files"]], ["src.txt"])

    def test_built_in_excludes_apply_without_a_gitignore(self):
        work = os.path.join(self.tmp, "noignore")
        os.makedirs(work)
        write(work, "src.txt", "code\n")
        shadow.mark_turn(work)
        write(work, "node_modules/left-pad/index.js", "module.exports=1\n")
        write(work, "src.txt", "code\nmore\n")

        got = changes.collect_turn(work)
        self.assertEqual([f["path"] for f in got["files"]], ["src.txt"])

    # -- 3. the project's own git is untouched --------------------------

    def test_the_projects_repository_is_left_exactly_as_it_was(self):
        work = os.path.join(self.tmp, "untouched")
        os.makedirs(work)
        git(work, "init", "-q")
        write(work, "a.txt", "base\n")
        git(work, "add", "-A")
        git(work, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
        # A half-staged tree: the state most likely to be disturbed.
        write(work, "a.txt", "base\nedited\n")
        write(work, "staged.txt", "staged\n")
        git(work, "add", "staged.txt")
        write(work, "untracked.txt", "untracked\n")

        before_head = git(work, "rev-parse", "HEAD")[1]
        before_status = git(work, "status", "--porcelain=v1", "-b")[1]
        before_tree = sorted(os.listdir(work))
        before_reflog = git(work, "reflog", "--all")[1]
        # Last, and read again first below: `git status` itself rewrites the
        # index (it writes back refreshed stat information), so a stat taken
        # with a status call in between would be measuring the test.
        before_index = os.stat(os.path.join(work, ".git", "index"))

        shadow.mark_turn(work)
        write(work, "a.txt", "base\nedited\nmore\n")
        got = changes.collect_turn(work)
        self.assertTrue(got["available"])

        after_index = os.stat(os.path.join(work, ".git", "index"))
        self.assertEqual(after_index.st_mtime, before_index.st_mtime)
        self.assertEqual(after_index.st_size, before_index.st_size)

        self.assertEqual(git(work, "rev-parse", "HEAD")[1], before_head)
        self.assertEqual(git(work, "reflog", "--all")[1], before_reflog)
        # One more line in an already-modified file: same status, letter
        # for letter.
        after_status = git(work, "status", "--porcelain=v1", "-b")[1]
        self.assertEqual(after_status, before_status)
        # Nothing was added to the folder, in particular no second .git.
        self.assertEqual(sorted(os.listdir(work)), before_tree)
        # And the snapshots really did go somewhere else.
        self.assertTrue(os.path.isdir(os.path.join(HOME, "shadow")))

    def test_no_commit_objects_are_created(self):
        """No commits means no identity to invent and nothing to sign."""
        work = os.path.join(self.tmp, "nocommits")
        os.makedirs(work)
        write(work, "a.txt", "one\n")
        shadow.mark_turn(work)
        git_dir = shadow._git_dir(work)
        code, out = git(git_dir, "--git-dir", git_dir, "rev-list", "--all")
        self.assertEqual(out.strip(), "")
        self.assertFalse(os.path.exists(os.path.join(git_dir, "refs", "heads",
                                                     "master")))

    # -- bookkeeping -----------------------------------------------------

    def test_each_turn_moves_the_mark(self):
        work = os.path.join(self.tmp, "turns")
        os.makedirs(work)
        write(work, "a.txt", "one\n")

        shadow.mark_turn(work)
        write(work, "a.txt", "one\ntwo\n")
        self.assertIn("+two", changes.collect_turn(work)["diff"])

        # The next turn starts from where the last one ended.
        shadow.mark_turn(work)
        write(work, "a.txt", "one\ntwo\nthree\n")
        second = changes.collect_turn(work)
        self.assertIn("+three", second["diff"])
        self.assertNotIn("+two", second["diff"])

    def test_since_is_when_the_turn_started(self):
        work = os.path.join(self.tmp, "when")
        os.makedirs(work)
        write(work, "a.txt", "one\n")
        before = time.time()
        shadow.mark_turn(work)
        got = changes.collect_turn(work)
        self.assertGreaterEqual(got["since"], before)
        self.assertLessEqual(got["since"], time.time())

    def test_home_and_root_are_refused(self):
        """`add -A` on $HOME would walk everything the user owns."""
        self.assertEqual(shadow.snapshot(os.path.expanduser("~")), "")
        self.assertEqual(shadow.snapshot("/"), "")
        self.assertIsNone(shadow.base_for(os.path.expanduser("~")))


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(HOME, ignore_errors=True)
