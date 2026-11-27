package com.bb10d.remote.data

import android.content.Context

/**
 * What is typed in a session's message box and not sent yet, per session,
 * on this phone. Leaving the session, the app being killed or a reboot no
 * longer loses it. Same idea as the web client's drafts.
 *
 * SharedPreferences, not the settings DataStore: it is written on every
 * keystroke, and apply() is an in-memory write with the disk write batched
 * behind it.
 */
class DraftStore(context: Context) {
    private val prefs = context.applicationContext
        .getSharedPreferences("drafts", Context.MODE_PRIVATE)

    fun get(ref: SessionRef): String {
        if (ref.sessionId.isEmpty()) return ""
        val raw = prefs.getString(key(ref), null) ?: return ""
        return raw.substringAfter('\n', "")
    }

    fun put(ref: SessionRef, text: String) {
        if (ref.sessionId.isEmpty()) return
        val edit = prefs.edit()
        if (text.isBlank()) {
            edit.remove(key(ref))
        } else {
            // "<millis>\n<text>": the time decides which drafts go first.
            edit.putString(key(ref), "${System.currentTimeMillis()}\n$text")
            prune(edit, keep = key(ref))
        }
        edit.apply()
    }

    private fun prune(edit: android.content.SharedPreferences.Editor, keep: String) {
        val all = prefs.all
        val now = System.currentTimeMillis()
        val stamped = all.mapNotNull { (k, v) ->
            val at = (v as? String)?.substringBefore('\n')?.toLongOrNull() ?: return@mapNotNull k to 0L
            k to at
        }
        // Thirty days, and at most a hundred: the oldest go.
        stamped.filter { (k, at) -> k != keep && now - at > TTL_MS }.forEach { edit.remove(it.first) }
        val fresh = stamped.filter { (k, at) -> k == keep || now - at <= TTL_MS }
        if (fresh.size > MAX) {
            fresh.filter { it.first != keep }.sortedBy { it.second }
                .take(fresh.size - MAX).forEach { edit.remove(it.first) }
        }
    }

    private fun key(ref: SessionRef) = "${ref.profileId}:${ref.sessionId}"

    private companion object {
        const val MAX = 100
        const val TTL_MS = 30L * 24 * 3600 * 1000
    }
}
