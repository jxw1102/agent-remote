package com.bb10d.remote.ui.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.ExecMode
import com.bb10d.remote.data.Profile
import com.bb10d.remote.data.ProjectDto
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class NewSessionViewModel(private val repo: AgentRepository) : ViewModel() {

    val profiles = repo.profiles

    private val _profileId = MutableStateFlow("")
    val profileId: StateFlow<String> = _profileId.asStateFlow()

    /** Harness for multi-provider hosts (claude / grok / codex). */
    private val _harness = MutableStateFlow("")
    val harness: StateFlow<String> = _harness.asStateFlow()

    private val _projects = MutableStateFlow<List<ProjectDto>>(emptyList())
    val projects: StateFlow<List<ProjectDto>> = _projects.asStateFlow()

    private val _loadingProjects = MutableStateFlow(false)
    val loadingProjects: StateFlow<Boolean> = _loadingProjects.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _starting = MutableStateFlow(false)
    val starting: StateFlow<Boolean> = _starting.asStateFlow()

    fun selectProfile(id: String) {
        if (_profileId.value == id) return
        _profileId.value = id
        _projects.value = emptyList()
        _error.value = null
        val profile = repo.profile(id)
        _harness.value = profile?.caps?.harnesses()?.firstOrNull()
            ?: profile?.provider.orEmpty()
        loadProjects(id)
        // Caps may be stale (or absent on a first run); a fresh ping decides
        // whether the cwd field is required and which models to offer.
        viewModelScope.launch {
            repo.profile(id)?.let {
                repo.pingProfile(it)
                // Re-pick harness after ping may have discovered multi.
                val fresh = repo.profile(id)
                val hs = fresh?.caps?.harnesses().orEmpty()
                if (_harness.value.isBlank() || _harness.value !in hs) {
                    _harness.value = hs.firstOrNull() ?: fresh?.provider.orEmpty()
                }
            }
        }
    }

    fun selectHarness(name: String) {
        if (_harness.value == name) return
        _harness.value = name
    }

    private fun loadProjects(id: String) {
        val client = repo.client(id) ?: return
        _loadingProjects.value = true
        viewModelScope.launch {
            runCatching { client.projects().projects }
                .onSuccess { if (_profileId.value == id) _projects.value = it }
                .onFailure { if (_profileId.value == id) _error.value = repo.reason(it) }
            if (_profileId.value == id) _loadingProjects.value = false
        }
    }

    fun execModeFor(profile: Profile, harness: String = _harness.value): String {
        val can = profile.caps.interactiveFor(harness.ifBlank { null })
        return if (profile.execMode.isNotBlank()) {
            ExecMode.normalize(profile.execMode, can)
        } else if (can) {
            ExecMode.INTERACTIVE
        } else {
            ExecMode.HEADLESS
        }
    }

    fun setExecMode(profileId: String, mode: String) {
        viewModelScope.launch {
            repo.profileStore.updateComposerDefaults(
                profileId,
                execMode = ExecMode.normalize(mode),
            )
        }
    }

    fun setModel(profileId: String, model: String) {
        viewModelScope.launch { repo.profileStore.updateComposerDefaults(profileId, model = model) }
    }

    fun setEffort(profileId: String, effort: String) {
        viewModelScope.launch {
            repo.profileStore.updateComposerDefaults(profileId, effort = effort)
        }
    }

    fun start(
        profile: Profile,
        cwd: String,
        prompt: String,
        onStarted: (jobId: String, provider: String) -> Unit,
    ) {
        if (_starting.value) return
        val client = repo.client(profile)
        val harness = _harness.value.ifBlank {
            profile.caps.harnesses().firstOrNull() ?: profile.provider
        }
        // Prefer a model/effort valid for THIS harness (stored profile defaults
        // may be from another harness on multi hosts).
        val models = profile.caps.modelsFor(harness)
        val efforts = profile.caps.effortsFor(harness)
        val model = profile.model.takeIf { it in models }
            ?: models.firstOrNull().orEmpty()
        val effort = profile.effort.takeIf { it in efforts }
            ?: efforts.firstOrNull().orEmpty()
        _starting.value = true
        _error.value = null
        viewModelScope.launch {
            runCatching {
                client.newSession(
                    cwd = cwd,
                    prompt = prompt,
                    execMode = execModeFor(profile, harness),
                    model = model,
                    effort = effort,
                    provider = harness,
                )
            }
                .onSuccess { jobId ->
                    repo.rememberFirstPrompt(jobId, prompt)
                    // Persist the resolved picks so the next open remembers them.
                    if (model.isNotBlank() && model != profile.model) {
                        repo.profileStore.updateComposerDefaults(profile.id, model = model)
                    }
                    if (effort.isNotBlank() && effort != profile.effort) {
                        repo.profileStore.updateComposerDefaults(profile.id, effort = effort)
                    }
                    onStarted(jobId, harness)
                }
                .onFailure { _error.value = repo.reason(it) }
            _starting.value = false
        }
    }
}
