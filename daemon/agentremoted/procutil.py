"""Cross-platform process helpers.

The daemon grew up on POSIX: process groups, os.killpg and signal.SIGKILL
do not exist on Windows, and npm-installed CLIs (claude, codex,
cursor-agent ...) are .cmd shims that CreateProcess cannot execute
directly. Everything Windows needs to differ lives here so the rest of the
tree reads the same on every platform.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess

IS_WINDOWS = os.name == "nt"

#: Windows has no SIGKILL; terminate is the strongest signal it accepts.
SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _windows(windows):
    return IS_WINDOWS if windows is None else bool(windows)


def argv_for(cmd, windows=None):
    """Run an npm-style .cmd/.bat shim through cmd.exe on Windows.

    shutil.which("claude") resolves to claude.CMD there, and CreateProcess
    refuses to execute batch files ("not a valid Win32 application").
    POSIX gets the argv unchanged.
    """
    if not _windows(windows) or not cmd:
        return cmd
    exe = shutil.which(cmd[0]) or cmd[0]
    if str(exe).lower().endswith((".cmd", ".bat")):
        return ["cmd.exe", "/c", exe] + list(cmd[1:])
    return [exe] + list(cmd[1:])


def spawn_kwargs(windows=None):
    """Own process group, so stop() can kill the CLI and its children."""
    if _windows(windows):
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def signal_tree(proc, sig, windows=None):
    """Signal the job's whole process tree; fall back to the process."""
    if _windows(windows):
        try:
            if sig == SIGKILL:
                # /T takes the children too - the job's tools run under it.
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            else:
                proc.terminate()
        except (OSError, ValueError):
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (OSError, ProcessLookupError):
        try:
            proc.send_signal(sig)
        except (OSError, ProcessLookupError):
            pass


def kill_pid_tree(pid, windows=None):
    """Kill a pid we hold no Popen for (dsh host reaping)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    if _windows(windows):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except OSError:
            pass
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
