package com.bb10d.remote.ui.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.Caps
import com.bb10d.remote.data.Profile
import com.bb10d.remote.net.DaemonClient
import com.bb10d.remote.net.DaemonException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class ProfilesViewModel(private val repo: AgentRepository) : ViewModel() {

    val profiles = repo.profiles
    val streamsUp = repo.streamsUp

    sealed interface TestState {
        data object Idle : TestState
        data object Running : TestState
        data class Ok(val caps: Caps) : TestState
        data class Failed(val message: String) : TestState
    }

    private val _testResult = MutableStateFlow<TestState>(TestState.Idle)
    val testResult: StateFlow<TestState> = _testResult.asStateFlow()

    private var testJob: Job? = null

    /**
     * Reachability is not enough.
     *
     * `/api/ping` is deliberately unauthenticated so the app can discover a
     * daemon before the token is typed — which means a ping alone happily
     * reports "Grok on racknerd" for a wrong token, and the failure only shows
     * up later as an empty session list. So the test also makes one
     * authenticated call and reports the 401 where the token is being typed.
     */
    fun test(url: String, token: String) {
        testJob?.cancel()
        _testResult.value = TestState.Running
        testJob = viewModelScope.launch {
            val client = DaemonClient(url, token)
            val ping = runCatching { client.ping() }
                .onFailure { _testResult.value = TestState.Failed(repo.reason(it)) }
                .getOrNull() ?: return@launch
            if (!ping.ok && ping.provider.isEmpty()) {
                _testResult.value =
                    TestState.Failed("That address answered, but it is not an agentremoted host")
                return@launch
            }
            runCatching { client.projects() }
                .onSuccess {
                    _testResult.value = TestState.Ok(Caps.from(ping, System.currentTimeMillis()))
                }
                .onFailure { e ->
                    val why = (e as? DaemonException)?.takeIf { it.unauthorized }
                        ?.let { "Reached ${ping.provider.ifBlank { "the daemon" }}, but the " +
                            "token was rejected" }
                        ?: repo.reason(e)
                    _testResult.value = TestState.Failed(why)
                }
        }
    }

    fun clearTest() {
        testJob?.cancel()
        _testResult.value = TestState.Idle
    }

    fun save(existing: Profile?, name: String, url: String, token: String) {
        val base = existing ?: Profile()
        val updated = base.copy(
            name = name.trim(),
            baseUrl = url.trim(),
            token = token.trim(),
        )
        viewModelScope.launch {
            repo.profileStore.upsert(updated)
            // Ping immediately so the row shows its provider badge and the
            // composer knows what the daemon can do before it is first used.
            repo.pingProfile(updated)
        }
    }

    fun delete(id: String) {
        viewModelScope.launch { repo.profileStore.delete(id) }
    }

    fun setEnabled(id: String, enabled: Boolean) {
        viewModelScope.launch { repo.profileStore.setEnabled(id, enabled) }
    }
}
