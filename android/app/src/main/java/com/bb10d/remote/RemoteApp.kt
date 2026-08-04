package com.bb10d.remote

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import com.bb10d.remote.data.AgentRepository
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class RemoteApp : Application() {

    /**
     * Process-scoped, not screen-scoped: the status streams and a running
     * turn's watcher must survive the transcript being closed, otherwise
     * backgrounding the app would silently stop tracking the work.
     */
    val appScope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    val repository: AgentRepository by lazy { AgentRepository(this, appScope) }

    override fun onCreate() {
        super.onCreate()
        instance = this
        createChannels()
    }

    private fun createChannels() {
        val manager = getSystemService(NotificationManager::class.java) ?: return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_PROGRESS,
                getString(R.string.notif_channel_jobs),
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = getString(R.string.notif_channel_jobs_desc)
                setShowBadge(false)
            },
        )
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ALERTS,
                getString(R.string.notif_channel_alerts),
                NotificationManager.IMPORTANCE_HIGH,
            ).apply {
                description = getString(R.string.notif_channel_alerts_desc)
                enableVibration(true)
            },
        )
    }

    companion object {
        const val CHANNEL_PROGRESS = "jobs"
        const val CHANNEL_ALERTS = "alerts"

        private lateinit var instance: RemoteApp

        fun get(context: Context): RemoteApp =
            context.applicationContext as? RemoteApp ?: instance
    }
}
