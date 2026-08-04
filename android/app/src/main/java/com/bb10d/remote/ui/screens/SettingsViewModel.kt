package com.bb10d.remote.ui.screens

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.bb10d.remote.data.AgentRepository
import kotlinx.coroutines.launch

class SettingsViewModel(private val repo: AgentRepository) : ViewModel() {
    val settings = repo.settings
    val profiles = repo.profiles

    fun setTheme(v: String) = edit { repo.settingsStore.setTheme(v) }
    fun setRichText(v: Boolean) = edit { repo.settingsStore.setRichText(v) }
    fun setBackgroundWatch(v: Boolean) = edit { repo.settingsStore.setBackgroundWatch(v) }
    fun setNotifyTurnDone(v: Boolean) = edit { repo.settingsStore.setNotifyTurnDone(v) }
    fun setSoundCues(v: Boolean) = edit { repo.settingsStore.setSoundCues(v) }
    fun setHapticCues(v: Boolean) = edit { repo.settingsStore.setHapticCues(v) }
    fun setShowAllSessions(v: Boolean) = edit { repo.settingsStore.setShowAllSessions(v) }

    private fun edit(block: suspend () -> Unit) {
        viewModelScope.launch { block() }
    }
}
