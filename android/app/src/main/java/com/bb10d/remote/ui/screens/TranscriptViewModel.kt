package com.bb10d.remote.ui.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.audio.Chime
import com.bb10d.remote.data.ActiveJobDto
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.ExecMode
import com.bb10d.remote.data.JobState
import com.bb10d.remote.data.JobWatcher
import com.bb10d.remote.data.MessageDto
import com.bb10d.remote.data.Profile
import com.bb10d.remote.data.SessionDto
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.data.Time
import com.bb10d.remote.net.DaemonClient
import com.bb10d.remote.net.DaemonException
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** One rendered row of the conversation. */
data class TranscriptItem(
    val id: String,
    /** user | assistant | status | notice */
    val role: String,
    val text: String,
    val ts: String = "",
    /** grok status lines: thought | worked. */
    val metaKind: String = "",
    /** Appended locally while a turn runs; replaced by the real record later. */
    val live: Boolean = false,
    /** notice severity: info | error. */
    val severity: String = "info",
)

data class TranscriptUi(
    val items: List<TranscriptItem> = emptyList(),
    val loading: Boolean = false,
    val loadingOlder: Boolean = false,
    val canLoadOlder: Boolean = false,
    val error: String? = null,
    /** Transient one-liner under the composer (queue notes, refusals). */
    val status: String = "",
    /**
     * Bumped when the newest message must come into view — opening the
     * session, sending, refreshing. It rides *inside* the UI state so the
     * jump and the list it refers to always arrive in the same frame.
     */
    val scrollTick: Int = 0,
    /**
     * Bumped when older messages were inserted *above* the current ones.
     *
     * A LazyColumn anchors on an index, so prepending N rows silently drags
     * the viewport N rows back through history. The screen uses this count to
     * re-anchor on the message the reader was already looking at.
     */
    val prependTick: Int = 0,
    val prependCount: Int = 0,
)

class TranscriptViewModel(
    private val repo: AgentRepository,
    initialRef: SessionRef,
    private val initialJobId: String,
) : ViewModel() {

    private val _ref = MutableStateFlow(initialRef)
    val ref: StateFlow<SessionRef> = _ref.asStateFlow()

    private val _ui = MutableStateFlow(TranscriptUi(loading = true))
    val ui: StateFlow<TranscriptUi> = _ui.asStateFlow()

    private val _session = MutableStateFlow<SessionDto?>(null)
    val session: StateFlow<SessionDto?> = _session.asStateFlow()

    val profile: StateFlow<Profile?> = repo.profiles
        .map { it.byId(initialRef.profileId) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, repo.profile(initialRef.profileId))

    private val watcher = JobWatcher(repo, initialRef.profileId, viewModelScope)
    val job: StateFlow<JobState> = watcher.state

    val settings = repo.settings

    /** Daemon-pushed status for this session, including turns we did not start. */
    val liveStatus: StateFlow<ActiveJobDto?> = combine(repo.active, _ref) { byProfile, ref ->
        byProfile[ref.profileId]?.firstOrNull { it.sessionIds().contains(ref.sessionId) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private var loadedTotal = 0
    private var earliestOffset = 0
    private var liveCounter = 0
    private var lastCueSignature = ""

    /** Jobs already seen through to the end; never re-attach to these. */
    private val handledJobs = mutableSetOf<String>()

    private val client: DaemonClient?
        get() = repo.client(_ref.value.profileId)

    init {
        viewModelScope.launch {
            watcher.events.collect { event ->
                if (event.text.isNotBlank()) {
                    append(
                        TranscriptItem(
                            id = "live-${liveCounter++}",
                            role = "assistant",
                            text = event.text,
                            live = true,
                        ),
                    )
                }
            }
        }
        viewModelScope.launch {
            watcher.sessionIdChanged.collect { newId ->
                if (newId.isNotEmpty() && newId != _ref.value.sessionId) {
                    _ref.value = _ref.value.copy(sessionId = newId)
                    loadSession()
                    // A fork restarts the transcript file; reload from scratch.
                    loadedTotal = 0
                    earliestOffset = 0
                    loadTail(keepLive = true)
                }
            }
        }
        viewModelScope.launch {
            watcher.ended.collect { state ->
                handledJobs += state.jobId
                repo.cue(if (state.status == "done") Chime.Cue.Done else Chime.Cue.Error)
                onTurnEnded(state)
            }
        }

        /*
         * Attach to whatever is running for this session, for as long as the
         * screen is open.
         *
         * This used to be a single check at startup, which quietly lost every
         * turn the status stream had not reported yet — the exact case you hit
         * by tapping a "needs your permission" notification on a cold start:
         * the activity opened before the SSE stream connected, found no job,
         * and so never showed the question panel at all. Watching the stream
         * instead means the panel appears whenever the daemon publishes it,
         * however late that is.
         */
        viewModelScope.launch {
            liveStatus.collect { active ->
                if (active == null || job.value.running) return@collect
                if (active.jobId in handledJobs) return@collect
                watcher.attach(active.jobId, _ref.value.sessionId)
            }
        }

        // Progress cues, keyed on the phase/tool SIGNATURE. The banner also
        // carries an elapsed counter that ticks every second — including it
        // here would beep once a second for the whole turn.
        viewModelScope.launch {
            combine(liveStatus, watcher.state) { live, state ->
                if (!state.running) return@combine ""
                listOf(
                    live?.phase.orEmpty(),
                    live?.phaseDetail.orEmpty(),
                    live?.tool.orEmpty(),
                    live?.toolDetail.orEmpty(),
                    state.toolLine,
                ).joinToString("|")
            }.collect { signature ->
                if (signature.isBlank() || signature == lastCueSignature) return@collect
                val first = lastCueSignature.isEmpty()
                lastCueSignature = signature
                if (!first) repo.cue(Chime.Cue.Status)
            }
        }

        // Blocked on the user — the cue that actually matters.
        viewModelScope.launch {
            watcher.state
                .map { it.pendingPermission != null || it.pendingQuestion != null }
                .distinctUntilChanged()
                .collect { blocked -> if (blocked) repo.cue(Chime.Cue.Attention) }
        }

        if (initialRef.sessionId.isNotEmpty()) {
            loadSession()
            loadTail()
        } else {
            // Brand-new session: show the prompt that created it right away
            // rather than an empty screen while the daemon spins up.
            val opening = repo.takeFirstPrompt(initialJobId)
            _ui.value = TranscriptUi(
                items = opening?.let {
                    listOf(TranscriptItem("live-${liveCounter++}", "user", it, live = true))
                }.orEmpty(),
                loading = opening == null,
                status = "Starting…",
            )
        }
        if (initialJobId.isNotEmpty()) {
            watcher.attach(initialJobId, initialRef.sessionId)
        } else {
            // Anything already running is picked up by the liveStatus
            // collector above; this just avoids waiting for the next frame
            // when the stream is already connected.
            repo.activeFor(initialRef)?.let { watcher.attach(it.jobId, initialRef.sessionId) }
        }
    }

    // -- loading -----------------------------------------------------------

    private fun loadSession() {
        val c = client ?: return
        val id = _ref.value.sessionId
        if (id.isEmpty()) return
        viewModelScope.launch {
            runCatching { c.session(id) }.onSuccess { _session.value = it }
        }
    }

    fun loadTail(keepLive: Boolean = false) {
        val c = client ?: return
        val id = _ref.value.sessionId
        if (id.isEmpty()) return
        _ui.value = _ui.value.copy(loading = true, error = null)
        viewModelScope.launch {
            // A session the daemon just named has no transcript file yet, so
            // the first reads legitimately 404. Waiting beats telling the user
            // their brand-new session does not exist.
            retryingWhileCreating { c.messages(id, offset = -1, limit = PAGE) }
                .onSuccess { page ->
                    loadedTotal = page.total
                    earliestOffset = page.offset
                    val fetched = toItems(page.messages, page.offset)
                    // A reload during a live turn (the fork adoption path) can
                    // race the daemon writing the very lines we already echoed
                    // locally. Keep only the echoes the transcript has not
                    // caught up with, or the user sees their prompt twice.
                    val settled = fetched.mapTo(HashSet()) { it.role + " " + it.text.trim() }
                    val live = if (keepLive) {
                        _ui.value.items.filter {
                            it.live && (it.role + " " + it.text.trim()) !in settled
                        }
                    } else {
                        emptyList()
                    }
                    _ui.value = TranscriptUi(
                        items = fetched + live,
                        loading = false,
                        canLoadOlder = page.offset > 0,
                        // Open on the newest message, the way every chat does;
                        // the window we just fetched is the tail.
                        scrollTick = _ui.value.scrollTick + 1,
                    )
                }
                .onFailure { e ->
                    _ui.value = _ui.value.copy(
                        loading = false,
                        error = repo.reason(e),
                    )
                }
        }
    }

    /**
     * Retries only the "not written yet" case, and only while the turn that
     * is creating the session is still alive. Any other failure — bad token,
     * unreachable host, a session that really is gone — surfaces at once.
     */
    private suspend fun <T> retryingWhileCreating(block: suspend () -> T): Result<T> {
        var attempt = 0
        while (true) {
            val outcome = runCatching { block() }
            val error = outcome.exceptionOrNull()
            val transient = error is DaemonException && error.notFound && job.value.running
            if (!transient || attempt >= CREATE_RETRIES) return outcome
            attempt++
            delay(CREATE_RETRY_MS)
        }
    }

    fun loadOlder() {
        val c = client ?: return
        if (_ui.value.loadingOlder || earliestOffset <= 0) return
        val id = _ref.value.sessionId
        val from = (earliestOffset - PAGE).coerceAtLeast(0)
        val count = earliestOffset - from
        _ui.value = _ui.value.copy(loadingOlder = true)
        viewModelScope.launch {
            runCatching { c.messages(id, offset = from, limit = count) }
                .onSuccess { page ->
                    earliestOffset = page.offset
                    val older = toItems(page.messages, page.offset)
                    _ui.value = _ui.value.copy(
                        items = older + _ui.value.items,
                        loadingOlder = false,
                        canLoadOlder = page.offset > 0,
                        prependTick = _ui.value.prependTick + 1,
                        prependCount = older.size,
                    )
                }
                .onFailure {
                    _ui.value = _ui.value.copy(
                        loadingOlder = false,
                        status = repo.reason(it),
                    )
                }
        }
    }

    /**
     * After a turn, pull only what was appended.
     *
     * Reloading the tail would be simpler but throws away any older pages the
     * user scrolled back through, and re-downloads a window that has not
     * changed. If the total went *backwards* the session was rewound or
     * forked, and only a full reload is correct.
     */
    private fun onTurnEnded(state: JobState) {
        val c = client ?: return
        val id = _ref.value.sessionId
        viewModelScope.launch {
            if (id.isNotEmpty()) {
                val fetched = runCatching { c.messages(id, offset = loadedTotal, limit = 200) }
                    .getOrNull()
                if (fetched == null || fetched.total < loadedTotal) {
                    loadTail()
                } else {
                    val fresh = toItems(fetched.messages, fetched.offset)
                    loadedTotal = fetched.total
                    _ui.value = _ui.value.copy(
                        items = _ui.value.items.filterNot { it.live } + fresh,
                    )
                }
            }
            val note = endNote(state)
            if (note != null) append(note)
            _ui.value = _ui.value.copy(status = "")
            repo.refreshSession(_ref.value)
        }
    }

    private fun endNote(state: JobState): TranscriptItem? {
        val bits = buildList {
            when (state.status) {
                "error" -> add(state.error.ifBlank { "The turn failed" })
                "stopped" -> add("Stopped")
            }
            if (state.droppedQueued > 0) {
                add("${state.droppedQueued} queued prompt(s) dropped")
            }
        }
        if (bits.isEmpty()) return null
        return TranscriptItem(
            id = "notice-${liveCounter++}",
            role = "notice",
            text = bits.joinToString(" · "),
            severity = if (state.status == "error") "error" else "info",
        )
    }

    /**
     * Keys must be unique *and* stable: the transcript index is part of the id
     * so two identical lines cannot collide (which would crash the LazyColumn),
     * while re-reading the same window keeps the same keys and the same scroll
     * position.
     */
    private fun toItems(messages: List<MessageDto>, offset: Int): List<TranscriptItem> =
        messages.flatMapIndexed { index, msg ->
            val key = "${offset + index}:${msg.uuid}"
            shellEscape(msg, key) ?: listOf(
                TranscriptItem(
                    id = key,
                    role = msg.role,
                    text = msg.text,
                    ts = msg.ts,
                    metaKind = msg.metaKind,
                ),
            )
        }

    /**
     * `!cmd` turns are stored as one user message carrying the command, the
     * output and a `[silent]` directive to the agent. Replaying that verbatim
     * shows the user their own plumbing, so it is split back into the two rows
     * it looked like when it ran — and the directive, which was never meant
     * for human eyes, is dropped.
     */
    private fun shellEscape(msg: MessageDto, key: String): List<TranscriptItem>? {
        if (msg.role != "user") return null
        if (!msg.text.startsWith(SHELL_PREFIX) || !msg.text.contains(SHELL_OUTPUT)) return null
        val command = msg.text.substringBefore(SHELL_OUTPUT).removePrefix("[shell] ").trim()
        val body = msg.text.substringAfter(SHELL_OUTPUT).substringBefore("\n[silent]").trim()
        return buildList {
            add(TranscriptItem(id = key, role = "user", text = command, ts = msg.ts))
            if (body.isNotEmpty()) {
                add(TranscriptItem(id = "$key:out", role = "assistant", text = body, ts = msg.ts))
            }
        }
    }

    private fun append(item: TranscriptItem, jump: Boolean = false) {
        _ui.value = _ui.value.copy(
            items = _ui.value.items + item,
            scrollTick = if (jump) _ui.value.scrollTick + 1 else _ui.value.scrollTick,
        )
    }

    /** Everything the user types is echoed and pulled into view immediately. */
    private fun echoUser(text: String) {
        append(TranscriptItem("live-${liveCounter++}", "user", text, live = true), jump = true)
    }

    private var statusClear: kotlinx.coroutines.Job? = null

    /**
     * Transient one-liner under the composer.
     *
     * Confirmations ("Queued", "Typed into the session") are acknowledgements,
     * not state — they clear themselves so the line does not sit there looking
     * like something still needs attention. Anything that reads as a problem
     * stays until the next action replaces it.
     */
    private fun setStatus(text: String, sticky: Boolean = true) {
        statusClear?.cancel()
        _ui.value = _ui.value.copy(status = text)
        if (text.isEmpty() || sticky) return
        statusClear = viewModelScope.launch {
            delay(STATUS_LINGER_MS)
            if (_ui.value.status == text) _ui.value = _ui.value.copy(status = "")
        }
    }

    // -- sending -----------------------------------------------------------

    /**
     * The composer's whole contract, ported from the BB10 client because the
     * rules were learned the hard way:
     *
     *  - `!cmd` runs a shell command on the daemon in this session's folder;
     *  - a `/command` the daemon did not advertise wastes an entire turn
     *    (headless CLIs answer "unknown skill"), so it is refused locally;
     *  - while a turn runs, interactive mode types into the host TUI and
     *    headless mode queues on the daemon — never on the phone, which can
     *    lose Wi-Fi or be killed at any moment.
     */
    fun send(raw: String) {
        val text = raw.trim()
        if (text.isEmpty()) return
        val profile = profile.value ?: return
        val c = client ?: return

        if (text.startsWith("!")) {
            runShell(text.removePrefix("!").trim())
            return
        }

        if (text.startsWith("/")) {
            val refusal = slashRefusal(text, profile)
            if (refusal != null) {
                setStatus(refusal)
                return
            }
        }

        val state = job.value
        if (state.running) {
            echoUser(text)
            val jobId = state.jobId
            if (jobId.isEmpty()) {
                // The job id has not come back yet; retry once it does rather
                // than dropping the message.
                pendingSends += text
                setStatus("Will send when the turn is under way", sticky = false)
                return
            }
            viewModelScope.launch {
                val interactive = profile.effectiveExecMode() == ExecMode.INTERACTIVE
                runCatching {
                    if (interactive) c.input(jobId, text) else c.queue(jobId, text)
                }.onFailure { e ->
                    val why = (e as? DaemonException)?.message ?: repo.reason(e)
                    setStatus(why)
                }.onSuccess {
                    setStatus(if (interactive) "Typed into the session" else "Queued", sticky = false)
                }
            }
            return
        }

        echoUser(text)
        watcher.markStarting()
        setStatus("")
        viewModelScope.launch {
            runCatching {
                c.continueSession(
                    sessionId = _ref.value.sessionId,
                    prompt = text,
                    execMode = profile.effectiveExecMode(),
                    model = profile.model,
                    effort = profile.effort,
                )
            }
                .onSuccess { jobId ->
                    watcher.attach(jobId, _ref.value.sessionId)
                    flushPending()
                }
                .onFailure { e ->
                    watcher.clearStarting()
                    append(
                        TranscriptItem(
                            "notice-${liveCounter++}",
                            "notice",
                            repo.reason(e),
                            severity = "error",
                        ),
                    )
                }
        }
    }

    private val pendingSends = mutableListOf<String>()

    private fun flushPending() {
        if (pendingSends.isEmpty()) return
        val queued = pendingSends.toList()
        pendingSends.clear()
        val c = client ?: return
        val profile = profile.value ?: return
        val jobId = job.value.jobId
        if (jobId.isEmpty()) return
        viewModelScope.launch {
            queued.forEach { text ->
                runCatching {
                    if (profile.effectiveExecMode() == ExecMode.INTERACTIVE) {
                        c.input(jobId, text)
                    } else {
                        c.queue(jobId, text)
                    }
                }
            }
        }
    }

    /**
     * Slash commands are a TUI feature, so they are refused outright unless
     * the turn actually runs in one.
     *
     * A headless `-p` turn does not implement them: the CLI answers "unknown
     * skill" (or, worse, writes an essay about the command) and the whole turn
     * is spent. Silently burning a turn is a much worse outcome than a
     * one-line refusal, so the check happens here and nothing is sent.
     *
     * Text that merely starts with a slash — a path like `/etc/hosts is
     * missing` — does not look like a command and passes through untouched.
     */
    /**
     * Harness of the OPEN session. A multi-harness host tags every row with
     * its provider, and its /api/ping root caps are a UNION of all of them —
     * so per-session gating (rewind, slash commands) must ask the row, not
     * the root. Null when the list has not loaded yet, which makes the caps
     * helpers fall back to the daemon-level answer.
     */
    private fun sessionHarness(): String? {
        val ref = _ref.value
        val row = repo.sessions.value.rows.firstOrNull { it.ref == ref }
        return row?.provider?.lowercase()?.takeIf { it.isNotEmpty() }
    }

    private fun slashRefusal(text: String, profile: Profile): String? {
        val command = text.substringBefore(' ').trim()
        if (!Regex("^/[A-Za-z][A-Za-z0-9_-]*$").matches(command)) return null
        if (profile.effectiveExecMode() != ExecMode.INTERACTIVE) {
            return "$command needs interactive execution — headless turns cannot run commands"
        }
        // No hardcoded whitelist: the daemon advertises what each harness
        // really has (claude/grok: /compact /exit /rewind — codex has no
        // rewind), so anything off THIS session's list is refused here.
        val known = profile.caps.slashFor(sessionHarness())
        if (known.contains(command)) return null
        return if (known.isEmpty()) {
            "This daemon does not advertise any slash commands"
        } else {
            "Unknown command $command — try: ${known.take(6).joinToString(" ")}"
        }
    }

    /**
     * `!cmd` runs on the daemon in this session's folder. The output is shown
     * at once, then handed to the agent as a turn so it becomes part of the
     * persisted conversation — with a `[silent]` directive so the agent takes
     * it as context and does not answer it.
     */
    private fun runShell(command: String) {
        if (command.isEmpty()) return
        val c = client ?: return
        echoUser("! $command")
        setStatus("Running…", sticky = false)
        viewModelScope.launch {
            runCatching {
                c.shell(command, _ref.value.sessionId, _session.value?.cwd.orEmpty())
            }
                .onSuccess { result ->
                    val body = result.output.trimEnd().ifEmpty { "(no output)" }
                    append(
                        TranscriptItem(
                            id = "live-${liveCounter++}",
                            role = "assistant",
                            text = "```\n$body\n```",
                            live = true,
                        ),
                    )
                    setStatus("")
                    if (_ref.value.sessionId.isNotEmpty()) {
                        feedShellToAgent(command, body, result.exitCode)
                    }
                }
                .onFailure { setStatus(repo.reason(it)) }
        }
    }

    private fun feedShellToAgent(command: String, output: String, exitCode: Int) {
        val profile = profile.value ?: return
        val c = client ?: return
        val prompt = buildString {
            append("[shell] ! ").append(command).append("\n[output]\n```\n")
            append(output.take(SHELL_FEED_LIMIT))
            if (exitCode != 0) append("\n(exit code ").append(exitCode).append(")")
            append("\n```\n[silent] Shell result for context only. Do not reply or acknowledge ")
            append("this message - wait for the next user instruction.")
        }
        watcher.markStarting()
        viewModelScope.launch {
            runCatching {
                c.continueSession(
                    sessionId = _ref.value.sessionId,
                    prompt = prompt,
                    execMode = profile.effectiveExecMode(),
                    model = profile.model,
                    effort = profile.effort,
                )
            }
                .onSuccess { watcher.attach(it, _ref.value.sessionId) }
                .onFailure {
                    watcher.clearStarting()
                    setStatus(repo.reason(it))
                }
        }
    }

    // -- turn control ------------------------------------------------------

    fun stop() {
        val c = client ?: return
        val jobId = job.value.jobId
        if (jobId.isEmpty()) return
        viewModelScope.launch { runCatching { c.stop(jobId) } }
        setStatus("Stopping…", sticky = false)
    }

    fun answerPermission(allow: Boolean) {
        val c = client ?: return
        val pending = job.value.pendingPermission ?: return
        val jobId = job.value.jobId
        viewModelScope.launch {
            runCatching { c.answerPermission(jobId, pending.requestId, allow) }
                .onFailure { setStatus(repo.reason(it)) }
        }
    }

    fun answerQuestion(answers: List<List<String>>, notes: List<String>) {
        val c = client ?: return
        val pending = job.value.pendingQuestion ?: return
        val jobId = job.value.jobId
        viewModelScope.launch {
            runCatching { c.answerQuestion(jobId, pending.requestId, answers, notes) }
                .onFailure { setStatus(repo.reason(it)) }
        }
    }

    fun cancelQuestion() {
        val c = client ?: return
        val pending = job.value.pendingQuestion ?: return
        val jobId = job.value.jobId
        viewModelScope.launch {
            runCatching { c.answerQuestion(jobId, pending.requestId, null) }
                .onFailure { setStatus(repo.reason(it)) }
        }
    }

    fun cancelQueued(queueId: String) {
        val c = client ?: return
        val jobId = job.value.jobId
        if (jobId.isEmpty()) return
        viewModelScope.launch {
            runCatching { c.cancelQueued(jobId, queueId) }
                .onFailure { setStatus(repo.reason(it)) }
        }
    }

    /**
     * Whether rewinding is possible at all, or why not.
     *
     * The TUI's checkpoint picker only exists for interactive turns, and grok
     * takes a prompt rather than a count — so both are hard requirements, not
     * hints.
     */
    fun rewindBlockedReason(): String? {
        val profile = profile.value ?: return "No profile"
        if (!profile.caps.rewindFor(sessionHarness())) {
            return "${sessionHarness() ?: "This daemon"} cannot rewind"
        }
        if (profile.effectiveExecMode() != ExecMode.INTERACTIVE) {
            return "Rewind needs interactive execution"
        }
        return null
    }

    /**
     * How many user messages back this row is — the argument the TUI's picker
     * expects, counted from the end. 0 means the row is not in the transcript.
     */
    fun rewindSteps(itemId: String): Int {
        var back = 0
        for (item in _ui.value.items.asReversed()) {
            if (item.role != "user") continue
            back++
            if (item.id == itemId) return back
        }
        return 0
    }

    /**
     * Rewind the session to a message. Destructive and not undoable — grok
     * additionally reverts uncommitted file changes — so the UI must confirm
     * before calling this.
     */
    fun rewindTo(itemId: String) {
        rewindBlockedReason()?.let {
            setStatus(it)
            return
        }
        val back = rewindSteps(itemId)
        if (back == 0) {
            setStatus("Could not locate that message")
            return
        }
        send("/rewind $back")
    }

    fun attachUpload(name: String, bytes: ByteArray, onPath: (String) -> Unit) {
        val c = client ?: return
        setStatus("Uploading $name…", sticky = false)
        viewModelScope.launch {
            runCatching { c.uploadAttachment(name, bytes) }
                .onSuccess {
                    setStatus("")
                    onPath(it.path)
                }
                .onFailure { setStatus(repo.reason(it)) }
        }
    }

    fun clearStatus() = setStatus("")

    // -- per-profile turn settings (shared by every session on that daemon) --

    fun setExecMode(mode: String) {
        val id = _ref.value.profileId
        viewModelScope.launch { repo.profileStore.updateComposerDefaults(id, execMode = mode) }
    }

    fun setModel(model: String) {
        val id = _ref.value.profileId
        viewModelScope.launch { repo.profileStore.updateComposerDefaults(id, model = model) }
    }

    fun setEffort(effort: String) {
        val id = _ref.value.profileId
        viewModelScope.launch { repo.profileStore.updateComposerDefaults(id, effort = effort) }
    }

    fun refresh() {
        loadSession()
        loadTail(keepLive = true)
        viewModelScope.launch { repo.profile(_ref.value.profileId)?.let { repo.pingProfile(it) } }
    }

    fun elapsedLabel(): String {
        val started = job.value.startedAtMs
        if (started <= 0) return ""
        return Time.elapsed(((System.currentTimeMillis() - started) / 1000).toInt())
    }

    override fun onCleared() {
        watcher.detach()
        super.onCleared()
    }

    private companion object {
        const val PAGE = 60
        const val SHELL_FEED_LIMIT = 8000
        const val STATUS_LINGER_MS = 2500L
        const val SHELL_PREFIX = "[shell] ! "
        const val SHELL_OUTPUT = "\n[output]\n"
        const val CREATE_RETRIES = 12
        const val CREATE_RETRY_MS = 1500L
    }
}
