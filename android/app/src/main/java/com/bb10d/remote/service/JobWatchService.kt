package com.bb10d.remote.service

import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.ServiceCompat
import com.bb10d.remote.RemoteApp
import com.bb10d.remote.data.ActiveJobDto
import com.bb10d.remote.data.AgentRepository
import com.bb10d.remote.data.SessionRef
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Keeps the app's status streams connected while turns are running, and turns
 * what they report into notifications.
 *
 * Watching happens here rather than in a screen because a turn outlives the
 * transcript that started it — and because turns started from the desktop TUI
 * or another phone should notify too. Everything it needs is already in the
 * repository's SSE state; the service adds only the lifecycle and the
 * transition detection.
 */
class JobWatchService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var watchJob: Job? = null

    private lateinit var repo: AgentRepository

    /** Jobs we have seen running, so their disappearance means "finished". */
    private val seen = mutableMapOf<String, Watched>()

    private data class Watched(
        val ref: SessionRef,
        val jobId: String,
        val prompt: String,
        val profileName: String,
        val provider: String,
        val nextSeq: Int,
        val blocked: Boolean,
    )

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        repo = RemoteApp.get(this).repository
        pushOngoing()
        watchJob = scope.launch {
            repo.active.collect { byProfile -> onFrame(byProfile) }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        pushOngoing()
        return START_STICKY
    }

    override fun onDestroy() {
        watchJob?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private fun pushOngoing() {
        val active = repo.active.value
        val running = active.values.sumOf { it.size }
        val notification = Notifier.ongoing(
            this,
            running.coerceAtLeast(1),
            summaryLine(active),
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceCompat.startForeground(
                this,
                Notifier.ongoingId(),
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } else {
            startForeground(Notifier.ongoingId(), notification)
        }
    }

    private suspend fun onFrame(byProfile: Map<String, List<ActiveJobDto>>) {
        val settings = repo.settings.value
        val current = mutableMapOf<String, Watched>()

        byProfile.forEach { (profileId, jobs) ->
            val profile = repo.profile(profileId) ?: return@forEach
            jobs.forEach { job ->
                val sessionId = job.newSessionId.ifEmpty { job.sessionId }
                val ref = SessionRef(profileId, sessionId)
                val watched = Watched(
                    ref = ref,
                    jobId = job.jobId,
                    prompt = job.prompt,
                    profileName = profile.displayName,
                    provider = profile.provider,
                    nextSeq = job.nextSeq,
                    blocked = job.pendingPermission || job.pendingQuestion,
                )
                current["$profileId/${job.jobId}"] = watched

                val previous = seen["$profileId/${job.jobId}"]
                if (watched.blocked && previous?.blocked != true) {
                    Notifier.blocked(
                        context = this,
                        ref = ref,
                        title = job.prompt.ifBlank { "Waiting on you" },
                        what = if (job.pendingPermission) {
                            "${profile.displayName} needs permission"
                        } else {
                            "${profile.displayName} is asking a question"
                        },
                        vibrate = settings.hapticCues,
                    )
                } else if (!watched.blocked && previous?.blocked == true) {
                    Notifier.clearBlocked(this, ref)
                }
            }
        }

        // Anything that was running and is gone from the frame has ended.
        val ended = seen.keys - current.keys
        ended.forEach { key ->
            val watched = seen[key] ?: return@forEach
            Notifier.clearBlocked(this, watched.ref)
            if (settings.notifyTurnDone) notifyEnd(watched)
        }

        seen.clear()
        seen.putAll(current)

        if (current.isEmpty()) {
            stopSelf()
        } else {
            pushOngoing()
        }
    }

    /** "Claude · editing apiclient.cpp" — what the ongoing notification says. */
    private fun summaryLine(byProfile: Map<String, List<ActiveJobDto>>): String {
        val lines = byProfile.flatMap { (profileId, jobs) ->
            val name = repo.profile(profileId)?.displayName ?: "Daemon"
            jobs.map { job ->
                val what = listOf(job.phase, job.phaseDetail.ifBlank { job.tool })
                    .filter { it.isNotBlank() }
                    .joinToString(" ")
                if (what.isBlank()) name else "$name · $what"
            }
        }
        return lines.firstOrNull().orEmpty().take(120)
    }

    /**
     * The stream says a job vanished but not why, so read the final snapshot.
     * `since = nextSeq` keeps it to a status-only response.
     */
    private suspend fun notifyEnd(watched: Watched) {
        val client = repo.client(watched.ref.profileId) ?: return
        val snapshot = runCatching {
            client.job(watched.jobId, watched.nextSeq.coerceAtLeast(0))
        }.getOrNull()
        val status = snapshot?.status ?: "done"
        if (status == "stopped" && snapshot?.error.isNullOrBlank()) {
            // A stop the user asked for needs no announcement.
            return
        }
        val title = snapshot?.resultText?.takeIf { it.isNotBlank() }?.lineSequence()?.first()
            ?: watched.prompt.ifBlank { "Turn finished" }
        Notifier.finished(
            context = this,
            ref = watched.ref,
            title = title.take(180),
            status = status,
            error = snapshot?.error.orEmpty(),
        )
        repo.refreshSession(watched.ref)
    }

    companion object {
        fun sync(context: Context, shouldRun: Boolean) {
            val intent = Intent(context, JobWatchService::class.java)
            if (shouldRun) {
                runCatching {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        context.startForegroundService(intent)
                    } else {
                        context.startService(intent)
                    }
                }
            } else {
                runCatching { context.stopService(intent) }
            }
        }
    }
}
