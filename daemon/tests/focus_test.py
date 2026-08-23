"""Focus list state: membership, the derived state tag, and title overrides.

The focus list is the one piece of session state the daemon owns outright (every
other row field is derived from a transcript on disk), so these checks cover
the rules that cannot be re-derived if they go wrong: what enrols a card, what
takes it off, and how the state tag falls out of live job state plus the read
cursor.

Run:  python3 tests/focus_test.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-focus-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")

from agentremoted import focus  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def new_focus(name):
    return focus.Focus(path=_tmp_path(name))


def _tmp_path(name):
    import pathlib
    return pathlib.Path(FAKE_HOME) / ("focus-%s.json" % name)


def test_membership():
    print("membership:")
    b = new_focus("membership")
    check("unknown session is not a member", not b.is_member("s1"))
    check("enroll reports a change", b.enroll("s1", provider="claude",
                                              cwd="/tmp/p") is True)
    check("enrolled session is a member", b.is_member("s1"))
    check("re-enroll is a no-op", b.enroll("s1") is False)
    check("member keeps provider", (b.member("s1") or {}).get("provider")
          == "claude")

    check("mark_done removes from the list", b.mark_done("s1") is True)
    check("done session is not a member", not b.is_member("s1"))
    check("mark_done twice is idempotent", b.mark_done("s1") is False)
    check("done row still exists for undo", b.member("s1") is not None)
    check("restore puts it back", b.restore("s1") is True)
    check("restored session is a member", b.is_member("s1"))
    check("restore twice is idempotent", b.restore("s1") is False)

    # The revive rule: acting on something you had filed away means you are
    # working on it again.
    b.mark_done("s1")
    check("enroll revives a done card", b.enroll("s1") is True)
    check("revived session is a member", b.is_member("s1"))

    check("empty key is rejected", b.enroll("") is False)
    check("active_keys lists members", b.active_keys() == {"s1"})


def test_state_tag():
    print("state tag:")
    # Derived, never stored — a pure function of live job state.
    check("pending beats running (needs answer)",
          focus.state_for(running=True, pending=True)
          == focus.STATE_NEEDS_ANSWER)
    check("pending while idle is still needs answer",
          focus.state_for(running=False, pending=True)
          == focus.STATE_NEEDS_ANSWER)
    check("pending beats failed",
          focus.state_for(running=False, pending=True, failed=True)
          == focus.STATE_NEEDS_ANSWER)
    check("running and unblocked is working",
          focus.state_for(running=True, pending=False)
          == focus.STATE_WORKING)
    check("a new turn outranks an older failure",
          focus.state_for(running=True, pending=False, failed=True)
          == focus.STATE_WORKING)
    check("idle after a failed turn is failed",
          focus.state_for(running=False, pending=False, failed=True)
          == focus.STATE_FAILED)
    check("idle and clean is turn finished",
          focus.state_for(running=False, pending=False)
          == focus.STATE_TURN_FINISHED)
    check("needs_answer sorts most urgent",
          focus.STATES[0] == focus.STATE_NEEDS_ANSWER)
    check("failed outranks working",
          focus.STATES.index(focus.STATE_FAILED)
          < focus.STATES.index(focus.STATE_WORKING))
    check("every state has a label",
          all(focus.STATE_LABELS.get(st) for st in focus.STATES))


def test_rekey():
    print("rekey (job placeholder to session id):")
    b = new_focus("rekey")
    placeholder = focus.JOB_KEY_PREFIX + "job7"
    b.enroll(placeholder, job_id="job7", cwd="/tmp/p")
    b.set_title(placeholder, "My new thing")
    check("placeholder is a member", b.is_member(placeholder))

    check("rekey reports a change", b.rekey(placeholder, "real-sid") is True)
    check("placeholder is gone", not b.is_member(placeholder))
    check("session id is a member", b.is_member("real-sid"))
    check("title follows the card", b.title("real-sid") == "My new thing")
    check("key_for_job finds the session", b.key_for_job("job7") == "real-sid")
    check("key_for_job empty on unknown", b.key_for_job("nope") == "")
    check("rekey of an unknown key is a no-op",
          b.rekey(focus.JOB_KEY_PREFIX + "nope", "x") is False)
    check("rekey to itself is a no-op", b.rekey("real-sid", "real-sid") is False)

    # A resumed session already has a card: merge, keep the older added_at and
    # the newer seen_at, and never leave the card filed as done.
    b2 = new_focus("rekey-merge")
    b2.enroll("sid", job_id="old")
    b2.mark_done("sid")
    ph = focus.JOB_KEY_PREFIX + "job9"
    b2.enroll(ph, job_id="job9")
    b2.rekey(ph, "sid")
    check("merge keeps one card", b2.member(ph) is None)
    check("merge is active, not done", b2.is_member("sid"))


def test_titles():
    print("titles:")
    b = new_focus("titles")
    check("no override by default", b.title("s1") == "")
    check("set_title returns the stored title",
          b.set_title("s1", "  Fix   the pager  ") == "Fix the pager")
    check("whitespace collapsed", b.title("s1") == "Fix the pager")
    check("manual flag recorded", (b.title_entry("s1") or {}).get("manual") is True)
    check("regenerated titles are not manual",
          b.set_title("s2", "Auto name", manual=False) == "Auto name"
          and (b.title_entry("s2") or {}).get("manual") is False)

    check("empty title clears the override", b.set_title("s1", "   ") == "")
    check("override gone", b.title("s1") == "")
    check("clear_title on a stranger is a no-op", b.clear_title("nope") is False)

    long = "x" * 400
    stored = b.set_title("s3", long)
    check("long title is capped", len(stored) <= 120, len(stored))
    check("capped title is elided", stored.endswith("…"))

    # A rename is independent of membership: you can name a session you are
    # not tracking, and naming one must not enrol it.
    check("naming does not enroll", not b.is_member("s3"))


def test_persistence():
    print("persistence:")
    path = _tmp_path("persist")
    b = focus.Focus(path=path)
    b.enroll("s1", provider="claude", cwd="/tmp/p", job_id="j1")
    b.set_title("s1", "Kept across restart")
    b.enroll("s2")
    b.mark_done("s2")

    again = focus.Focus(path=path)
    check("membership survives restart", again.is_member("s1"))
    check("title survives restart", again.title("s1") == "Kept across restart")
    check("done stays done across restart", not again.is_member("s2"))
    check("done row still restorable", again.restore("s2") is True)

    # A corrupt or half-written file must not take the daemon down with it.
    path.write_text("{ not json", encoding="utf-8")
    salvaged = focus.Focus(path=path)
    check("corrupt file loads as empty", salvaged.active_keys() == set())
    check("corrupt file still writable", salvaged.enroll("s9") is True)


def test_pruning():
    print("pruning:")
    import time
    b = new_focus("prune")
    b.enroll("old")
    b.mark_done("old")
    # Backdate past the undo window, then trigger a prune with any write.
    b._members["old"]["done_at"] = time.time() - (focus._DONE_GRACE_S + 60)
    b.enroll("fresh")
    check("expired done card is pruned", b.member("old") is None)
    check("fresh card kept", b.is_member("fresh"))


def main():
    print("focus:")
    test_membership()
    test_state_tag()
    test_rekey()
    test_titles()
    test_persistence()
    test_pruning()
    print()
    if failures:
        print("FAILED: %d" % len(failures))
        for name in failures:
            print("  - %s" % name)
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
