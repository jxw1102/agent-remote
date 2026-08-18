"""Configuration and token handling.

Everything lives under ~/.agentremoted/ (override with AGENTREMOTED_HOME):
    config.json  — providers, port, bind, claude_bin / grok_bin / …
    token        — shared secret clients must present (auto-generated)
    daemon.log   — log file when running under launchd/systemd

Recommended config uses ``"providers": ["claude", "grok", "codex"]`` (one
process, every harness). A lone ``"provider"`` string still works as a
fallback when ``providers`` is empty.
"""

import hashlib
import json
import logging
import os
import platform
import secrets
import stat
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_HOME = Path.home() / ".agentremoted"


def _resolve_config_dir() -> Path:
    env = (os.environ.get("AGENTREMOTED_HOME") or "").strip()
    if env:
        return Path(env).expanduser()
    return _DEFAULT_HOME


CONFIG_DIR = _resolve_config_dir()


def tmux_socket() -> str:
    """`tmux -L` socket for this daemon's interactive TUI fleet.

    tmux's default socket is keyed by UID alone, so *any* process on the
    machine sees every session — including the reaper in each interactive
    manager's ``_adopt_or_reap``, which kills the sessions its own
    ``tuis.json`` does not list. A test points AGENTREMOTED_HOME at a temp
    dir, so its registry is empty while its tmux view is the real one: it
    reaped every live TUI, killing the very host that was running it
    ("claude TUI exited mid-turn" mid-tool-call).

    Naming the socket after CONFIG_DIR ties the fleet to the registry that
    describes it: a different AGENTREMOTED_HOME gets a different, empty
    server it cannot see past. It also keeps the daemon's panes out of the
    user's own ``tmux ls``.
    """
    digest = hashlib.sha1(
        os.path.realpath(str(CONFIG_DIR)).encode("utf-8")).hexdigest()[:8]
    return "agentremoted-%s" % digest


_TMUX_HELPER = """#!/bin/sh
# agentremoted fleet tmux — GENERATED, rewritten on every daemon start.
#
# The daemon's TUIs run on their own tmux socket, named after this config dir
# (see config.tmux_socket), so a plain `tmux ls` shows nothing. This wrapper
# carries the -L for you.
#
#   %(self)s                 list the fleet
#   %(self)s peek <name>     print a pane, read-only, no attach  <- safest
#   %(self)s attach <name>   attach without resizing the TUI
#   %(self)s <any tmux command ...>
#
# Why "attach" is special: attaching a client makes tmux resize the window to
# YOUR terminal. The daemon launches panes at 220x50, and a smaller terminal
# reflows the running CLI's screen — which is also what the daemon scrapes for
# busy/ready state. So attach pins the window size first, and you see a
# viewport onto the big pane instead of squashing it.
SOCK=%(sock)s
TMUX=%(tmux)s

case "${1:-ls}" in
  ls|list|"")
    exec "$TMUX" -L "$SOCK" ls
    ;;
  peek)
    [ -n "$2" ] || { echo "usage: $0 peek <session>" >&2; exit 2; }
    exec "$TMUX" -L "$SOCK" capture-pane -p -e -t "$2"
    ;;
  attach)
    [ -n "$2" ] || { echo "usage: $0 attach <session>" >&2; exit 2; }
    "$TMUX" -L "$SOCK" set-option -t "$2" window-size manual >/dev/null 2>&1
    echo "attached read-only; detach with ctrl-b d" >&2
    exec "$TMUX" -L "$SOCK" attach-session -r -t "$2"
    ;;
  *)
    exec "$TMUX" -L "$SOCK" "$@"
    ;;
esac
"""


def write_tmux_helper() -> str:
    """Drop a `tmux` wrapper beside the config so the socket name — a hash
    nobody should have to memorise — never has to be typed.

    Rewritten at every start so it always matches the live socket and the
    tmux currently on PATH.
    """
    import shutil

    path = CONFIG_DIR / "tmux"
    body = _TMUX_HELPER % {
        "self": path,
        "sock": tmux_socket(),
        "tmux": shutil.which("tmux") or "/opt/homebrew/bin/tmux",
    }
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        os.chmod(str(path), 0o755)
    except OSError as e:
        log.warning("could not write tmux helper %s: %s", path, e)
        return ""
    return str(path)


_server_lock = threading.Lock()


def ensure_tmux_server(tmux_bin: str) -> None:
    """Bring the fleet's tmux server up before anyone runs `new-session`.

    Two turns starting at once (a phone continue and a web continue land
    milliseconds apart) each ran `new-session` against a socket with no
    server yet, so both tmux clients raced to *create* one. The loser's
    server is torn down — and it takes every session on that socket with
    it, killing unrelated turns mid-flight with nothing in the daemon log,
    because the daemon never issued a kill.

    `start-server` is idempotent and returns immediately, so holding a lock
    across it costs nothing and leaves exactly one creator. All three
    harnesses share the socket, hence a module-level lock rather than one
    per manager.
    """
    with _server_lock:
        try:
            subprocess.run([tmux_bin, "-L", tmux_socket(), "start-server"],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass  # new-session will surface the real error


DEFAULTS = {
    # Fallback when "providers" is empty / missing.
    "provider": "claude",
    # Preferred: list of harness names served by this process.
    "providers": [],
    # Network. The BB10 browser stack tops out at TLS 1.0/1.2 with old ciphers,
    # so the daemon speaks plain HTTP by default and relies on the shared
    # token + a trusted LAN (or an app-level VPN) instead of transport
    # encryption. For an internet-facing VPS behind Cloudflare, set tls_cert
    # + tls_key (self-signed is fine in Full mode; the phone sees CF's cert).
    "bind": "0.0.0.0",
    "port": 8473,
    "tls_cert": "",
    "tls_key": "",
    # Hard cap (seconds) on one agent turn; 0 disables. Protects against a
    # wedged CLI holding a job (and its queue) forever.
    "turn_timeout": 1800,
    # Cap on in-memory finished jobs kept for the client to fetch results.
    "max_finished_jobs": 50,
    # Where phone uploads land (POST /api/attachments). Empty -> home/uploads.
    "upload_dir": "",
    # Cap (MB) on one uploaded attachment.
    "max_upload_mb": 16,
    # Host→phone drop folder. Empty -> ~/Public on macOS, else home/drop.
    "drop_dir": "",
    # Cap (MB) on one file served from the drop folder.
    "max_drop_mb": 64,
    # Extra slash commands offered to the app (merged with what the provider
    # discovers itself — claude also scans ~/.claude/commands/*.md).
    "slash_commands": [],
    # Extra model names offered in the app's model picker.
    "models": [],
    # Extra reasoning-effort levels for the app's effort picker (grok).
    "efforts": [],

    # ---- claude provider ------------------------------------------------
    "projects_dir": str(Path.home() / ".claude" / "projects"),
    "claude_bin": "claude",
    # One of: default, acceptEdits, plan, bypassPermissions.
    "permission_mode": "bypassPermissions",
    "permission_timeout": 300,
    # How long to wait for the phone to answer a question panel. 0 = forever.
    "question_timeout": 0,
    "claude_env": {},

    # ---- grok provider --------------------------------------------------
    "grok_home": str(Path.home() / ".grok"),
    "grok_bin": "grok",
    "grok_prompt_flags": "",
    "grok_default_cwd": "",
    "grok_env": {},

    # ---- codex provider -------------------------------------------------
    "codex_home": str(Path.home() / ".codex"),
    "codex_bin": "codex",
    "codex_env": {},
    # Sandbox for `codex exec -s …`. One of:
    #   read-only | workspace-write | danger-full-access | yolo
    # yolo / danger-full-access → --dangerously-bypass-approvals-and-sandbox
    "codex_sandbox": "danger-full-access",
    # Extra flags for every `codex exec` (whitespace-split).
    "codex_exec_flags": "",

    # ---- deepseek harness (dsh web on localhost) ------------------------
    # The official UI is `npx @deepseek-ai/dsh web`. Agent Remote talks to
    # that process over loopback HTTP — never expose :3080 to the phone.
    # If the URL is already up we adopt it; otherwise we start `dsh web`
    # ourselves (set dsh_manage false to keep "start it yourself").
    "dsh_url": "http://127.0.0.1:3080",
    "dsh_home": str(Path.home() / ".dsh"),
    "dsh_bin": "dsh",
    "dsh_manage": True,
}


class Config:
    def __init__(self, data: dict):
        self._data = dict(DEFAULTS)
        self._data.update(data or {})

    def __getattr__(self, name):
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)

    @property
    def projects_path(self) -> Path:
        return Path(self._data["projects_dir"]).expanduser()

    @property
    def grok_home_path(self) -> Path:
        return Path(self._data["grok_home"]).expanduser()

    @property
    def codex_home_path(self) -> Path:
        return Path(self._data["codex_home"]).expanduser()

    @property
    def upload_path(self) -> Path:
        raw = str(self._data.get("upload_dir") or "").strip()
        return Path(raw).expanduser() if raw else CONFIG_DIR / "uploads"

    @property
    def drop_path(self) -> Path:
        raw = str(self._data.get("drop_dir") or "").strip()
        if raw:
            return Path(raw).expanduser()
        if platform.system() == "Darwin":
            return Path.home() / "Public"
        return CONFIG_DIR / "drop"

    @property
    def log_path(self) -> Path:
        return CONFIG_DIR / "client-timing.log"

    def provider_names(self) -> list:
        """Ordered list of harnesses this process should load."""
        raw = self._data.get("providers")
        if isinstance(raw, list) and raw:
            out = []
            for item in raw:
                name = str(item or "").strip().lower()
                if name and name not in out:
                    out.append(name)
            if out:
                return out
        name = str(self._data.get("provider") or "claude").strip().lower() or "claude"
        return [name]

    def multi_mode(self) -> bool:
        return len(self.provider_names()) > 1


def load_config() -> Config:
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    cfg_file = CONFIG_DIR / "config.json"
    data = {}
    if cfg_file.is_file():
        data = json.loads(cfg_file.read_text(encoding="utf-8"))
    return Config(data)


def load_or_create_token() -> str:
    CONFIG_DIR.mkdir(mode=0o700, exist_ok=True)
    token_file = CONFIG_DIR / "token"
    if token_file.is_file():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_hex(16)
    token_file.write_text(token + "\n", encoding="utf-8")
    token_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return token
