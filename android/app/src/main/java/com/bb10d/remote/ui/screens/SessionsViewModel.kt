package com.bb10d.remote.ui.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.ProjectDto
import com.bb10d.remote.data.SessionRef
import com.bb10d.remote.data.SessionRow
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

data class ProjectFilter(val profileId: String, val projectId: String, val name: String)

@OptIn(FlowPreview::class)
class SessionsViewModel(val repo: AgentRepository) : ViewModel() {

    val profiles = repo.profiles
    val settings = repo.settings
    val workingKeys = repo.workingKeys
    val blockedKeys = repo.blockedKeys
    val streamsUp = repo.streamsUp

    private val _query = MutableStateFlow("")
    val query: StateFlow<String> = _query.asStateFlow()

    private val _profileFilter = MutableStateFlow<String?>(null)
    val profileFilter: StateFlow<String?> = _profileFilter.asStateFlow()

    private val _projectFilter = MutableStateFlow<ProjectFilter?>(null)
    val projectFilter: StateFlow<ProjectFilter?> = _projectFilter.asStateFlow()

    /** Rows after the local profile/project filters — search runs server-side. */
    val rows: StateFlow<List<SessionRow>> =
        combine(repo.sessions, _profileFilter, _projectFilter) { state, profileId, project ->
            state.rows.filter { row ->
                (profileId == null || row.ref.profileId == profileId) &&
                    (
                        project == null ||
                            (
                                row.ref.profileId == project.profileId &&
                                    row.session.projectId == project.projectId
                                )
                        )
            }
        }.stateIn(viewModelScope, SharingStarted.Eagerly, emptyList())

    val sessionsState = repo.sessions

    private var searchJob: Job? = null
    private var refreshJob: Job? = null

    init {
        // Re-run the query as it is typed, but only after it settles: each
        // keystroke would otherwise fan out one request per profile.
        searchJob = viewModelScope.launch {
            _query.drop(1).debounce(300).collect { reload() }
        }
        // Profiles arrive asynchronously from DataStore, so this is also the
        // cold-start path: the first emission carries the saved daemons and
        // triggers the initial ping + load. A daemon added or re-pointed
        // later reloads the same way, with no manual pull-to-refresh.
        viewModelScope.launch {
            repo.profiles
                // The token is part of the identity: fixing a rejected token
                // must reload, even though the id and address never changed.
                .map { state ->
                    state.enabled.map { "${it.id}|${it.baseUrl}|${it.token.hashCode()}" }.toSet()
                }
                .distinctUntilChanged()
                .collect {
                    repo.pingAll()
                    reload()
                }
        }
        // Switching Focus/All changes which endpoint the list comes from.
        viewModelScope.launch {
            repo.settings.map { it.focusMode }.drop(1).collect { reload() }
        }
    }

    // -- focus list --------------------------------------------------------

    fun setFocusMode(on: Boolean) {
        viewModelScope.launch { repo.settingsStore.setFocusMode(on) }
    }

    /** Mark done (member = false) or put a session back in Focus. */
    fun setFocusMember(ref: SessionRef, member: Boolean) {
        viewModelScope.launch {
            repo.setFocusMember(ref, member)
            // Membership decides whether the row belongs in Focus mode at all.
            if (settings.value.focusMode) reload()
        }
    }

    /** Called when a transcript is opened: dims that row's finished tag. */
    fun markSeen(ref: SessionRef) {
        viewModelScope.launch { repo.markSeen(ref) }
    }

    private val _renaming = MutableStateFlow<String?>(null)

    /** Non-null while a title request is in flight, so the sheet can wait. */
    val renaming: StateFlow<String?> = _renaming.asStateFlow()

    fun rename(ref: SessionRef, title: String, onDone: (String?) -> Unit = {}) {
        viewModelScope.launch {
            _renaming.value = "Saving…"
            val res = repo.renameSession(ref, title)
            _renaming.value = null
            onDone(res.exceptionOrNull()?.let { repo.reason(it) })
        }
    }

    fun regenerateTitle(ref: SessionRef, onDone: (String?, String?) -> Unit = { _, _ -> }) {
        viewModelScope.launch {
            _renaming.value = "Asking the model for a title…"
            val res = repo.regenerateTitle(ref)
            _renaming.value = null
            onDone(res.getOrNull(), res.exceptionOrNull()?.let { repo.reason(it) })
        }
    }

    fun setQuery(value: String) {
        _query.value = value
    }

    fun clearQuery() {
        _query.value = ""
        reload()
    }

    fun setProfileFilter(profileId: String?) {
        _profileFilter.value = profileId
        if (profileId == null || _projectFilter.value?.profileId != profileId) {
            _projectFilter.value = null
        }
    }

    fun setProjectFilter(filter: ProjectFilter?) {
        _projectFilter.value = filter
        if (filter != null) _profileFilter.value = filter.profileId
    }

    fun reload() {
        refreshJob?.cancel()
        refreshJob = viewModelScope.launch { repo.refreshSessions(_query.value.trim()) }
    }

    fun refreshEverything() {
        refreshJob?.cancel()
        refreshJob = viewModelScope.launch {
            repo.pingAll()
            repo.refreshSessions(_query.value.trim())
        }
    }

    fun touch(ref: SessionRef) {
        viewModelScope.launch { repo.refreshSession(ref) }
    }

    // -- projects (for the filter sheet and the new-session flow) ----------

    private val _projects = MutableStateFlow<Map<String, List<ProjectDto>>>(emptyMap())
    val projects: StateFlow<Map<String, List<ProjectDto>>> = _projects.asStateFlow()

    private val _projectsError = MutableStateFlow<Map<String, String>>(emptyMap())
    val projectsError: StateFlow<Map<String, String>> = _projectsError.asStateFlow()

    fun loadProjects(profileId: String) {
        val client = repo.client(profileId) ?: return
        viewModelScope.launch {
            runCatching { client.projects().projects }
                .onSuccess {
                    _projects.value = _projects.value + (profileId to it)
                    _projectsError.value = _projectsError.value - profileId
                }
                .onFailure {
                    _projectsError.value = _projectsError.value + (profileId to repo.reason(it))
                }
        }
    }
}
