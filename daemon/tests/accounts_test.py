"""Isolation checks for scoped sub-accounts (guests.json).

Ensures main and guest tokens never receive each other's jobs/sessions,
and guest A never receives guest B data.

Run:  python3 tests/accounts_test.py
"""

import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-accounts-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")

from agentremoted import accounts  # noqa: E402
from agentremoted.jobs import Job  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def write_guests(entries):
    path = accounts.guests_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"guests": entries}), encoding="utf-8")
    accounts.load_guests(force=True)


def main():
    root_a = os.path.join(FAKE_HOME, "sandbox", "alice")
    root_b = os.path.join(FAKE_HOME, "sandbox", "bob")
    os.makedirs(root_a, exist_ok=True)
    os.makedirs(root_b, exist_ok=True)
    main_home = os.path.join(FAKE_HOME, "Projects")
    os.makedirs(main_home, exist_ok=True)

    token_a = "a" * 32
    token_b = "b" * 32
    main_token = "m" * 32

    write_guests([
        {"name": "alice", "token": token_a, "root": root_a,
         "providers": ["claude", "grok"]},
        {"name": "bob", "token": token_b, "folder": root_b,
         "provider": "codex"},
    ])

    main_p = accounts.resolve_principal(main_token, main_token)
    alice = accounts.resolve_principal(token_a, main_token)
    bob = accounts.resolve_principal(token_b, main_token)
    bad = accounts.resolve_principal("wrong", main_token)

    check("main resolves", main_p is not None and main_p.is_main)
    check("alice resolves guest", alice is not None and alice.is_guest)
    check("bob resolves guest", bob is not None and bob.is_guest)
    check("bad token rejected", bad is None)
    check("alice root", alice.root == os.path.realpath(root_a))
    check("account ids differ",
          main_p.account != alice.account != bob.account)
    check("alice allows claude+grok",
          alice.allows_provider("claude") and alice.allows_provider("grok")
          and not alice.allows_provider("codex"))
    check("bob allows only codex",
          bob.allows_provider("codex") and not bob.allows_provider("claude"))
    check("main allows all",
          main_p.allows_provider("claude") and main_p.allows_provider("codex"))
    check("alice filter providers",
          alice.filter_provider_names(["claude", "codex", "grok"])
          == ["claude", "grok"])

    # --- path confinement ---
    cwd, err = accounts.confine_cwd("", alice)
    check("guest empty cwd → root", cwd == alice.root and err is None)
    cwd, err = accounts.confine_cwd(root_b, alice)
    check("alice cannot use bob folder", err is not None)
    cwd, err = accounts.confine_cwd(main_home, alice)
    check("alice cannot use main folder", err is not None)
    cwd, err = accounts.confine_cwd(os.path.join(root_a, "sub"), alice)
    check("alice can use subfolder", err is None and accounts.path_under(cwd, alice.root))
    cwd, err = accounts.confine_cwd(root_a, main_p)
    check("main cannot open guest root", err is not None)
    cwd, err = accounts.confine_cwd(main_home, main_p)
    check("main can open own folder", err is None)

    # --- job ownership ---
    job_main = Job("j1", "", "hi", main_home)
    job_main.account = main_p.account
    job_main.isolate_root = ""
    job_alice = Job("j2", "", "hi", root_a)
    job_alice.account = alice.account
    job_alice.isolate_root = alice.root
    job_bob = Job("j3", "", "hi", root_b)
    job_bob.account = bob.account
    job_bob.isolate_root = bob.root

    check("main owns main job", accounts.job_in_scope(job_main, main_p))
    check("main not own alice job", not accounts.job_in_scope(job_alice, main_p))
    check("alice owns alice job", accounts.job_in_scope(job_alice, alice))
    check("alice not own bob job", not accounts.job_in_scope(job_bob, alice))
    check("alice not own main job", not accounts.job_in_scope(job_main, alice))
    check("bob not own alice job", not accounts.job_in_scope(job_alice, bob))

    # --- list filtering ---
    job_main.provider = "claude"
    job_alice.provider = "claude"
    job_bob.provider = "codex"
    rows = [
        job_main.brief(),
        job_alice.brief(),
        job_bob.brief(),
        {"id": "s1", "cwd": main_home, "title": "main sess", "provider": "claude"},
        {"id": "s2", "cwd": root_a, "title": "alice sess", "provider": "claude"},
        {"id": "s3", "cwd": root_b, "title": "bob sess", "provider": "codex"},
        # Alice's folder but disallowed harness → must hide.
        {"id": "s2b", "cwd": root_a, "title": "alice codex", "provider": "codex"},
    ]
    main_rows = accounts.filter_records(rows, main_p)
    alice_rows = accounts.filter_records(rows, alice)
    bob_rows = accounts.filter_records(rows, bob)

    main_ids = {r.get("id") for r in main_rows}
    alice_ids = {r.get("id") for r in alice_rows}
    bob_ids = {r.get("id") for r in bob_rows}

    check("main sees own job+session only",
          main_ids == {"j1", "s1"}, detail=main_ids)
    check("alice sees own job+session only",
          alice_ids == {"j2", "s2"}, detail=alice_ids)
    check("alice hides disallowed harness row",
          "s2b" not in alice_ids)
    check("bob sees own job+session only",
          bob_ids == {"j3", "s3"}, detail=bob_ids)
    check("no cross-guest leakage",
          not (alice_ids & bob_ids) and "j1" not in alice_ids)

    # Drop paths are per-account.
    check("alice drop under root",
          str(alice.drop_path()).startswith(alice.root))
    check("main drop not under alice",
          not str(main_p.drop_path()).startswith(alice.root + os.sep)
          and str(main_p.drop_path()) != alice.root)

    # Isolation helpers: shell wrap + backend detection.
    backend = accounts.isolation_backend()
    check("isolation backend detected or empty",
          backend in ("", "bwrap", "sandbox-exec", "chroot"),
          detail=backend)
    wrapped = accounts.wrap_shell_command("ls /", alice.root)
    check("wrap is non-empty", bool(wrapped))
    if backend == "sandbox-exec":
        check("wrap uses sandbox-exec", "sandbox-exec" in wrapped)
        prof = accounts.ensure_sandbox_profile(alice.root)
        check("profile written", bool(prof) and os.path.isfile(prof))
        body = open(prof, encoding="utf-8").read()
        check("profile allows guest root", alice.root in body)
        host_home = os.path.realpath(os.path.expanduser("~"))
        check("profile does not allow whole host home",
              '(subpath "%s")' % host_home not in body)
    elif backend == "bwrap":
        check("wrap uses bwrap", "bwrap" in wrapped)
        argv = accounts.isolate_argv(["/bin/echo", "hi"], alice.root)
        check("isolate_argv starts with bwrap",
              argv and os.path.basename(argv[0]) == "bwrap")
        check("bwrap binds guest root", alice.root in argv)
    env = accounts.isolation_env({}, alice.root)
    check("isolation HOME is guest root", env.get("HOME") == alice.root)
    if backend:
        check("isolation_ready", accounts.isolation_ready(alice.root))

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
