"""Finding the project a session really works in (projects.py).

A session started in a folder *above* its repositories (``~/Developer``)
must still answer both Changes scopes, measured in the repository the agent
wrote in. Checked against real git repositories in a temporary folder.
"""

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = tempfile.mkdtemp(prefix="agentremoted-projects-home-")
os.environ["AGENTREMOTED_HOME"] = HOME

from agentremoted import changes, projects, shadow, steps  # noqa: E402


def git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@example.invalid")
    subprocess.run(["git", "-c", "commit.gpgsign=false"] + list(args), cwd=cwd,
                   env=env, capture_output=True, check=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def make_repo(path):
    write(os.path.join(path, "README"), "hello\n")
    git(path, "init", "-q")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")


class FakeStore:
    """A provider store whose transcript wrote `paths`, oldest first."""

    def __init__(self, paths):
        self.paths = paths

    def get_messages(self, session_id, offset=None, limit=50, steps=False):
        return {"messages": [{"role": "assistant", "steps": [
            {"kind": "tool_use", "writes": [p]} for p in self.paths]}]}


class WrittenPaths(unittest.TestCase):
    def test_edit_and_write_shapes(self):
        self.assertEqual(steps.written_paths(
            {"file_path": "/a/x.py", "old_string": "a", "new_string": "b"}),
            ["/a/x.py"])
        self.assertEqual(steps.written_paths(
            {"variant": "SearchReplace", "file_path": "/a/y", "old_string": "",
             "new_string": "z"}), ["/a/y"])
        self.assertEqual(steps.written_paths(
            {"variant": "Write", "file_path": "/a/w", "content": "z"}), ["/a/w"])
        self.assertEqual(steps.written_paths(
            {"path": "/a/c", "old_str": "1", "new_str": "2"}), ["/a/c"])
        self.assertEqual(steps.written_paths(
            {"file_path": "/a/m", "edits": [
                {"old_string": "a", "new_string": "b"},
                {"file_path": "/a/n", "old_string": "c", "new_string": "d"}]}),
            ["/a/m", "/a/n"])

    def test_reads_write_nothing(self):
        self.assertEqual(steps.written_paths(
            {"variant": "ReadFile", "target_file": "/a/r"}), [])
        self.assertEqual(steps.written_paths({"pattern": "x", "path": "/a"}), [])
        self.assertEqual(steps.written_paths('{"command": "ls"}'), [])

    def test_patch_and_workdir(self):
        patch = ("*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n"
                 "*** Add File: /abs/b.txt\n+new\n*** End Patch\n")
        self.assertEqual(steps.written_paths(patch), ["src/a.py", "/abs/b.txt"])
        self.assertEqual(steps.written_paths({"input": patch}),
                         ["src/a.py", "/abs/b.txt"])
        self.assertEqual(steps.written_paths(
            {"command": ["bash", "-lc", "make"], "workdir": "/w/app"}),
            ["/w/app"])

    def test_tool_use_step_carries_writes(self):
        st = steps.tool_use("r", "", "Edit", "", "diff",
                            raw={"file_path": "/a/x", "old_string": "a",
                                 "new_string": "b"})
        self.assertEqual(st["writes"], ["/a/x"])
        self.assertNotIn("writes", steps.tool_use("r", "", "Read", "", "",
                                                  raw={"file_path": "/a/x"}))


class Projects(unittest.TestCase):
    def setUp(self):
        self.ws = os.path.realpath(tempfile.mkdtemp(prefix="ws-"))
        self.app = os.path.join(self.ws, "app")
        self.lib = os.path.join(self.ws, "group", "lib")
        make_repo(self.app)
        make_repo(self.lib)

    def test_paths_map_to_their_repository(self):
        self.assertEqual(projects.root_for_path(
            self.ws, os.path.join(self.app, "src", "new.py")), self.app)
        self.assertEqual(projects.root_for_path(self.ws, "group/lib/README"),
                         self.lib)
        # Outside the session folder: says nothing about this session.
        self.assertEqual(projects.root_for_path(self.ws, "/etc/hosts"), "")

    def test_plain_folder_in_a_workspace_is_its_own_project(self):
        # Not a repository, next to ones that are: its first level under the
        # workspace, never the whole workspace.
        notes = os.path.join(self.ws, "notes")
        self.assertEqual(projects.root_for_path(
            self.ws, os.path.join(notes, "deep", "a.md")), notes)
        # A plain folder with no repositories in it is measured whole.
        plain = os.path.realpath(tempfile.mkdtemp(prefix="plain-"))
        self.assertEqual(projects.root_for_path(plain, "sub/a.txt"), plain)

    def test_folder_inside_a_repository_is_its_own_project(self):
        sub = os.path.join(self.app, "src")
        os.makedirs(sub)
        self.assertEqual(projects.resolve(sub), [sub])
        self.assertEqual(projects.root_for_path(sub, "x.py"), sub)

    def test_resolve_newest_edit_first_and_remembered(self):
        store = FakeStore([os.path.join(self.app, "README"),
                           os.path.join(self.lib, "README"),
                           "/elsewhere/memory.md"])
        self.assertEqual(projects.resolve(self.ws, "s1", store),
                         [self.lib, self.app])
        # Without a transcript, the next turn still knows.
        self.assertEqual(projects.remembered(self.ws, "s1"), [self.lib, self.app])

    def test_single_repository_found_without_edits(self):
        only = os.path.realpath(tempfile.mkdtemp(prefix="ws1-"))
        make_repo(os.path.join(only, "proj"))
        self.assertEqual(projects.resolve(only), [os.path.join(only, "proj")])
        # Two repositories and no edits: no guess, the folder itself.
        self.assertEqual(projects.resolve(self.ws, "fresh"), [self.ws])

    def test_turn_is_measured_in_the_project(self):
        store = FakeStore([os.path.join(self.app, "README")])
        marked = projects.mark_turn(self.ws, "s2", store)
        self.assertEqual(marked, [self.app])
        write(os.path.join(self.app, "README"), "hello\nworld\n")
        got = changes.collect_turn(self.app)
        self.assertTrue(got["available"])
        self.assertEqual([f["path"] for f in got["files"]], ["README"])
        # The outer folder itself was never walked.
        self.assertIsNone(shadow.base_for(self.ws))

    def test_unmarked_project_reports_no_mark(self):
        got = changes.collect_turn(self.lib)
        self.assertFalse(got["available"])
        self.assertTrue(got.get("no_mark"))


if __name__ == "__main__":
    unittest.main()
