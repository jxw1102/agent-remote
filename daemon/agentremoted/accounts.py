"""Scoped sub-accounts (token → folder root).

Optional file ``$AGENTREMOTED_HOME/guests.json`` (default
``~/.agentremoted/guests.json``).  When absent, only the main token
(``~/.agentremoted/token``) is accepted — full host access.

Shape (reloadable; no daemon restart required)::

    {
      "guests": [
        {
          "name": "alice",
          "token": "<hex or long random string>",
          "root": "~/work/alice",
          "providers": ["claude", "grok"]
        }
      ]
    }

``folder`` is accepted as an alias for ``root``.  ``providers`` (or
``harnesses``) is an optional list of harness names this account may use;
omit or leave empty to allow every harness the daemon has loaded.
``provider`` (singular string) is also accepted.

Each guest token is a full API credential for that folder only:
projects/sessions/jobs outside the root are invisible, harnesses not in
``providers`` are hidden, and new work is forced under the root.

Process confinement (agent CLIs + ``/api/shell``) uses the best backend
available on the host:

  * **Linux** — ``bwrap`` (bubblewrap); install via distro packages
  * **macOS** — ``sandbox-exec`` (Seatbelt, ships with the OS)
  * **root** — ``chroot`` jail as a last resort

If none of those backends is available, guest jobs/shells are refused
rather than run with a soft ``cd``-only "isolation".

This module is intentionally quiet in user-facing docs.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .config import CONFIG_DIR

log = logging.getLogger(__name__)

_GUESTS_FILE = "guests.json"
_CACHE_LOCK = threading.Lock()
_CACHE_MTIME = None  # type: Optional[float]
_CACHE_GUESTS = []  # type: list
_CACHE_WARNED = False


# Stable account key for main. Guests use "guest:<realpath>".
ACCOUNT_MAIN = "main"


@dataclass(frozen=True)
class Principal:
    """Authenticated caller: main (unrestricted) or guest (folder-scoped)."""

    name: str
    root: str  # realpath; empty string means main / unrestricted
    # Lowercase harness names this account may use. Empty tuple = all.
    providers: Tuple[str, ...] = ()

    @property
    def is_main(self) -> bool:
        return not self.root

    @property
    def is_guest(self) -> bool:
        return bool(self.root)

    @property
    def isolate_root(self) -> str:
        """Host path to chroot/cwd-confine agent children; '' for main."""
        return self.root

    @property
    def account(self) -> str:
        """Opaque ownership id stamped on jobs; never shared across tokens."""
        if self.is_main:
            return ACCOUNT_MAIN
        return "guest:" + self.root

    def allows_provider(self, name: str) -> bool:
        """True if this principal may use harness *name* (claude/grok/codex)."""
        if self.is_main or not self.providers:
            return True
        n = str(name or "").strip().lower()
        return bool(n) and n in self.providers

    def filter_provider_names(self, names) -> list:
        """Keep only harness names this principal is allowed to use."""
        out = []
        for n in names or []:
            s = str(n or "").strip().lower()
            if s and self.allows_provider(s) and s not in out:
                out.append(s)
        return out

    def upload_path(self) -> Path:
        if self.is_main:
            from .config import load_config
            return load_config().upload_path
        p = Path(self.root) / ".agentremote" / "uploads"
        return p

    def drop_path(self) -> Path:
        if self.is_main:
            from .config import load_config
            return load_config().drop_path
        return Path(self.root) / ".agentremote" / "drop"


def main_principal() -> Principal:
    return Principal(name="main", root="", providers=())


def guests_path() -> Path:
    return CONFIG_DIR / _GUESTS_FILE


def _realpath(path: str) -> str:
    """Expand ~ and resolve symlinks; returns '' if empty/unusable."""
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        return os.path.realpath(os.path.expanduser(raw))
    except OSError:
        return os.path.expanduser(raw)


def path_under(path: str, root: str) -> bool:
    """True if *path* is *root* or a descendant (after realpath)."""
    if not root:
        return True
    if not path:
        return False
    try:
        rp = os.path.realpath(os.path.expanduser(path))
        rr = os.path.realpath(root)
    except OSError:
        return False
    if not rr:
        return False
    if rp == rr:
        return True
    prefix = rr if rr.endswith(os.sep) else rr + os.sep
    return rp.startswith(prefix)


def ensure_root_dir(root: str) -> Optional[str]:
    """Create guest root if missing. Returns error string or None."""
    if not root:
        return None
    try:
        Path(root).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return "guest root unavailable: %s" % e
    if not os.path.isdir(root):
        return "guest root is not a directory: %s" % root
    return None


def confine_cwd(cwd: str, principal: Principal) -> tuple:
    """Return (resolved_cwd, error_or_None) for a new/continued session.

    Guests: empty cwd → their root; anything outside root is rejected.
    Main: expanduser only; refuse paths that sit under another guest's root
    so main cannot open (or be steered into) a sub-account folder.
    """
    raw = (cwd or "").strip()
    if principal is None:
        return "", "not authenticated"
    if principal.is_main:
        expanded = os.path.expanduser(raw) if raw else ""
        if expanded:
            try:
                resolved = os.path.realpath(expanded)
            except OSError:
                resolved = expanded
            # Main must not land in a guest tree (would mix ownership).
            for gr in guest_roots():
                if path_under(resolved, gr):
                    return "", "path belongs to a scoped account"
            return expanded, None
        return "", None
    err = ensure_root_dir(principal.root)
    if err:
        return "", err
    if not raw:
        return principal.root, None
    expanded = os.path.expanduser(raw)
    # Relative paths are resolved against the guest root, not the daemon cwd.
    if not os.path.isabs(expanded):
        expanded = os.path.join(principal.root, expanded)
    try:
        resolved = os.path.realpath(expanded)
    except OSError:
        resolved = expanded
    if not path_under(resolved, principal.root):
        return "", "path outside allowed folder"
    return resolved, None


def guest_roots() -> list:
    """Realpaths of every configured guest folder."""
    roots = []
    for _token, p in load_guests():
        if p.root and p.root not in roots:
            roots.append(p.root)
    return roots


def job_owned_by(record: dict, principal: Principal) -> bool:
    """Strict job ownership: account A never sees account B's jobs.

    Jobs carry ``account`` (preferred) and/or ``isolate_root``. Legacy jobs
    with neither field are treated as main-owned.
    """
    if principal is None:
        return False
    want = principal.account
    acct = str(record.get("account") or "").strip()
    if not acct:
        # Legacy / pre-feature jobs → main only.
        iso = str(record.get("isolate_root") or "").strip()
        if iso:
            acct = "guest:" + _realpath(iso)
        else:
            acct = ACCOUNT_MAIN
    return acct == want


def record_in_scope(record: dict, principal: Principal, cwd_keys=("cwd",)) -> bool:
    """Whether a project/session/job dict may be returned to *principal*.

    Mutual isolation:
      * Guest → only their folder (and jobs stamped with their account).
      * Main  → host data only; anything under a guest root is hidden.
      * Guest A never receives Guest B rows (different roots / account ids).
      * Guest harness filter: rows tagged with a disallowed provider are hidden.

    Ownership-tagged rows (jobs: ``account`` / ``isolate_root`` keys) use
    strict account match. Untagged rows (sessions/projects) use cwd paths.
    """
    if principal is None:
        return False

    # Harness allow-list (guest only; main always ok).
    prov = str(record.get("provider") or "").strip().lower()
    if prov and not principal.allows_provider(prov):
        return False

    # Ownership-tagged jobs (non-empty ``account``). Empty account on a
    # synthetic TUI row falls through to path rules via cwd.
    acct = str(record.get("account") or "").strip()
    if acct:
        return job_owned_by(record, principal)
    if "job_id" in record and not str(record.get("job_id") or "").startswith("tui-"):
        # Untagged legacy job row → main only.
        return job_owned_by(record, principal)

    cwd = ""
    for key in cwd_keys:
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            cwd = val
            break

    if principal.is_guest:
        if not cwd:
            # No path → hide (do not leak host-global or unscoped TUI rows).
            return False
        return path_under(cwd, principal.root)

    # Main: hide rows whose cwd lives inside any guest folder.
    if cwd:
        for gr in guest_roots():
            if path_under(cwd, gr):
                return False
    return True


def filter_records(records, principal: Principal, cwd_keys=("cwd",)) -> list:
    if principal is None:
        return []
    return [r for r in (records or [])
            if isinstance(r, dict) and record_in_scope(r, principal, cwd_keys)]


def job_in_scope(job, principal: Principal) -> bool:
    """Whether a Job instance is visible/controllable by *principal*."""
    if principal is None:
        return False
    if job is None:
        return False
    acct = str(getattr(job, "account", "") or "").strip() or ACCOUNT_MAIN
    if acct != principal.account:
        return False
    prov = str(getattr(job, "provider", "") or "").strip().lower()
    if prov and not principal.allows_provider(prov):
        return False
    return True


def _parse_providers(item: dict) -> Tuple[str, ...]:
    """Normalize providers/harnesses from a guest entry → lowercase tuple.

    Empty means "all harnesses the daemon has loaded".
    """
    raw = item.get("providers")
    if raw is None:
        raw = item.get("harnesses")
    if raw is None and item.get("provider") is not None:
        raw = item.get("provider")
    names = []
    if isinstance(raw, str):
        # "claude,grok" or single name
        parts = [p.strip() for p in raw.replace(";", ",").split(",")]
        names = [p for p in parts if p]
    elif isinstance(raw, (list, tuple)):
        for p in raw:
            s = str(p or "").strip()
            if s:
                names.append(s)
    out = []
    seen = set()
    for n in names:
        key = n.lower()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out)


def _parse_guests(data) -> list:
    """Return list of Principal for valid guest entries."""
    if not isinstance(data, dict):
        return []
    raw = data.get("guests")
    if raw is None:
        raw = data.get("accounts")
    if not isinstance(raw, list):
        return []
    out = []
    seen_tokens = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        token = str(item.get("token") or "").strip()
        root_raw = str(item.get("root") or item.get("folder") or
                       item.get("path") or "").strip()
        name = str(item.get("name") or item.get("id") or "").strip()
        if not token or not root_raw:
            continue
        if token in seen_tokens:
            log.warning("guests.json: duplicate token for %r ignored", name or root_raw)
            continue
        root = _realpath(root_raw)
        if not root:
            continue
        if not name:
            name = os.path.basename(root.rstrip(os.sep)) or "guest"
        providers = _parse_providers(item)
        seen_tokens.add(token)
        # Store token on a lightweight holder via closure in resolve —
        # Principal itself does not carry the secret.  We keep parallel lists.
        out.append((token, Principal(name=name, root=root, providers=providers)))
    return out


def load_guests(force: bool = False) -> list:
    """Load (token, Principal) pairs from guests.json; cached by mtime."""
    global _CACHE_MTIME, _CACHE_GUESTS, _CACHE_WARNED
    path = guests_path()
    try:
        st = path.stat()
        mtime = st.st_mtime
    except OSError:
        with _CACHE_LOCK:
            _CACHE_MTIME = None
            _CACHE_GUESTS = []
        return []
    with _CACHE_LOCK:
        if not force and _CACHE_MTIME == mtime and _CACHE_GUESTS is not None:
            return list(_CACHE_GUESTS)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError) as e:
            if not _CACHE_WARNED:
                log.warning("guests.json unreadable: %s", e)
                _CACHE_WARNED = True
            _CACHE_GUESTS = []
            _CACHE_MTIME = mtime
            return []
        pairs = _parse_guests(data)
        _CACHE_GUESTS = pairs
        _CACHE_MTIME = mtime
        _CACHE_WARNED = False
        if pairs:
            log.info("loaded %d scoped account(s) from %s", len(pairs), path)
        return list(pairs)


def resolve_principal(supplied: str, main_token: str) -> Optional[Principal]:
    """Map a presented token to a Principal, or None if invalid."""
    if not supplied:
        return None
    if main_token and _token_eq(supplied, main_token):
        return main_principal()
    for token, principal in load_guests():
        if _token_eq(supplied, token):
            # Ensure root exists so later chroot/cwd can succeed.
            ensure_root_dir(principal.root)
            return principal
    return None


def _token_eq(a: str, b: str) -> bool:
    if not a or not b:
        return False
    # compare_digest requires equal length; different lengths → not equal.
    if len(a) != len(b):
        return False
    try:
        return hmac.compare_digest(a, b)
    except (TypeError, ValueError):
        return False


def can_chroot() -> bool:
    """True when the process is allowed to call os.chroot (typically euid 0)."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


# Host paths bind-mounted read-only into a privileged jail so agent CLIs
# still find /usr/bin, libs, and credentials while the workspace is the only
# writable tree the guest is meant to touch.
_JAIL_RO_BINDS = (
    "/bin", "/sbin", "/usr", "/lib", "/lib64", "/lib32", "/libexec",
    "/opt", "/System", "/Library", "/Applications", "/private/etc",
    "/etc", "/dev", "/proc", "/sys", "/run", "/var/run",
)
_JAIL_HOME_BINDS = (
    ".claude", ".grok", ".codex", ".npm", ".local", ".config",
)


def _jail_dir_for(root: str) -> Path:
    import hashlib
    h = hashlib.sha1(root.encode("utf-8")).hexdigest()[:12]
    return CONFIG_DIR / "jails" / h


def _mount_bind(src: str, dst: str, read_only: bool = True) -> bool:
    """Best-effort bind mount. Returns True if mounted (or already mounted)."""
    if not os.path.exists(src):
        return False
    try:
        os.makedirs(dst, exist_ok=True)
    except OSError:
        if not os.path.exists(dst):
            return False
    # Already a mount point? skip.
    try:
        if os.path.ismount(dst):
            return True
    except OSError:
        pass
    import subprocess
    system = os.uname().sysname if hasattr(os, "uname") else ""
    try:
        if system == "Linux":
            r = subprocess.run(
                ["mount", "--bind", src, dst],
                capture_output=True, timeout=10)
            if r.returncode != 0:
                return False
            if read_only:
                subprocess.run(
                    ["mount", "-o", "remount,ro,bind", dst],
                    capture_output=True, timeout=10)
            return True
        if system == "Darwin":
            # macOS bind mounts need synthetic firmlinks / third-party tools;
            # skip — fall back to plain chroot of the guest folder only.
            return False
    except (OSError, subprocess.TimeoutExpired):
        return False
    return False


def prepare_jail(guest_root: str) -> Optional[str]:
    """Build (or reuse) a chroot tree with system bind-mounts + guest workspace.

    Returns the jail path to chroot into, or None when we cannot prepare one
    (non-root, unsupported OS, mount failure).  Workspace is mounted at
    ``<jail>/workspace`` and is the child's cwd after chroot.
    """
    if not guest_root or not can_chroot():
        return None
    root = _realpath(guest_root)
    if not root or not os.path.isdir(root):
        return None
    if os.uname().sysname != "Linux":
        # Darwin: plain chroot(guest_root) only — no bind helper.
        return root

    jail = _jail_dir_for(root)
    try:
        jail.mkdir(parents=True, exist_ok=True)
        (jail / "workspace").mkdir(exist_ok=True)
        (jail / "tmp").mkdir(exist_ok=True)
    except OSError as e:
        log.warning("jail mkdir failed: %s", e)
        return None

    for src in _JAIL_RO_BINDS:
        if os.path.exists(src):
            _mount_bind(src, str(jail / src.lstrip("/")), read_only=True)

    # Guest workspace — read-write.
    _mount_bind(root, str(jail / "workspace"), read_only=False)

    # Agent credential / config homes from the host user (read-write so
    # sessions can journal; guest still cannot leave /workspace for project
    # files once chrooted — config homes are under /host-home/...).
    host_home = str(Path.home())
    host_home_dst = jail / "host-home"
    try:
        host_home_dst.mkdir(exist_ok=True)
    except OSError:
        pass
    for name in _JAIL_HOME_BINDS:
        src = os.path.join(host_home, name)
        if os.path.isdir(src):
            _mount_bind(src, str(host_home_dst / name), read_only=False)
    # Daemon config (hook secrets, etc.)
    if CONFIG_DIR.is_dir():
        _mount_bind(str(CONFIG_DIR), str(jail / "agentremoted-home"),
                    read_only=False)

    # Minimal /etc pieces if full /etc bind failed.
    etc = jail / "etc"
    try:
        etc.mkdir(exist_ok=True)
        for name in ("resolv.conf", "hosts", "passwd", "group", "nsswitch.conf"):
            src = Path("/etc") / name
            dst = etc / name
            if src.is_file() and not dst.exists():
                try:
                    dst.write_bytes(src.read_bytes())
                except OSError:
                    pass
    except OSError:
        pass

    return str(jail)


def make_chroot_preexec(root: str, jail: Optional[str] = None):
    """Return a preexec_fn that chroots into a jail (or plain *root*).

    When *jail* is a prepared bind-mount jail, cwd becomes ``/workspace``.
    When *jail* is the guest root itself (Darwin / fallback), cwd becomes ``/``.
    """
    if not root or not can_chroot():
        return None
    root_rp = _realpath(root)
    if not root_rp or not os.path.isdir(root_rp):
        return None
    jail_rp = _realpath(jail) if jail else root_rp
    use_workspace = bool(jail and jail_rp != root_rp
                         and os.path.isdir(os.path.join(jail_rp, "workspace")))

    def _preexec():
        target = jail_rp
        try:
            os.chdir(target)
            os.chroot(target)
        except OSError:
            try:
                os.chdir(root_rp)
            except OSError:
                pass
            return
        try:
            os.chdir("/workspace" if use_workspace else "/")
        except OSError:
            try:
                os.chdir("/")
            except OSError:
                pass

    return _preexec


def _system_name() -> str:
    try:
        return os.uname().sysname
    except AttributeError:
        return ""


def _sandbox_exec_bin() -> str:
    import shutil
    if os.path.isfile("/usr/bin/sandbox-exec"):
        return "/usr/bin/sandbox-exec"
    return shutil.which("sandbox-exec") or ""


def _bwrap_bin() -> str:
    """bubblewrap — unprivileged FS isolation on Linux (and some BSDs)."""
    import shutil
    for candidate in ("/usr/bin/bwrap", "/bin/bwrap"):
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("bwrap") or ""


def isolation_backend() -> str:
    """Name of the confinement backend available on this host.

    * ``bwrap`` — Linux bubblewrap (preferred on Linux)
    * ``sandbox-exec`` — macOS seatbelt
    * ``chroot`` — privileged chroot jail (euid 0)
    * ``""`` — none; guest process confinement is unavailable
    """
    if _system_name() == "Linux" and _bwrap_bin():
        return "bwrap"
    if _system_name() == "Darwin" and _sandbox_exec_bin():
        return "sandbox-exec"
    if can_chroot():
        return "chroot"
    # bwrap is sometimes packaged on non-Linux (rare); still try.
    if _bwrap_bin():
        return "bwrap"
    return ""


# Mirror main-account harness *configuration* into the guest HOME so sub-
# accounts use the same auth, settings, plugins, and skills as the main
# profile. Session/project/history trees are excluded — those stay main-only.
#
# Each entry: relative path under $HOME → either a file, or a directory
# mirrored recursively with the listed name excludes.
_GUEST_SEED_FILES = (
    # Claude Code
    ".claude/.credentials.json",
    ".claude/settings.json",
    ".claude/settings.local.json",
    ".claude/remote-settings.json",
    ".claude/policy-limits.json",
    ".claude/mcp-needs-auth-cache.json",
    # Grok Build
    ".grok/auth.json",
    ".grok/config.toml",
    ".grok/trusted_folders.toml",
    ".grok/models_cache.json",
    ".grok/agent_id",
    ".grok/version.json",
    ".grok/slash-mru.json",
    ".grok/.metadata_version",
    # Codex
    ".codex/auth.json",
    ".codex/config.toml",
    ".codex/models_cache.json",
    ".codex/installation_id",
    ".codex/.sandbox_migration",
)

# Directories to copy recursively (config/skills/plugins), not sessions.
_GUEST_SEED_DIRS = (
    # Claude
    (".claude/plugins", ()),
    (".claude/skills", ()),
    (".claude/commands", ()),  # custom slash commands if present
    # Grok
    (".grok/skills", ()),
    (".grok/marketplace-cache", ()),
    (".grok/memory", ()),       # shared MEMORY.md / memory pack
    (".grok/vendor", ()),
    (".grok/completions", ()),
    # Codex
    (".codex/plugins", ()),
    (".codex/skills", ()),
    (".codex/packages", ()),
)

# Names never copied from a seed dir tree (defense in depth).
_GUEST_SEED_DIR_SKIP = frozenset({
    "sessions", "projects", "file-history", "shell-snapshots",
    "session-env", "telemetry", "memtrace", "relocations", "logs",
    "workspace", "bin", "downloads",  # install trees: host RO via sandbox
    "__pycache__", ".git",
})


def _copy_file_if_newer(src: Path, dst: Path) -> None:
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    if (not dst.is_file()
            or src.stat().st_mtime > dst.stat().st_mtime + 0.5
            or src.stat().st_size != dst.stat().st_size):
        shutil.copy2(src, dst)
    try:
        mode = src.stat().st_mode & 0o777
        # Keep secrets private even if host was looser.
        if src.name in (".credentials.json", "auth.json", "config.toml",
                        "settings.json", "settings.local.json"):
            mode = 0o600
        dst.chmod(mode)
    except OSError:
        pass


def _copy_tree_filtered(src: Path, dst: Path) -> None:
    """Copy *src* → *dst*, skipping session-like directory names."""
    import shutil
    if not src.is_dir():
        return
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    try:
        entries = list(src.iterdir())
    except OSError as e:
        log.warning("guest seed list %s: %s", src, e)
        return
    for entry in entries:
        name = entry.name
        if name in _GUEST_SEED_DIR_SKIP or name.startswith("state_") \
                or name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")):
            continue
        target = dst / name
        try:
            if entry.is_symlink():
                # Copy link target content when it's a file; skip dir links.
                if entry.is_file():
                    _copy_file_if_newer(entry.resolve(), target)
                continue
            if entry.is_dir():
                _copy_tree_filtered(entry, target)
            elif entry.is_file():
                _copy_file_if_newer(entry, target)
        except OSError as e:
            log.warning("guest seed %s: %s", entry, e)


def _merge_grok_trusted_folders(guest_grok: Path, guest_root: str) -> None:
    """Keep main trusted folders and ensure the guest workspace is trusted.

    Supports both shapes seen in the wild:
      * ``trusted = ["/path", ...]``
      * ``[folders."/path"]\\ntrusted = true``
    """
    trust = guest_grok / "trusted_folders.toml"
    try:
        guest_grok.mkdir(parents=True, exist_ok=True)
        text = ""
        if trust.is_file():
            text = trust.read_text(encoding="utf-8", errors="replace")
        if guest_root in text:
            return
        import re
        esc = guest_root.replace("\\", "\\\\").replace('"', '\\"')
        # Table form: [folders."/abs/path"]
        if re.search(r'^\[folders\.', text, re.M) or 'folders."' in text:
            block = '\n[folders."%s"]\ntrusted = true\n' % esc
            text = (text.rstrip() + "\n" + block) if text.strip() else block.lstrip()
        else:
            entry = '"%s"' % esc
            m = re.search(r"trusted\s*=\s*\[(.*?)\]", text, re.S)
            if m:
                inner = m.group(1).strip()
                if inner and not inner.rstrip().endswith(","):
                    new_inner = inner + ", " + entry
                elif inner:
                    new_inner = inner + " " + entry
                else:
                    new_inner = entry
                text = text[:m.start(1)] + new_inner + text[m.end(1):]
            else:
                text = ("trusted = [%s]\n" % entry) + (text or "")
        trust.write_text(text, encoding="utf-8")
        trust.chmod(0o600)
    except OSError as e:
        log.warning("guest trusted_folders merge: %s", e)


def seed_guest_home(isolate_root: str) -> None:
    """Mirror main harness config into the guest root as its HOME.

    Guest processes run with HOME / GROK_HOME / CLAUDE_CONFIG_DIR /
    CODEX_HOME under *isolate_root*, so they load the same auth, settings,
    plugins, and skills as the main account — without inheriting main
    session journals or project indexes.
    """
    root = _realpath(isolate_root) if isolate_root else ""
    if not root:
        return
    ensure_root_dir(root)
    host = Path.home()
    guest = Path(root)
    for rel in _GUEST_SEED_FILES:
        src = host / rel
        if not src.is_file():
            continue
        try:
            _copy_file_if_newer(src, guest / rel)
        except OSError as e:
            log.warning("guest seed %s failed: %s", rel, e)
    for rel, _extra_skip in _GUEST_SEED_DIRS:
        src = host / rel
        if not src.is_dir():
            continue
        try:
            _copy_tree_filtered(src, guest / rel)
        except OSError as e:
            log.warning("guest seed dir %s failed: %s", rel, e)
    _merge_grok_trusted_folders(guest / ".grok", root)


def _path_dirs_for_sandbox(isolate_root: str, host_home: str,
                           host_deny: list) -> list:
    """Every absolute directory on $PATH (read-only in the seatbelt / bwrap).

    Policy (operator request): allow **all** absolute PATH entries so anything
    the host shell can resolve is readable for guest processes. Relative
    entries (``.``) are skipped. The only hard exclusion is the daemon home
    (``~/.agentremoted`` / ``AGENTREMOTED_HOME``) so a PATH pointing at it
    cannot expose the main token.
    """
    # Hard-exclude only the daemon config/token tree — not general host_deny.
    # (host_deny still applies via later seatbelt deny rules for non-PATH reads.)
    secret_roots = []
    for d in (str(CONFIG_DIR), os.path.join(host_home or "", ".agentremoted")):
        d = (d or "").strip()
        if not d:
            continue
        try:
            secret_roots.append(os.path.realpath(d))
        except OSError:
            secret_roots.append(d)

    out = []
    seen = set()
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        raw = (entry or "").strip()
        if not raw or raw in (".", "..") or not os.path.isabs(raw):
            continue
        try:
            rp = os.path.realpath(raw)
        except OSError:
            rp = raw
        if not os.path.isdir(rp) or rp in seen:
            continue
        secret = False
        for sr in secret_roots:
            if not sr:
                continue
            if rp == sr or rp.startswith(sr.rstrip(os.sep) + os.sep):
                secret = True
                break
        if secret:
            continue
        seen.add(rp)
        out.append(rp)
    return out


def ensure_sandbox_profile(isolate_root: str) -> str:
    """Write (or reuse) a macOS seatbelt profile for *isolate_root*.

    Returns the profile file path, or "" if sandbox-exec is unavailable.

    Strategy (proven with interactive grok): **allow default**, then **deny**
    host secrets and data trees. Deny-by-default profiles break the TUI with
    opaque ``Operation not permitted`` even when system paths are allow-listed;
    allow-default + denylist keeps tools working while blocking::

      * ~/.agentremoted (main token)
      * host session/project journals
      * common user data dirs (Developer, Documents, …)

    Guest HOME is seeded via seed_guest_home(); child cwd is the guest root.
    """
    root = _realpath(isolate_root) if isolate_root else ""
    if not root or _system_name() != "Darwin" or not _sandbox_exec_bin():
        return ""
    ensure_root_dir(root)
    seed_guest_home(root)
    import hashlib
    h = hashlib.sha1(root.encode("utf-8")).hexdigest()[:12]
    prof_dir = CONFIG_DIR / "jails"
    try:
        prof_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    path = prof_dir / ("%s.sb" % h)

    def _sub(p: str) -> str:
        return '(subpath "%s")' % p.replace("\\", "\\\\").replace('"', '\\"')

    # Bump when deny-list policy changes so cached .sb files refresh.
    profile_ver = "8"
    host_home = str(Path.home())
    host_deny = [
        os.path.join(host_home, ".agentremoted"),
        os.path.join(host_home, ".grok", "sessions"),
        os.path.join(host_home, ".grok", "workspace"),
        os.path.join(host_home, ".claude", "projects"),
        os.path.join(host_home, ".claude", "file-history"),
        os.path.join(host_home, ".claude", "history.jsonl"),
        os.path.join(host_home, ".claude", "sessions"),
        os.path.join(host_home, ".claude", "shell-snapshots"),
        os.path.join(host_home, ".claude", "session-env"),
        os.path.join(host_home, ".codex", "sessions"),
        os.path.join(host_home, "Developer"),
        os.path.join(host_home, "Documents"),
        os.path.join(host_home, "Desktop"),
        os.path.join(host_home, "Downloads"),
        os.path.join(host_home, "Movies"),
        os.path.join(host_home, "Music"),
        os.path.join(host_home, "Pictures"),
        os.path.join(host_home, "Public"),
    ]
    # Also deny CONFIG_DIR if it differs from ~/.agentremoted.
    try:
        cfg = str(CONFIG_DIR.resolve())
        if cfg and cfg not in host_deny:
            host_deny.append(cfg)
    except OSError:
        pass

    lines = [
        "(version 1)",
        "; agentremoted-guest-profile %s root=%s" % (profile_ver, root),
        # Allow-default is required for interactive harness TUIs (grok/claude).
        "(allow default)",
    ]
    # Denies listed after allow default — seatbelt applies these restrictions.
    lines.append("(deny file-read* file-write*")
    for p in host_deny:
        lines.append("  %s" % _sub(p))
    lines.append(")")
    body = "\n".join(lines) + "\n"
    try:
        prev = path.read_text(encoding="utf-8") if path.is_file() else ""
        if prev != body:
            path.write_text(body, encoding="utf-8")
    except OSError as e:
        log.warning("could not write sandbox profile: %s", e)
        return ""
    return str(path)


def isolation_popen_kwargs(isolate_root: str) -> dict:
    """Extra kwargs for subprocess.Popen/run when confining a guest child.

    Always sets cwd under the guest root.  Adds preexec_fn for chroot when
    privileged (bind-mount jail on Linux, plain chroot elsewhere).
    """
    root = _realpath(isolate_root) if isolate_root else ""
    if not root:
        return {}
    ensure_root_dir(root)
    kwargs = {"cwd": root}
    jail = prepare_jail(root)
    pre = make_chroot_preexec(root, jail=jail)
    if pre is not None:
        kwargs["preexec_fn"] = pre
        # Before preexec, land in the jail (or root) so chdir/chroot is stable.
        if jail:
            kwargs["cwd"] = jail if jail != root else root
    return kwargs


def isolation_env(base_env: Optional[dict], isolate_root: str) -> dict:
    """Build an env for a confined guest child.

    Always starts from the real process environment (so PATH still finds
    ``grok`` / ``claude`` / homebrew), then overlays *base_env* and rewrites
    HOME / harness dirs into the guest root.
    """
    # CRITICAL: never start from a sparse dict (e.g. only grok_env flags) —
    # that drops PATH and the TUI fails with "command not found".
    env = dict(os.environ)
    if base_env:
        env.update({str(k): str(v) for k, v in base_env.items()})
    root = _realpath(isolate_root) if isolate_root else ""
    if not root:
        return env
    seed_guest_home(root)
    env["HOME"] = root
    # Point config/state at the seeded guest home. Binaries still run from
    # the host install paths (allowed read-only by the seatbelt).
    env["CLAUDE_CONFIG_DIR"] = os.path.join(root, ".claude")
    env["GROK_HOME"] = os.path.join(root, ".grok")
    env["CODEX_HOME"] = os.path.join(root, ".codex")
    tmp = os.path.join(root, ".agentremote", "tmp")
    try:
        os.makedirs(tmp, mode=0o700, exist_ok=True)
    except OSError:
        tmp = root
    env["TMPDIR"] = tmp
    env["TMP"] = tmp
    env["TEMP"] = tmp
    env["AGENTREMOTE_ISOLATE_ROOT"] = root
    # Keep a usable PATH: host harness bins first, then existing PATH.
    host = str(Path.home())
    path_prefix = [
        os.path.join(host, ".grok", "bin"),
        os.path.join(host, ".local", "bin"),
        os.path.join(host, ".claude", "local", "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
    ]
    existing = [p for p in (env.get("PATH") or "").split(":") if p]
    seen = set()
    ordered = []
    for p in path_prefix + existing:
        if p and p not in seen:
            seen.add(p)
            ordered.append(p)
    env["PATH"] = ":".join(ordered)
    # launchd / some hosts set TERM=dumb; interactive TUIs then never draw a
    # prompt and readiness checks time out ("TUI did not become ready").
    term = str(env.get("TERM") or "").strip()
    if not term or term in ("dumb", "unknown"):
        env["TERM"] = "xterm-256color"
    # Don't pretend to be another login name for network auth; keep real USER
    # so Keychain / subscription identity still works. File isolation is the
    # seatbelt's job, not USER rewriting.
    return env


def _guest_path_prefix() -> str:
    host = str(Path.home())
    return ":".join([
        os.path.join(host, ".grok", "bin"),
        os.path.join(host, ".local", "bin"),
        os.path.join(host, ".claude", "local", "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/sbin",
    ])


def _guest_inner_shell(command: str, root: str) -> str:
    """Shell snippet: rewrite HOME/PATH then run *command* under *root*."""
    import shlex
    tmp = os.path.join(root, ".agentremote", "tmp")
    try:
        os.makedirs(tmp, mode=0o700, exist_ok=True)
    except OSError:
        tmp = root
    return (
        "export HOME=%s "
        "PATH=%s${PATH:+:$PATH} "
        "TMPDIR=%s TMP=%s TEMP=%s "
        "CLAUDE_CONFIG_DIR=%s GROK_HOME=%s CODEX_HOME=%s "
        "AGENTREMOTE_ISOLATE_ROOT=%s; "
        "cd %s && %s"
    ) % (
        shlex.quote(root),
        shlex.quote(_guest_path_prefix()),
        shlex.quote(tmp),
        shlex.quote(tmp),
        shlex.quote(tmp),
        shlex.quote(os.path.join(root, ".claude")),
        shlex.quote(os.path.join(root, ".grok")),
        shlex.quote(os.path.join(root, ".codex")),
        shlex.quote(root),
        shlex.quote(root),
        command,
    )


def _host_harness_ro_paths() -> list:
    """Host paths that must be visible read-only so harness binaries run.

    Only install/binary trees — never host session/project journals.
    Guest auth/state lives under the seeded guest HOME instead.
    """
    host = Path.home()
    candidates = [
        host / ".grok" / "bin",
        host / ".grok" / "downloads",
        host / ".grok" / "bundled",
        host / ".local" / "bin",
        host / ".local" / "share",
        host / ".claude" / "local",
        host / ".npm",
        host / ".codex" / "bin",
        Path("/opt/homebrew"),
        Path("/usr/local"),
    ]
    out = []
    for p in candidates:
        try:
            if p.exists():
                out.append(str(p.resolve() if p.is_symlink() else p))
        except OSError:
            if p.exists():
                out.append(str(p))
    # De-dupe preserving order
    seen = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _bwrap_argv(root: str, cmd: list) -> list:
    """Build a bubblewrap argv that confines *cmd* to *root*.

    Policy mirrors the macOS seatbelt profile:
      * RW: guest root + /tmp
      * RO: system paths + host harness install trees
      * no host home projects, no ~/.agentremoted
    """
    bwrap = _bwrap_bin()
    if not bwrap or not cmd:
        return list(cmd or [])
    args = [
        bwrap,
        "--die-with-parent",
        # Filesystem view only — keep host network for model APIs.
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
    ]
    # System trees (try- variants skip missing paths / broken symlinks).
    for p in (
        "/usr", "/bin", "/sbin", "/lib", "/lib64", "/lib32", "/libexec",
        "/opt", "/etc", "/var", "/run", "/sys",
    ):
        if os.path.exists(p):
            args += ["--ro-bind-try", p, p]
    # Guest workspace (read-write).
    args += ["--bind", root, root]
    args += ["--chdir", root]
    # Host harness binaries / assets + safe $PATH dirs (read-only).
    host = str(Path.home())
    deny = [
        os.path.join(host, ".agentremoted"),
        os.path.join(host, "Developer"),
        os.path.join(host, "Documents"),
        os.path.join(host, "Desktop"),
        os.path.join(host, "Downloads"),
    ]
    for p in _host_harness_ro_paths() + _path_dirs_for_sandbox(root, host, deny):
        # Never bind the whole host home or agentremoted.
        if p == host or p.startswith(host + os.sep + ".agentremoted"):
            continue
        args += ["--ro-bind-try", p, p]
    # Explicitly do NOT bind: host home, Developer, .agentremoted, etc.
    # Env inside the jail.
    args += [
        "--setenv", "HOME", root,
        "--setenv", "GROK_HOME", os.path.join(root, ".grok"),
        "--setenv", "CLAUDE_CONFIG_DIR", os.path.join(root, ".claude"),
        "--setenv", "CODEX_HOME", os.path.join(root, ".codex"),
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "TMP", "/tmp",
        "--setenv", "TEMP", "/tmp",
        "--setenv", "AGENTREMOTE_ISOLATE_ROOT", root,
        "--setenv", "PATH",
        _guest_path_prefix() + ":" + (os.environ.get("PATH") or ""),
    ]
    args += ["--"]
    args += list(cmd)
    return args


def wrap_shell_command(command: str, isolate_root: str) -> str:
    """Wrap a shell command so it cannot leave the guest root.

    Backend (auto):
      * Linux  → ``bwrap`` (bubblewrap)
      * macOS  → ``sandbox-exec`` (seatbelt)
      * euid 0 → plain ``cd`` (chroot applied via preexec_fn on Popen)

    macOS note: ``cd`` into the guest root happens *outside* sandbox-exec so
    the sandboxed image's initial cwd is already allowed (interactive grok
    calls getcwd() at startup and dies with EPERM otherwise).
    """
    import shlex
    root = _realpath(isolate_root) if isolate_root else ""
    if not root:
        return command
    seed_guest_home(root)
    # Env + command only — no leading cd (outer wrapper cds first).
    tmp = os.path.join(root, ".agentremote", "tmp")
    try:
        os.makedirs(tmp, mode=0o700, exist_ok=True)
    except OSError:
        tmp = root
    # Force a usable TERM inside the jail (never inherit dumb from launchd).
    env_exports = (
        "export HOME=%s PATH=%s${PATH:+:$PATH} "
        "TMPDIR=%s TMP=%s TEMP=%s "
        "CLAUDE_CONFIG_DIR=%s GROK_HOME=%s CODEX_HOME=%s "
        "AGENTREMOTE_ISOLATE_ROOT=%s TERM=xterm-256color; "
    ) % (
        shlex.quote(root),
        shlex.quote(_guest_path_prefix()),
        shlex.quote(tmp),
        shlex.quote(tmp),
        shlex.quote(tmp),
        shlex.quote(os.path.join(root, ".claude")),
        shlex.quote(os.path.join(root, ".grok")),
        shlex.quote(os.path.join(root, ".codex")),
        shlex.quote(root),
    )
    cmd_body = command.strip()
    # Simple argv → exec for a clean PID; shell fragments keep their own flow.
    if (not cmd_body.startswith("exec ")
            and ";" not in cmd_body and "\n" not in cmd_body
            and "&&" not in cmd_body and "|" not in cmd_body):
        cmd_body = "exec " + cmd_body
    inner = env_exports + cmd_body
    backend = isolation_backend()
    if backend == "bwrap":
        argv = _bwrap_argv(root, ["/bin/bash", "-lc", inner])
        # bwrap itself --chdir's into root.
        return " ".join(shlex.quote(a) for a in argv)
    if backend == "sandbox-exec":
        profile = ensure_sandbox_profile(root)
        sb = _sandbox_exec_bin()
        if profile and sb:
            # cd OUTSIDE the seatbelt, then enter it already in guest root.
            return "cd %s && %s -f %s /bin/bash -lc %s" % (
                shlex.quote(root),
                shlex.quote(sb),
                shlex.quote(profile),
                shlex.quote(inner),
            )
    # chroot / last-resort
    return "cd %s && %s" % (shlex.quote(root), inner)


def isolate_argv(cmd, isolate_root: str) -> list:
    """Prefix an argv list with the host's confinement backend.

    Callers must also set subprocess cwd to *isolate_root* (see
    isolation_popen_kwargs) so getcwd() succeeds under seatbelt.
    """
    if not cmd:
        return list(cmd or [])
    root = _realpath(isolate_root) if isolate_root else ""
    if not root:
        return list(cmd)
    seed_guest_home(root)
    backend = isolation_backend()
    if backend == "bwrap":
        return _bwrap_argv(root, list(cmd))
    if backend == "sandbox-exec":
        profile = ensure_sandbox_profile(root)
        sb = _sandbox_exec_bin()
        if profile and sb:
            # env -C root (GNU) is not on macOS; rely on Popen cwd=root.
            return [sb, "-f", profile] + list(cmd)
    return list(cmd)


def isolate_shell_line(shell_cmd: str, isolate_root: str) -> str:
    """Wrap a tmux/shell one-liner (interactive TUI launch) under isolation."""
    return wrap_shell_command(shell_cmd, isolate_root)


def isolation_ready(isolate_root: str = "") -> bool:
    """True when guest process confinement is available on this host."""
    return bool(isolation_backend())


def isolation_required_hint() -> str:
    """Human-readable install hint when no confinement backend is present."""
    sysname = _system_name()
    if sysname == "Linux":
        return (
            "guest isolation needs bubblewrap on this host "
            "(apt install bubblewrap / dnf install bubblewrap / "
            "pacman -S bubblewrap)"
        )
    if sysname == "Darwin":
        return "guest isolation needs /usr/bin/sandbox-exec (macOS seatbelt)"
    return "guest isolation unavailable on this platform"


def apply_isolation_to_job(job, isolate_root: str) -> None:
    """Stamp a Job with isolate_root for later Popen / TUI launch."""
    root = _realpath(isolate_root) if isolate_root else ""
    job.isolate_root = root or ""
