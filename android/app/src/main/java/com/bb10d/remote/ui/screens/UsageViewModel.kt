package com.bb10d.remote.ui.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.UsageBucketDto
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class UsageViewModel(private val repo: AgentRepository) : ViewModel() {
    val profiles = repo.profiles

    data class Section(
        val provider: String,
        val buckets: List<UsageBucketDto>,
        val error: String = "",
    )

    sealed interface Result {
        data object Loading : Result
        data class Ok(
            val buckets: List<UsageBucketDto>,
            /** Multi-harness host: one block per provider. Empty → use [buckets]. */
            val sections: List<Section> = emptyList(),
        ) : Result
        data class Failed(val message: String) : Result
    }

    private val _results = MutableStateFlow<Map<String, Result>>(emptyMap())
    val results: StateFlow<Map<String, Result>> = _results.asStateFlow()

    fun refresh() {
        repo.profiles.value.enabled.filter { it.caps.canShowUsage }.forEach { profile ->
            val client = repo.client(profile)
            _results.value = _results.value + (profile.id to Result.Loading)
            viewModelScope.launch {
                runCatching { client.usage() }
                    .onSuccess { usage ->
                        _results.value = _results.value + (
                            profile.id to when {
                                usage.multi && usage.sections.isNotEmpty() -> {
                                    val sections = usage.sections.map { sec ->
                                        Section(
                                            provider = sec.provider,
                                            buckets = if (sec.ok) sec.buckets else emptyList(),
                                            error = if (sec.ok) "" else (
                                                sec.error.ifBlank { "Not available" }
                                                ),
                                        )
                                    }
                                    val anyData = sections.any {
                                        it.buckets.isNotEmpty() || it.error.isNotBlank()
                                    }
                                    if (!anyData && !usage.ok && usage.error.isNotBlank()) {
                                        Result.Failed(usage.error)
                                    } else {
                                        Result.Ok(usage.buckets, sections)
                                    }
                                }
                                !usage.ok && usage.error.isNotBlank() &&
                                    usage.buckets.isEmpty() -> Result.Failed(usage.error)
                                else -> Result.Ok(usage.buckets)
                            }
                            )
                    }
                    .onFailure {
                        _results.value =
                            _results.value + (profile.id to Result.Failed(repo.reason(it)))
                    }
            }
        }
    }
}
