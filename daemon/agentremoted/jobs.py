"""Run agent turns headlessly and buffer output for polling.

The BB10 client cannot hold a streaming connection reliably, so each
"continue" request creates a Job: an agent CLI subprocess whose output is
parsed into an in-memory event list. The client polls
GET /api/jobs/<id>?since=N and receives only new events.

Everything provider-specific (command construction, stream parsing, session
id discovery) lives in the injected runner — see providers/__init__.py for
the interface. The queue, permission bridge, stop/kill handling, and the
chain of follow-up jobs are provider-agnostic.
"""

import os
import queue
import signal
import subprocess
import threading
import time
import uuid

from .providers import RunnerError

_STDERR_TAIL = 4000

# Prompts a client may queue behind one running job (chain).
_MAX_QUEUED = 10

# How long the stream loop sleeps waiting for output before running
# housekeeping (runner.tick, deadline check).
_READ_TIMEOUT = 0.25


class Job:
    def __init__(self, job_id: str, session_id: str, prompt: str, cwd: str):
        self.id = job_id
        self.session_id = session_id       # session being resumed ("" = new)
        self.new_session_id = ""           # session id created by this run
        self.prompt = prompt
        self.cwd = cwd
        self.status = "starting"           # starting|running|done|error|stopped
        self.error = ""
        self.result_text = ""
        self.events = []                   # [{seq, kind, text, ...}]
        self.lock = threading.Lock()
        self.proc = None
        self.started_at = time.time()

        # What the agent is doing right now (thinking/writing/tool + snippet)
        # — feeds the live status banner alongside the last tool event.
        self.phase = ""
        self.phase_detail = ""

        # Scratch space owned by the provider runner for this job.
        self.runner_state = {}

        # Prompts queued behind this job. The daemon owns the queue (the
        # phone may die or lose Wi-Fi at any time): when this job finishes
        # cleanly the manager starts the next prompt as a follow-up job and
        # records its id in next_job_id so the client can follow the chain.
        self.permission_mode = ""
        self.model = ""                    # "" -> provider/CLI default
        self.effort = ""                   # reasoning effort ("" -> default)
        self.queued = []                   # [{"id": qid, "prompt": str}]
        self.next_job_id = ""
        self.dropped_queued = 0            # queue size discarded on stop/error
        self._queue_seq = 0

        # Interactive permission approval (claude, non-bypass modes). The
        # helper MCP tool calls request_permission() from an HTTP worker
        # thread and blocks on _perm_event until the phone POSTs a decision.
        self.perm_nonce = uuid.uuid4().hex
        self.pending_permission = None     # {request_id, tool_name, detail}
        self._perm_seq = 0
        self._perm_event = threading.Event()
        self._perm_decision = None         # {"allow": bool, "message": str}

        # AskUserQuestion (interactive TUI). Same blocking shape as the
        # permission channel, but the answer is a pick list per question:
        # the TUI panel is driven with tmux keys once the phone replies.
        self.pending_question = None       # {request_id, questions:[...]}
        self._q_seq = 0
        self._q_event = threading.Event()
        self._q_answers = None             # [[label, ...], ...] or None
        self.question_notes = []           # free text typed with the picks

    @staticmethod
    def _clip_mid(text, max_len: int) -> str:
        """Single-line, middle-ellipsis clip for status-banner fields.

        Head and tail stay readable (paths/commands); the middle is dropped.
        """
        if not text:
            return ""
        t = " ".join(str(text).split())
        if len(t) <= max_len:
            return t
        if max_len < 3:
            return t[:max_len]
        keep = max_len - 1  # room for "…"
        head = keep // 2
        tail = keep - head
        return t[:head] + "…" + t[-tail:]

    def add_event(self, kind: str, **fields):
        # Tool rows feed the status banner (poll + /ws/status last-tool).
        # Keep name/detail single-line and short so a multi-line shell
        # command can't blow the phone strip to a dozen wraps.
        if kind == "tool":
            if "detail" in fields and fields["detail"]:
                fields["detail"] = self._clip_mid(fields["detail"], 200)
            if "name" in fields and fields["name"]:
                fields["name"] = self._clip_mid(fields["name"], 80)
        with self.lock:
            event = {"seq": len(self.events), "kind": kind}
            event.update(fields)
            self.events.append(event)

    def set_phase(self, phase: str, detail: str = ""):
        # Status banner on the phone is a 1–2 line strip: collapse whitespace
        # and hard-cap length (middle ellipsis so head + tail stay readable).
        if detail:
            detail = self._clip_mid(detail, 200)
        with self.lock:
            self.phase = phase
            self.phase_detail = detail

    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "session_id": self.session_id,
                "new_session_id": self.new_session_id,
                "status": self.status,
                "error": self.error,
                "result_text": self.result_text if self.status in ("done", "error") else "",
                "pending_permission": dict(self.pending_permission)
                if self.pending_permission else None,
                "pending_question": dict(self.pending_question)
                if self.pending_question else None,
                "queued": [dict(q) for q in self.queued],
                "next_job_id": self.next_job_id,
                "dropped_queued": self.dropped_queued,
                "next_seq": len(self.events),
                "events": self.events[since:],
            }

    # -- permission approval -------------------------------------------

    def request_permission(self, tool_name: str, detail: str, timeout: float) -> dict:
        """Register a pending request and block until decided/timed out.

        Called from the daemon's /internal/permission handler thread.
        """
        with self.lock:
            self._perm_seq += 1
            request_id = "%s-p%d" % (self.id, self._perm_seq)
            self.pending_permission = {
                "request_id": request_id,
                "tool_name": tool_name,
                "detail": detail,
            }
            self._perm_decision = None
            self._perm_event.clear()
        self.add_event("permission", request_id=request_id,
                       tool_name=tool_name, detail=detail)

        got = self._perm_event.wait(timeout)

        with self.lock:
            decision = self._perm_decision
            self.pending_permission = None
        if not got or decision is None:
            self.add_event("permission_resolved", request_id=request_id,
                           allow=False, reason="timeout")
            return {"allow": False, "message": "timed out waiting for approval"}
        self.add_event("permission_resolved", request_id=request_id,
                       allow=bool(decision.get("allow")))
        return decision

    def resolve_permission(self, request_id: str, allow: bool,
                           message: str = "") -> bool:
        """Record the phone's decision and wake request_permission()."""
        with self.lock:
            pending = self.pending_permission
            if not pending or pending["request_id"] != request_id:
                return False
            self._perm_decision = {"allow": bool(allow), "message": message}
            self._perm_event.set()
        return True

    def cancel_permission(self):
        """Deny any in-flight request (job stopping / ending)."""
        with self.lock:
            if not self.pending_permission:
                return
            self._perm_decision = {"allow": False, "message": "job ended"}
            self._perm_event.set()

    # -- AskUserQuestion -----------------------------------------------

    def request_question(self, questions: list, timeout: float):
        """Publish AskUserQuestion to the phone and block for the answer.

        Returns a list (one entry per question) of chosen labels, or None
        when cancelled / timed out — the caller then Escapes the panel.
        """
        with self.lock:
            self._q_seq += 1
            request_id = "%s-q%d" % (self.id, self._q_seq)
            self.pending_question = {
                "request_id": request_id,
                "questions": questions,
            }
            self._q_answers = None
            self.question_notes = []
            self._q_event.clear()
        job_event = {"request_id": request_id, "questions": questions}
        self.add_event("question", **job_event)

        got = self._q_event.wait(timeout)

        with self.lock:
            answers = self._q_answers
            self.pending_question = None
        if not got or answers is None:
            self.add_event("question_resolved", request_id=request_id,
                           cancelled=True)
            return None
        self.add_event("question_resolved", request_id=request_id,
                       cancelled=False, answers=answers)
        return answers

    def resolve_question(self, request_id: str, answers, notes=None) -> bool:
        """Record the phone's picks (or None to cancel) and wake the wait.
        notes carries the free text typed alongside a pick, one per
        question, for options that accept one."""
        with self.lock:
            pending = self.pending_question
            if not pending or pending["request_id"] != request_id:
                return False
            self._q_answers = answers
            self.question_notes = list(notes) if notes else []
            self._q_event.set()
        return True

    def cancel_question(self):
        with self.lock:
            if not self.pending_question:
                return
            self._q_answers = None
            self._q_event.set()

    def brief(self) -> dict:
        with self.lock:
            return {
                "id": self.id,
                "session_id": self.session_id,
                "new_session_id": self.new_session_id,
                "status": self.status,
                "prompt": self.prompt[:120],
                "queued_count": len(self.queued),
                "event_count": len(self.events),
            }


class JobManager:
    def __init__(self, config, runner):
        self.config = config
        self.runner = runner
        self.jobs = {}
        self.lock = threading.Lock()

    def start_job(self, prompt: str, cwd: str, session_id: str = "",
                  permission_mode: str = "", model: str = "", effort: str = "",
                  queued: list = None) -> Job:
        job = Job(uuid.uuid4().hex[:12], session_id, prompt, cwd)
        job.permission_mode = permission_mode
        job.model = model
        job.effort = effort
        if queued:
            job.queued = list(queued)
        with self.lock:
            self.jobs[job.id] = job
            self._prune_locked()
        thread = threading.Thread(target=self._run, args=(job, permission_mode), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job:
        with self.lock:
            return self.jobs.get(job_id)

    def list_jobs(self) -> list:
        with self.lock:
            jobs = list(self.jobs.values())
        return [j.brief() for j in jobs]

    def active_status(self) -> list:
        """Live status of every active job — the /ws/status stream payload."""
        with self.lock:
            jobs = list(self.jobs.values())
        out = []
        now = time.time()
        for job in jobs:
            with job.lock:
                if job.status not in ("starting", "running"):
                    continue
                tool = {}
                for event in reversed(job.events):
                    if event["kind"] == "tool":
                        tool = event
                        break
                out.append({
                    "job_id": job.id,
                    "session_id": job.session_id,
                    "new_session_id": job.new_session_id,
                    "status": job.status,
                    "prompt": job.prompt[:120],
                    "elapsed_s": int(now - job.started_at),
                    "queued_count": len(job.queued),
                    "tool": tool.get("name", ""),
                    "tool_detail": tool.get("detail", ""),
                    "phase": job.phase,
                    "phase_detail": job.phase_detail,
                    "pending_permission": bool(job.pending_permission),
                    "pending_question": bool(job.pending_question),
                    # Doorbell for the phone: it only fetches
                    # /api/jobs/<id>?since=N when this grows past its cursor.
                    "next_seq": len(job.events),
                })
        out.sort(key=lambda s: s["job_id"])
        return out

    def enqueue(self, job_id: str, prompt: str):
        """Queue a prompt behind a running job (following the chain).

        Returns (queued_list, "") on success or (None, reason).
        """
        job = self._chain_head(job_id)
        if job is None:
            return None, "job not running"
        with job.lock:
            if job.status not in ("starting", "running"):
                return None, "job not running"
            if len(job.queued) >= _MAX_QUEUED:
                return None, "queue full"
            job._queue_seq += 1
            job.queued.append({
                "id": "%s-q%d" % (job.id, job._queue_seq),
                "prompt": prompt,
            })
            return [dict(q) for q in job.queued], ""

    def type_into_tui(self, job_id: str, prompt: str):
        """Type a message into the running job's interactive TUI.

        Interactive turns don't use this queue: the hosted TUI has one of its
        own, so a message sent mid-turn goes straight into the pane and the
        running job keeps watching. Returns "" or a reason."""
        job = self._chain_head(job_id)
        if job is None:
            return "job not running"
        with job.lock:
            if job.status not in ("starting", "running"):
                return "job not running"
            if (job.permission_mode or "") != "interactive":
                return "not an interactive turn"
            session = job.new_session_id or job.session_id
        typer = getattr(self.runner, "type_into_tui", None)
        if typer is None:
            return "provider has no interactive TUI"
        if not session:
            return "session not known yet"
        return typer(session, prompt) or ""

    def cancel_queued(self, job_id: str, qid: str):
        """Remove one queued prompt. Returns (queued_list, prompt) or None."""
        job = self._chain_head(job_id)
        if job is None:
            return None
        with job.lock:
            for i, entry in enumerate(job.queued):
                if entry["id"] == qid:
                    removed = job.queued.pop(i)
                    return [dict(q) for q in job.queued], removed["prompt"]
        return None

    def _chain_head(self, job_id: str) -> Job:
        """Follow next_job_id links to the currently active job, if any.

        The phone may still hold a finished job's id for one poll cycle
        while the daemon has already started the next queued prompt.
        """
        job = self.get(job_id)
        seen = set()
        while job is not None and job.id not in seen:
            seen.add(job.id)
            with job.lock:
                if job.status in ("starting", "running"):
                    return job
                next_id = job.next_job_id
            job = self.get(next_id) if next_id else None
        return None

    def stop(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel_permission()
        job.cancel_question()
        with job.lock:
            proc = job.proc
            if job.status in ("starting", "running"):
                job.status = "stopped"
            # Stop means stop: whatever was queued behind this job dies too.
            if job.queued:
                job.dropped_queued += len(job.queued)
                job.queued = []
        if proc and proc.poll() is None:
            _signal_group(proc, signal.SIGTERM)
            # Escalate if the CLI ignores SIGTERM (it may be mid tool-call).
            threading.Thread(target=_ensure_dead, args=(proc,), daemon=True).start()
        return True

    def resolve_permission(self, job_id: str, request_id: str, allow: bool,
                           message: str = "") -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        return job.resolve_permission(request_id, allow, message)

    def resolve_question(self, job_id: str, request_id: str, answers,
                         notes=None) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        return job.resolve_question(request_id, answers, notes)

    def request_permission(self, job_id: str, nonce: str, tool_name: str,
                           tool_input: dict) -> dict:
        """Bridge for the /internal/permission endpoint (nonce-authenticated)."""
        from .providers.claude import tool_detail
        job = self.get(job_id)
        if job is None or not nonce or nonce != job.perm_nonce:
            return {"allow": False, "message": "unknown job"}
        timeout = float(getattr(self.config, "permission_timeout", 300) or 300)
        return job.request_permission(tool_name, tool_detail(tool_input, 300), timeout)

    # -- internals -----------------------------------------------------

    def _run(self, job: Job, permission_mode: str):
        mode = permission_mode or self.config.permission_mode
        # A runner may claim the whole job (claude's "interactive" mode runs
        # in a tmux TUI, not a subprocess). It sets the final status itself.
        alt = getattr(self.runner, "run_alternate", None)
        if alt is not None:
            try:
                handled = alt(job, mode)
            except Exception as e:  # never let a mode bug hang the job
                self._fail_early(job, "interactive run failed: %s" % e)
                return
            if handled:
                self._dispatch_next(job)
                return
        try:
            cmd, env = self.runner.prepare(job, mode)
        except RunnerError as e:
            self._fail_early(job, str(e))
            return
        if job.cwd and not os.path.isdir(job.cwd):
            self._fail_early(job, "project directory not found: %s" % job.cwd)
            return
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=job.cwd or None,
                env=env,
                # Prompt is always on argv; leave stdin closed so CLIs that
                # optionally read more input (codex exec) do not hang.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # Own process group so stop() can kill the CLI *and* whatever
                # tools it spawned, not just the top-level process.
                start_new_session=True,
            )
        except OSError as e:
            self.runner.cleanup(job)
            self._fail_early(job, "failed to launch %s: %s" % (self.runner.name, e))
            return

        with job.lock:
            job.proc = proc
            job.status = "running"

        # Drain stderr concurrently: if the CLI fills the 64 KB stderr pipe
        # while we are still reading stdout, both processes deadlock.
        stderr_chunks = []
        drain = threading.Thread(
            target=_drain_stderr, args=(proc, stderr_chunks), daemon=True)
        drain.start()

        # Read stdout through a queue instead of blocking on readline():
        # the loop keeps waking to run runner.tick() (grok's new-session
        # scan) and to enforce the turn deadline even when the CLI is silent.
        out_q = _stdout_queue(proc)
        timeout_s = float(getattr(self.config, "turn_timeout", 0) or 0)
        deadline = job.started_at + timeout_s if timeout_s > 0 else None
        timed_out = False
        while True:
            try:
                line = out_q.get(timeout=_READ_TIMEOUT)
            except queue.Empty:
                self.runner.tick(job)
                if deadline and time.time() > deadline and proc.poll() is None:
                    timed_out = True
                    _signal_group(proc, signal.SIGKILL)
                    break
                continue
            if line is None:  # EOF
                break
            line = line.strip()
            if line:
                self.runner.handle_stream_line(job, line)
            self.runner.tick(job)

        code = _reap(proc)
        drain.join(timeout=5)
        job.cancel_permission()
        self.runner.cleanup(job)
        stderr_tail = "".join(stderr_chunks)[-2000:]
        ok = self.runner.finalize(job, code, stderr_tail)
        with job.lock:
            if job.status == "stopped":
                pass
            elif timed_out:
                job.status = "error"
                job.error = job.error or ("turn timed out after %ds" % int(timeout_s))
            elif ok is True or (ok is None and code == 0):
                job.status = "done"
            else:
                job.status = "error"
                job.error = job.error or (stderr_tail.strip()
                                          or "%s exited with code %s" % (self.runner.name, code))
        self._dispatch_next(job)

    def _fail_early(self, job: Job, message: str):
        with job.lock:
            job.status = "error"
            job.error = message
        self._dispatch_next(job)

    def _dispatch_next(self, job: Job):
        """Start the next queued prompt as a follow-up job (resume the fork
        this job created), carrying the rest of the queue along."""
        with job.lock:
            if job.status != "done":
                # Failed/stopped runs don't dispatch; report the drop.
                if job.queued:
                    job.dropped_queued += len(job.queued)
                    job.queued = []
                return
            if not job.queued:
                return
            session = job.new_session_id or job.session_id
            if not session:
                job.dropped_queued += len(job.queued)
                job.queued = []
                return
            entry = job.queued.pop(0)
            remaining = job.queued
            job.queued = []
            cwd = job.cwd
            mode = job.permission_mode
            model = job.model
            effort = job.effort
        nxt = self.start_job(entry["prompt"], cwd, session_id=session,
                             permission_mode=mode, model=model, effort=effort,
                             queued=remaining)
        with job.lock:
            job.next_job_id = nxt.id

    def _prune_locked(self):
        """Drop the oldest finished jobs beyond the cap. Caller holds self.lock."""
        finished = [j for j in self.jobs.values()
                    if j.status in ("done", "error", "stopped")]
        excess = len(finished) - int(self.config.max_finished_jobs)
        for job in finished[:max(0, excess)]:
            self.jobs.pop(job.id, None)


def _stdout_queue(proc) -> "queue.Queue":
    """Feed proc.stdout lines into a queue; None marks EOF."""
    q = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                q.put(line)
        except (OSError, ValueError):
            pass
        q.put(None)

    threading.Thread(target=pump, daemon=True).start()
    return q


def _drain_stderr(proc, chunks):
    try:
        for line in proc.stderr:
            chunks.append(line)
            while len(chunks) > 1 and sum(len(c) for c in chunks) > _STDERR_TAIL:
                chunks.pop(0)
    except (OSError, ValueError):
        pass


def _reap(proc, grace_s: float = 5.0):
    try:
        return proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
        try:
            return proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            return -1


def _signal_group(proc, sig):
    """Signal the job's whole process group; fall back to the process."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (OSError, ProcessLookupError):
        try:
            proc.send_signal(sig)
        except (OSError, ProcessLookupError):
            pass


def _ensure_dead(proc, grace_s: float = 10.0):
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
