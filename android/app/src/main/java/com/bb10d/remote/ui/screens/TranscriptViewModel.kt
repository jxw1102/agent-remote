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
import com.bb10d.remote.data.SessionTab
import com.bb10d.remote.data.StepDto
import com.bb10d.remote.data.Time
import com.bb10d.remote.net.DaemonClient
import com.bb10d.remote.net.DaemonException
import java.util.UUID
import kotlinx.coroutines.CancellationException
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
    /** Process view: the working steps that followed this message. */
    val steps: List<StepDto> = emptyList(),
)

/** A host file the agent linked, opened through the daemon. */
data class HostFileUi(
    val name: String = "",
    val path: String = "",
    val line: Int = 0,
    val busy: Boolean = false,
    val text: String? = null,
    val image: ByteArray? = null,
    val error: String? = null,
) {
    val open: Boolean get() = name.isNotEmpty()
}

/** ":12", ":12:3", "#L12", "#L12-L20" — the location an agent appends. */
private val HOST_LINK_LOC = Regex("""(?::(\d+)(?::\d+)?|#L(\d+)(?:-L?\d+)?)$""")

data class ShareUi(
    val busy: Boolean = false,
    val url: String = "",
    val error: String? = null,
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
    /** Harness known at navigation time (session row or new-session picker). */
    private val initialProvider: String = "",
) : ViewModel() {

    private val _ref = MutableStateFlow(initialRef)
    val ref: StateFlow<SessionRef> = _ref.asStateFlow()

    private val _ui = MutableStateFlow(TranscriptUi(loading = true))
    val ui: StateFlow<TranscriptUi> = _ui.asStateFlow()

    private val _share = MutableStateFlow(ShareUi())
    val share: StateFlow<ShareUi> = _share.asStateFlow()

    private val _session = MutableStateFlow<SessionDto?>(null)
    val session: StateFlow<SessionDto?> = _session.asStateFlow()

    val profile: StateFlow<Profile?> = repo.profiles
        .map { it.byId(initialRef.profileId) }
        .stateIn(viewModelScope, SharingStarted.Eagerly, repo.profile(initialRef.profileId))

    /**
     * Accent / banner harness for this open transcript.
     * Prefer the session's own provider (multi hosts tag each row); then the
     * list/nav hint; never the multi profile's default ping provider alone —
     * that was painting every multi session as Claude (or Neutral).
     */
    val harnessProvider: StateFlow<String> = combine(
        _session,
        profile,
        repo.sessions,
        _ref,
    ) { sess, prof, sessions, ref ->
        sess?.provider?.takeIf { it.isNotBlank() }
            ?: initialProvider.takeIf { it.isNotBlank() }
            ?: sessions.rows.firstOrNull {
                it.ref.profileId == ref.profileId && it.ref.sessionId == ref.sessionId
            }?.provider?.takeIf { it.isNotBlank() }
            ?: prof?.provider.orEmpty()
    }.stateIn(
        viewModelScope,
        SharingStarted.Eagerly,
        initialProvider.ifBlank {
            repo.sessions.value.rows.firstOrNull {
                it.ref.profileId == initialRef.profileId
                    && it.ref.sessionId == initialRef.sessionId
            }?.provider?.ifBlank { null }
                ?: repo.profile(initialRef.profileId)?.provider.orEmpty()
        },
    )

    private val watcher = JobWatcher(repo, initialRef.profileId, viewModelScope).also { w ->
        w.shownTexts = {
            _ui.value.items.asSequence()
                .filter { it.role == "assistant" && !it.live }
                .map { it.text.trim() }
                .toSet()
        }
    }
    val job: StateFlow<JobState> = watcher.state

    val settings = repo.settings

    /** Daemon-pushed status for this session, including turns we did not start. */
    val liveStatus: StateFlow<ActiveJobDto?> = combine(repo.active, _ref) { byProfile, ref ->
        byProfile[ref.profileId]?.firstOrNull { it.sessionIds().contains(ref.sessionId) }
    }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    /** This session's daemon: is the live connection up? */
    val conn: StateFlow<com.bb10d.remote.data.ConnState?> = combine(repo.conn, _ref) { byProfile, ref ->
        byProfile[ref.profileId]
    }.stateIn(viewModelScope, SharingStarted.Eagerly, repo.conn.value[initialRef.profileId])

    /** Process view for THIS session (persisted per session, like web). */
    val processView: StateFlow<Boolean> = combine(repo.settings, _ref) { s, ref ->
        ref.sessionId.isNotEmpty() && ref.sessionId in s.processViewSessions
    }.stateIn(
        viewModelScope,
        SharingStarted.Eagerly,
        initialRef.sessionId in repo.settings.value.processViewSessions,
    )

    /**
     * Which face of the session window shows: false = Chat, true = the host
     * terminal (TUI). Remembered per session, so a session opens on the face
     * it was left on.
     */
    val tuiView: StateFlow<Boolean> = combine(repo.settings, _ref) { s, ref ->
        ref.sessionId.isNotEmpty() && tuiFaceKey(ref) in s.tuiViewSessions
    }.stateIn(
        viewModelScope,
        SharingStarted.Eagerly,
        initialRef.sessionId.isNotEmpty() &&
            tuiFaceKey(initialRef) in repo.settings.value.tuiViewSessions,
    )

    fun setTuiView(on: Boolean) {
        val ref = _ref.value
        if (ref.sessionId.isEmpty()) return
        viewModelScope.launch { repo.settingsStore.setTuiView(tuiFaceKey(ref), on) }
    }

    // Profile and session, as the web client keys it: two profiles that
    // front the same daemon see the same session ids.
    private fun tuiFaceKey(ref: SessionRef) = "${ref.profileId}:${ref.sessionId}"

    /** For the TUI face, which talks to the daemon on its own. */
    val repository: AgentRepository get() = repo

    /**
     * The Codex goal on this thread, if it has one. Null until the options
     * sheet asks for it: a session list does not need the round trip.
     */
    private val _goal = MutableStateFlow<com.bb10d.remote.data.GoalDto?>(null)
    val goal: StateFlow<com.bb10d.remote.data.GoalDto?> = _goal.asStateFlow()

    /** Expanded step bodies (ref → full text once fetched; "" = preview only). */
    private val _openSteps = MutableStateFlow<Map<String, String>>(emptyMap())
    val openSteps: StateFlow<Map<String, String>> = _openSteps.asStateFlow()

    private var loadedTotal = 0
    private var earliestOffset = 0
    private var liveCounter = 0
    private var lastCueSignature = ""
    private var processRefreshAtMs = 0L
    private var processRefresh: kotlinx.coroutines.Job? = null

    /** Jobs already seen through to the end; never re-attach to these. */
    private val handledJobs = mutableSetOf<String>()

    private val client: DaemonClient?
        get() = repo.client(_ref.value.profileId)

    init {
        // This conversation is one of the open tabs over the session sheet,
        // under its current title.
        viewModelScope.launch {
            combine(_ref, _session, harnessProvider, profile) { ref, session, harness, profile ->
                SessionTab(
                    profileId = ref.profileId,
                    sessionId = ref.sessionId,
                    title = session?.title?.trim().orEmpty().ifEmpty { "Session" },
                    provider = harness,
                    profileName = profile?.displayName.orEmpty(),
                )
            }.collect { if (it.sessionId.isNotEmpty()) repo.tabStore.add(it) }
        }
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
                watcher.attach(active.jobId, _ref.value.sessionId, adopted = true)
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
                // Steps live in the journal, not the status stream — with
                // process view on, each new phase/tool pulls the tail so the
                // strip does not trail the chime by a whole turn.
                if (processView.value) scheduleProcessRefresh()
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
            repo.activeFor(initialRef)?.let {
                watcher.attach(it.jobId, initialRef.sessionId, adopted = true)
            }
        }
    }

    /** The model this session runs on — see [sessionModelOf]. */
    fun sessionModel(): String =
        sessionModelOf(_session.value, profile.value, harnessProvider.value)

    fun sessionEffort(): String =
        sessionEffortOf(_session.value, profile.value, harnessProvider.value)

    /** Whether this daemon can hold a model against one session (>= 2.27). */
    fun canPinOptions(): Boolean =
        profile.value?.caps?.sessionOptions == true && _ref.value.sessionId.isNotEmpty()

    // -- loading -----------------------------------------------------------

    private fun loadSession() {
        val c = client ?: return
        val id = _ref.value.sessionId
        if (id.isEmpty()) return
        viewModelScope.launch {
            runCatching { c.session(id) }.onSuccess { _session.value = it }
        }
    }

    fun loadTail(keepLive: Boolean = false, quiet: Boolean = false) {
        val c = client ?: return
        val id = _ref.value.sessionId
        if (id.isEmpty()) return
        if (!quiet) _ui.value = _ui.value.copy(loading = true, error = null)
        viewModelScope.launch {
            // A session the daemon just named has no transcript file yet, so
            // the first reads legitimately 404. Waiting beats telling the user
            // their brand-new session does not exist.
            retryingWhileCreating {
                c.messages(id, offset = -1, limit = PAGE, steps = processView.value)
            }
                .onSuccess { page ->
                    loadedTotal = page.total
                    earliestOffset = page.offset
                    val fetched = toItems(page.messages, page.offset)
                    // A reload during a live turn (the fork adoption path) can
                    // race the daemon writing the very lines we already echoed
                    // locally. Keep only the echoes the transcript has not
                    // caught up with, or the user sees their prompt twice.
                    val settled = fetched.mapTo(HashSet()) { it.role + "" + it.text.trim() }
                    val live = if (keepLive) {
                        _ui.value.items.filter {
                            it.live && (it.role + "" + it.text.trim()) !in settled
                        }
                    } else {
                        emptyList()
                    }
                    _ui.value = TranscriptUi(
                        items = fetched + live,
                        loading = false,
                        canLoadOlder = page.offset > 0,
                        // Open on the newest message, the way every chat does;
                        // the window we just fetched is the tail. A quiet
                        // (process-view) refresh must not yank the reader.
                        scrollTick = _ui.value.scrollTick + (if (quiet) 0 else 1),
                        prependTick = _ui.value.prependTick,
                        prependCount = _ui.value.prependCount,
                        status = _ui.value.status,
                    )
                }
                .onFailure { e ->
                    if (!quiet) {
                        _ui.value = _ui.value.copy(
                            loading = false,
                            error = repo.reason(e),
                        )
                    }
                }
        }
    }

    /**
     * Throttled tail pull while a turn runs with process view on — steps only
     * arrive with `?detail=steps`, so the status stream alone cannot paint
     * them. Mirrors the web client's 900 ms minimum gap.
     */
    private fun scheduleProcessRefresh() {
        if (processRefresh?.isActive == true) return
        val wait = (processRefreshAtMs + PROCESS_REFRESH_MIN_MS - System.currentTimeMillis())
            .coerceAtLeast(0)
        processRefresh = viewModelScope.launch {
            delay(wait)
            processRefreshAtMs = System.currentTimeMillis()
            if (processView.value) loadTail(keepLive = true, quiet = true)
        }
    }

    /** Turn the working-steps strip on/off for this session (persisted). */
    /** Read this session's goal (and what the daemon's watchdog is doing). */
    fun loadGoal() {
        val id = _ref.value.sessionId
        if (id.isEmpty()) return
        viewModelScope.launch {
            val row = runCatching { client?.goals()?.goals }.getOrNull()
                ?.firstOrNull { it.sessionId == id }
            _goal.value = row
        }
    }

    /** Ask the daemon to keep this goal moving, or to stop. */
    fun setGoalWatch(on: Boolean) {
        val id = _ref.value.sessionId
        if (id.isEmpty()) return
        // Paint the new state at once; the reply confirms (or corrects) it.
        _goal.value = _goal.value?.copy(watching = on, stoppedWhy = "")
        viewModelScope.launch {
            val reply = runCatching { client?.setGoalWatch(id, on) }.getOrNull()
            if (reply?.goal != null) _goal.value = reply.goal else loadGoal()
        }
    }

    fun setProcessView(on: Boolean) {
        val id = _ref.value.sessionId
        if (id.isEmpty()) return
        viewModelScope.launch {
            repo.settingsStore.setProcessView(id, on)
            // Steps only arrive with ?detail=steps — refetch either way so
            // toggling off also drops them.
            loadTail(keepLive = true)
        }
    }

    /**
     * Expand/collapse one step. First expand of a truncated step fetches the
     * full body from the daemon — that is what keeps a 200KB tool result out
     * of every window fetch.
     */
    fun toggleStep(step: StepDto) {
        val open = _openSteps.value
        if (step.ref in open) {
            _openSteps.value = open - step.ref
            return
        }
        _openSteps.value = open + (step.ref to "")
        if (!step.truncated) return
        val c = client ?: return
        val id = _ref.value.sessionId
        viewModelScope.launch {
            runCatching { c.stepText(id, step.ref) }
                .onSuccess { full ->
                    if (step.ref in _openSteps.value && full.text.isNotEmpty()) {
                        _openSteps.value = _openSteps.value + (step.ref to full.text)
                    }
                }
                .onFailure { setStatus(repo.reason(it)) }
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
            runCatching { c.messages(id, offset = from, limit = count, steps = processView.value) }
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
                val fetched = runCatching {
                    c.messages(id, offset = loadedTotal, limit = 200, steps = processView.value)
                }.getOrNull()
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
            // Watching the turn finish on this screen counts as having read
            // it — dim the Focus "turn finished" unread styling without
            // requiring a leave-and-reopen.
            repo.markSeen(_ref.value)
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
            val rows = shellEscape(msg, key) ?: listOf(
                TranscriptItem(
                    id = key,
                    role = msg.role,
                    text = msg.text,
                    ts = msg.ts,
                    metaKind = msg.metaKind,
                ),
            )
            // Steps hang under the message they FOLLOWED — on a split shell
            // turn that is the output row, the last of the pair.
            if (msg.steps.isEmpty()) rows
            else rows.dropLast(1) + rows.last().copy(steps = msg.steps)
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
                val interactive = profile.effectiveExecMode(harnessProvider.value) == ExecMode.INTERACTIVE
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
            val model = sessionModel()
            val effort = sessionEffort()
            runCatching {
                c.continueSession(
                    sessionId = _ref.value.sessionId,
                    prompt = text,
                    execMode = profile.effectiveExecMode(harnessProvider.value),
                    model = model,
                    effort = effort,
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
                    if (profile.effectiveExecMode(harnessProvider.value) == ExecMode.INTERACTIVE) {
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
     * so per-session gating (slash commands) must ask the row, not
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
        // /rewind never reaches the harness — the daemon (≥ 2.5) rewinds the
        // session journal itself — so it is exempt from the interactive-only
        // rule. The advertised-list check below still gates old daemons.
        if (profile.effectiveExecMode(harnessProvider.value) != ExecMode.INTERACTIVE && command != "/rewind") {
            return "$command needs interactive execution — headless turns cannot run commands"
        }
        // Daemon advertises harness built-ins; always allow /rewind and /goal
        // so older daemons / all clients stay consistent.
        val known = profile.caps.slashFor(sessionHarness())
        val always = listOf("/rewind", "/goal")
        if (known.contains(command) || always.contains(command)) return null
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
            // No trailing "wait for the next user instruction": the model
            // echoed that clause straight back instead of staying quiet.
            append("\n```\n[silent] Shell result for context only. ")
            append("Do not reply or acknowledge this message.")
        }
        watcher.markStarting()
        viewModelScope.launch {
            val model = sessionModel()
            val effort = sessionEffort()
            runCatching {
                c.continueSession(
                    sessionId = _ref.value.sessionId,
                    prompt = prompt,
                    execMode = profile.effectiveExecMode(harnessProvider.value),
                    model = model,
                    effort = effort,
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
        watcher.clearPendingPermission()
        viewModelScope.launch {
            runCatching { c.answerPermission(jobId, pending.requestId, allow) }
                .onFailure { setStatus(repo.reason(it)) }
        }
    }

    fun answerQuestion(answers: List<List<String>>, notes: List<String>) {
        val c = client ?: return
        val pending = job.value.pendingQuestion ?: return
        val jobId = job.value.jobId
        // Drop + suppress re-open: daemon may lag until the interactive driver
        // types the picks into the host TUI.
        watcher.clearPendingQuestion(suppressPolls = true)
        viewModelScope.launch {
            runCatching { c.answerQuestion(jobId, pending.requestId, answers, notes) }
                .onFailure {
                    // Failed submit — let the same ask surface again.
                    watcher.restorePendingQuestion(pending.requestId)
                    setStatus(repo.reason(it))
                }
        }
    }

    /**
     * Explicit Cancel on the sheet (not swipe-away). Escapes the host panel.
     * Sheet dismiss alone must not call this — see TranscriptScreen banner.
     */
    fun cancelQuestion() {
        val c = client ?: return
        val pending = job.value.pendingQuestion ?: return
        val jobId = job.value.jobId
        watcher.clearPendingQuestion(suppressPolls = true)
        viewModelScope.launch {
            runCatching { c.answerQuestion(jobId, pending.requestId, null) }
                .onFailure {
                    watcher.restorePendingQuestion(pending.requestId)
                    setStatus(repo.reason(it))
                }
        }
    }

    /** If a prior mistaken dismiss suppressed this id, allow polls to restore it. */
    fun reopenQuestion(requestId: String) {
        watcher.restorePendingQuestion(requestId)
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
     * Whether rewinding is possible at all, or why not. The daemon (≥ 2.5)
     * rewinds by editing the harness's own session journal, so both exec
     * modes qualify — only the harness capability gates it.
     */
    fun rewindBlockedReason(): String? {
        val profile = profile.value ?: return "No profile"
        if (!profile.caps.rewindFor(sessionHarness())) {
            return "${sessionHarness() ?: "this daemon"} cannot rewind (needs daemon 2.5+)"
        }
        return null
    }

    /**
     * How many user messages back this row is — the N in "/rewind N",
     * counted from the end. 0 means the row is not in the transcript.
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
     * Rewind the session to a message. Destructive and not undoable
     * (conversation only — host file changes stay), so the UI must confirm
     * before calling this.
     */
    fun shareSession() {
        val profile = profile.value
        if (profile == null || !profile.share) {
            _share.value = ShareUi(error = "This daemon is too old to share sessions")
            return
        }
        if (_share.value.busy) return
        viewModelScope.launch {
            _share.value = ShareUi(busy = true)
            repo.shareSession(_ref.value).fold(
                onSuccess = { _share.value = ShareUi(url = it) },
                onFailure = { _share.value = ShareUi(error = repo.reason(it)) },
            )
        }
    }

    fun dismissShare() {
        _share.value = ShareUi()
    }

    // -- changes: what the agent changed, in one of two scopes -------------

    /**
     * [output] is what the last git action printed; [busy] while one runs.
     * [turnScope] picks the question: what this turn changed, or everything
     * since the last commit.
     */
    data class ChangesUi(
        val open: Boolean = false,
        val loading: Boolean = false,
        val data: com.bb10d.remote.data.ChangesDto? = null,
        val error: String? = null,
        val output: String = "",
        val busy: Boolean = false,
        val turnScope: Boolean = false,
        /** The project picked when the agent wrote in several; "" = the daemon's pick. */
        val root: String = "",
        /** Counts commits that succeeded, so the sheet knows when to clear its message. */
        val committed: Int = 0,
    )

    private val _changes = MutableStateFlow(ChangesUi())
    val changes: StateFlow<ChangesUi> = _changes.asStateFlow()

    fun openChanges() {
        _changes.value = ChangesUi(open = true)
        loadChanges()
    }

    fun closeChanges() {
        _changes.value = ChangesUi()
    }

    /** Switch scope and reload; the two answers are not interchangeable. */
    fun setChangesTurnScope(turn: Boolean) {
        if (_changes.value.turnScope == turn) return
        _changes.value = _changes.value.copy(turnScope = turn, data = null)
        loadChanges()
    }

    /** Measure in another of the session's projects. */
    fun setChangesRoot(path: String) {
        if (_changes.value.data?.project == path) return
        _changes.value = _changes.value.copy(root = path, data = null)
        loadChanges()
    }

    fun loadChanges() {
        val c = client ?: return
        val sid = _ref.value.sessionId
        if (sid.isEmpty() || !_changes.value.open) return
        val turn = _changes.value.turnScope
        val root = _changes.value.root
        _changes.value = _changes.value.copy(loading = true, error = null)
        viewModelScope.launch {
            val result = runCatching { c.changes(sid, turn = turn, root = root) }
            if (!_changes.value.open) return@launch
            _changes.value = result.fold(
                onSuccess = { _changes.value.copy(loading = false, data = it) },
                onFailure = { _changes.value.copy(loading = false, error = repo.reason(it)) },
            )
        }
    }

    /** Stage everything listed and commit it with [message]. */
    fun commitChanges(message: String) {
        val text = message.trim()
        if (text.isEmpty()) return
        // Single-quoted for the shell: the message is data, never syntax.
        runGit(
            "git add -A && git commit -m '" + text.replace("'", "'\\''") + "'",
            "git add -A && git commit",
            isCommit = true,
        )
    }

    /** Push; publishes the branch to origin when it has no upstream yet. */
    fun pushChanges() {
        val command = if (_changes.value.data?.upstream.isNullOrEmpty()) "git push -u origin HEAD" else "git push"
        runGit(command, command)
    }

    private fun runGit(command: String, shown: String, isCommit: Boolean = false) {
        val c = client ?: return
        if (_changes.value.busy) return
        val cwd = _changes.value.data?.root?.ifEmpty { null } ?: _session.value?.cwd.orEmpty()
        _changes.value = _changes.value.copy(busy = true)
        viewModelScope.launch {
            var ok = false
            val output = runCatching { c.shell(command, _ref.value.sessionId, cwd) }.fold(
                onSuccess = {
                    ok = it.exitCode == 0
                    "$ $shown\n" + it.output.trim().ifEmpty { "(no output)" } +
                        if (it.exitCode != 0) "\n(exit code ${it.exitCode})" else ""
                },
                onFailure = { "$ $shown\n" + repo.reason(it) },
            )
            if (!_changes.value.open) return@launch
            _changes.value = _changes.value.copy(
                busy = false,
                output = output,
                // The sheet clears its message when this moves, and only then:
                // a failed commit keeps what was typed.
                committed = _changes.value.committed + if (isCommit && ok) 1 else 0,
            )
            loadChanges()
        }
    }

    private val _hostFile = MutableStateFlow(HostFileUi())
    val hostFile: StateFlow<HostFileUi> = _hostFile.asStateFlow()

    /**
     * Open a link to a bare host path ("/Users/me/proj/out.txt:12") — what
     * Codex writes for files it produced. The phone cannot open that path,
     * so the bytes come from the daemon, which only serves files the
     * session itself linked inside its working folder.
     */
    fun openHostFile(href: String) {
        val decoded = runCatching { java.net.URLDecoder.decode(href.replace("+", "%2B"), "UTF-8") }
            .getOrDefault(href)
        val loc = HOST_LINK_LOC.find(decoded)
        val line = loc?.let { (it.groupValues[1].ifEmpty { it.groupValues[2] }).toIntOrNull() } ?: 0
        val bare = if (loc != null) decoded.substring(0, loc.range.first) else decoded
        val name = bare.substringAfterLast('/').ifEmpty { bare }
        _hostFile.value = HostFileUi(name = name, path = bare, line = line, busy = true)
        viewModelScope.launch {
            repo.linkedFile(_ref.value, decoded).fold(
                onSuccess = { file ->
                    val type = file.contentType.lowercase()
                    _hostFile.value = when {
                        type.startsWith("image/") ->
                            _hostFile.value.copy(busy = false, image = file.bytes)
                        type.startsWith("text/") ->
                            _hostFile.value.copy(busy = false, text = file.bytes.decodeToString())
                        else -> _hostFile.value.copy(
                            busy = false,
                            error = "$name is not text or an image, so the phone cannot show it " +
                                "(${file.bytes.size / 1024} KB)",
                        )
                    }
                },
                onFailure = { e ->
                    val code = (e as? com.bb10d.remote.net.DaemonException)?.status ?: 0
                    _hostFile.value = _hostFile.value.copy(
                        busy = false,
                        error = when (code) {
                            404 -> "$name is not available. The daemon only opens files " +
                                "this session linked inside its working folder or ~/Public."
                            413 -> "$name is too large to open on the phone (over 10 MB)."
                            else -> repo.reason(e)
                        },
                    )
                },
            )
        }
    }

    fun dismissHostFile() {
        _hostFile.value = HostFileUi()
    }

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

    /**
     * Composer draft lives here (not in Compose remember) so it survives the
     * document-picker Activity Result lifecycle.
     */
    // Kept on the phone per session as well (DraftStore), so leaving the
    // session or the app being killed does not lose what was typed.
    private val _composerText = MutableStateFlow(repo.draftStore.get(initialRef))
    val composerText: StateFlow<String> = _composerText.asStateFlow()

    fun updateComposer(text: String) {
        _composerText.value = text
        repo.draftStore.put(_ref.value, text)
    }

    fun clearComposer() {
        _composerText.value = ""
        repo.draftStore.put(_ref.value, "")
    }

    /**
     * Attachments are chips beside the composer, never text spliced into the
     * TextField: async writes into a live TextField race its internal state
     * and the old `[attached: …]` splice kept getting lost. Chips are
     * VM-owned, show upload progress, can be removed, and the markers join
     * the prompt only at send time in [sendComposer].
     */
    data class ComposerAttachment(
        val id: String,
        val name: String,
        val path: String = "",  // host path once uploaded
        val uploading: Boolean = true,
    )

    private val _attachments = MutableStateFlow<List<ComposerAttachment>>(emptyList())
    val attachments: StateFlow<List<ComposerAttachment>> = _attachments.asStateFlow()

    fun attach(name: String, bytes: ByteArray) {
        val c = client ?: run {
            setStatus("Not connected to a daemon")
            return
        }
        if (bytes.isEmpty()) {
            setStatus("File is empty on this device (cloud-only?)")
            return
        }
        val safeName = name.ifBlank { "file" }
        // Say so up front rather than upload hundreds of chunks for the
        // daemon to refuse at the end.
        val maxMb = profile.value?.caps?.maxUploadMb ?: 16
        if (bytes.size.toLong() > maxMb.toLong() * 1024 * 1024) {
            setStatus("$safeName is too large (max $maxMb MB on this daemon)")
            return
        }
        val chip = ComposerAttachment(id = UUID.randomUUID().toString(), name = safeName)
        _attachments.value = _attachments.value + chip
        viewModelScope.launch {
            try {
                val chunked = profile.value?.caps?.chunkedUpload == true
                val dto = c.uploadAttachment(safeName, bytes, chunked = chunked)
                val path = dto.path.trim()
                if (path.isEmpty()) throw DaemonException(0, "daemon returned no path")
                _attachments.value = _attachments.value.map {
                    if (it.id == chip.id) it.copy(path = path, uploading = false) else it
                }
                setStatus("Attached $safeName", sticky = false)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _attachments.value = _attachments.value.filterNot { it.id == chip.id }
                setStatus("Upload failed: ${repo.reason(e)}")
            }
        }
    }

    fun removeAttachment(id: String) {
        _attachments.value = _attachments.value.filterNot { it.id == id }
    }

    /** Send the composer draft plus the markers for every uploaded chip. */
    fun sendComposer() {
        val chips = _attachments.value
        if (chips.any { it.uploading }) {
            setStatus("Attachment still uploading…", sticky = false)
            return
        }
        val markers = chips.filter { it.path.isNotEmpty() }
            .joinToString(" ") { "[attached: ${it.path}]" }
        val text = listOf(_composerText.value.trim(), markers)
            .filter { it.isNotBlank() }
            .joinToString(" ")
        if (text.isBlank()) return
        _composerText.value = ""
        repo.draftStore.put(_ref.value, "")
        _attachments.value = emptyList()
        send(text)
    }

    fun clearStatus() = setStatus("")

    /** Surface a short note under the composer (e.g. file-picker read errors). */
    fun note(text: String, sticky: Boolean = true) = setStatus(text, sticky = sticky)

    // -- turn settings -------------------------------------------------------
    //
    // Execution is a property of the daemon (one terminal, one host); model
    // and effort are properties of the SESSION. Pinning them used to write
    // one value per daemon, so changing the model of the session you were
    // reading moved every other session on that daemon too.

    fun setExecMode(mode: String) {
        val id = _ref.value.profileId
        viewModelScope.launch { repo.profileStore.updateComposerDefaults(id, execMode = mode) }
    }

    fun setModel(model: String) = pinOption(model = model)

    fun setEffort(effort: String) = pinOption(effort = effort)

    /**
     * Pin to the open session; fall back to the daemon-wide default only on
     * a daemon too old to hold one (< 2.27).
     *
     * Optimistic: the local row is updated before the POST, so the chip
     * never sits on the old value while the request is in flight.
     */
    private fun pinOption(model: String? = null, effort: String? = null) {
        val profileId = _ref.value.profileId
        val sessionId = _ref.value.sessionId
        if (!canPinOptions()) {
            viewModelScope.launch {
                repo.profileStore.updateComposerDefaults(
                    profileId, model = model, effort = effort,
                )
            }
            return
        }
        _session.value = _session.value?.copy(
            modelPinned = model ?: _session.value?.modelPinned.orEmpty(),
            effortPinned = effort ?: _session.value?.effortPinned.orEmpty(),
        )
        val c = client ?: return
        viewModelScope.launch {
            runCatching { c.setSessionOptions(sessionId, model = model, effort = effort) }
                .onSuccess { reply ->
                    // The daemon normalises ("default" is not a pin), so take
                    // what it stored rather than what we asked for.
                    _session.value = _session.value?.copy(
                        modelPinned = reply.model, effortPinned = reply.effort,
                    )
                }
                .onFailure { setStatus(repo.reason(it)) }
        }
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
        const val PROCESS_REFRESH_MIN_MS = 900L
        const val SHELL_FEED_LIMIT = 8000
        const val STATUS_LINGER_MS = 2500L
        const val SHELL_PREFIX = "[shell] ! "
        const val SHELL_OUTPUT = "\n[output]\n"
        const val CREATE_RETRIES = 12
        const val CREATE_RETRY_MS = 1500L
    }
}

/**
 * The model ONE session runs on, which is a different question from "what
 * did this phone last pick".
 *
 * Three answers, in order of how much they know:
 *   1. [SessionDto.modelPinned] — a model chosen for this session, from any
 *      client, which has not run yet.
 *   2. [SessionDto.model] — what it actually last ran on, read by the daemon
 *      from the harness's own transcript.
 *   3. the profile default — all a session that has never run can offer.
 *
 * A session may report a model the harness no longer lists (an older id, a
 * `[1m]` variant). It is still the model that session runs on, so it is
 * still what the chip shows; the picker adds it to its own list for the
 * same reason.
 *
 * Free functions, not methods, so a composable reading them also reads the
 * states they are built from and recomposes when the session changes.
 */
fun sessionModelOf(
    session: SessionDto?,
    profile: Profile?,
    harness: String?,
): String {
    session?.modelPinned?.takeIf { it.isNotBlank() }?.let { return it }
    session?.model
        ?.takeIf { it.isNotBlank() && it != "default" && it != "interactive" }
        ?.let { return it }
    val prof = profile ?: return ""
    val models = prof.caps.modelsFor(harness?.ifBlank { null })
    return prof.model.takeIf { it in models } ?: models.firstOrNull().orEmpty()
}

fun sessionEffortOf(
    session: SessionDto?,
    profile: Profile?,
    harness: String?,
): String {
    session?.effortPinned?.takeIf { it.isNotBlank() }?.let { return it }
    val prof = profile ?: return ""
    val efforts = prof.caps.effortsFor(harness?.ifBlank { null })
    return prof.effort.takeIf { it in efforts } ?: efforts.firstOrNull().orEmpty()
}
