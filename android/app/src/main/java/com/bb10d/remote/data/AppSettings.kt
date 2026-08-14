package com.bb10d.remote.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
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
    /**
     * Kanban mode: show only the projects the human enrolled by acting on them
     * through the daemon. A filter over the same list, not a second layout.
     */
    val focusMode: Boolean = false,
    /** Render assistant markdown; off = plain monospace, always safe. */
    val richText: Boolean = true,
    /**
     * Sessions with process view on: the transcript additionally shows the
     * agent's working steps (tool calls, results, thinking) under each
     * message. Per session and off by default, like the web client.
     */
    val processViewSessions: Set<String> = emptySet(),
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
        val focusMode = booleanPreferencesKey("focusMode")
        val richText = booleanPreferencesKey("richText")
        val processViewSessions = stringSetPreferencesKey("processViewSessions")
    }

    val state: Flow<AppSettings> = store.data.map { p ->
        val d = AppSettings()
        AppSettings(
            theme = p[K.theme] ?: d.theme,
            notifyTurnDone = p[K.notifyTurnDone] ?: d.notifyTurnDone,
            soundCues = p[K.soundCues] ?: d.soundCues,
            hapticCues = p[K.hapticCues] ?: d.hapticCues,
            backgroundWatch = p[K.backgroundWatch] ?: d.backgroundWatch,
            focusMode = p[K.focusMode] ?: d.focusMode,
            richText = p[K.richText] ?: d.richText,
            processViewSessions = p[K.processViewSessions] ?: d.processViewSessions,
        )
    }

    suspend fun setTheme(v: String) = store.edit { it[K.theme] = v }
    suspend fun setNotifyTurnDone(v: Boolean) = store.edit { it[K.notifyTurnDone] = v }
    suspend fun setSoundCues(v: Boolean) = store.edit { it[K.soundCues] = v }
    suspend fun setHapticCues(v: Boolean) = store.edit { it[K.hapticCues] = v }
    suspend fun setBackgroundWatch(v: Boolean) = store.edit { it[K.backgroundWatch] = v }
    suspend fun setFocusMode(v: Boolean) = store.edit { it[K.focusMode] = v }
    suspend fun setRichText(v: Boolean) = store.edit { it[K.richText] = v }

    suspend fun setProcessView(sessionId: String, on: Boolean) = store.edit { p ->
        val current = p[K.processViewSessions] ?: emptySet()
        p[K.processViewSessions] = if (on) current + sessionId else current - sessionId
    }
}
