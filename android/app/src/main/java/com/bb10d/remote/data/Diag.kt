package com.bb10d.remote.data

import android.util.Log
import com.bb10d.remote.BuildConfig

/**
 * Debug-only breadcrumbs.
 *
 * A daemon that answers `/api/ping` but returns nothing useful is the hardest
 * failure to diagnose from a phone screen, so the fan-out logs what each one
 * actually contributed. Release builds compile these away.
 */
object Diag {
    private const val TAG = "AgentRemote"

    fun log(message: String, error: Throwable? = null) {
        if (!BuildConfig.DEBUG) return
        if (error != null) Log.w(TAG, message, error) else Log.d(TAG, message)
    }
}
