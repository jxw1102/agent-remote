package com.bb10d.remote.data

import com.bb10d.remote.net.DaemonClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

data class JobState(
    val jobId: String = "",
    val status: String = "",
    val error: String = "",
    val sessionId: String = "",
    val queued: List<QueuedDto> = emptyList(),
    val droppedQueued: Int = 0,
    val pendingPermission: PendingPermissionDto? = null,
    val pendingQuestion: PendingQuestionDto? = null,
    /** "⚙ Edit  src/main.kt" — last tool line, shown while the turn runs. */
    val toolLine: String = "",
    val startedAtMs: Long = 0,
    /** Set the moment the POST is sent, before the job id comes back. */
    val starting: Boolean = false,
) {
    val running: Boolean get() = starting || jobId.isNotEmpty()
}

/**
 * Follows one running turn on one daemon.
 *
 * Polling is event-driven, exactly like the BB10 client's "doorbell": the
 * status stream already pushes every job's `next_seq` about once a second, so
 * the expensive `GET /api/jobs/<id>?since=N` only fires when that cursor moves
 * (or when the queue/permission flags change, which do not append events).
 * The timer stays as a watchdog for when the stream is down — plain polling is
 * always the fallback, never the primary path.
 */
class JobWatcher(
    private val repo: AgentRepository,
    private val profileId: String,
    private val scope: CoroutineScope,
) {
    private val _state = MutableStateFlow(JobState())
    val state: StateFlow<JobState> = _state.asStateFlow()

    /** Text/result events, in order, for the transcript to append. */
    val events = MutableSharedFlow<JobEventDto>(extraBufferCapacity = 256)

    /** Fires once when the turn is over (done | error | stopped). */
    val ended = MutableSharedFlow<JobState>(extraBufferCapacity = 8)

    /** The session id the daemon settled on (a headless resume forks it). */
    val sessionIdChanged = MutableSharedFlow<String>(extraBufferCapacity = 8)

    private var loop: Job? = null
    private var since = 0
    private var failures = 0
    /** request_ids the user already answered/cancelled — ignore lagging polls. */
    private val dismissedQuestionIds = mutableSetOf<String>()
    private val dismissedPermissionIds = mutableSetOf<String>()

    fun markStarting() {
        _state.value = _state.value.copy(starting = true, error = "", droppedQueued = 0)
    }

    fun clearStarting() {
        _state.value = _state.value.copy(starting = false)
    }

    fun attach(jobId: String, sessionId: String) {
        if (jobId.isEmpty()) return
        if (_state.value.jobId == jobId && loop?.isActive == true) return
        loop?.cancel()
        since = 0
        failures = 0
        dismissedQuestionIds.clear()
        dismissedPermissionIds.clear()
        _state.value = JobState(
            jobId = jobId,
            sessionId = sessionId,
            status = "starting",
            startedAtMs = System.currentTimeMillis(),
        )
        loop = scope.launch { run(jobId) }
    }

    fun detach() {
        loop?.cancel()
        loop = null
        dismissedQuestionIds.clear()
        dismissedPermissionIds.clear()
        _state.value = JobState()
    }

    /**
     * After the user **answers or cancels** on the daemon, drop the sheet and
     * ignore lagging polls for that request_id. Do **not** call this when the
     * user only dismisses the sheet — that must keep `pendingQuestion` so the
     * banner can reopen it (web parity).
     */
    fun clearPendingQuestion(suppressPolls: Boolean = true) {
        _state.value.pendingQuestion?.requestId?.takeIf { it.isNotBlank() && suppressPolls }
            ?.let { dismissedQuestionIds += it }
        if (_state.value.pendingQuestion == null) return
        _state.value = _state.value.copy(pendingQuestion = null)
    }

    /** Un-suppress a request_id so a still-pending daemon ask can surface again. */
    fun restorePendingQuestion(requestId: String) {
        if (requestId.isBlank()) return
        dismissedQuestionIds.remove(requestId)
    }

    fun clearPendingPermission() {
        _state.value.pendingPermission?.requestId?.takeIf { it.isNotBlank() }
            ?.let { dismissedPermissionIds += it }
        if (_state.value.pendingPermission == null) return
        _state.value = _state.value.copy(pendingPermission = null)
    }

    private suspend fun run(initialJobId: String) {
        var jobId = initialJobId
        var lastFetchMs = 0L
        var lastSeenSeq = -1
        var lastQueued = -1
        var lastBlocked = false
        val client: DaemonClient = repo.client(profileId) ?: run {
            finish(_state.value.copy(status = "error", error = "Profile is gone"))
            return
        }

        while (scope.isActive) {
            val now = System.currentTimeMillis()
            val frame = repo.activeByJob(profileId, jobId)
            var fetch = false

            if (frame != null) {
                val blocked = frame.pendingPermission || frame.pendingQuestion
                // next_seq moving past our cursor is the doorbell; queue and
                // permission changes ring it too because they add no events.
                if (frame.nextSeq >= 0 && frame.nextSeq > since) fetch = true
                if (frame.queuedCount != lastQueued) fetch = true
                if (blocked != lastBlocked) fetch = true
                lastQueued = frame.queuedCount
                lastBlocked = blocked
                lastSeenSeq = frame.nextSeq
            } else if (lastSeenSeq >= 0) {
                // The job dropped out of the active list: it ended. Fetch the
                // final snapshot rather than waiting for the next tick.
                fetch = true
            }

            // Watchdog: with no stream (or a daemon too old to send next_seq)
            // this is the only thing that moves the turn forward.
            val streamUsable = frame != null && frame.nextSeq >= 0 &&
                repo.streamsUp.value.contains(profileId)
            val interval = if (streamUsable) IDLE_POLL_MS else ACTIVE_POLL_MS
            if (now - lastFetchMs >= interval) fetch = true

            if (fetch) {
                lastFetchMs = now
                val snapshot = runCatching { client.job(jobId, since) }
                    .onFailure {
                        failures++
                        if (failures >= MAX_FAILURES) {
                            finish(
                                _state.value.copy(
                                    status = "error",
                                    error = "Lost contact with the daemon",
                                ),
                            )
                            return
                        }
                    }
                    .getOrNull()

                if (snapshot != null) {
                    failures = 0
                    since = snapshot.nextSeq
                    apply(snapshot)

                    // The daemon chains queued prompts: when a turn ends
                    // cleanly it starts the next one and points at it. Follow
                    // the chain instead of tearing the UI down and back up.
                    if (snapshot.status == "done" && snapshot.nextJobId.isNotEmpty()) {
                        jobId = snapshot.nextJobId
                        since = 0
                        lastSeenSeq = -1
                        lastQueued = -1
                        lastBlocked = false
                        _state.value = _state.value.copy(
                            jobId = jobId,
                            status = "running",
                            toolLine = "",
                            startedAtMs = System.currentTimeMillis(),
                        )
                        continue
                    }
                    if (snapshot.finished) {
                        finish(_state.value)
                        return
                    }
                }
            }
            delay(TICK_MS)
        }
    }

    private suspend fun apply(snapshot: JobSnapshotDto) {
        snapshot.events.forEach { event ->
            when (event.kind) {
                "text" -> if (event.text.isNotBlank()) events.emit(event)
                "tool" -> _state.value = _state.value.copy(toolLine = toolLine(event))
                "result" -> if (event.isError) {
                    _state.value = _state.value.copy(error = snapshot.error)
                }
            }
        }
        val fork = snapshot.newSessionId
        val currentSid = _state.value.sessionId
        val placeholder = currentSid == "job:${_state.value.jobId}"
                || currentSid == _state.value.jobId
        if (fork.isNotEmpty() && (fork != currentSid || placeholder)) {
            if (fork != currentSid) {
                _state.value = _state.value.copy(sessionId = fork)
                sessionIdChanged.emit(fork)
            }
        }
        val pendingQ = snapshot.pendingQuestion?.let { q ->
            if (q.requestId.isNotBlank() && q.requestId in dismissedQuestionIds) null else q
        }
        val pendingP = snapshot.pendingPermission?.let { p ->
            if (p.requestId.isNotBlank() && p.requestId in dismissedPermissionIds) null else p
        }
        _state.value = _state.value.copy(
            status = snapshot.status,
            error = snapshot.error.ifBlank { _state.value.error },
            queued = snapshot.queued,
            droppedQueued = snapshot.droppedQueued,
            pendingPermission = pendingP,
            pendingQuestion = pendingQ,
            starting = false,
        )
    }

    private suspend fun finish(state: JobState) {
        loop = null
        _state.value = JobState(
            sessionId = state.sessionId,
            status = state.status,
            error = state.error,
            droppedQueued = state.droppedQueued,
        )
        ended.emit(state)
    }

    private fun toolLine(event: JobEventDto): String {
        val name = event.name.trim()
        val detail = event.detail.trim()
        return when {
            name.isEmpty() && detail.isEmpty() -> ""
            detail.isEmpty() -> name
            else -> "$name  $detail"
        }
    }

    private companion object {
        const val TICK_MS = 250L
        /** Stream is healthy: the timer is only a safety net. */
        const val IDLE_POLL_MS = 6_000L
        /** No usable stream: this is the real update rate. */
        const val ACTIVE_POLL_MS = 1_500L
        const val MAX_FAILURES = 5
    }
}
