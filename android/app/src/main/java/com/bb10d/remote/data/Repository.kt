package com.bb10d.remote.data

import android.content.Context
import com.bb10d.remote.audio.Chime
import com.bb10d.remote.net.DaemonClient
import com.bb10d.remote.net.DaemonException
import com.bb10d.remote.net.StatusStream
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.launchIn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.supervisorScope
import java.util.concurrent.ConcurrentHashMap

/** A session always travels with the daemon it lives on. */
data class SessionRef(val profileId: String, val sessionId: String) {
    val key: String get() = "$profileId/$sessionId"

    companion object {
        fun parse(key: String): SessionRef? {
            val at = key.indexOf('/')
            if (at <= 0 || at == key.length - 1) return null
            return SessionRef(key.substring(0, at), key.substring(at + 1))
        }
    }
}

/** One row of the unified list: a session plus which profile it came from. */
data class SessionRow(
    val ref: SessionRef,
    val profileName: String,
    val provider: String,
    val session: SessionDto,
    val sortKey: Long,
)

/** What one daemon contributed to the unified list on the last refresh. */
data class ProfileFeed(
    val profileId: String,
    val loading: Boolean = false,
    val error: String? = null,
    val count: Int = 0,
)

data class SessionsState(
    val rows: List<SessionRow> = emptyList(),
    val feeds: Map<String, ProfileFeed> = emptyMap(),
    val loading: Boolean = false,
    val searchQuery: String = "",
    /**
     * True when [rows] came from a Focus fetch. Without it the Focus chip
     * briefly showed the All list's count between the tap and the fetch
     * landing.
     */
    val focusRows: Boolean = false,
) {
    /** Errors worth showing above the list (dead daemon, bad token). */
    val problems: List<Pair<String, String>>
        get() = feeds.values.mapNotNull { f -> f.error?.let { f.profileId to it } }
}

/**
 * The app's single source of truth.
 *
 * Everything the BB10 client kept per-app (one daemon, one provider) is here
 * keyed by profile instead, because the whole point of this app is that the
 * session list is *not* per daemon: a Claude turn on the Mac and a Grok turn
 * on the VPS sit in one list, sorted by when they last moved.
 */
class AgentRepository(context: Context, private val scope: CoroutineScope) {

    val profileStore = ProfileStore(context)
    val settingsStore = SettingsStore(context)
    private val chime = Chime(context)

    /** Play a progress cue, honouring the two switches. */
    fun cue(cue: Chime.Cue) {
        val current = settings.value
        if (!current.soundCues && !current.hapticCues) return
        chime.play(cue, sound = current.soundCues, haptic = current.hapticCues)
    }

    val profiles: StateFlow<ProfileState> =
        profileStore.state.stateIn(scope, SharingStarted.Eagerly, ProfileState())

    val settings: StateFlow<AppSettings> =
        settingsStore.state.stateIn(scope, SharingStarted.Eagerly, AppSettings())

    private val _sessions = MutableStateFlow(SessionsState())
    val sessions: StateFlow<SessionsState> = _sessions.asStateFlow()

    /** Live active-job lists per profile, straight off each SSE stream. */
    private val _active = MutableStateFlow<Map<String, List<ActiveJobDto>>>(emptyMap())
    val active: StateFlow<Map<String, List<ActiveJobDto>>> = _active.asStateFlow()

    /** Profiles whose status stream is currently connected. */
    private val _streamsUp = MutableStateFlow<Set<String>>(emptySet())
    val streamsUp: StateFlow<Set<String>> = _streamsUp.asStateFlow()

    /** Emitted when a watched job ends, so notifications can fire once. */
    val jobEnded = MutableSharedFlow<JobEndNotice>(extraBufferCapacity = 16)

    private val clients = ConcurrentHashMap<String, Pair<String, DaemonClient>>()
    private val streams = ConcurrentHashMap<String, Job>()

    /**
     * First prompt of a session that does not exist yet, keyed by job id.
     *
     * The New Session screen posts the prompt and navigates away before any
     * session id exists, so the transcript has nothing to show. Handing the
     * text over here lets it open with the message already on screen instead
     * of a bare spinner.
     */
    private val firstPrompts = ConcurrentHashMap<String, String>()

    fun rememberFirstPrompt(jobId: String, prompt: String) {
        if (jobId.isNotEmpty()) firstPrompts[jobId] = prompt
    }

    fun takeFirstPrompt(jobId: String): String? = firstPrompts.remove(jobId)

    init {
        // Streams follow the profile list: adding a daemon connects it, editing
        // its URL reconnects it, disabling it drops the connection.
        profiles
            .map { state -> state.enabled.map { it.id to streamSignature(it) } }
            .distinctUntilChanged()
            .onEach { syncStreams() }
            .launchIn(scope)
    }

    private fun streamSignature(p: Profile) = "${p.baseUrl}|${p.token}"

    fun client(profile: Profile): DaemonClient {
        val signature = streamSignature(profile)
        val cached = clients[profile.id]
        if (cached != null && cached.first == signature) return cached.second
        val fresh = DaemonClient(profile.baseUrl, profile.token)
        clients[profile.id] = signature to fresh
        return fresh
    }

    fun client(profileId: String): DaemonClient? =
        profiles.value.byId(profileId)?.takeIf { it.configured }?.let { client(it) }

    fun profile(profileId: String): Profile? = profiles.value.byId(profileId)

    // -- capabilities ------------------------------------------------------

    /** Ping every configured daemon and cache what it can do. */
    suspend fun pingAll() {
        supervisorScope {
            profiles.value.profiles.filter { it.configured }.forEach { p ->
                launch { runCatching { pingProfile(p) } }
            }
        }
    }

    suspend fun pingProfile(profile: Profile): Result<Caps> = runCatching {
        val ping = client(profile).ping()
        val caps = Caps.from(ping, System.currentTimeMillis())
        profileStore.updateCaps(profile.id, caps)
        caps
    }

    // -- unified session list ---------------------------------------------

    suspend fun refreshSessions(query: String = "") {
        val state = profiles.value
        val targets = state.enabled
        // Agent-spawned sessions are always filtered out: subagent transcripts
        // and shells that never got a turn are not work you started, and no
        // setting brings them back.
        val all = false
        // Focus is a filter over this same list, not a second screen.
        val focus = settings.value.focusMode && query.isBlank()
        _sessions.value = _sessions.value.copy(
            loading = true,
            searchQuery = query,
            feeds = targets.associate { it.id to ProfileFeed(it.id, loading = true) },
        )
        val collected = java.util.Collections.synchronizedList(mutableListOf<SessionRow>())
        val feeds = ConcurrentHashMap<String, ProfileFeed>()
        Diag.log("refresh across ${targets.size} profile(s): ${targets.map { it.displayName }}")
        supervisorScope {
            targets.forEach { profile ->
                launch {
                    val outcome = runCatching {
                        val list = when {
                            query.isNotBlank() ->
                                client(profile).search(query, limit = PER_PROFILE_LIMIT, all = all)
                                    .results
                            // Focus mode asks the daemon for the rows rather
                            // than filtering the session list here: a project
                            // untouched for weeks falls outside the recency
                            // window, and that is the row that must not be lost.
                            focus -> if (profile.focus) {
                                client(profile).focus().sessions
                            } else {
                                // Its whole session list would silently fill
                                // Focus with sessions never enrolled.
                                emptyList()
                            }
                            else ->
                                client(profile).sessions(
                                    limit = PER_PROFILE_LIMIT,
                                    all = all,
                                ).sessions
                        }
                        list.map { s -> row(profile, s) }
                    }
                    outcome.onSuccess { rows ->
                        collected.addAll(rows)
                        feeds[profile.id] = ProfileFeed(profile.id, count = rows.size)
                        Diag.log("feed ${profile.displayName}: ${rows.size} rows")
                    }.onFailure { e ->
                        feeds[profile.id] = ProfileFeed(profile.id, error = reason(e))
                        Diag.log("feed ${profile.displayName} failed: ${reason(e)}", e)
                    }
                }
            }
        }
        val sorted = collected.sortedByDescending { it.sortKey }
        _sessions.value = SessionsState(
            rows = sorted,
            feeds = feeds.toMap(),
            loading = false,
            searchQuery = query,
            focusRows = focus,
        )
    }

    // -- focus list --------------------------------------------------------

    /**
     * Take a session out of Focus, or put it back. Membership lives on
     * the daemon so every client agrees; the local row is patched immediately
     * so the list does not wait for a refetch.
     */
    suspend fun setFocusMember(ref: SessionRef, member: Boolean): Result<Boolean> {
        val profile = profile(ref.profileId) ?: return Result.failure(
            IllegalStateException("unknown daemon"),
        )
        return runCatching {
            val res = client(profile).focusDone(ref.sessionId, done = !member)
            patchSession(ref) { it.copy(focus = res.focus) }
            res.focus
        }
    }

    /**
     * Dim a finished turn's tag once the human has looked at it.
     * Called when opening the session from the list, and when a turn ends
     * while its transcript screen is already open.
     */
    suspend fun markSeen(ref: SessionRef) {
        val profile = profile(ref.profileId) ?: return
        if (!profile.focus) return
        runCatching { client(profile).focusSeen(ref.sessionId) }
        patchSession(ref) { it.copy(focusUnread = false) }
    }

    /** Rename a session; an empty title restores the derived name. */
    suspend fun renameSession(ref: SessionRef, title: String): Result<String> {
        val profile = profile(ref.profileId) ?: return Result.failure(
            IllegalStateException("unknown daemon"),
        )
        return runCatching {
            val res = client(profile).setTitle(ref.sessionId, title)
            if (res.title.isBlank()) {
                // Cleared: the provider's own title comes back on the next read.
                refreshSession(ref)
            } else {
                patchSession(ref) { it.copy(title = res.title, titleManual = res.manual) }
            }
            res.title
        }
    }

    /** Ask the daemon to derive a fresh title from the transcript. */
    suspend fun regenerateTitle(ref: SessionRef): Result<String> {
        val profile = profile(ref.profileId) ?: return Result.failure(
            IllegalStateException("unknown daemon"),
        )
        return runCatching {
            val res = client(profile).regenerateTitle(ref.sessionId)
            patchSession(ref) { it.copy(title = res.title, titleManual = false) }
            res.title
        }
    }

    /** Patch one row's session in place, leaving sort order alone. */
    private fun patchSession(ref: SessionRef, edit: (SessionDto) -> SessionDto) {
        _sessions.value = _sessions.value.let { state ->
            val at = state.rows.indexOfFirst { it.ref == ref }
            if (at < 0) {
                state
            } else {
                val rows = state.rows.toMutableList()
                rows[at] = rows[at].copy(session = edit(rows[at].session))
                state.copy(rows = rows)
            }
        }
    }

    /** Refresh a single row in place — cheap after a turn ends. */
    suspend fun refreshSession(ref: SessionRef) {
        val profile = profile(ref.profileId) ?: return
        val updated = runCatching { client(profile).session(ref.sessionId) }.getOrNull() ?: return
        val fresh = row(profile, updated)
        _sessions.value = _sessions.value.let { state ->
            val rows = state.rows.toMutableList()
            val at = rows.indexOfFirst { it.ref == ref }
            if (at >= 0) rows[at] = fresh else rows.add(fresh)
            state.copy(rows = rows.sortedByDescending { it.sortKey })
        }
    }

    private fun row(profile: Profile, session: SessionDto) = SessionRow(
        ref = SessionRef(profile.id, session.id),
        profileName = profile.displayName,
        // Multi-harness daemon tags the session; single falls back to ping.
        provider = session.provider.ifBlank { profile.provider },
        session = session,
        sortKey = Time.epochMs(session.lastActive).takeIf { it > 0 }
            ?: Time.epochMs(session.started),
    )

    fun reason(e: Throwable): String = when (e) {
        is DaemonException -> e.message
        else -> e.message?.takeIf { it.isNotBlank() } ?: "Something went wrong"
    }

    // -- status streams ----------------------------------------------------

    private fun syncStreams() {
        val wanted = profiles.value.enabled.associateBy { it.id }
        // Drop streams for profiles that went away or changed connection.
        streams.keys.toList().forEach { id ->
            if (!wanted.containsKey(id)) {
                streams.remove(id)?.cancel()
                _active.value = _active.value - id
                _streamsUp.value = _streamsUp.value - id
            }
        }
        wanted.values.forEach { profile ->
            val signature = streamSignature(profile)
            val tag = "${profile.id}@$signature"
            if (streamTags[profile.id] == tag && streams[profile.id]?.isActive == true) return@forEach
            streams.remove(profile.id)?.cancel()
            streamTags[profile.id] = tag
            streams[profile.id] = scope.launch {
                StatusStream(client(profile)).events().collect { event ->
                    when (event) {
                        is StatusStream.Event.Frame -> {
                            _active.value = _active.value + (profile.id to event.active)
                            _streamsUp.value = _streamsUp.value + profile.id
                        }

                        is StatusStream.Event.Up -> {
                            _streamsUp.value = _streamsUp.value + profile.id
                        }

                        is StatusStream.Event.Down -> {
                            _streamsUp.value = _streamsUp.value - profile.id
                            _active.value = _active.value + (profile.id to emptyList())
                        }
                    }
                }
            }
        }
    }

    private val streamTags = ConcurrentHashMap<String, String>()

    /** Every session id any daemon is working on right now. */
    val workingKeys: StateFlow<Set<String>> = active
        .map { byProfile ->
            buildSet {
                byProfile.forEach { (profileId, jobs) ->
                    jobs.forEach { job ->
                        job.sessionIds().forEach { add(SessionRef(profileId, it).key) }
                    }
                }
            }
        }
        .stateIn(scope, SharingStarted.Eagerly, emptySet())

    /**
     * Sessions whose turn is parked on a permission prompt or a question.
     *
     * A blocked turn makes no progress and produces no output, so without a
     * marker the row just looks like a slow one — the user has no way to know
     * the agent is standing there waiting for an answer.
     */
    val blockedKeys: StateFlow<Set<String>> = active
        .map { byProfile ->
            buildSet {
                byProfile.forEach { (profileId, jobs) ->
                    jobs.filter { it.pendingPermission || it.pendingQuestion }.forEach { job ->
                        job.sessionIds().forEach { add(SessionRef(profileId, it).key) }
                    }
                }
            }
        }
        .stateIn(scope, SharingStarted.Eagerly, emptySet())

    fun activeFor(ref: SessionRef): ActiveJobDto? =
        _active.value[ref.profileId]?.firstOrNull { it.sessionIds().contains(ref.sessionId) }

    fun activeByJob(profileId: String, jobId: String): ActiveJobDto? =
        _active.value[profileId]?.firstOrNull { it.jobId == jobId }

    /** Jobs needing an answer right now, across every daemon. */
    fun blockedJobs(): List<Pair<String, ActiveJobDto>> = _active.value.flatMap { (id, jobs) ->
        jobs.filter { it.pendingPermission || it.pendingQuestion }.map { id to it }
    }

    companion object {
        const val PER_PROFILE_LIMIT = 40
    }
}

data class JobEndNotice(
    val ref: SessionRef,
    val profileName: String,
    val provider: String,
    val title: String,
    val status: String,
    val error: String,
)
