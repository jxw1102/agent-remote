"""Interactive mode: drive a real `claude` TUI inside tmux.

Why this exists: headless `claude -p` cannot use claude.ai connectors (their
OAuth lives account-side; the servers hang in "pending" forever), while the
interactive TUI connects them all. So the phone gets a second execution mode
("interactive"): the daemon keeps one detached tmux session per claude
session (parallel conversations in the same project each get their own TUI)
running the genuine TUI, types the prompt into it, and reads the turn back.

Signalling (patterned after the flipper-claude-buddy bridge): a companion
plugin (daemon/tui-plugin/agentremote-bridge) is loaded into daemon-spawned TUIs
via `--plugin-dir`, its hooks POSTing every lifecycle event to this daemon's
/internal/hook endpoint — SessionStart (session id + transcript path; a TUI
`--resume` forks a new id exactly like -p), Stop (turn finished), Notification
(waiting on input), SessionEnd, and compaction markers. The hook script only
acts when AGENTREMOTE_HOOK_URL (with the auth secret) is in its environment, which
the daemon injects at launch — the user's own claude sessions are untouched.

The turn's events are NOT screen-scraped: the TUI appends the same JSONL
transcript the headless stream mirrors, so we tail the file and reuse the
assistant-message shapes (text / tool_use / thinking blocks). A phone-side
Stop sends Escape into the pane, the TUI's interrupt key.

Prompts starting with "!" or "/" are handled natively by the TUI: "!" is sent
as a keypress (flipping the input into bash mode) before the rest is pasted,
"/" pastes as-is (the TUI parses commands on submit). Neither necessarily
runs the model, so those turns may end on a quiet period after their
transcript output instead of a Stop hook; a /command that shows a UI panel
(/cost, /mcp...) gets snapshotted for the phone and dismissed with Escape.

The TUI runs with --permission-mode bypassPermissions: permission prompts
would render inside the pane where nobody can see them, so interactive mode
is by definition an auto mode (the phone UI says so).

AskUserQuestion is the one panel the model itself opens mid-turn, and it
blocks the pane until answered. Its questions/options reach the phone
(pending_question in the job snapshot) and the phone's picks are typed back
into the panel with arrow keys — hooks cannot supply a tool result, so that
half has to be keystrokes.

The questions themselves come from the PreToolUse hook, NOT the transcript:
on CLI 2.1.220 the tool_use line is flushed only after the panel is answered
(with a panel open, the newest AskUserQuestion on disk was the previous,
already-answered one), so transcript tailing showed the phone nothing while
the turn sat blocked. The transcript path is still handled as a fallback for
older CLIs. A TUI must be relaunched to pick up a new hook — the plugin dir
is read at launch.
"""

import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid

from ..config import CONFIG_DIR, ensure_tmux_server, tmux_socket

log = logging.getLogger(__name__)

_MAX_TUIS = 6             # live TUI cap; oldest idle ones are evicted
_START_TIMEOUT_S = 90     # claude TUI cold start (incl. MCP connects)
_POLL_S = 0.3             # transcript tail poll
_PASTE_SETTLE_S = 0.3     # between paste-buffer and Enter
_READY_SETTLE_S = 1.0     # between SessionStart hook and first paste

# Local (non-model) turns have no Stop hook, so they end on a quiet period
# after their transcript output — never while the pane shows a busy spinner.
_SLASH_QUIET_S = 2.0      # /command: settle after <local-command-stdout>
_BASH_QUIET_S = 5.0       # !command: settle if no model turn follows
_PANEL_TIMEOUT_S = 8.0    # /command with no output = UI panel; Esc closes it

# A pasted prompt can land in the input box with the Enter lost, leaving the
# turn waiting on a message claude never saw. Confirm the submit against the
# transcript and re-press Enter this many times before giving up on confirming.
_SUBMIT_CONFIRM_S = 3.0
_SUBMIT_RETRIES = 2

# Crash watchdog. A live TUI always keeps its input box (❯), footer (⏵⏵) or
# busy spinner on the bottom lines; a pane showing none of them (a node
# backtrace) while the transcript stops growing is a crashed TUI — without
# this the phone hangs on "working…" forever (no Stop hook will ever come).
_CRASH_STALL_S = 20.0     # dead-looking pane this long = crashed
_LOST_STOP_S = 180.0      # healthy-idle chat turn with no Stop = hook lost

# Typed-ahead messages (type_text) don't map 1:1 onto Stop hooks: claude pulls
# queued messages into the turn that is already running, so N messages can end
# in fewer than N Stops. After a Stop, the pane — not the count — decides
# whether another turn is really coming.
_STOP_SETTLE_S = 2.0      # let the ended turn's spinner clear first
_STOP_CONFIRM_S = 8.0     # window for a queued message to take the pane

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# tmux session name prefix: `tmux ls` should read as "claude" at a glance
# (grok's fleet is grk-*). Legacy bb10i-* sessions are still adopted/reaped.
_PREFIX = "cld-"
_OLD_PREFIXES = ("cld-", "bb10i-")

# The AskUserQuestion panel's footer, i.e. "the panel is still up". Used to
# tell "submitted, panel gone" from "still waiting" after the last pick.
_ASK_PANEL_MARKER = "to navigate"


def _tag_text(s: str, tag: str) -> str:
    """Extract <tag>...</tag> from a transcript marker string."""
    a, b = "<%s>" % tag, "</%s>" % tag
    i = s.find(a)
    if i < 0:
        return ""
    j = s.find(b, i)
    return s[i + len(a):j] if j >= 0 else s[i + len(a):]


_SECRET_FILE = CONFIG_DIR / "hook-secret"
# tmux name -> claude session mapping, persisted so a daemon restart re-adopts
# the running TUIs instead of killing them.
_STATE_FILE = CONFIG_DIR / "tuis.json"

# The companion plugin shipping with the daemon (agentremote-bridge). --plugin-dir
# takes the plugin root itself (the dir holding .claude-plugin/).
_PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "tui-plugin", "agentremote-bridge")


def _hook_secret() -> str:
    """Persistent shared secret for /internal/hook (survives daemon restarts
    so long-lived tmux TUIs keep working)."""
    try:
        secret = _SECRET_FILE.read_text(encoding="utf-8").strip()
        if secret:
            return secret
    except OSError:
        pass
    secret = uuid.uuid4().hex
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_text(secret, encoding="utf-8")
        os.chmod(str(_SECRET_FILE), 0o600)
    except OSError:
        pass
    return secret


class _Tui:
    """One detached tmux session hosting one claude TUI."""

    def __init__(self, name: str, cwd: str, isolate_root: str = ""):
        self.name = name
        self.cwd = cwd
        self.isolate_root = isolate_root or ""
        self.session_id = ""          # current claude session id
        self.transcript = ""          # its JSONL path
        self.spawned = False          # tmux session created (prunable when dead)
        self.last_used = time.time()  # LRU stamp for the _MAX_TUIS cap
        self.typed_ahead = 0          # messages typed into the pane mid-turn
        self.hook_ask = None      # AskUserQuestion payload from PreToolUse
        self.job = None               # job of the turn in flight, if any
        self.start_event = threading.Event()
        self.stop_event = threading.Event()
        self.stop_payload = {}        # last Stop hook payload (has final text)
        self.compacting = False       # between PreCompact and PostCompact
        self.lock = threading.Lock()  # one turn at a time per TUI


class InteractiveManager:
    """Owns the tmux TUIs and runs "interactive" jobs against them."""

    def __init__(self, config):
        self.config = config
        self.secret = _hook_secret()
        self._tuis = {}               # tmux name -> _Tui
        self._lock = threading.Lock()
        self._adopt_or_reap()

    def _save_state(self):
        """Persist the name->session mapping (see _adopt_or_reap). Only
        launched TUIs: a registered-but-unlaunched one has no tmux session."""
        rows = [{"name": t.name, "cwd": t.cwd, "session_id": t.session_id,
                 "transcript": t.transcript, "last_used": t.last_used}
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
        outlive us and stay fully addressable (the hook secret and each TUI's
        baked-in tui= URL persist too), so the only thing a restart used to
        lose was this registry — now reloaded from disk. cld-* sessions we
        have no record of can never receive a turn, so those are killed."""
        known = {}
        try:
            saved = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = []
        for entry in saved if isinstance(saved, list) else []:
            if isinstance(entry, dict) and str(entry.get("name", "")).startswith(_OLD_PREFIXES):
                known[str(entry["name"])] = entry
        try:
            r = self._tmux("list-sessions", "-F", "#{session_name}",
                           capture=True)
            if r.returncode != 0:
                return
            for name in r.stdout.decode("utf-8", errors="replace").split():
                if not name.startswith(_OLD_PREFIXES):
                    continue
                entry = known.get(name)
                if entry is None:
                    # Never kill. An unrecognised TUI is far more likely to be
                    # someone's live turn than a leak — this reaper used to
                    # take down every running TUI on the box whenever a test
                    # (empty registry, real tmux) constructed a manager, which
                    # is what "claude TUI exited mid-turn" mid-tool-call was.
                    log.info("ignoring unknown TUI %s (absent from %s)",
                             name, _STATE_FILE.name)
                    continue
                tui = _Tui(name, str(entry.get("cwd", ""))
                           or os.path.expanduser("~"))
                tui.session_id = str(entry.get("session_id", ""))
                tui.transcript = str(entry.get("transcript", ""))
                tui.spawned = True
                try:
                    tui.last_used = float(entry.get("last_used") or 0) or time.time()
                except (TypeError, ValueError):
                    tui.last_used = time.time()
                self._tuis[name] = tui
                log.info("adopted TUI %s (session %s)", name,
                         tui.session_id[:8] or "unknown")
        except (OSError, subprocess.TimeoutExpired):
            pass
        self._save_state()

    # -- hook plumbing ---------------------------------------------------

    def _hook_url(self, tui_name: str) -> str:
        port = int(getattr(self.config, "port", 8473) or 8473)
        return ("http://127.0.0.1:%d/internal/hook?secret=%s&tui=%s"
                % (port, self.secret, tui_name))

    def on_hook(self, payload: dict, secret: str, tui_name: str = "") -> bool:
        """/internal/hook: a daemon-spawned TUI reported a lifecycle event.
        Routed by the tui= name baked into that TUI's hook URL (session id
        is a fallback) — cwd is ambiguous with parallel TUIs per project."""
        if not secret or secret != self.secret:
            return False
        event = str(payload.get("hook_event_name", ""))
        sid = str(payload.get("session_id", ""))
        transcript = str(payload.get("transcript_path", ""))
        log.debug("hook %s tui=%s sid=%s transcript=%s",
                  event, tui_name, sid[:8], transcript)
        with self._lock:
            tui = self._tuis.get(tui_name)
            if tui is None and sid:
                for t in self._tuis.values():
                    if t.session_id == sid:
                        tui = t
                        break
        if tui is None:
            return True  # not ours (e.g. a TUI we already dropped)
        if event == "SessionEnd":
            # /clear, /exit, crash: next turn must relaunch-and-rediscover.
            if not tui.session_id or tui.session_id == sid:
                tui.session_id = ""
                tui.transcript = ""
                self._save_state()
            return True
        if (sid and sid != tui.session_id) or (transcript
                                              and transcript != tui.transcript):
            tui.session_id = sid or tui.session_id
            tui.transcript = transcript or tui.transcript
            self._save_state()  # keep the on-disk mapping restart-ready
        job = tui.job
        if event == "SessionStart":
            tui.start_event.set()
        elif event == "Stop":
            tui.stop_payload = payload
            tui.stop_event.set()
        elif event == "PreToolUse":
            # AskUserQuestion opens a panel that blocks the pane, and the
            # transcript line for it is flushed only AFTER it is answered
            # (verified on CLI 2.1.220: while the panel was up, the newest
            # AskUserQuestion in the JSONL was the previous, already-answered
            # one). So the hook — which carries tool_input — is the only
            # signal that arrives in time. The turn loop picks this up.
            if str(payload.get("tool_name") or "") == "AskUserQuestion":
                qs = (payload.get("tool_input") or {}).get("questions")
                if isinstance(qs, list) and qs:
                    tui.hook_ask = qs
        elif event == "Notification" and job is not None:
            # "Claude is waiting for your input" etc. — surface on the banner.
            msg = str(payload.get("message", "") or "waiting for input")
            job.set_phase("waiting", msg)
        elif event in ("PreCompact", "PostCompact"):
            tui.compacting = event == "PreCompact"
            if job is not None:
                job.set_phase("compacting", "")
        return True

    # -- tmux helpers ------------------------------------------------------

    def _tmux(self, *args, capture=False, input_bytes=None):
        # -L: the fleet's own socket (see config.tmux_socket) — never the
        # shared default one, where the reaper below would meet TUIs that
        # belong to another AGENTREMOTED_HOME.
        cmd = [self._tmux_bin, "-L", tmux_socket()] + list(args)
        return subprocess.run(
            cmd, input=input_bytes,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=15)

    @property
    def _tmux_bin(self):
        return shutil.which("tmux") or "/opt/homebrew/bin/tmux"

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

    def _pane_tail(self, name: str, n: int = 20) -> str:
        """Bottom of the pane only — scrollback must not drive busy state.

        Older turns leave lines like "Waiting for 1 background agent to finish"
        in the buffer long after the session is idle at ❯; scanning the full
        capture kept active_tui_status (and the phone "working" pulse) stuck.
        """
        rows = [l for l in self._pane_text(name).splitlines() if l.strip()]
        return "\n".join(rows[-n:]) if rows else ""

    def _pane_busy(self, name: str) -> bool:
        """A model turn / subagent is still in flight on the host TUI.

        Classic chat turns show "esc to interrupt". Multi-agent work often
        leaves the main input idle while a background agent runs — the chrome
        then says "Waiting for N background agent(s)" without Esc. Treat both
        as busy so turn_timeout does not drop the session from clients'
        working list and active_tui_status still reports them.

        Only the tail is inspected (see _pane_tail). Do not match bare
        "agent" in the status bar ("· ← 1 agent") or historical transcript.
        """
        t = self._pane_tail(name).lower()
        if "esc to interrupt" in t:
            return True
        # Live multi-agent wait chrome (current turn), not old scrollback.
        if "waiting for" in t and "background agent" in t:
            return True
        if "waiting for" in t and "agent" in t and "finish" in t:
            return True
        # Avoid matching bare "agent" / "subagent" in assistant prose or the
        # idle chrome ("· ← 1 agent").
        if "running subagent" in t:
            return True
        if "background agent" in t and (
                "running" in t or "active" in t or "waiting" in t):
            return True
        return False

    def _pane_queued(self, name: str) -> bool:
        """Typed-ahead messages are still waiting in the TUI's own queue."""
        return "queued message" in self._pane_tail(name).lower()

    def active_tui_status(self) -> list:
        """Busy host TUIs for /ws/status even when no remote job is running.

        After a turn_timeout (or a lost job on restart) the tmux Claude can
        keep working for a long time. Clients only paint the "working" pulse
        from the active-job stream, so surface those TUIs as synthetic rows.
        """
        out = []
        with self._lock:
            items = list(self._tuis.values())
        now = time.time()
        for t in items:
            if not t.spawned:
                continue
            sid = (t.session_id or "").strip()
            if not sid:
                continue
            if not self._tmux_alive(t.name) or self._pane_dead(t.name)[0]:
                continue  # gone, or a frozen pane whose claude has exited
            job = t.job
            if job is not None:
                with job.lock:
                    if job.status in ("starting", "running"):
                        continue  # real job row already covers this session
            busy = bool(t.compacting or t.hook_ask)
            if not busy:
                try:
                    busy = self._pane_busy(t.name) or self._pane_queued(t.name)
                except Exception:
                    busy = False
            if not busy:
                continue
            acct = ""
            iso = ""
            if job is not None:
                acct = str(getattr(job, "account", "") or "")
                iso = str(getattr(job, "isolate_root", "") or "")
            out.append({
                "job_id": "tui-%s" % (sid.replace("-", "")[:12]),
                "session_id": sid,
                "new_session_id": sid,
                "status": "running",
                "prompt": "(live TUI)",
                "cwd": getattr(t, "cwd", "") or "",
                "account": acct,
                "isolate_root": iso,
                "elapsed_s": max(0, int(now - float(t.last_used or now))),
                "queued_count": 0,
                "tool": "",
                "tool_detail": "",
                "phase": "working",
                "phase_detail": "host interactive TUI",
                "pending_permission": False,
                "pending_question": False,
                "next_seq": 0,
            })
        return out

    def _next_turn_starts(self, job, tui) -> bool:
        """After a Stop with typed-ahead debt: does another turn really begin?

        Poll the pane past a short settle (the ended turn's spinner can
        linger a moment) and report busy = a queued message took the pane.
        An idle prompt through the whole window means the finished turn
        already consumed the queue — no further Stop is coming."""
        deadline = time.time() + _STOP_CONFIRM_S
        settle_end = time.time() + _STOP_SETTLE_S
        while time.time() < deadline:
            with job.lock:
                if job.status == "stopped":
                    return False
            if time.time() >= settle_end and (
                    tui.compacting or tui.hook_ask
                    or self._pane_busy(tui.name)):
                return True
            time.sleep(_POLL_S)
        return False

    def _pane_healthy(self, name: str) -> bool:
        """True while the TUI's chrome is on screen. Checks only the bottom
        lines: a crash backtrace may scroll old ❯ lines into view above."""
        rows = [l for l in self._pane_text(name).splitlines() if l.strip()][-8:]
        t = "\n".join(rows)
        return ("esc to interrupt" in t.lower()) or "⏵⏵" in t or "❯" in t

    def _save_crash(self, tui: _Tui) -> str:
        """Preserve a crashed TUI's full scrollback (the error line sits far
        above the stack-frame tail) under ~/.agentremoted/crashes/ and return a
        one-line description: the error line if found, else the pane tail."""
        try:
            out = self._tmux("capture-pane", "-p", "-S", "-3000",
                             "-t", tui.name, capture=True)
            full = out.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired):
            full = ""
        path = ""
        if full.strip():
            try:
                d = CONFIG_DIR / "crashes"
                d.mkdir(parents=True, exist_ok=True)
                path = str(d / ("%s-%d.log" % (tui.name, int(time.time()))))
                with open(path, "w", encoding="utf-8") as f:
                    f.write(full)
            except OSError:
                path = ""
        err = ""
        for l in full.splitlines():
            ls = l.strip()
            if re.search(r"\b(\w*Error|panic|EMFILE|ENOMEM|fatal)\b", ls) \
                    and not ls.startswith(("-", "at ")):
                err = ls[:300]
                break
        detail = err or self._pane_tail(tui.name, 10)
        log.warning("TUI %s crashed mid-turn: %s (full screen: %s)",
                    tui.name, detail, path or "not saved")
        if path:
            detail += " [full trace: %s]" % path
        return detail

    def _pane_tail(self, name: str, lines: int = 6) -> str:
        """Last visible pane lines — the only diagnostics an interactive
        failure has (trust prompts, login screens, crashes)."""
        rows = [l.rstrip() for l in self._pane_text(name).splitlines()
                if l.strip()]
        return " | ".join(rows[-lines:])

    # -- TUI lifecycle -----------------------------------------------------

    def _tui_name(self, cwd: str) -> str:
        """Unique per TUI (not per project): <cwd-hash>-<random> so parallel
        sessions in one project each get their own tmux session."""
        h = hashlib.sha1(os.path.realpath(cwd).encode("utf-8")).hexdigest()[:8]
        return "%s%s-%s" % (_PREFIX, h, uuid.uuid4().hex[:6])

    def _launch(self, tui: _Tui, resume_sid: str, model: str) -> str:
        """Start the TUI in a fresh tmux session. Returns "" or an error."""
        claude = str(getattr(self.config, "claude_bin", "claude") or "claude")
        parts = [claude, "--permission-mode", "bypassPermissions",
                 "--plugin-dir", _PLUGIN_DIR]
        if resume_sid:
            parts += ["--resume", resume_sid]
        if model and model != "default":
            parts += ["--model", model]
        # The hook URL (with secret) travels in the TUI's environment; the
        # plugin's hook script no-ops without it. The ulimit raise matters:
        # a tmux server born under launchd gives panes a 256-fd soft limit,
        # under which claude crashes with EMFILE mid-turn (hard limit is
        # unlimited, so the pane shell may raise it itself).
        tui.start_event.clear()
        tui.session_id = ""
        tui.transcript = ""
        launch_cwd = tui.isolate_root or tui.cwd
        if tui.isolate_root:
            # Same shape as grok guest launch: isolate only the CLI, force
            # env/cwd via accounts.isolate_shell_line (never TERM=dumb).
            from .. import accounts as _accounts
            body = (
                "ulimit -n 65536 2>/dev/null; "
                "export AGENTREMOTE_HOOK_URL=%s; exec %s"
                % (shlex.quote(self._hook_url(tui.name)),
                   " ".join(shlex.quote(p) for p in parts))
            )
            shell_cmd = _accounts.isolate_shell_line(body, tui.isolate_root)
        else:
            shell_cmd = (
                "ulimit -n 65536 2>/dev/null; AGENTREMOTE_HOOK_URL=%s exec %s"
                % (shlex.quote(self._hook_url(tui.name)),
                   " ".join(shlex.quote(p) for p in parts))
            )
        # One creator for the shared socket: concurrent new-session calls
        # would each try to spawn the server, and the loser's teardown takes
        # every other session with it (see config.ensure_tmux_server).
        ensure_tmux_server(self._tmux_bin)
        try:
            r = self._tmux("new-session", "-d", "-s", tui.name,
                           "-x", "220", "-y", "50", "-c", launch_cwd, shell_cmd)
        except OSError as e:
            return "tmux not available: %s" % e
        except subprocess.TimeoutExpired:
            return "tmux new-session timed out"
        if r.returncode != 0:
            return "tmux failed: %s" % r.stderr.decode("utf-8", errors="replace").strip()
        # The pane is `exec claude`, so claude's death used to take the pane,
        # the session and every diagnostic with it — leaving the turn nothing
        # to report but a blind "exited mid-turn". remain-on-exit freezes the
        # corpse instead, so the exit status and final screen survive.
        self._tmux("set-option", "-t", tui.name, "remain-on-exit", "on")
        tui.spawned = True
        if not tui.start_event.wait(_START_TIMEOUT_S):
            tail = self._pane_tail(tui.name)
            self._kill(tui)
            return ("claude TUI did not become ready" +
                    (" — screen: %s" % tail if tail else ""))
        time.sleep(_READY_SETTLE_S)
        return ""

    def _pane_dead(self, name: str):
        """(dead, why) for a session's pane.

        With remain-on-exit the tmux session outlives claude, so "the TUI is
        gone" is no longer "the session is gone" — it is a pane tmux marks
        dead, carrying the exit status (and signal, when something killed it)
        that finally names the cause.
        """
        try:
            out = self._tmux(
                "display-message", "-p", "-t", name,
                "#{pane_dead}:#{pane_dead_status}:#{pane_dead_signal}",
                capture=True)
            if out.returncode != 0:
                return False, ""
            txt = out.stdout.decode("utf-8", errors="replace").strip()
        except (OSError, subprocess.TimeoutExpired):
            return False, ""
        parts = txt.split(":")
        if not parts or parts[0] != "1":
            return False, ""
        status = parts[1] if len(parts) > 1 else ""
        signal = parts[2] if len(parts) > 2 else ""
        why = "exit status %s" % (status if status else "?")
        if signal:
            why += ", killed by signal %s" % signal
        return True, why

    def _kill(self, tui: _Tui):
        # Name the caller: a TUI dying mid-turn is indistinguishable from one
        # the daemon killed itself unless the log says which happened.
        try:
            caller = sys._getframe(1).f_code.co_name
        except Exception:  # noqa: BLE001
            caller = "?"
        log.info("killing TUI %s (from %s)", tui.name, caller)
        try:
            self._tmux("kill-session", "-t", tui.name)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _ensure_tui(self, cwd: str, session_id: str, model: str,
                    isolate_root: str = ""):
        """Return (tui, error). One TUI per claude session: a continued
        session is matched to its live TUI by session id; a new session (or
        one whose TUI died) gets a fresh TUI, --resume'ing when continuing.
        Parallel sessions in the same project never evict each other."""
        iso = (isolate_root or "").strip()
        with self._lock:
            # Prune TUIs whose tmux died (/exit, crash, manual kill) — but
            # never one another turn just registered and hasn't launched yet.
            # Under remain-on-exit a claude that exited leaves the session up
            # with a dead pane, whose frozen last screen still shows the ❯ and
            # footer _pane_healthy looks for — so ask tmux, not the screen,
            # or the next turn pastes into a corpse.
            dead = []
            for name, t in self._tuis.items():
                if not t.spawned:
                    continue
                if not self._tmux_alive(name):
                    dead.append((name, False))
                elif self._pane_dead(name)[0]:
                    dead.append((name, True))
            for name, frozen in dead:
                if frozen:
                    self._tmux("kill-session", "-t", name)
                del self._tuis[name]
            if dead:
                self._save_state()
            if session_id:
                for t in self._tuis.values():
                    if t.session_id == session_id:
                        if (getattr(t, "isolate_root", "") or "") != iso:
                            log.info("relaunching TUI %s under new isolation",
                                     t.name)
                            self._kill(t)
                            del self._tuis[t.name]
                            break
                        if self._pane_healthy(t.name):
                            t.last_used = time.time()
                            return t, ""
                        # Crashed UI (backtrace on screen): pasting into it
                        # is useless — relaunch and resume instead.
                        log.warning("TUI %s unhealthy, relaunching", t.name)
                        self._kill(t)
                        del self._tuis[t.name]
                        break
            # Cap the fleet: evict the least-recently-used idle TUIs (never
            # one with a turn in flight) before adding another.
            while len(self._tuis) >= _MAX_TUIS:
                idle = [t for t in self._tuis.values() if t.job is None]
                if not idle:
                    break  # everything is mid-turn; allow the overflow
                victim = min(idle, key=lambda t: t.last_used)
                log.info("evicting idle TUI %s (cap %d)", victim.name, _MAX_TUIS)
                self._kill(victim)
                del self._tuis[victim.name]
                self._save_state()
            tui = _Tui(self._tui_name(cwd), cwd, isolate_root=iso)
            self._tuis[tui.name] = tui
        err = self._launch(tui, session_id, model)
        if err:
            with self._lock:
                self._tuis.pop(tui.name, None)
            self._save_state()
            return None, err
        self._save_state()
        return tui, ""

    # -- turn execution -----------------------------------------------------

    def run(self, job) -> None:
        """Execute one job as a TUI turn. Sets job status before returning."""
        if not shutil.which("tmux") and not os.path.exists("/opt/homebrew/bin/tmux"):
            self._fail(job, "interactive mode needs tmux (brew install tmux)")
            return
        cwd = os.path.expanduser(job.cwd or "") or os.path.expanduser("~")
        iso = str(getattr(job, "isolate_root", "") or "")
        tui, err = self._ensure_tui(
            cwd, job.session_id, job.model, isolate_root=iso)
        if err:
            self._fail(job, err)
            return
        with tui.lock:  # serialize turns per TUI
            tui.job = job
            job.tui_name = tui.name
            try:
                self._run_turn(job, tui, resume=False)
            finally:
                tui.job = None

    def resume(self, job) -> None:
        """Re-attach a mid-turn interactive job after a daemon restart.

        The host Claude process is still in tmux (adopted via tuis.json). We
        do **not** re-submit the prompt — only rejoin transcript drain + Stop
        hooks so the phone's job id keeps working.
        """
        if not shutil.which("tmux") and not os.path.exists("/opt/homebrew/bin/tmux"):
            self._fail(job, "interactive mode needs tmux (brew install tmux)")
            return
        sid = (job.new_session_id or job.session_id or "").strip()
        tui = None
        with self._lock:
            if job.tui_name and job.tui_name in self._tuis:
                tui = self._tuis[job.tui_name]
            if tui is None and sid:
                for t in self._tuis.values():
                    if t.session_id == sid:
                        tui = t
                        break
        if tui is None or not self._tmux_alive(tui.name):
            cwd = os.path.expanduser(job.cwd or "") or os.path.expanduser("~")
            iso = str(getattr(job, "isolate_root", "") or "")
            tui, err = self._ensure_tui(cwd, sid, job.model, isolate_root=iso)
            if err:
                self._fail(job, "interrupted by daemon restart: %s" % err)
                return
        job.tui_name = tui.name
        with tui.lock:
            tui.job = job
            try:
                job.add_event(
                    "tool",
                    name="daemon",
                    detail="resumed mid-turn after daemon restart",
                )
                job.set_phase("thinking", "resumed")
                self._run_turn(job, tui, resume=True)
            finally:
                tui.job = None

    def close_for_session(self, session_id: str) -> bool:
        """Kill the live TUI hosting this session, if any. Rewind edits the
        session journal on disk — a running CLI would neither see the edit
        nor survive under it — so the TUI dies first and the next turn
        --resume's the rewound session. Refuses mid-turn."""
        sid = (session_id or "").strip()
        if not sid:
            return False
        with self._lock:
            victim = None
            for t in self._tuis.values():
                if t.session_id == sid:
                    victim = t
                    break
            if victim is None:
                return False
            if victim.job is not None:
                from . import RunnerError
                raise RunnerError("finish or stop the running turn first")
            try:
                self._tmux("kill-session", "-t", victim.name)
            except (OSError, subprocess.TimeoutExpired):
                pass
            del self._tuis[victim.name]
            self._save_state()
        return True

    def _fail(self, job, message: str):
        # Interactive failures used to leave no trace at all in daemon.log —
        # only the phone saw them, so a turn that died overnight was
        # undiagnosable the next morning.
        log.warning("interactive job %s failed: %s",
                    getattr(job, "id", "?"), message)
        with job.lock:
            if job.status != "stopped":
                job.status = "error"
                job.error = message

    def _transcript_submits(self, transcript: str) -> int:
        """How many human messages this transcript holds — file-based proof
        that a paste+Enter was accepted (claude writes the user line on
        submit). -1 when the transcript can't be read."""
        if not transcript:
            return -1
        n = 0
        try:
            with open(transcript, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"type":"user"' in line or '"type": "user"' in line:
                        n += 1
        except OSError:
            return -1
        return n

    def _submit(self, tui: _Tui, prompt: str) -> str:
        """Send a message and make sure the TUI really took it.

        A pasted prompt occasionally sits in the input box with the Enter
        lost, leaving the turn waiting on a message claude never saw, so
        confirm against the transcript and press Enter again if needed.
        Pressing Enter on an already-empty box is a no-op, so a retry cannot
        duplicate a message that did land."""
        before = self._transcript_submits(tui.transcript)
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
                if self._transcript_submits(tui.transcript) > before:
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
        # Not proof of failure: a busy TUI queues the text and writes the line
        # later. Let the turn run; its own timeout is the backstop.
        log.warning("TUI %s: submit unconfirmed (queued, or still in the box)",
                    tui.name)

    def type_text(self, session_id: str, text: str) -> str:
        """Type a message straight into this session's live TUI.

        Interactive mode has no use for the daemon's prompt queue: the TUI
        queues typed-ahead input itself, exactly as it would for someone at
        the keyboard. The running job stays on watch (typed_ahead) so the
        reply still streams to the phone instead of landing off-screen.
        Returns "" or an error."""
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
            return "the claude TUI has exited"
        # Arm the watch BEFORE sending: the running turn can finish inside the
        # next few milliseconds, and if typed_ahead were still 0 the job would
        # end and this message's reply would stream to nobody.
        tui.typed_ahead += 1
        before = self._transcript_submits(tui.transcript)
        err = self._send_prompt(tui, text)
        if err:
            tui.typed_ahead = max(0, tui.typed_ahead - 1)
            return err
        # Confirm off-thread: while the TUI is busy the message is queued and
        # nothing is written for a while, and the phone should not wait.
        if before >= 0:
            threading.Thread(target=self._confirm_submit, args=(tui, before),
                             daemon=True).start()
        return ""

    def capture_tui(self, session_id: str, *, ansi: bool = False) -> dict:
        """Live pane frame for Live TUI clients (plain by default; ansi=True for colours)."""
        from ..live_tui import capture_session
        return capture_session(self, session_id, ansi=ansi)

    def send_tui_keys(self, session_id: str, keys=None, text: str = "") -> str:
        """Key/text injection for Live TUI (no Enter unless keys include it)."""
        from ..live_tui import send_to_session
        return send_to_session(self, session_id, keys=keys, text=text)

    def _send_prompt(self, tui: _Tui, prompt: str) -> str:
        """Type the prompt into the pane. Bracketed paste keeps multi-line
        prompts from submitting early; one Enter submits. Returns "" or err.

        A leading "!" must arrive as a real keypress — that's what flips the
        empty input into bash mode; pasted it would just be message text.
        Slash commands paste fine (the TUI parses "/..." on submit).

        The buffer name is unique per call: it used to be the fixed "bb10p",
        and `paste-buffer -d` deletes it, so two sends close together (a turn
        plus a typed-ahead message, or two sessions) could paste each other's
        text or lose it outright."""
        buf = "bb10p-%s" % uuid.uuid4().hex[:8]
        try:
            if prompt.startswith("!"):
                r = self._tmux("send-keys", "-t", tui.name, "!")
                if r.returncode != 0:
                    return "tmux send-keys failed: %s" % (
                        r.stderr.decode("utf-8", errors="replace").strip())
                time.sleep(0.2)
                prompt = prompt[1:].lstrip()
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

    # -- pane waiting ----------------------------------------------------------

    def _await_pane(self, tui: _Tui, needle: str, timeout: float) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if needle in self._pane_text(tui.name):
                return True
            time.sleep(_POLL_S)
        return False

    # -- AskUserQuestion (selection panel) -----------------------------------

    @staticmethod
    def _ask_fingerprint(questions: list) -> str:
        """Stable id for a question set (hook + transcript must not double-fire)."""
        import hashlib
        import json
        try:
            blob = json.dumps(questions, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            blob = repr(questions)
        return hashlib.sha1(blob.encode("utf-8", errors="replace")).hexdigest()[:20]

    def _ask_seen(self, job, tool_input: dict, state: dict):
        """Record an AskUserQuestion the transcript (or PreToolUse hook) showed.

        Hook fires while the panel is open; the JSONL tool_use line is often
        written only *after* the user answers. Without dedupe, that late
        transcript line re-queues the same panel on the phone even though
        Live TUI already applied the picks.
        """
        questions = []
        for q in tool_input.get("questions") or []:
            if not isinstance(q, dict):
                continue
            options = [{"label": str(o.get("label")),
                        "description": str(o.get("description") or "")}
                       for o in q.get("options") or []
                       if isinstance(o, dict) and o.get("label")]
            if not options:
                continue
            questions.append({
                "question": str(q.get("question") or ""),
                "header": str(q.get("header") or ""),
                "options": options,
                "multi_select": bool(q.get("multiSelect") or q.get("multi_select")),
            })
        if not questions:
            return
        fp = self._ask_fingerprint(questions)
        done = state.setdefault("ask_done", set())
        if fp in done:
            return
        # Already queued / in flight for this turn.
        if state.get("ask") is not None and self._ask_fingerprint(state["ask"]) == fp:
            return
        thread = state.get("ask_thread")
        if thread is not None and thread.is_alive():
            return
        if job.pending_question:
            return
        state["ask"] = questions
        headers = " · ".join(q["header"] or q["question"][:24] for q in questions)
        job.add_event("tool", name="AskUserQuestion", detail=headers)
        job.set_phase("asking", headers)

    def _answer_questions(self, job, tui: _Tui, questions: list):
        """Ask the phone, then answer the panel with keys. Runs on its own
        thread: the turn's drain loop must keep going (Stop still works)."""
        # 0 / unset = no deadline (None blocks Event.wait forever): the
        # user answers when they get to their phone.
        secs = float(getattr(self.config, "question_timeout", 0) or 0)
        timeout = secs if secs > 0 else None
        self._await_pane(tui, "Esc to cancel", 15.0)
        answers = job.request_question(questions, timeout)
        try:
            if answers is None:
                # Escape cancels the panel: the tool returns "cancelled" and
                # the model carries on rather than blocking the TUI forever.
                self._tmux("send-keys", "-t", tui.name, "Escape")
                return
            err, summary = self._drive_ask(tui, questions, answers)
        except (OSError, subprocess.TimeoutExpired) as e:
            err, summary = "tmux input failed: %s" % e, ""
        if err:
            log.warning("AskUserQuestion: %s", err)
            job.add_event("tool", name="AskUserQuestion", detail=err)
        elif summary:
            job.add_event("tool", name="AskUserQuestion answered", detail=summary)

    def _drive_ask(self, tui: _Tui, questions: list, answers: list):
        """Drive the selection panel. The cursor starts on option 1 of the
        current question and Down moves it. Single-select: Enter picks and
        jumps to the next question. Multi-select: Enter toggles a checkbox
        and leaves the cursor put, so Tab moves on. After the last question
        a review page opens whose default entry ("Submit answers") a final
        Enter applies. Returns (error, summary)."""
        picks = []
        for i, q in enumerate(questions):
            labels = [o["label"] for o in q["options"]]
            chosen = [l for l in (answers[i] if i < len(answers) else [])
                      if l in labels]
            if not chosen:
                chosen = labels[:1]
            idxs = sorted(labels.index(l) for l in chosen)
            if not q["multi_select"]:
                idxs = idxs[:1]
            picks.append("%s: %s" % (q["header"] or q["question"][:24],
                                     ", ".join(labels[i] for i in idxs)))
            cursor = 0
            for idx in idxs:
                for _ in range(idx - cursor):
                    self._tmux("send-keys", "-t", tui.name, "Down")
                    time.sleep(0.12)
                cursor = idx
                self._tmux("send-keys", "-t", tui.name, "Enter")
                time.sleep(0.3)
            if q["multi_select"]:
                self._tmux("send-keys", "-t", tui.name, "Tab")
                time.sleep(0.4)
        # A multi-question panel ends on a review page ("Submit answers");
        # a single question submits on its own last Enter and the panel is
        # simply gone. Waiting unconditionally for the review page cost 8s and
        # then fired a stray Escape into a pane that had already moved on.
        end = time.time() + 8.0
        while time.time() < end:
            pane = self._pane_text(tui.name)
            if "Submit answers" in pane:
                self._tmux("send-keys", "-t", tui.name, "Enter")
                break
            if _ASK_PANEL_MARKER not in pane:
                break            # panel closed: the picks were taken
            time.sleep(_POLL_S)
        else:
            self._tmux("send-keys", "-t", tui.name, "Escape")
            return ("the question panel did not close — screen: %s"
                    % self._pane_tail(tui.name), "")
        return "", " · ".join(picks)

    def _run_turn(self, job, tui: _Tui, resume: bool = False):
        with job.lock:
            job.status = "running"
            if tui.session_id:
                job.new_session_id = tui.session_id or job.new_session_id
            job.tui_name = tui.name
        if not resume:
            job.add_event("init", session_id=tui.session_id, model="interactive")

        transcript = tui.transcript
        offset = 0
        try:
            # Resume: only watch *new* bytes so prior events are not replayed.
            offset = os.path.getsize(transcript) if transcript else 0
        except OSError:
            pass

        tui.stop_event.clear()
        tui.stop_payload = {}
        if not resume:
            err = self._submit(tui, job.prompt)
            if err:
                self._fail(job, err)
                return
        else:
            # Rejoin an in-flight host TUI; never re-paste the prompt.
            log.info("TUI %s: resume watch for job %s (offset %d)",
                     tui.name, job.id, offset)

        timeout_s = float(getattr(self.config, "turn_timeout", 0) or 0)
        # On resume the original started_at is kept; extend deadline from now
        # so a long pre-restart wait does not instantly time out.
        if resume and timeout_s > 0:
            deadline = time.time() + timeout_s
        else:
            deadline = job.started_at + timeout_s if timeout_s > 0 else None
        prompt = job.prompt or ""
        kind = ("bash" if prompt.startswith("!") else
                "slash" if prompt.startswith("/") else "chat")
        state = {"last_text": "", "kind": kind,
                 "local_done": 0.0, "pending_local": "",
                 "ask": None, "ask_thread": None,
                 "ask_done": set()}  # fingerprints already shown / answered
        # Re-opened pending_question after restart has no waiter — clear it
        # so the phone does not show a dead gate; a still-open panel will
        # re-fire via hook / transcript.
        if resume and job.pending_question:
            with job.lock:
                job.pending_question = None
            job.cancel_question()
        if resume and job.pending_permission:
            with job.lock:
                job.pending_permission = None
            job.cancel_permission()
        submit_t = time.time()
        interrupted = False
        ask_wait_from = None
        stalled = False
        last_offset = offset     # crash watchdog: transcript progress...
        life_t = submit_t       # ...and when we last saw a sign of life
        dead_t = 0.0            # since when the pane has looked crashed
        pane_t = 0.0            # last watchdog pane capture
        tui.typed_ahead = 0     # messages typed into the pane during this turn
        while True:
            if tui.stop_event.wait(_POLL_S):
                # Turn finished. A message typed into the pane mid-turn
                # (type_text) sits in the TUI's own queue and MAY start the
                # next turn now — but claude also pulls queued messages into
                # the turn that just ended, so several messages can share
                # one Stop. The count is a hint, not a contract: keep the
                # job on watch only if the pane shows another turn starting.
                if ((tui.typed_ahead > 0 or self._pane_queued(tui.name))
                        and self._next_turn_starts(job, tui)):
                    tui.typed_ahead = max(0, tui.typed_ahead - 1)
                    tui.stop_event.clear()
                    state["local_done"] = 0.0
                    submit_t = life_t = time.time()
                    dead_t = 0.0
                    # turn_timeout bounds a TURN, not the job: typing ahead
                    # legitimately keeps one job running across several, and
                    # a job deadline would fail it while the TUI works on.
                    if timeout_s > 0:
                        deadline = time.time() + timeout_s
                    job.set_phase("thinking", "")
                    continue
                tui.typed_ahead = 0
                break
            offset, transcript = self._drain(job, tui, transcript, offset, state)
            # TaskCreate writes land under ~/.claude/tasks/ slightly after the
            # tool_use line; re-read so the phone checklist stays current.
            now = time.time()
            if now - float(state.get("todo_tick_at") or 0) >= 1.5:
                state["todo_tick_at"] = now
                from .claude import emit_claude_todos
                sid = (tui.session_id
                       or getattr(job, "session_id", None)
                       or getattr(job, "new_session_id", None)
                       or "")
                if sid:
                    emit_claude_todos(job, str(sid))
            # A hook-delivered ask (see on_hook/PreToolUse) is the same
            # thing as one spotted in the transcript, just earlier.
            if tui.hook_ask and not state["ask"]:
                self._ask_seen(job, {"questions": tui.hook_ask}, state)
                tui.hook_ask = None
            thread = state["ask_thread"]
            if state["ask"] and not (thread and thread.is_alive()):
                questions, state["ask"] = state["ask"], None
                # Mark handled *before* the phone answers so a late JSONL
                # tool_use for the same panel cannot re-open the sheet.
                state.setdefault("ask_done", set()).add(
                    self._ask_fingerprint(questions))
                state["ask_thread"] = threading.Thread(
                    target=self._answer_questions, args=(job, tui, questions),
                    daemon=True)
                state["ask_thread"].start()
            with job.lock:
                stopped = job.status == "stopped"
            if stopped:
                interrupted = True
                try:  # Escape = the TUI's interrupt key
                    self._tmux("send-keys", "-t", tui.name, "Escape")
                except (OSError, subprocess.TimeoutExpired):
                    pass
                break
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
                # turn_timeout is a *stall* bound, not a hard wall. Multi-agent
                # and long tool loops keep "esc to interrupt" up for >30 min
                # while still making progress — failing here left the host TUI
                # working but dropped the session from clients' "working" list
                # (job no longer in active_status).
                still_going = (
                    tui.compacting
                    or bool(state.get("ask"))
                    or bool(job.pending_question)
                    or self._pane_busy(tui.name)
                    or self._pane_queued(tui.name)
                )
                if still_going and timeout_s > 0:
                    deadline = time.time() + timeout_s
                    life_t = time.time()
                    log.info("TUI %s: extending turn deadline by %ds (pane still busy)",
                             tui.name, int(timeout_s))
                    continue
                tail = self._pane_tail(tui.name)
                self._fail(job, "turn timed out after %ds%s" % (
                    int(timeout_s), " — screen: %s" % tail if tail else ""))
                return
            gone = not self._tmux_alive(tui.name)
            died, why = (False, "") if gone else self._pane_dead(tui.name)
            if gone or died:
                if prompt.strip() in ("/exit", "/quit"):
                    # Killing the TUI is what /exit is for — a clean end, not
                    # a failure. The next message launches a fresh TUI and
                    # --resume's this session.
                    from ..render_blocks import markdown_to_blocks
                    msg = "TUI closed. The next message starts a fresh one."
                    job.add_event("text", text=msg,
                                  blocks=markdown_to_blocks(msg))
                    with job.lock:
                        if job.status != "stopped":
                            job.result_text = msg
                            job.status = "done"
                    job.add_event("result", is_error=False, duration_ms=int(
                        (time.time() - job.started_at) * 1000), cost_usd=0)
                    self._kill(tui)
                    return
                detail = ""
                if died:
                    # The frozen pane still holds the last screen: save the
                    # scrollback and quote the reason, then release it.
                    detail = "%s — %s" % (why, self._save_crash(tui))
                    self._kill(tui)
                    tui.session_id = ""
                    tui.transcript = ""
                    self._save_state()
                self._fail(job, "claude TUI exited mid-turn%s"
                           % (" (%s)" % detail if detail else
                              " (tmux session vanished — nothing left to read)"))
                return
            # Crash watchdog (_CRASH_STALL_S): claude can die leaving a
            # backtrace in a still-alive pane — no Stop hook will ever come.
            now = time.time()
            asking = state["ask"] or (state["ask_thread"]
                                      and state["ask_thread"].is_alive())
            if offset != last_offset or tui.compacting or asking:
                last_offset = offset
                life_t = now
                dead_t = 0.0
            elif now - pane_t >= 1.0:
                pane_t = now
                if self._pane_busy(tui.name):
                    life_t = now
                    dead_t = 0.0
                elif not self._pane_healthy(tui.name):
                    dead_t = dead_t or now
                    if now - dead_t > _CRASH_STALL_S:
                        detail = self._save_crash(tui)
                        self._kill(tui)
                        tui.session_id = ""
                        tui.transcript = ""
                        self._fail(job, "claude crashed mid-turn (the next "
                                   "message restarts it) — %s" % detail)
                        return
                else:
                    dead_t = 0.0
                    if kind == "chat" and now - life_t > _LOST_STOP_S:
                        # Healthy idle input box, transcript quiet, yet no
                        # Stop hook: the hook was lost. End with what we have.
                        log.warning("TUI %s: idle %.0fs with no Stop hook",
                                    tui.name, _LOST_STOP_S)
                        stalled = True
                        break
            # Local turns (!shell, most /commands) never fire a Stop hook:
            # they end after a quiet period following their output — unless a
            # model turn is (still) running, which then owns the ending.
            if kind == "chat" or tui.compacting:
                continue
            done_ts = state["local_done"]
            quiet = _SLASH_QUIET_S if kind == "slash" else _BASH_QUIET_S
            if done_ts and time.time() - done_ts > quiet \
                    and not self._pane_busy(tui.name):
                break
            if (kind == "slash" and not done_ts
                    and time.time() - submit_t > _PANEL_TIMEOUT_S
                    and not self._pane_busy(tui.name)):
                # No output and nothing running: a UI panel (/cost, /mcp...).
                # Snapshot it for the phone, then Esc it away so the input
                # box is usable again.
                rows = [l.rstrip() for l in
                        self._pane_text(tui.name).splitlines()
                        if l.strip() and not l.startswith("❯")
                        and "⏵⏵" not in l and set(l.strip()) != {"─"}]
                state["pending_local"] = "\n".join(rows)[-4000:]
                try:
                    self._tmux("send-keys", "-t", tui.name, "Escape")
                except (OSError, subprocess.TimeoutExpired):
                    pass
                state["local_done"] = time.time()
        # Final transcript drains: the TUI flushes the JSONL lazily, usually
        # AFTER the Stop hook fires, so keep reading briefly until the
        # assistant text shows up (or the grace window closes). Local turns
        # (no Stop) already sat through their quiet period — one drain only.
        grace = time.time() + 3.0
        while True:
            offset, transcript = self._drain(job, tui, transcript, offset, state)
            if (interrupted or state["last_text"]
                    or not tui.stop_event.is_set() or time.time() > grace):
                break
            time.sleep(0.2)
        if stalled and not state["last_text"]:
            self._fail(job, "turn ended with no reply (Stop hook lost) — "
                       "screen: %s" % self._pane_tail(tui.name))
            return
        if not interrupted:
            if tui.stop_event.is_set():
                # Transcript may never catch up (or stop at an interim
                # message) — the Stop payload carries the turn's true text.
                t = str((tui.stop_payload or {}).get(
                    "last_assistant_message", "") or "")
                if t and t != state["last_text"]:
                    from ..render_blocks import markdown_to_blocks
                    state["last_text"] = t
                    job.add_event("text", text=t, blocks=markdown_to_blocks(t))
            if not state["last_text"] and state["pending_local"]:
                # A local command whose only output was its stdout (or the
                # snapshot of the panel it opened).
                from ..render_blocks import markdown_to_blocks
                t = state["pending_local"]
                state["last_text"] = t
                job.add_event("text", text="```\n%s\n```" % t,
                              blocks=markdown_to_blocks("```\n%s\n```" % t))
        with job.lock:
            job.new_session_id = tui.session_id or job.new_session_id
            if not interrupted and job.status != "stopped":
                job.result_text = state["last_text"]
                job.status = "done"
        if not interrupted:
            job.add_event("result", is_error=False,
                          duration_ms=int((time.time() - job.started_at) * 1000),
                          cost_usd=0)

    # -- transcript tailing --------------------------------------------------

    def _drain(self, job, tui: _Tui, transcript: str, offset: int, state: dict):
        """Read new transcript lines into job events. Follows the file the
        hooks most recently reported (a resume forks to a new path)."""
        if tui.transcript and tui.transcript != transcript:
            transcript, offset = tui.transcript, 0
        if not transcript:
            return offset, transcript
        try:
            with open(transcript, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                offset = f.tell()
        except OSError:
            return offset, transcript
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            self._transcript_line(job, obj, state)
        return offset, transcript

    def _transcript_line(self, job, obj: dict, state: dict):
        """Same assistant-content shapes as the -p stream, minus the wrapper.
        Plus the TUI-only shapes: bash-mode (!) lines arrive as user messages
        with <bash-*> markers, /command echo and output as system lines with
        subtype local_command."""
        from .claude import tool_detail, _PHASE_BY_TOOL, _TASK_TOOLS, emit_claude_todos
        from ..render_blocks import markdown_to_blocks
        if obj.get("isSidechain"):
            return
        typ = obj.get("type")
        if typ == "user":
            self._user_line(job, obj, state)
            return
        if typ == "system":
            self._system_line(job, obj, state)
            return
        if typ != "assistant":
            return
        message = obj.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and block.get("text"):
                t = block["text"]
                state["last_text"] = t
                job.add_event("text", text=t, blocks=markdown_to_blocks(t))
                job.set_phase("writing", t[-160:])
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                if name == "AskUserQuestion":
                    # The panel is blocking the pane right now: hand the
                    # questions to the phone (_run_turn picks this up) and
                    # answer them with keys once it replies.
                    self._ask_seen(job, block.get("input") or {}, state)
                    continue
                tool_input = block.get("input") or {}
                if name in _TASK_TOOLS:
                    sid = (obj.get("session_id")
                           or getattr(job, "session_id", None)
                           or getattr(job, "new_session_id", None)
                           or state.get("session_id")
                           or "")
                    emit_claude_todos(
                        job, str(sid or ""),
                        tool_input=tool_input if isinstance(tool_input, dict) else None)
                    continue
                detail = tool_detail(tool_input if isinstance(tool_input, dict) else {})
                job.add_event("tool", name=name, detail=detail)
                job.set_phase(_PHASE_BY_TOOL.get(name, "tool"), detail or name)
            elif block.get("type") == "thinking":
                job.set_phase("thinking", "")

    def _user_line(self, job, obj: dict, state: dict):
        """Bash-mode (!) input/output, /command echo+output (which some CLI
        paths record as user lines rather than system/local_command), and the
        clean markdown some /commands (e.g. /context) inject as an isMeta
        user message."""
        from ..render_blocks import markdown_to_blocks
        content = (obj.get("message") or {}).get("content")
        if not isinstance(content, str):
            return
        if "<local-command-caveat>" in content:
            return  # boilerplate wrapper around local-command output
        if content.startswith("<bash-input>"):
            cmd = _tag_text(content, "bash-input").strip()
            job.add_event("tool", name="shell", detail=cmd)
            job.set_phase("tool", cmd)
        elif content.startswith("<bash-stdout>"):
            state["local_done"] = time.time()
            out = (_tag_text(content, "bash-stdout").rstrip() + "\n" +
                   _tag_text(content, "bash-stderr").rstrip()).strip()
            if out:
                state["last_text"] = out
                fenced = "```\n%s\n```" % out
                job.add_event("text", text=fenced,
                              blocks=markdown_to_blocks(fenced))
        elif content.startswith(("<command-name>", "<local-command-stdout>")):
            self._local_command(job, content, state)
        elif obj.get("isMeta") and state.get("kind") == "slash":
            # Only in slash turns: normal turns inject isMeta user lines too
            # (hook output, reminders) which must stay invisible.
            t = content.strip()
            if t:
                state["last_text"] = t
                job.add_event("text", text=t, blocks=markdown_to_blocks(t))

    def _system_line(self, job, obj: dict, state: dict):
        if obj.get("subtype") != "local_command":
            return
        self._local_command(job, str(obj.get("content") or ""), state)

    def _local_command(self, job, content: str, state: dict):
        """/command echo and local output. Output is held back as a result
        fallback: when a nicer isMeta markdown twin follows (e.g. /context),
        that one wins."""
        name = _tag_text(content, "command-name").strip()
        if name:
            args = _tag_text(content, "command-args").strip()
            job.add_event("tool", name=name, detail=args)
            job.set_phase("tool", (name + " " + args).strip())
            return
        if "<local-command-stdout>" in content:
            state["local_done"] = time.time()
            out = _ANSI_RE.sub(
                "", _tag_text(content, "local-command-stdout")).strip()
            if out and not state["pending_local"]:
                state["pending_local"] = out
