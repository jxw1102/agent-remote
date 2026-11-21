"""Per-session model pins: the model belongs to the session, not the client.

What has to hold:

1. A pin is stored against one session id and no other.
2. "default" / "auto" / "" mean *no* pin, and clear one that exists.
3. Pins survive a daemon restart, and follow a session whose id the harness
   re-mints on resume.
4. The store cannot grow without bound.
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HOME = tempfile.mkdtemp(prefix="agentremoted-prefs-home-")
os.environ["AGENTREMOTED_HOME"] = HOME

from agentremoted import session_prefs  # noqa: E402


class SessionPrefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentremoted-prefs-")
        self.path = os.path.join(self.tmp, "session-prefs.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def store(self):
        return session_prefs.SessionPrefs(path=self.path)

    # -- 1. one session at a time ---------------------------------------

    def test_a_pin_is_scoped_to_one_session(self):
        p = self.store()
        p.set("sess-a", model="claude-opus-5")
        self.assertEqual(p.model("sess-a"), "claude-opus-5")
        # The whole point: the session next door is untouched.
        self.assertEqual(p.model("sess-b"), "")
        self.assertEqual(p.get("sess-b"), {"model": "", "effort": ""})

    def test_model_and_effort_are_independent(self):
        p = self.store()
        p.set("s", model="grok-4.7")
        p.set("s", effort="high")
        self.assertEqual(p.get("s"), {"model": "grok-4.7", "effort": "high"})
        # None leaves a field alone; it is not "clear it".
        p.set("s", model="grok-4.6")
        self.assertEqual(p.get("s"), {"model": "grok-4.6", "effort": "high"})

    def test_an_unset_session_has_no_pin(self):
        p = self.store()
        self.assertEqual(p.get(""), {"model": "", "effort": ""})
        self.assertEqual(p.get("never-seen"), {"model": "", "effort": ""})

    # -- 2. "default" is not a choice -----------------------------------

    def test_default_and_auto_clear_rather_than_pin(self):
        p = self.store()
        for neutral in ("default", "auto", "", "  ", "DEFAULT"):
            p.set("s", model="claude-opus-5")
            self.assertEqual(p.model("s"), "claude-opus-5")
            p.set("s", model=neutral)
            self.assertEqual(p.model("s"), "",
                             "%r should clear the pin" % neutral)

    def test_clearing_every_field_drops_the_row(self):
        p = self.store()
        p.set("s", model="gpt-6.2-sol", effort="high")
        p.set("s", model="", effort="")
        self.assertEqual(p.get("s"), {"model": "", "effort": ""})
        # Not just empty strings: no row at all, so the file does not fill
        # with sessions that pin nothing.
        self.assertNotIn("s", p._pins)

    def test_an_absurdly_long_value_is_refused(self):
        p = self.store()
        p.set("s", model="x" * 500)
        self.assertEqual(p.model("s"), "")

    # -- 3. it outlives the daemon, and follows a re-minted id ----------

    def test_pins_survive_a_restart(self):
        self.store().set("s", model="claude-sonnet-5-5", effort="high")
        reopened = self.store()
        self.assertEqual(reopened.get("s"),
                         {"model": "claude-sonnet-5-5", "effort": "high"})

    def test_a_pin_follows_a_reminted_session_id(self):
        """grok mints a fresh id on resume; the pin is for the conversation."""
        p = self.store()
        p.set("old-id", model="grok-4.7")
        self.assertTrue(p.rekey("old-id", "new-id"))
        self.assertEqual(p.model("new-id"), "grok-4.7")
        self.assertEqual(p.model("old-id"), "")
        # Nothing to move, or a target that already has its own pin: no-op.
        self.assertFalse(p.rekey("old-id", "new-id"))
        self.assertFalse(p.rekey("new-id", "new-id"))

    def test_a_corrupt_file_is_not_fatal(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        p = self.store()
        self.assertEqual(p.get("s"), {"model": "", "effort": ""})
        p.set("s", model="codex-x")
        self.assertEqual(p.model("s"), "codex-x")

    # -- 4. bounded -----------------------------------------------------

    def test_the_store_is_capped(self):
        p = self.store()
        for i in range(session_prefs._MAX_PINS + 25):
            p.set("sess-%04d" % i, model="m-%d" % i)
        self.assertLessEqual(len(p._pins), session_prefs._MAX_PINS)
        # Oldest-first eviction: the newest pin is always still there.
        self.assertEqual(p.model("sess-%04d" % (session_prefs._MAX_PINS + 24)),
                         "m-%d" % (session_prefs._MAX_PINS + 24))

    def test_normalize(self):
        self.assertEqual(session_prefs.normalize("  opus  "), "opus")
        self.assertEqual(session_prefs.normalize("Default"), "")
        self.assertEqual(session_prefs.normalize(None), "")


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(HOME, ignore_errors=True)
