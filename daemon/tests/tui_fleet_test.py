"""TUI fleet cap, Claude ready-detection, guest grok --cwd skip.

Run:  python3 tests/tui_fleet_test.py
"""

import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.jobs import Job  # noqa: E402
from agentremoted.live_tui import idle_eviction_victim  # noqa: E402
from agentremoted.providers.claude_interactive import (  # noqa: E402
    _claude_pane_ready, _claude_project_dir, _claude_splash, _newest_jsonl,
)
from agentremoted.providers.grok import GrokRunner  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def _tui(name, last_used, isolate="", job=None):
    t = types.SimpleNamespace()
    t.name = name
    t.last_used = last_used
    t.isolate_root = isolate
    t.job = job
    return t


def main():
    guest = "/tmp/guest-root"
    host_a = _tui("grk-host-old", 1.0)
    host_b = _tui("grk-host-new", 5.0)
    guest_a = _tui("grk-guest", 0.5, isolate=guest)
    busy = _tui("grk-busy", 0.1, job=object())

    v = idle_eviction_victim([host_a, host_b, guest_a], incoming_isolate_root=guest)
    check("incoming guest evicts oldest host, not guest",
          v is host_a, getattr(v, "name", None))

    v = idle_eviction_victim([host_a, host_b, guest_a], incoming_isolate_root="")
    check("incoming host never evicts guest even if guest is LRU",
          v is host_a, getattr(v, "name", None))

    v = idle_eviction_victim([guest_a, busy], incoming_isolate_root="")
    check("incoming host overflows when only guests/busy remain", v is None)

    v = idle_eviction_victim([guest_a, busy], incoming_isolate_root=guest)
    check("incoming guest may evict another idle guest",
          v is guest_a, getattr(v, "name", None))

    v = idle_eviction_victim([busy], incoming_isolate_root=guest)
    check("no idle → overflow", v is None)

    splash = (
        " Let's get started.\n"
        " Choose the text style that looks best with your terminal\n"
        " ❯ 3. Light mode ✔\n"
        "   Syntax theme: GitHub (ctrl+t to disable)\n"
    )
    check("onboarding splash detected", _claude_splash(splash))
    check("theme-picker chevron is not ready", not _claude_pane_ready(splash))
    trust = "Quick safety check: Is this a project you created\n ❯ 1. Yes, I trust this folder\n"
    check("trust dialog is splash", _claude_splash(trust))
    check("trust dialog is not ready", not _claude_pane_ready(trust))
    check("prompt chrome is ready", _claude_pane_ready("foo\n❯ \n⏵⏵ auto"))
    check("empty pane is not ready", not _claude_pane_ready(""))

    root = "/Users/xiaowen.ji/sandbox/test-guest"
    d = _claude_project_dir(root, isolate_root=root)
    slug = os.path.realpath(root).replace("/", "-")
    check("guest project dir under isolate .claude",
          d == os.path.join(root, ".claude", "projects", slug),
          d)

    td = tempfile.mkdtemp(prefix="agentremoted-jsonl-")
    older = os.path.join(td, "old.jsonl")
    newer = os.path.join(td, "new.jsonl")
    open(older, "w").write("a")
    os.utime(older, (1, 1))
    open(newer, "w").write("b")
    os.utime(newer, (100, 100))
    check("newest jsonl", _newest_jsonl(td, after_mtime=0) == newer)
    check("newest jsonl respects after_mtime",
          _newest_jsonl(td, after_mtime=50) == newer)
    check("newest jsonl empty when all older",
          _newest_jsonl(td, after_mtime=200) == "")

    home = tempfile.mkdtemp(prefix="agentremoted-grokhome-")
    cfg = types.SimpleNamespace(
        grok_bin="/usr/bin/true",
        grok_home_path=Path(home),
        grok_env={},
        grok_prompt_flags="",
    )
    runner = GrokRunner(cfg)
    job = Job("j1", "", "hi", root)
    job.isolate_root = root
    cmd, _env = runner.prepare(job, "")
    check("guest grok omits --cwd", "--cwd" not in cmd, cmd)
    check("guest grok still -p", "-p" in cmd)

    job2 = Job("j2", "", "hi", "/tmp/proj")
    job2.isolate_root = ""
    cmd2, _env2 = runner.prepare(job2, "")
    check("host grok keeps --cwd", "--cwd" in cmd2 and "/tmp/proj" in cmd2, cmd2)

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
