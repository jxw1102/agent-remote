"""Interactive mode for grok: drive a real `grok` TUI inside tmux.

Why this exists: headless `grok -p` frequently hangs mid-turn, while the TUI
is the path xAI actually exercises. So the phone gets the same second
execution mode claude has ("interactive"): the daemon keeps one detached tmux
session per grok session, types the prompt into it, and reads the turn back.

Unlike claude's TUI bridge this needs no hooks and no companion plugin,
because grok hands us both things hooks were used for:

  * the session id is ours to choose. `-s <uuid>` pins the id of a NEW
    conversation, and `--resume <id>` keeps the same id (grok only forks with
    --fork-session) — so no SessionStart hook and no fs-diff scan.
  * every session journals to ~/.grok/sessions/<munged-cwd>/<id>/updates.jsonl,
    whose `turn_completed` record is exactly the turn-end signal a Stop hook
    would provide. That journal also carries the turn's text, thoughts and
    tool calls, so GrokRunner's existing tail parser is reused verbatim (it
    is already the live-status source for headless turns).

The TUI runs with --permission-mode bypassPermissions (grok renders it as
"always-approve") and --trust-folder: permission and folder-trust prompts
would appear inside a pane nobody can see, so interactive mode is by
definition an auto mode — the phone UI says so.

The same machinery backs the phone's Usage button (see fetch_usage): grok
publishes its subscription limits only inside the TUI's /usage command, so
the daemon reads them out of a throwaway TUI.
"""

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid

from ..config import CONFIG_DIR
from ..render_blocks import markdown_to_blocks

log = logging.getLogger(__name__)

_MAX_TUIS = 6             # live TUI cap; oldest idle ones are evicted
_START_TIMEOUT_S = 90     # grok TUI cold start (incl. MCP connects)
_POLL_S = 0.3             # journal tail poll
_PASTE_SETTLE_S = 0.3     # between paste-buffer and Enter
_READY_SETTLE_S = 0.8     # between "ready" pane and first paste

# A /command is handled inside the TUI and never reaches the model, so no
# turn_completed arrives: those turns end on a quiet journal instead.
_LOCAL_QUIET_S = 2.5

# A pasted prompt can land in the input box with the Enter lost, leaving the
# turn waiting on a message grok never saw. Confirm the submit against the
# journal and re-press Enter this many times before giving up on confirming.
_SUBMIT_CONFIRM_S = 3.0
_SUBMIT_RETRIES = 2

# After answering a plan-approval panel, how long to wait for grok to clear
# awaiting_plan_approval in plan_mode.json before sending the prompt, and
# how long to let its reply to that answer finish first.
_PLAN_CLEAR_S = 8.0
_PLAN_REPLY_S = 90.0

# "Ready for input" = the prompt chevron sits at the bottom of the pane.
# Every screen mode draws it (fullscreen boxes it in │…│, --minimal puts it
# bare on its own line), so this survives xAI rewording the hint lines —
# missing the signal would stall every launch until the 90s start timeout.
# It is anchored to the last few rows on purpose: a resumed conversation
# echoes old prompts as "❯ …" lines further up the scrollback.
_READY_TAIL_ROWS = 3
_READY_CHEVRON = "❯"
# Footers kept as a fallback, in case a future layout parks the chevron
# further from the bottom.
_READY_FOOTERS = ("Shift+Tab:mode", "ctrl+o transcript", "minimal · /help")


def _pane_rows(text: str) -> list:
    """Non-blank pane rows, stripped of box-drawing chrome."""
    return [l.strip().strip("│").strip()
            for l in text.splitlines() if l.strip()]


def _pane_ready(text: str) -> bool:
    for row in _pane_rows(text)[-_READY_TAIL_ROWS:]:
        if row.startswith(_READY_CHEVRON):
            return True
    return any(m in text for m in _READY_FOOTERS)


_TMUX_FALLBACK = "/opt/homebrew/bin/tmux"


def tmux_available() -> bool:
    """Whether this host can run TUIs at all (interactive mode, /usage)."""
    return bool(shutil.which("tmux")) or os.path.exists(_TMUX_FALLBACK)

# "/rewind [N]" drives grok's checkpoint picker rather than being pasted as a
# prompt (it needs arrow keys). Its two screens have no journal trail while
# open — no model turn runs — so these markers are the only signal.
_REWIND_RE = re.compile(r"^/rewind(?:\s+(\d+))?\s*$")
_REWIND_PICKER = "Rewind to which turn?"
_REWIND_SCOPE = "What do you want to rewind?"
_REWIND_OPEN_S = 8.0

# tmux name -> grok session mapping, persisted so a daemon restart re-adopts
# the running TUIs instead of killing them.
_STATE_FILE = CONFIG_DIR / "grok-tuis.json"
_PREFIX = "grk-"          # `tmux ls` reads as grok at a glance (claude = cld-*)
_OLD_PREFIXES = ("grk-", "bb10g-")   # legacy names still adopted/reaped

# ---------------------------------------------------------------- usage probe
#
# The dedicated /usage TUI. Its tmux name is fixed (one probe at a time, and a
# leaked one is reaped by _adopt_or_reap as an unknown grk-* session), and its
# grok session id is remembered so every fetch resumes the same conversation.
_USAGE_NAME = _PREFIX + "usage"
_USAGE_STATE_FILE = CONFIG_DIR / "grok-usage-session.json"
_USAGE_START_S = 45       # cold `grok --resume` (warm: ~1.5s)
_USAGE_RENDER_S = 20      # /usage printing its lines (warm: ~0.3s)
_USAGE_EXIT_S = 3         # /exit closing the pane before we kill it

# What /usage prints (grok 0.2.118, --minimal):
#     Session usage: no model calls yet in this session.
#     Weekly limit: 17%
#     Next reset: August 2, 17:39
# Parsed generically — any "<label>: <n>%" row is a bucket, so a future
# hourly/monthly row shows up on the phone without a daemon change.
_USAGE_PCT_RE = re.compile(r"^([A-Za-z][^:]{0,40}):\s*(\d{1,3}(?:\.\d+)?)\s*%$")
_USAGE_RESET_RE = re.compile(r"^(?:next reset|resets?)\s*:\s*(.+)$", re.I)
_USAGE_WARN_PCT = 75
_USAGE_CRIT_PCT = 90


def _usage_buckets(rows: list) -> list:
    """Turn /usage rows into the phone's {title, percent, resets_text,
    severity} bars — the same shape claude's usage endpoint yields, so the
    Usage sheet renders both providers unchanged.

    A "Next reset:" row belongs to the limit above it (grok prints them as a
    pair), so it is attached to the bucket most recently opened."""
    buckets = []
    for row in rows:
        m = _USAGE_PCT_RE.match(row)
        if m:
            try:
                pct = max(0, min(100, int(round(float(m.group(2))))))
            except (TypeError, ValueError):
                continue
            buckets.append({
                "title": m.group(1).strip(),
                "percent": pct,
                "resets_text": "",
                "severity": ("critical" if pct >= _USAGE_CRIT_PCT else
                             "warning" if pct >= _USAGE_WARN_PCT else "normal"),
            })
            continue
        m = _USAGE_RESET_RE.match(row)
        if m and buckets and not buckets[-1]["resets_text"]:
            buckets[-1]["resets_text"] = "Resets " + m.group(1).strip()
    return buckets


def _new_rows(before: list, after: list) -> list:
    """Pane rows that appeared since `before`.

    --minimal appends finalized output to the scrollback, so everything that
    was already on screen is a shared prefix and the remainder is what the
    command just printed. If the pane scrolled the prefix breaks and this
    returns the whole screen — still parseable, just less precise."""
    n = 0
    while n < len(before) and n < len(after) and before[n] == after[n]:
        n += 1
    return after[n:]


class _Tui:
    """One detached tmux session hosting one grok TUI."""

    def __init__(self, name: str, cwd: str):
        self.name = name
        self.cwd = cwd
        self.session_id = ""          # grok session id (we choose it)
        self.spawned = False          # tmux session created (prunable when dead)
        self.last_used = time.time()  # LRU stamp for the _MAX_TUIS cap
        self.job = None               # job of the turn in flight, if any
        self.typed_ahead = 0          # messages typed into the pane mid-turn
        self.lock = threading.Lock()  # one turn at a time per TUI


class GrokInteractiveManager:
    """Owns the tmux TUIs and runs "interactive" jobs against them."""

    def __init__(self, config, runner):
        self.config = config
        self.runner = runner          # GrokRunner: journal parser + store
        self._tuis = {}               # tmux name -> _Tui
        self._lock = threading.Lock()
        self._usage_lock = threading.Lock()   # one /usage probe at a time
        self._usage_sid = None        # dedicated usage session id (None = unread)
        self._adopt_or_reap()

    # -- registry ----------------------------------------------------------

    def _save_state(self):
        """Persist the name->session mapping (see _adopt_or_reap). Only
        launched TUIs: a registered-but-unlaunched one has no tmux session."""
        rows = [{"name": t.name, "cwd": t.cwd, "session_id": t.session_id,
                 "last_used": t.last_used}
                for t in list(self._tuis.values()) if t.spawned]
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = str(_STATE_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rows, f)
            os.replace(tmp, str(_STATE_FILE))
        except OSError:
            pass

    def _adopt_or_reap(self):
        """Re-adopt the TUIs of a previous daemon run: their tmux sessions
        outlive us and stay fully addressable, so the only thing a restart
        loses is this registry — reloaded here from disk. A grk-* session
        we have no record of can never receive a turn, so it is killed."""
        known = {}
        try:
            saved = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = []
        for entry in saved if isinstance(saved, list) else []:
            if isinstance(entry, dict) and str(entry.get("name", "")).startswith(_OLD_PREFIXES):
                known[str(entry["name"])] = entry
        try:
            r = self._tmux("list-sessions", "-F", "#{session_name}", capture=True)
            if r.returncode != 0:
                return
            for name in r.stdout.decode("utf-8", errors="replace").split():
                if not name.startswith(_OLD_PREFIXES):
                    continue
                entry = known.get(name)
                if entry is None:
                    log.info("reaping unknown grok TUI %s", name)
                    self._tmux("kill-session", "-t", name)
                    continue
                tui = _Tui(name, str(entry.get("cwd", ""))
                           or os.path.expanduser("~"))
                tui.session_id = str(entry.get("session_id", ""))
                tui.spawned = True
                try:
                    tui.last_used = float(entry.get("last_used") or 0) or time.time()
                except (TypeError, ValueError):
                    tui.last_used = time.time()
                self._tuis[name] = tui
                log.info("adopted grok TUI %s (session %s)", name,
                         tui.session_id[:8] or "unknown")
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._save_state()

    # -- tmux helpers ------------------------------------------------------

    @property
    def _tmux_bin(self):
        return shutil.which("tmux") or "/opt/homebrew/bin/tmux"

    def _tmux(self, *args, capture=False, input_bytes=None):
        return subprocess.run(
            [self._tmux_bin] + list(args), input=input_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=15)

    def _tmux_alive(self, name: str) -> bool:
        try:
            return self._tmux("has-session", "-t", name).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _pane_text(self, name: str, *, ansi: bool = False) -> str:
        # -e keeps SGR colour sequences for Live TUI clients; internal
        # readiness / busy checks use plain text (ansi=False).
        args = ["capture-pane", "-p"]
        if ansi:
            args.append("-e")
        args.extend(["-t", name])
        try:
            out = self._tmux(*args, capture=True)
            return out.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            return ""

    def _pane_tail(self, name: str, lines: int = 6) -> str:
        """Last visible pane lines — the only diagnostics an interactive
        failure has (login screens, upsell panels, crashes)."""
        rows = [l.rstrip() for l in self._pane_text(name).splitlines() if l.strip()]
        return " | ".join(rows[-lines:])

    def _pane_snapshot(self, name: str) -> str:
        """The pane minus its chrome — the only output a /command that draws
        a panel instead of journalling anything leaves behind."""
        rows = []
        for l in self._pane_text(name).splitlines():
            s = l.strip().strip("│").strip()
            if not s or s.startswith("❯") or _pane_ready(s):
                continue
            if set(s) <= set("─╭╮╰╯│ ┃"):
                continue
            rows.append(s)
        return "\n".join(rows)[-4000:]

    # -- TUI lifecycle -----------------------------------------------------

    def _tui_name(self, cwd: str) -> str:
        """Unique per TUI (not per project): <cwd-hash>-<random> so parallel
        sessions in one project each get their own tmux session."""
        h = hashlib.sha1(os.path.realpath(cwd).encode("utf-8")).hexdigest()[:8]
        return "%s%s-%s" % (_PREFIX, h, uuid.uuid4().hex[:6])

    def _disable_project_picker(self):
        """Make sure grok never opens its "Run Grok Build in a project
        directory?" picker in a daemon TUI.

        That picker blocks startup on a keypress, and nothing on disk
        announces it (it runs before a session exists), so no turn could ever
        clear it — the phone just sat at "working". Grok's own "Don't ask me
        again" writes this hint into config.toml, so we write it ourselves.
        It must precede any [section] to stay a top-level TOML key."""
        try:
            path = self.config.grok_home_path / "config.toml"
            text = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            return
        if "project_picker_disabled" in text:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("hints = { project_picker_disabled = true }\n" + text,
                            encoding="utf-8")
            log.info("grok config.toml: disabled the project picker")
        except OSError as e:
            log.warning("could not disable grok project picker: %s", e)

    def _launch(self, tui: _Tui, resume_sid: str, model: str, effort: str,
                timeout_s: float = _START_TIMEOUT_S) -> str:
        """Start the TUI in a fresh tmux session. Returns "" or an error."""
        self._disable_project_picker()
        grok = str(getattr(self.config, "grok_bin", "grok") or "grok")
        # --minimal: scrollback-native rendering. Nothing here reads the
        # transcript off the screen (turns come from updates.jsonl), and a
        # pane that isn't redrawing a fullscreen UI is cheaper and keeps the
        # pinned prompt region stable for pasting.
        parts = [grok, "--minimal",
                 "--permission-mode", "bypassPermissions", "--trust-folder"]
        if resume_sid:
            # grok reuses the id on resume (only --fork-session makes a new
            # one), so the session the phone is looking at stays intact.
            parts += ["--resume", resume_sid]
            sid = resume_sid
        else:
            # We name the new session ourselves — nothing left to discover.
            sid = str(uuid.uuid4())
            parts += ["--session-id", sid]
        if model and model != "default":
            parts += ["--model", model]
        if effort and effort != "default":
            parts += ["--reasoning-effort", effort]
        env = dict(getattr(self.config, "grok_env", None) or {})
        env.setdefault("GROK_DISABLE_AUTOUPDATER", "1")
        shell_cmd = "%s exec %s" % (
            " ".join("%s=%s" % (k, shlex.quote(str(v))) for k, v in env.items()),
            " ".join(shlex.quote(p) for p in parts))
        tui.session_id = ""
        try:
            r = self._tmux("new-session", "-d", "-s", tui.name,
                           "-x", "220", "-y", "50", "-c", tui.cwd, shell_cmd)
        except OSError as e:
            return "tmux not available: %s" % e
        except subprocess.TimeoutExpired:
            return "tmux new-session timed out"
        if r.returncode != 0:
            return "tmux failed: %s" % r.stderr.decode("utf-8", errors="replace").strip()
        tui.spawned = True
        # No SessionStart hook to wait on: the prompt box's footer appearing
        # is what "ready for input" looks like.
        deadline = time.time() + timeout_s
        while not _pane_ready(self._pane_text(tui.name)):
            if time.time() > deadline or not self._tmux_alive(tui.name):
                tail = self._pane_tail(tui.name)
                self._kill(tui)
                return ("grok TUI did not become ready" +
                        (" — screen: %s" % tail if tail else ""))
            time.sleep(_POLL_S)
        tui.session_id = sid
        time.sleep(_READY_SETTLE_S)
        return ""

    def _kill(self, tui: _Tui):
        try:
            self._tmux("kill-session", "-t", tui.name)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _ensure_tui(self, cwd: str, session_id: str, model: str, effort: str):
        """Return (tui, error). One TUI per grok session: a continued session
        is matched to its live TUI by session id; a new session (or one whose
        TUI died) gets a fresh TUI, --resume'ing when continuing. Parallel
        sessions in the same project never evict each other."""
        with self._lock:
            # Prune TUIs whose tmux died (/exit, crash, manual kill) — but
            # never one another turn just registered and hasn't launched yet.
            dead = [n for n, t in self._tuis.items()
                    if t.spawned and not self._tmux_alive(n)]
            for name in dead:
                del self._tuis[name]
            if dead:
                self._save_state()
            if session_id:
                for t in self._tuis.values():
                    if t.session_id == session_id:
                        t.last_used = time.time()
                        return t, ""
            # Cap the fleet: evict the least-recently-used idle TUIs (never
            # one with a turn in flight) before adding another.
            while len(self._tuis) >= _MAX_TUIS:
                idle = [t for t in self._tuis.values() if t.job is None]
                if not idle:
                    break  # everything is mid-turn; allow the overflow
                victim = min(idle, key=lambda t: t.last_used)
                log.info("evicting idle grok TUI %s (cap %d)", victim.name, _MAX_TUIS)
                self._kill(victim)
                del self._tuis[victim.name]
                self._save_state()
            tui = _Tui(self._tui_name(cwd), cwd)
            self._tuis[tui.name] = tui
        err = self._launch(tui, session_id, model, effort)
        if err:
            with self._lock:
                self._tuis.pop(tui.name, None)
            self._save_state()
            return None, err
        self._save_state()
        return tui, ""

    # -- usage probe -------------------------------------------------------

    def _load_usage_sid(self) -> str:
        if self._usage_sid is None:
            sid = ""
            try:
                data = json.loads(_USAGE_STATE_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    sid = str(data.get("session_id") or "")
            except (OSError, ValueError):
                sid = ""
            self._usage_sid = sid
        return self._usage_sid

    def _save_usage_sid(self, sid: str):
        """Remember the usage session so the next fetch resumes it (only a
        brand-new one is ever written — resuming keeps the id)."""
        if not sid or sid == self._usage_sid:
            return
        self._usage_sid = sid
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = str(_USAGE_STATE_FILE) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"session_id": sid}, f)
            os.replace(tmp, str(_USAGE_STATE_FILE))
        except OSError:
            pass

    def _exit_tui(self, tui: _Tui):
        """Close a TUI: /exit is the clean path (grok flushes the session and
        drops its leader registration), kill-session the backstop."""
        if not self._send_prompt(tui, "/exit"):
            end = time.time() + _USAGE_EXIT_S
            while time.time() < end:
                if not self._tmux_alive(tui.name):
                    return
                time.sleep(_POLL_S)
        self._kill(tui)

    def fetch_usage(self) -> dict:
        """Read grok's subscription limits, as {"ok", "buckets"} / {"ok",
        "error"} — the shape claude's usage endpoint returns.

        grok publishes these numbers nowhere a daemon can reach: no usage
        endpoint, no `grok usage` subcommand, nothing in the session journal.
        They exist only as the three lines the TUI's /usage command prints. So
        the daemon keeps ONE grok session dedicated to asking, and every fetch
        resumes it, types /usage, reads the pane and exits:

          * resumed rather than created, so repeated fetches don't litter
            ~/.grok/sessions with a session per tap;
          * it never sends a message to the model, so its summary.json stays
            at num_messages 0 — which is exactly what GrokStore skips, so the
            phone's session and project lists never show it;
          * exited immediately, so no idle TUI lingers on the host and the
            probe never competes with chat TUIs for the _MAX_TUIS fleet cap.

        Nothing here touches the chat TUIs: separate tmux session, separate
        grok session, its own lock (the phone's Refresh button can double-fire).
        """
        if not tmux_available():
            return {"ok": False,
                    "error": "Usage needs tmux on the host (brew install tmux)."}
        with self._usage_lock:
            sid = self._load_usage_sid()
            out = self._usage_probe(sid)
            if not out.pop("retry", False):
                return out
            # The remembered session would not resume (deleted, or written by
            # a grok that no longer reads it). Fall back to a fresh one once.
            log.info("usage session %s did not resume; starting a new one",
                     sid[:8])
            self._usage_sid = ""
            out = self._usage_probe("")
            out.pop("retry", None)
            return out

    def _usage_probe(self, sid: str) -> dict:
        """One resume → /usage → /exit cycle. `retry` in the result means the
        TUI never came up, which a fresh session id may fix."""
        probe = _Tui(_USAGE_NAME, self.runner._default_cwd())
        self._kill(probe)   # a leaked probe from a killed fetch owns the name
        err = self._launch(probe, sid, "", "", timeout_s=_USAGE_START_S)
        if err:
            return {"ok": False, "error": err, "retry": bool(sid)}
        self._save_usage_sid(probe.session_id)
        rows = []
        buckets = []
        try:
            before = _pane_rows(self._pane_text(probe.name))
            err = self._send_prompt(probe, "/usage")
            if err:
                return {"ok": False, "error": err}
            # /usage is handled inside the TUI (no model call, nothing
            # journalled), so the pane is the only place it lands. Poll until
            # a percentage row shows up rather than sleeping a fixed guess.
            end = time.time() + _USAGE_RENDER_S
            while True:
                rows = _new_rows(before, _pane_rows(self._pane_text(probe.name)))
                buckets = _usage_buckets(rows)
                if buckets or time.time() > end:
                    break
                time.sleep(_POLL_S)
        finally:
            self._exit_tui(probe)
        if buckets:
            return {"ok": True, "buckets": buckets}
        screen = " | ".join(rows)[:300]
        return {"ok": False,
                "error": "Could not read grok's /usage output" +
                         (" — screen: %s" % screen if screen else "")}

    # -- turn execution ----------------------------------------------------

    def run(self, job) -> None:
        """Execute one job as a TUI turn. Sets job status before returning."""
        if not tmux_available():
            self._fail(job, "interactive mode needs tmux (brew install tmux)")
            return
        cwd = os.path.expanduser(job.cwd or "") or self.runner._default_cwd()
        tui, err = self._ensure_tui(cwd, job.session_id, job.model, job.effort)
        if err:
            self._fail(job, err)
            return
        with tui.lock:  # serialize turns per TUI
            tui.job = job
            try:
                m = _REWIND_RE.match((job.prompt or "").strip())
                if m:
                    self._run_rewind(job, tui, int(m.group(1) or 1))
                else:
                    self._run_turn(job, tui)
            finally:
                tui.job = None

    def _await_pane(self, tui: _Tui, needle: str, limit: float) -> bool:
        end = time.time() + limit
        while time.time() < end:
            if needle in self._pane_text(tui.name):
                return True
            time.sleep(_POLL_S)
        return False

    def _fail(self, job, message: str):
        with job.lock:
            if job.status != "stopped":
                job.status = "error"
                job.error = message

    def _journal_submits(self, session_id: str) -> int:
        """How many messages this session has ever submitted, per its journal.

        grok appends a user_message_chunk the moment it accepts input, so this
        is the file-based proof that a paste+Enter actually went through."""
        sdir = self.runner.store.find_session_dir(session_id) if session_id else None
        if sdir is None:
            return -1
        n = 0
        try:
            with open(sdir / "updates.jsonl", "r", encoding="utf-8",
                      errors="replace") as f:
                for line in f:
                    if '"user_message_chunk"' in line:
                        n += 1
        except OSError:
            return -1
        return n

    def _submit(self, tui: _Tui, prompt: str) -> str:
        """Send a message and make sure the TUI really took it.

        A pasted prompt occasionally sits in the input box with the Enter
        lost (seen 2026-07-31: nothing submitted for five minutes), so
        confirm against the journal and press Enter again if needed. Pressing
        Enter on an already-empty box is a no-op, so a retry cannot duplicate
        a message that did land — and when the TUI is busy the message is
        queued, which the confirmation simply misses (harmless)."""
        before = self._journal_submits(tui.session_id)
        err = self._send_prompt(tui, prompt)
        if err or before < 0:
            return err
        self._confirm_submit(tui, before)
        return ""

    def _confirm_submit(self, tui: _Tui, before: int):
        """Poll for proof the input was accepted, re-pressing Enter if not."""
        for attempt in range(_SUBMIT_RETRIES + 1):
            end = time.time() + _SUBMIT_CONFIRM_S
            while time.time() < end:
                if self._journal_submits(tui.session_id) > before:
                    return ""
                time.sleep(_POLL_S)
            if attempt >= _SUBMIT_RETRIES:
                break
            log.warning("TUI %s: no submit after %.0fs, pressing Enter again",
                        tui.name, _SUBMIT_CONFIRM_S)
            try:
                self._tmux("send-keys", "-t", tui.name, "Enter")
            except (OSError, subprocess.TimeoutExpired) as e:
                log.warning("TUI %s: retry keypress failed: %s", tui.name, e)
                return
        # Not proof of failure: a busy TUI queues the text and journals it
        # later. Let the turn run; its own timeout is the backstop.
        log.warning("TUI %s: submit unconfirmed (queued, or still in the box)",
                    tui.name)

    def type_text(self, session_id: str, text: str) -> str:
        """Type a message straight into this session's live TUI.

        Interactive mode does not use the daemon's prompt queue: the TUI
        queues typed-ahead input itself, just as it would for someone at the
        keyboard. The running job stays on watch (typed_ahead) so the reply
        still streams to the phone. Returns "" or an error."""
        if not text.strip():
            return "empty message"
        with self._lock:
            tui = None
            for t in self._tuis.values():
                if session_id and t.session_id == session_id:
                    tui = t
                    break
        if tui is None:
            return "no interactive TUI for this session"
        if not self._tmux_alive(tui.name):
            return "the grok TUI has exited"
        # Arm the watch BEFORE sending: the running turn can finish inside the
        # next few milliseconds, and if typed_ahead were still 0 the job would
        # end and this message's reply would stream to nobody.
        tui.typed_ahead += 1
        before = self._journal_submits(tui.session_id)
        err = self._send_prompt(tui, text)
        if err:
            tui.typed_ahead = max(0, tui.typed_ahead - 1)
            return err
        # Confirm off-thread: while the TUI is busy the message is queued and
        # nothing is journalled for a while, and the phone should not wait.
        if before >= 0:
            threading.Thread(target=self._confirm_submit, args=(tui, before),
                             daemon=True).start()
        return ""

    def capture_tui(self, session_id: str, *, ansi: bool = False) -> dict:
        from ..live_tui import capture_session
        return capture_session(self, session_id, ansi=ansi)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        from ..live_tui import send_to_session
        return send_to_session(self, session_id, keys=keys, text=text)

    def _send_prompt(self, tui: _Tui, prompt: str) -> str:
        """Type the prompt into the pane. Bracketed paste keeps multi-line
        prompts from submitting early; one Enter submits. Returns "" or err.

        The buffer name is unique per call: it used to be the fixed "bb10g",
        and `paste-buffer -d` deletes it, so two sends close together (a turn
        plus a typed-ahead message, or two sessions) could paste each other's
        text or lose it outright."""
        buf = "bb10g-%s" % uuid.uuid4().hex[:8]
        try:
            for step in (
                ("load-buffer", "-b", buf, "-"),
                ("paste-buffer", "-p", "-d", "-b", buf, "-t", tui.name),
            ):
                r = self._tmux(*step, input_bytes=prompt.encode("utf-8")
                               if step[0] == "load-buffer" else None)
                if r.returncode != 0:
                    return "tmux %s failed: %s" % (
                        step[0], r.stderr.decode("utf-8", errors="replace").strip())
            time.sleep(_PASTE_SETTLE_S)
            r = self._tmux("send-keys", "-t", tui.name, "Enter")
            if r.returncode != 0:
                return "tmux send-keys failed: %s" % (
                    r.stderr.decode("utf-8", errors="replace").strip())
        except (OSError, subprocess.TimeoutExpired) as e:
            return "tmux input failed: %s" % e
        return ""

    # -- plan approval -------------------------------------------------------

    def _plan_questions(self, plan: str) -> list:
        body = plan or "_(grok wrote no plan.md)_"
        if len(body) > 6000:
            body = body[:6000] + "\n…"
        return [{
            "question": body,
            # The phone renders these through the transcript's own painter,
            # so the plan reads as markdown instead of raw text.
            "blocks": markdown_to_blocks(body),
            "header": "Plan approval",
            "options": [
                {"label": "Approve",
                 "description": "Start building this plan"},
                {"label": "Request changes",
                 "description": "Send your note back; plan mode stays on"},
                {"label": "Quit plan mode",
                 "description": "Abandon the plan, leave plan mode"},
            ],
            "multi_select": False,
            # Tells the phone which pick takes the free-text note.
            "note_for": "Request changes",
            "note_hint": "What should change?",
        }]

    def _answer_plan(self, job, tui: _Tui, plan: str, preturn: bool = False):
        """Ask the phone how to answer the approval panel, then press the key.

        Keys are grok's own (from its built-in help): `a` approve and start
        building, `s` request changes (focus moves to the prompt so the next
        message becomes the revision notes), `q` quit plan mode. Runs on its
        own thread so the turn keeps draining while the phone decides.

        Only Approve leaves grok working, so only then does a turn_completed
        follow; the other two end the turn here. No answer at all abandons
        the plan rather than approving it — nothing should get built on a
        plan the user never saw.

        preturn=True means a panel from an earlier turn was blocking this
        turn's prompt: `s`/`q` free the input box, so the caller still sends
        the prompt (under `s` it becomes the revision notes grok is asking
        for) and only Approve ends the turn, since grok is then busy
        building and the prompt would collide with that."""
        state = job.runner_state
        # 0 / unset = no deadline (None blocks Event.wait forever): the
        # user answers when they get to their phone.
        secs = float(getattr(self.config, "question_timeout", 0) or 0)
        timeout = secs if secs > 0 else None
        answers = job.request_question(self._plan_questions(plan), timeout)
        pick = ""
        if answers:
            first = answers[0]
            pick = (first[0] if isinstance(first, list) and first
                    else first if isinstance(first, str) else "")
        if pick.startswith("Approve"):
            key = "a"
            note = ("Plan approved — grok is building it. Send your message "
                    "again once it finishes." if preturn else "")
        elif pick.startswith("Request"):
            key = "s"
            # `s` moves focus to the prompt: the note typed on the phone goes
            # straight in as the revision notes grok is asking for.
            feedback = ""
            for n in (getattr(job, "question_notes", None) or []):
                if str(n or "").strip():
                    feedback = str(n).strip()
                    break
            note = ("" if (preturn or feedback) else
                    "Plan changes requested. Send your revision notes as the "
                    "next message.")
        else:
            key = "q"
            note = ("" if preturn else
                    "Plan abandoned, plan mode off." if pick else
                    "No answer from the phone — plan abandoned and plan mode "
                    "turned off. plan.md is kept.")
        try:
            self._tmux("send-keys", "-t", tui.name, key)
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("plan approval input failed: %s", e)
            return
        state["plan_key"] = key
        detail = {"a": "approved", "s": "changes requested",
                  "q": "plan mode off"}[key]
        if key == "s" and feedback:
            # Focus is in the prompt now; send the phone's note as the notes.
            time.sleep(_PASTE_SETTLE_S)
            err = self._send_prompt(tui, feedback)
            if err:
                log.warning("plan feedback: %s", err)
            else:
                detail = "changes requested: " + feedback[:80]
                # grok revises and re-opens the panel; this turn continues.
                state["plan_pending"] = False
        job.add_event("tool", name="plan approval", detail=detail)
        if note:
            job.add_event("text", text=note, blocks=markdown_to_blocks(note))
            state["full"].append(note)
            state["turn_done"] = True

    # -- /rewind (checkpoint picker) -----------------------------------------

    def _rewind_rows(self, name: str) -> list:
        """The picker's turn rows, newest first (they are bulleted "· …")."""
        rows = []
        for row in _pane_rows(self._pane_text(name)):
            body = row.lstrip("┃ ").strip()
            if body.startswith("· "):
                rows.append(body[2:].strip())
        return rows

    def _run_rewind(self, job, tui: _Tui, steps: int):
        """Drive grok's /rewind picker: N turns back, conversation only.

        The picker lists one point per human prompt, newest first, with the
        cursor on row 1 — so "N back" is N-1 Downs then Enter, matching the
        phone's /rewind N (and claude's). grok then asks what to rewind:
        `a` both, `c` conversation only, `f` files only. We always send `c`:
        reverting files is grok's own default but those changes are gone for
        good unless they were committed, and the phone's affordance only
        promises to rewind the conversation.

        This one panel has no journal trail while it is open (no model turn
        runs), so unlike plan/ask it has to be driven off the pane."""
        with job.lock:
            job.status = "running"
            job.new_session_id = tui.session_id
        job.add_event("init", session_id=tui.session_id, model="interactive")
        steps = max(1, steps)
        job.add_event("tool", name="/rewind",
                      detail="%d turn%s back" % (steps, "" if steps == 1 else "s"))
        job.set_phase("tool", "/rewind")
        err = self._send_prompt(tui, "/rewind")
        if err:
            self._fail(job, err)
            return
        try:
            if not self._await_pane(tui, _REWIND_PICKER, _REWIND_OPEN_S):
                self._fail(job, "the rewind picker did not open — screen: %s"
                           % self._pane_tail(tui.name))
                return
            rows = self._rewind_rows(tui.name)
            if steps > len(rows) and rows:
                log.info("rewind: only %d point(s), clamping from %d",
                         len(rows), steps)
                steps = len(rows)
            target = rows[steps - 1] if steps - 1 < len(rows) else ""
            for _ in range(steps - 1):
                self._tmux("send-keys", "-t", tui.name, "Down")
                time.sleep(0.15)
            self._tmux("send-keys", "-t", tui.name, "Enter")
            if not self._await_pane(tui, _REWIND_SCOPE, _REWIND_OPEN_S):
                self._fail(job, "the rewind scope prompt did not open — "
                           "screen: %s" % self._pane_tail(tui.name))
                return
            self._tmux("send-keys", "-t", tui.name, "c")
            time.sleep(1.5)
            # grok refills the prompt with the rewound message. Escape and
            # Ctrl+U both leave it (the cursor sits at column 0, and Ctrl+U
            # kills leftwards); Ctrl+A then Ctrl+K clears it either way.
            self._tmux("send-keys", "-t", tui.name, "C-a")
            time.sleep(0.2)
            self._tmux("send-keys", "-t", tui.name, "C-k")
        except (OSError, subprocess.TimeoutExpired) as e:
            self._fail(job, "rewind failed: %s" % e)
            return
        msg = "Rewound %d turn%s (conversation only)" % (
            steps, "" if steps == 1 else "s")
        if target:
            msg += " — restored to before: “%s”" % target[:120]
        job.add_event("text", text=msg, blocks=markdown_to_blocks(msg))
        with job.lock:
            job.new_session_id = tui.session_id or job.new_session_id
        self._done(job, msg)

    # -- ask_user_question (selection panel) ---------------------------------

    _OTHER_LABEL = "Type my own answer"

    def _ask_questions_for_phone(self, questions: list) -> list:
        """grok's rawInput questions -> the phone's question format (the same
        one the plan panel uses, so the app needs no new screen).

        Every question also offers grok's "z" free-text row as an extra
        option, whose text the phone types into the note box (the sheet keeps
        one note per question)."""
        out = []
        total = len(questions)
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                continue
            options = [{"label": str(o.get("label")),
                        "description": str(o.get("description") or "")}
                       for o in q.get("options") or []
                       if isinstance(o, dict) and o.get("label")]
            if not options:
                continue
            text = str(q.get("question") or "")
            row = {
                "question": text,
                "blocks": markdown_to_blocks(text),
                "header": ("Question" if total == 1
                           else "Question %d/%d" % (i + 1, total)),
                "options": list(options),
                "multi_select": bool(q.get("multiSelect")),
            }
            row["options"].append(
                {"label": self._OTHER_LABEL,
                 "description": "Type your own answer instead of picking"})
            row["note_for"] = self._OTHER_LABEL
            row["note_hint"] = "Your answer"
            out.append(row)
        return out

    def _answer_ask(self, job, tui: _Tui, questions: list):
        """Ask the phone, then drive grok's panel with its own keys.

        Keys (verified against grok 0.2.114): the digit of an option picks it
        AND advances to the next question, the last pick submits, "z" opens a
        free-text row that takes a pasted answer plus Enter. Escape does NOT
        dismiss the panel, so there is no cancel: with no answer from the
        phone the panel is left alone and re-offered next turn rather than
        auto-deciding something the user never saw."""
        phone_qs = self._ask_questions_for_phone(questions)
        if not phone_qs:
            return
        # 0 / unset = no deadline (None blocks Event.wait forever): the
        # user answers when they get to their phone.
        secs = float(getattr(self.config, "question_timeout", 0) or 0)
        timeout = secs if secs > 0 else None
        answers = job.request_question(phone_qs, timeout)
        if not answers:
            log.warning("TUI %s: ask panel unanswered, left open", tui.name)
            job.add_event("tool", name="questions",
                          detail="no answer from the phone — still waiting")
            return
        notes = list(getattr(job, "question_notes", None) or [])
        picks = []
        try:
            for i, q in enumerate(phone_qs):
                labels = [o["label"] for o in q["options"]]
                chosen = [l for l in (answers[i] if i < len(answers) else [])
                          if l in labels]
                if not chosen:
                    chosen = labels[:1]
                if q["multi_select"] and len(chosen) > 1:
                    # A digit advances immediately, so extra picks would answer
                    # the NEXT question. Send the first and say so.
                    log.warning("TUI %s: multi-select ask, sending only %r",
                                tui.name, chosen[0])
                label = chosen[0]
                if label == self._OTHER_LABEL:
                    # notes are aligned with the questions, one each.
                    note = str(notes[i]).strip() if i < len(notes) else ""
                    self._tmux("send-keys", "-t", tui.name, "z")
                    time.sleep(0.4)
                    if note:
                        err = self._send_prompt(tui, note)
                        if err:
                            log.warning("ask free text: %s", err)
                        picks.append("own answer: " + note[:60])
                    else:
                        self._tmux("send-keys", "-t", tui.name, "Enter")
                        picks.append("own answer")
                    continue
                idx = labels.index(label) + 1
                if idx > 9:
                    log.warning("TUI %s: option %d past the digit keys, "
                                "picking the first", tui.name, idx)
                    idx = 1
                self._tmux("send-keys", "-t", tui.name, str(idx))
                picks.append(label)
                time.sleep(0.5)   # the panel advances between picks
        except (OSError, subprocess.TimeoutExpired) as e:
            log.warning("ask panel input failed: %s", e)
            return
        job.add_event("tool", name="questions answered",
                      detail=" · ".join(picks)[:200])

    def _drain_until_done(self, job, limit: float) -> bool:
        """Pump the journal until grok finishes the turn it is on."""
        end = time.time() + limit
        while time.time() < end:
            self.runner._poll_updates(job)
            if job.runner_state.get("turn_done"):
                return True
            time.sleep(_POLL_S)
        return False

    def _await_plan_clear(self, session_id: str, limit: float) -> bool:
        """Wait for plan_mode.json to stop reporting awaiting_plan_approval."""
        end = time.time() + limit
        while time.time() < end:
            if not self.runner.plan_awaiting(session_id):
                return True
            time.sleep(_POLL_S)
        return False

    def _done(self, job, text: str):
        with job.lock:
            if job.status != "stopped":
                job.result_text = text
                job.status = "done"
        job.add_event("result", is_error=False,
                      duration_ms=int((time.time() - job.started_at) * 1000),
                      cost_usd=0)

    def _run_turn(self, job, tui: _Tui):
        runner = self.runner
        state = job.runner_state
        state.clear()
        state.update({"parts": [], "full": [], "end": None,
                      "seen_tool_ids": set(),
                      "updates_path": None, "updates_offset": 0,
                      # Tell the shared journal parser that this turn has no
                      # stdout stream, so text/thoughts/end come off disk too.
                      "disk_text": True, "turn_done": False,
                      # Plan-approval panel (journal-driven, see grok.py
                      # _note_plan_approval): id of the exit_plan call, and
                      # whether it is still unanswered.
                      "plan_call_id": "", "plan_pending": False,
                      "plan_asked": "", "plan_thread": None,
                      # ask_user_question panel (same journal-driven shape)
                      "ask_call_id": "", "ask_questions": None,
                      "ask_asked": "", "ask_thread": None})
        with job.lock:
            job.status = "running"
            job.new_session_id = tui.session_id
        job.add_event("init", session_id=tui.session_id, model="interactive")
        # Tail from the journal's current end so earlier turns don't replay.
        runner._bind_updates(job, from_start=False)

        # An ask_user_question panel left open by an earlier turn intercepts
        # keys, so a pasted prompt would be read as option keystrokes (a "1"
        # in the text picks an option). Answer it from the phone first, then
        # let grok's reply to it land before typing. The journal says whether
        # one is up, so this survives a daemon restart — no pane reads.
        pend = runner.ask_pending(tui.session_id)
        if pend:
            state["ask_asked"] = pend[0]
            job.set_phase("asking", "questions")
            self._answer_ask(job, tui, pend[1])
            self._drain_until_done(job, _PLAN_REPLY_S)
            state["turn_done"] = False

        # A panel left open by an earlier turn owns the input box: the prompt
        # would land in its comment field and grok would never see it. Clear
        # it first (plan_mode.json says whether one is up — no pane reads).
        if runner.plan_awaiting(tui.session_id):
            job.set_phase("asking", "plan approval")
            self._answer_plan(job, tui, runner.plan_text(tui.session_id),
                              preturn=True)
            self._await_plan_clear(tui.session_id, _PLAN_CLEAR_S)
            if state.get("turn_done"):
                # Approve: grok is building, so this turn is the approval.
                text = "".join(state.get("full") or [])
                with job.lock:
                    job.new_session_id = tui.session_id or job.new_session_id
                self._done(job, text)
                return
            # s/q freed the input box, so this turn still runs the prompt.
            # Under `s` grok is waiting for revision notes and the prompt IS
            # them; under `q` grok first answers the quit, so let that reply
            # land (the phone sees it) before typing over it.
            state["plan_pending"] = False
            if state.get("plan_key") == "q":
                self._drain_until_done(job, _PLAN_REPLY_S)
                state["turn_done"] = False
            time.sleep(_PASTE_SETTLE_S)

        err = self._submit(tui, job.prompt)
        if err:
            self._fail(job, err)
            return

        prompt = (job.prompt or "").strip()
        local = prompt.startswith("/")
        timeout_s = float(getattr(self.config, "turn_timeout", 0) or 0)
        deadline = job.started_at + timeout_s if timeout_s > 0 else None
        quiet_since = time.time()
        interrupted = False
        ask_wait_from = None
        tui.typed_ahead = 0
        while True:
            time.sleep(_POLL_S)
            before = state.get("updates_offset")
            runner._poll_updates(job)
            if state.get("updates_offset") != before:
                quiet_since = time.time()
            # exit_plan_mode opened the approval panel: no turn_completed will
            # follow it, so ask the phone (once) and let the answer resume the
            # turn. The journal's tool_call_update clears plan_pending.
            cid = state.get("plan_call_id") or ""
            if state.get("plan_pending") and cid != state.get("plan_asked"):
                state["plan_asked"] = cid   # ask once per exit_plan call
                job.set_phase("asking", "plan approval")
                state["plan_thread"] = threading.Thread(
                    target=self._answer_plan,
                    args=(job, tui, runner.plan_text(tui.session_id)),
                    daemon=True)
                state["plan_thread"].start()
            # ask_user_question is modal the same way: forward it once per
            # tool call and let the phone's picks close it.
            aid = state.get("ask_call_id") or ""
            askq = state.get("ask_questions")
            athread = state.get("ask_thread")
            if askq and aid != state.get("ask_asked") \
                    and not (athread and athread.is_alive()):
                state["ask_asked"] = aid
                job.set_phase("asking", "questions")
                state["ask_thread"] = threading.Thread(
                    target=self._answer_ask, args=(job, tui, askq),
                    daemon=True)
                state["ask_thread"].start()
            if state.get("turn_done"):
                # A message typed into the pane mid-turn (type_text) is the
                # TUI's own queue and starts running now: stay on watch so
                # its reply still streams to the phone.
                if tui.typed_ahead > 0:
                    tui.typed_ahead -= 1
                    state["turn_done"] = False
                    quiet_since = time.time()
                    # turn_timeout bounds a TURN, not the job: typing ahead
                    # legitimately keeps one job running across several, and
                    # a job deadline would fail it while the TUI works on.
                    if timeout_s > 0:
                        deadline = time.time() + timeout_s
                    job.set_phase("thinking", "")
                    continue
                break
            with job.lock:
                stopped = job.status == "stopped"
            if stopped:
                interrupted = True
                try:  # Escape = the TUI's interrupt key
                    self._tmux("send-keys", "-t", tui.name, "Escape")
                except (OSError, subprocess.TimeoutExpired):
                    pass
                break
            if not self._tmux_alive(tui.name):
                if prompt in ("/exit", "/quit"):
                    # Killing the TUI is what /exit is for — a clean end, not
                    # a failure. The next message launches a fresh TUI and
                    # --resume's this session.
                    msg = "TUI closed. The next message starts a fresh one."
                    job.add_event("text", text=msg,
                                  blocks=markdown_to_blocks(msg))
                    self._done(job, msg)
                    return
                self._fail(job, "grok TUI exited mid-turn")
                return
            # A pending question is the human's clock, not ours: hold the
            # turn deadline while the phone has a panel to answer, then give
            # the turn back exactly the time that was spent waiting.
            if job.pending_question:
                if ask_wait_from is None:
                    ask_wait_from = time.time()
            elif ask_wait_from is not None:
                if deadline:
                    deadline += time.time() - ask_wait_from
                ask_wait_from = None
            if deadline and time.time() > deadline:
                tail = self._pane_tail(tui.name)
                self._fail(job, "turn timed out after %ds%s" % (
                    int(timeout_s), " — screen: %s" % tail if tail else ""))
                return
            # /commands are handled inside the TUI and never reach the model,
            # so no turn_completed will come: end them on a quiet journal.
            if local and time.time() - quiet_since > _LOCAL_QUIET_S:
                break

        runner._poll_updates(job)
        runner._flush_text(job)
        text = "".join(state.get("full") or [])
        if not text and local and not interrupted:
            # A /command whose only output was the panel it drew.
            snap = self._pane_snapshot(tui.name)
            if snap:
                text = snap
                fenced = "```\n%s\n```" % snap
                job.add_event("text", text=fenced,
                              blocks=markdown_to_blocks(fenced))
        with job.lock:
            job.new_session_id = tui.session_id or job.new_session_id
        if not interrupted:
            self._done(job, text)
