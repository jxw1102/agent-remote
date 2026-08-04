package com.bb10d.remote.service

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.bb10d.remote.MainActivity
import com.bb10d.remote.R
import com.bb10d.remote.RemoteApp
import com.bb10d.remote.data.SessionRef

/**
 * Notifications are the reason this app is worth using over SSH: a turn can
 * take twenty minutes, and the phone should say so instead of being polled.
 *
 * Three kinds, deliberately distinct:
 *  - one ongoing, silent summary while turns run (also the foreground-service
 *    notification that keeps the status streams connected);
 *  - a high-priority alert when the agent is *blocked* on the user;
 *  - a normal alert when a turn finishes.
 */
object Notifier {

    private const val ID_ONGOING = 1
    private const val ID_BLOCKED_BASE = 1000
    private const val ID_DONE_BASE = 2000

    fun canPost(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    fun ongoing(context: Context, running: Int, detail: String): Notification =
        NotificationCompat.Builder(context, RemoteApp.CHANNEL_PROGRESS)
            .setSmallIcon(R.drawable.ic_stat_agent)
            .setContentTitle(
                if (running == 1) "1 turn running" else "$running turns running",
            )
            .setContentText(detail)
            .setOngoing(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setContentIntent(openApp(context, null))
            .build()

    fun blocked(
        context: Context,
        ref: SessionRef,
        title: String,
        what: String,
        vibrate: Boolean,
    ) {
        if (!canPost(context)) return
        val notification = NotificationCompat.Builder(context, RemoteApp.CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_agent)
            .setContentTitle(what)
            .setContentText(title)
            .setStyle(NotificationCompat.BigTextStyle().bigText(title))
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            // The channel's own vibration is a user-facing OS setting; this
            // only silences what the app itself asks for.
            .setSilent(!vibrate)
            .setContentIntent(openApp(context, ref))
            .build()
        manager(context)?.notify(ID_BLOCKED_BASE + ref.key.hashCode().and(0xFF), notification)
    }

    fun clearBlocked(context: Context, ref: SessionRef) {
        manager(context)?.cancel(ID_BLOCKED_BASE + ref.key.hashCode().and(0xFF))
    }

    fun finished(
        context: Context,
        ref: SessionRef,
        title: String,
        status: String,
        error: String,
    ) {
        if (!canPost(context)) return
        val ok = status == "done"
        val notification = NotificationCompat.Builder(context, RemoteApp.CHANNEL_ALERTS)
            .setSmallIcon(R.drawable.ic_stat_agent)
            .setContentTitle(
                when (status) {
                    "done" -> "Turn finished"
                    "stopped" -> "Turn stopped"
                    else -> "Turn failed"
                },
            )
            .setContentText(if (ok || error.isBlank()) title else error)
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .bigText(if (ok || error.isBlank()) title else "$title\n\n$error"),
            )
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(openApp(context, ref))
            .build()
        manager(context)?.notify(ID_DONE_BASE + ref.key.hashCode().and(0xFF), notification)
    }

    fun ongoingId() = ID_ONGOING

    private fun manager(context: Context): NotificationManager? =
        context.getSystemService(NotificationManager::class.java)

    private fun openApp(context: Context, ref: SessionRef?): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (ref != null) putExtra(MainActivity.EXTRA_OPEN_SESSION, ref.key)
        }
        return PendingIntent.getActivity(
            context,
            ref?.key?.hashCode() ?: 0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
    }
}
