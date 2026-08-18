"""Supervise a local `dsh web` for the DeepSeek provider.

The official DeepSeek UI is a long-running localhost HTTP host
(``dsh web``, default http://127.0.0.1:3080). Agent Remote is a client of
that /api — it never exposes :3080.

Policy, matching the other harnesses:

* If the configured URL already answers ``session.list``, adopt it.
* Otherwise, if the URL is loopback and ``dsh_manage`` is on (the default),
  start ``dsh web --host 127.0.0.1 --port <n>`` and wait until /api is up.
* A remote ``dsh_url`` is adopt-only — we never spawn against a foreign host.
* A process we started is recorded in ``dsh-web.pid`` so a daemon restart
  re-adopts it (launchd / systemd ``KillMode=process`` leave children).
* An externally started host is never killed.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from ..config import CONFIG_DIR
from .dsh_rpc import DEFAULT_URL, DshClient, DshError

log = logging.getLogger(__name__)

_READY_TIMEOUT = 30.0
_POLL_S = 0.25
_BOOT_WAIT_S = 8.0
_FALLBACK_BINS = (
    "/opt/homebrew/bin/dsh",
    "~/.local/bin/dsh",
    "/usr/local/bin/dsh",
)


def url_from_config(config) -> str:
    return str(getattr(config, "dsh_url", "") or DEFAULT_URL).strip() or DEFAULT_URL


def bind_from_url(url: str):
    """(host, port) the supervisor would bind ``dsh web`` to."""
    raw = (url or "").strip() or DEFAULT_URL
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    port = parsed.port or 3080
    return host, int(port)


def is_loopback(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1", "")


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=0.4):
            return True
    except OSError:
        return False


class DshHost:
    """Adopt or start one ``dsh web`` for a configured loopback URL."""

    def __init__(self, config, client=None, spawn=None):
        self.config = config
        self.client = client or DshClient(url_from_config(config))
        self._spawn_fn = spawn
        self.proc = None
        self.owned = False
        self.source = ""          # "external" | "managed" | ""
        self.last_error = ""
        self._lock = threading.Lock()
        self._log_fp = None
        self._bg_pending = False

    def manage_enabled(self) -> bool:
        val = getattr(self.config, "dsh_manage", True)
        if isinstance(val, str):
            return val.strip().lower() not in ("0", "false", "no", "off")
        return bool(val)

    def resolve_bin(self) -> str:
        raw = str(getattr(self.config, "dsh_bin", "") or "dsh").strip() or "dsh"
        custom = raw != "dsh"
        if os.path.sep in raw or raw.startswith("~"):
            path = os.path.expanduser(raw)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
            # Explicit path: do not fall through to a different `dsh`.
            if custom:
                return ""
        found = shutil.which(raw)
        if found:
            return found
        if custom:
            return ""
        for p in _FALLBACK_BINS:
            path = os.path.expanduser(p)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return ""

    def ensure(self) -> bool:
        """Make /api reachable. Returns True when session.list answers."""
        with self._lock:
            return self._ensure_locked()

    def ensure_bg(self) -> None:
        """Kick ensure() on a daemon thread (ping must not block on spawn)."""
        with self._lock:
            if self._bg_pending:
                return
            self._bg_pending = True

        def run():
            try:
                self.ensure()
            finally:
                with self._lock:
                    self._bg_pending = False

        threading.Thread(target=run, name="dsh-web-ensure", daemon=True).start()

    def call(self, method, payload=None, timeout=None):
        if not self.ensure():
            raise DshError(self.last_error or (
                "dsh web is not reachable at %s" % self.client.base))
        return self.client.call(method, payload, timeout=timeout)

    def shutdown(self) -> None:
        """Kill only a process we started. Leave an adopted host alone."""
        with self._lock:
            if self.owned:
                self._kill_owned()

    def _ensure_locked(self) -> bool:
        if self.client.reachable():
            return self._mark_ready()

        if self.proc is not None and not self._child_alive():
            log.warning("managed dsh web exited (pid %s)",
                        getattr(self.proc, "pid", "?"))
            self.proc = None

        if not self.manage_enabled():
            self.last_error = "dsh web is not reachable at %s" % self.client.base
            self.source = ""
            return False

        host, port = bind_from_url(self.client.base)
        if not is_loopback(host):
            self.last_error = (
                "dsh_url %s is not loopback; start dsh web there yourself"
                % self.client.base)
            self.source = ""
            return False

        # Previous daemon start left a child that may still be booting.
        if self._pidfile_alive() and not self._child_alive():
            if self._wait_ready(_BOOT_WAIT_S):
                return self._mark_ready(managed=True)

        if port_open(host, port) and not self.client.reachable():
            self.last_error = (
                "port %s is in use but is not dsh web" % port)
            self.source = ""
            return False

        bin_path = self.resolve_bin()
        if not bin_path:
            configured = str(getattr(self.config, "dsh_bin", "") or "dsh").strip() or "dsh"
            self.last_error = (
                "dsh not found: %s" % configured if configured != "dsh"
                else "dsh not on PATH")
            self.source = ""
            return False

        try:
            self.proc = self._spawn(host, port, bin_path)
        except Exception as e:
            self.last_error = "could not start dsh web: %s" % e
            log.warning("%s", self.last_error)
            self.source = ""
            return False
        self.owned = True

        if self._wait_ready(_READY_TIMEOUT):
            self._write_pidfile()
            self.source = "managed"
            self.last_error = ""
            log.info("started dsh web (pid %s) at %s",
                     getattr(self.proc, "pid", "?"), self.client.base)
            return True

        # Spawn lost a race with an external host that came up mid-wait.
        if self.client.reachable():
            if not self._child_alive():
                self.proc = None
                return self._mark_ready(managed=False)
            self._write_pidfile()
            return self._mark_ready(managed=True)

        self._kill_owned()
        self.last_error = "dsh web did not become ready at %s" % self.client.base
        log.warning("%s", self.last_error)
        self.source = ""
        return False

    def _mark_ready(self, managed=None) -> bool:
        self.last_error = ""
        if managed is True or self._child_alive() or self._pidfile_alive():
            self.owned = True
            self.source = "managed"
        else:
            self.owned = False
            self.source = "external"
        return True

    def _child_alive(self) -> bool:
        proc = self.proc
        if proc is None:
            return False
        poll = getattr(proc, "poll", None)
        if callable(poll):
            try:
                return poll() is None
            except Exception:
                return False
        return pid_alive(getattr(proc, "pid", None))

    def _pid_path(self) -> Path:
        return CONFIG_DIR / "dsh-web.pid"

    def _read_pidfile(self):
        path = self._pid_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def _pidfile_alive(self) -> bool:
        data = self._read_pidfile()
        if not data:
            return False
        if str(data.get("url") or "") != self.client.base:
            return False
        return pid_alive(data.get("pid"))

    def _write_pidfile(self) -> None:
        pid = getattr(self.proc, "pid", None)
        if not pid:
            return
        path = self._pid_path()
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "pid": int(pid),
                "url": self.client.base,
            }) + "\n", encoding="utf-8")
        except OSError as e:
            log.warning("could not write %s: %s", path, e)

    def _clear_pidfile(self) -> None:
        data = self._read_pidfile()
        if data and str(data.get("url") or "") != self.client.base:
            return
        try:
            self._pid_path().unlink()
        except OSError:
            pass

    def _wait_ready(self, timeout: float) -> bool:
        deadline = time.time() + max(0.2, float(timeout))
        while time.time() < deadline:
            if self.client.reachable():
                return True
            if self.proc is not None and not self._child_alive():
                return False
            time.sleep(_POLL_S)
        return self.client.reachable()

    def _spawn(self, host, port, bin_path):
        if self._spawn_fn is not None:
            return self._spawn_fn(host, port)
        return self._spawn_dsh(host, port, bin_path)

    def _spawn_dsh(self, host, port, bin_path):
        log_path = CONFIG_DIR / "dsh-web.log"
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            log_fp = open(log_path, "ab")
        except OSError:
            log_fp = subprocess.DEVNULL
        self._log_fp = log_fp if log_fp is not subprocess.DEVNULL else None
        env = os.environ.copy()
        home = str(Path(str(getattr(self.config, "dsh_home", "")
                            or "~/.dsh")).expanduser())
        env["DSH_HOME"] = home
        # Bind loopback only — the phone talks to agentremoted, not :3080.
        bind_host = "127.0.0.1" if is_loopback(host) else host
        cmd = [bin_path, "web", "--host", bind_host, "--port", str(int(port))]
        log.info("starting %s", " ".join(cmd))
        return subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
            cwd=str(Path.home()),
        )

    def _kill_owned(self) -> None:
        proc = self.proc
        self.proc = None
        self.owned = False
        self._clear_pidfile()
        if proc is None:
            return
        pid = getattr(proc, "pid", None)
        # Child is its own session leader (start_new_session).
        try:
            if pid:
                os.killpg(int(pid), 15)
            else:
                proc.terminate()
        except (OSError, ProcessLookupError, TypeError):
            try:
                proc.terminate()
            except (OSError, ProcessLookupError, AttributeError):
                pass
        try:
            proc.wait(timeout=4)
        except (TypeError, subprocess.TimeoutExpired):
            try:
                if pid:
                    os.killpg(int(pid), 9)
                else:
                    proc.kill()
            except (OSError, ProcessLookupError, TypeError, AttributeError):
                pass
        if self._log_fp is not None:
            try:
                self._log_fp.close()
            except OSError:
                pass
            self._log_fp = None
