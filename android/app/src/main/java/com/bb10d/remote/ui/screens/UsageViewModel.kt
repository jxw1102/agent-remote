package com.bb10d.remote.ui.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.UsageBucketDto
import com.bb10d.remote.data.UsageDto
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

class UsageViewModel(private val repo: AgentRepository) : ViewModel() {
    val profiles = repo.profiles

    data class Section(
        val provider: String,
        val account: String = "",
        val accountId: String = "",
        val buckets: List<UsageBucketDto>,
        val error: String = "",
        /** Host profile names that reported this seat (after cross-host merge). */
        val hosts: List<String> = emptyList(),
    )

    sealed interface Result {
        data object Loading : Result
        data class Ok(
            val buckets: List<UsageBucketDto>,
            /** One block per (provider, account) after cross-host dedup. */
            val sections: List<Section> = emptyList(),
        ) : Result
        data class Failed(val message: String) : Result
    }

    private val _results = MutableStateFlow<Map<String, Result>>(emptyMap())
    val results: StateFlow<Map<String, Result>> = _results.asStateFlow()

    /** Merged view across all hosts — preferred for the Usage screen. */
    private val _merged = MutableStateFlow<Result>(Result.Loading)
    val merged: StateFlow<Result> = _merged.asStateFlow()

    fun refresh() {
        val enabled = repo.profiles.value.enabled.filter { it.caps.canShowUsage }
        if (enabled.isEmpty()) {
            _results.value = emptyMap()
            _merged.value = Result.Ok(emptyList(), emptyList())
            return
        }
        _merged.value = Result.Loading
        val hostSections = mutableListOf<Section>()
        var pending = enabled.size
        val lock = Any()

        enabled.forEach { profile ->
            val client = repo.client(profile)
            _results.value = _results.value + (profile.id to Result.Loading)
            viewModelScope.launch {
                val hostName = profile.displayName.ifBlank { profile.baseUrl }
                runCatching { client.usage() }
                    .onSuccess { usage ->
                        val sections = sectionsFromUsage(usage, profile.provider, hostName)
                        _results.value = _results.value + (
                            profile.id to when {
                                sections.isNotEmpty() -> Result.Ok(usage.buckets, sections)
                                !usage.ok && usage.error.isNotBlank() &&
                                    usage.buckets.isEmpty() -> Result.Failed(usage.error)
                                else -> Result.Ok(usage.buckets)
                            }
                            )
                        synchronized(lock) {
                            hostSections += sections
                            pending -= 1
                            if (pending <= 0) {
                                _merged.value = Result.Ok(
                                    emptyList(),
                                    mergeSections(hostSections),
                                )
                            }
                        }
                    }
                    .onFailure {
                        _results.value =
                            _results.value + (profile.id to Result.Failed(repo.reason(it)))
                        synchronized(lock) {
                            pending -= 1
                            if (pending <= 0) {
                                val mergedSecs = mergeSections(hostSections)
                                _merged.value = if (mergedSecs.isEmpty()) {
                                    Result.Failed(repo.reason(it))
                                } else {
                                    Result.Ok(emptyList(), mergedSecs)
                                }
                            }
                        }
                    }
            }
        }
    }

    private fun sectionsFromUsage(
        usage: UsageDto,
        fallbackProvider: String,
        hostName: String,
    ): List<Section> {
        if (usage.multi && usage.sections.isNotEmpty()) {
            return usage.sections.map { sec ->
                Section(
                    provider = sec.provider.ifBlank { fallbackProvider },
                    account = sec.account,
                    accountId = sec.accountId.ifBlank { sec.account },
                    buckets = if (sec.ok) sec.buckets else emptyList(),
                    error = if (sec.ok) {
                        sec.error
                    } else {
                        sec.error.ifBlank { "Not available" }
                    },
                    hosts = listOf(hostName),
                )
            }
        }
        if (!usage.ok && usage.error.isNotBlank() && usage.buckets.isEmpty()) {
            return listOf(
                Section(
                    provider = usage.provider.ifBlank { fallbackProvider },
                    account = usage.account,
                    accountId = usage.accountId.ifBlank { usage.account },
                    buckets = emptyList(),
                    error = usage.error,
                    hosts = listOf(hostName),
                ),
            )
        }
        return listOf(
            Section(
                provider = usage.provider.ifBlank { fallbackProvider },
                account = usage.account,
                accountId = usage.accountId.ifBlank { usage.account },
                buckets = usage.buckets,
                error = if (usage.ok) "" else usage.error,
                hosts = listOf(hostName),
            ),
        )
    }

    companion object {
        fun identityKey(provider: String, accountId: String, account: String, host: String): String {
            val p = provider.lowercase().trim()
            val id = accountId.ifBlank { account }.lowercase().trim()
            return if (p.isNotEmpty() && id.isNotEmpty()) {
                "$p|$id"
            } else {
                "$p|host:$host"
            }
        }

        fun mergeSections(input: List<Section>): List<Section> {
            val order = mutableListOf<String>()
            val map = linkedMapOf<String, Section>()
            for (sec in input) {
                val host = sec.hosts.firstOrNull().orEmpty()
                val key = identityKey(sec.provider, sec.accountId, sec.account, host)
                val prev = map[key]
                if (prev == null) {
                    map[key] = sec
                    order += key
                    continue
                }
                val hosts = (prev.hosts + sec.hosts).distinct()
                val pickBuckets = when {
                    prev.buckets.isEmpty() && sec.buckets.isNotEmpty() -> sec.buckets
                    sec.buckets.isEmpty() -> prev.buckets
                    sec.buckets.maxOfOrNull { it.percent } ?: 0 >=
                        (prev.buckets.maxOfOrNull { it.percent } ?: 0) -> sec.buckets
                    else -> prev.buckets
                }
                map[key] = prev.copy(
                    account = prev.account.ifBlank { sec.account },
                    accountId = prev.accountId.ifBlank { sec.accountId },
                    buckets = pickBuckets,
                    error = when {
                        pickBuckets.isNotEmpty() -> prev.error.ifBlank { sec.error }
                        else -> prev.error.ifBlank { sec.error }
                    },
                    hosts = hosts,
                )
            }
            return order.mapNotNull { map[it] }
        }
    }
}
