package com.bb10d.remote.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer

/** A conversation opened from the list, shown as a bubble over the session sheet. */
@Serializable
data class SessionTab(
    val profileId: String,
    val sessionId: String,
    val title: String = "",
    val provider: String = "",
    val profileName: String = "",
) {
    val ref: SessionRef get() = SessionRef(profileId, sessionId)
}

/**
 * The open conversations, like a browser's tabs: each session opened from
 * the list joins at the end, and stays until it is closed. Kept on the
 * phone; at most [MAX], the oldest going first. Same idea as the web client.
 */
class TabStore(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("tabs", Context.MODE_PRIVATE)
    private val serializer = ListSerializer(SessionTab.serializer())
    private val _tabs = MutableStateFlow(load())
    val tabs: StateFlow<List<SessionTab>> = _tabs.asStateFlow()

    private fun load(): List<SessionTab> = runCatching {
        Json.decodeFromString(serializer, prefs.getString("tabs", null) ?: "[]")
    }.getOrDefault(emptyList())

    private fun save(list: List<SessionTab>) {
        _tabs.value = list
        prefs.edit().putString("tabs", Json.encodeToString(serializer, list)).apply()
    }

    /** Opened (or renamed): in place if it is already a tab, else at the end. */
    @Synchronized
    fun add(tab: SessionTab) {
        if (tab.sessionId.isEmpty()) return
        val list = _tabs.value.toMutableList()
        val at = list.indexOfFirst { it.ref.key == tab.ref.key }
        if (at >= 0) {
            if (list[at] == tab) return
            list[at] = tab
        } else {
            list += tab
            while (list.size > MAX) list.removeAt(0)
        }
        save(list)
    }

    /** Closes a tab; returns the one that should come forward if it was open. */
    @Synchronized
    fun close(ref: SessionRef): SessionTab? {
        val list = _tabs.value.toMutableList()
        val at = list.indexOfFirst { it.ref.key == ref.key }
        if (at < 0) return null
        list.removeAt(at)
        save(list)
        return list.getOrNull(at.coerceAtMost(list.size - 1))
    }

    private companion object {
        const val MAX = 10
    }
}
