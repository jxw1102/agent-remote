"""Windows portability helpers.

agentremoted runs on Windows for headless turns: process groups become
CREATE_NEW_PROCESS_GROUP, os.killpg becomes taskkill /T, and npm CLIs
(claude.CMD) are routed through cmd.exe. The interactive/Live TUI needs
tmux and stays POSIX-only (capabilities already say so).

Run:  python3 tests/win_port_test.py
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted import procutil  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" - " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


check("SIGKILL resolves on every platform", procutil.SIGKILL > 0)
check("posix argv unchanged",
      procutil.argv_for(["/usr/bin/claude", "-p", "hi"]) ==
      ["/usr/bin/claude", "-p", "hi"])
check("windows keeps a native exe",
      procutil.argv_for([r"C:\Python\python.exe", "-m", "x"], windows=True)
      == [r"C:\Python\python.exe", "-m", "x"])

with tempfile.TemporaryDirectory() as tmp:
    shim = os.path.join(tmp, "claude.cmd")
    open(shim, "w").write("@echo off\r\n")
    os.chmod(shim, 0o755)
    out = procutil.argv_for([shim, "-p", "hello world"], windows=True)
    check("windows routes .cmd through cmd.exe",
          out[:3] == ["cmd.exe", "/c", shim], out)
    check("windows keeps the arguments", out[3:] == ["-p", "hello world"], out)
    check("posix leaves the .cmd path alone",
          procutil.argv_for([shim], windows=False) == [shim])

if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
    check("windows spawns its own process group",
          procutil.spawn_kwargs(windows=True).get("creationflags")
          == subprocess.CREATE_NEW_PROCESS_GROUP)
check("posix spawns a new session",
      procutil.spawn_kwargs(windows=False) == {"start_new_session": True})

print("")
print("FAILED (%d)" % len(failures) if failures else "all windows port checks passed")
raise SystemExit(1 if failures else 0)
