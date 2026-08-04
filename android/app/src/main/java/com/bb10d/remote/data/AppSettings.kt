package com.bb10d.remote.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

data class AppSettings(
    /** system | dark | light */
    val theme: String = "system",
    /** Notify when a turn ends while the app is backgrounded. */
    val notifyTurnDone: Boolean = true,
    /**
     * The BB10 progress cues: a blip per phase/tool, do-re-mi on finish, a low
     * double-tap on failure. Independently switchable from the haptic channel,
     * the way `soundCues` and `ledCues` were on the BlackBerry build.
     */
    val soundCues: Boolean = true,
    /** The old status-LED channel: a short vibration alongside each cue. */
    val hapticCues: Boolean = true,
    /** Keep the status stream + job watch alive when the app is not visible. */
    val backgroundWatch: Boolean = true,
    /** Include agent-spawned / contentless sessions (?all=1). */
    val showAllSessions: Boolean = false,
    /** Render assistant markdown; off = plain monospace, always safe. */
    val richText: Boolean = true,
)

private val Context.settingsStore: DataStore<Preferences> by preferencesDataStore("settings")

class SettingsStore(context: Context) {
    private val store = context.applicationContext.settingsStore

    private object K {
        val theme = stringPreferencesKey("theme")
        val notifyTurnDone = booleanPreferencesKey("notifyTurnDone")
        val soundCues = booleanPreferencesKey("soundCues")
        val hapticCues = booleanPreferencesKey("hapticCues")
        val backgroundWatch = booleanPreferencesKey("backgroundWatch")
        val showAllSessions = booleanPreferencesKey("showAllSessions")
        val richText = booleanPreferencesKey("richText")
    }

    val state: Flow<AppSettings> = store.data.map { p ->
        val d = AppSettings()
        AppSettings(
            theme = p[K.theme] ?: d.theme,
            notifyTurnDone = p[K.notifyTurnDone] ?: d.notifyTurnDone,
            soundCues = p[K.soundCues] ?: d.soundCues,
            hapticCues = p[K.hapticCues] ?: d.hapticCues,
            backgroundWatch = p[K.backgroundWatch] ?: d.backgroundWatch,
            showAllSessions = p[K.showAllSessions] ?: d.showAllSessions,
            richText = p[K.richText] ?: d.richText,
        )
    }

    suspend fun setTheme(v: String) = store.edit { it[K.theme] = v }
    suspend fun setNotifyTurnDone(v: Boolean) = store.edit { it[K.notifyTurnDone] = v }
    suspend fun setSoundCues(v: Boolean) = store.edit { it[K.soundCues] = v }
    suspend fun setHapticCues(v: Boolean) = store.edit { it[K.hapticCues] = v }
    suspend fun setBackgroundWatch(v: Boolean) = store.edit { it[K.backgroundWatch] = v }
    suspend fun setShowAllSessions(v: Boolean) = store.edit { it[K.showAllSessions] = v }
    suspend fun setRichText(v: Boolean) = store.edit { it[K.richText] = v }
}
