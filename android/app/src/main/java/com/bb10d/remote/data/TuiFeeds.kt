package com.bb10d.remote.data

import androidx.annotation.MainThread
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ProcessLifecycleOwner
import com.bb10d.remote.net.TuiStream
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** What the TUI face shows for one session. */
data class TuiView(
    val text: String = "Connecting to host TUI…",
    val status: String = "Host TUI",
    val live: Boolean = false,
    val error: String? = null,
    /** Frame number; 0 = no frame yet. */
    val seq: Long = 0L,
    /** The host pane's width in columns (the daemon says; 120 before it does). */
    val cols: Int = 120,
)

/**
 * The TUI connections, held by the app rather than by the screen.
 *
 * Leaving a session page used to close its terminal stream, and coming back
 * opened a new one and waited for its first frame. Now the stream stays up
 * while the app is in the foreground, so going back to the list or to another
 * conversation and returning shows the pane at once. Once the app has been in
 * the background for five minutes every stream is let go; the ones a visible
 * session page still shows reconnect when the app comes back.
 *
 * At most [KEEP_IDLE] streams nobody is looking at are kept (the most
 * recently left ones).
 */
class TuiFeeds(private val repo: AgentRepository, private val scope: CoroutineScope) {

    private class Feed(val ref: SessionRef) {
        val state = MutableStateFlow(TuiView())
        var job: Job? = null
        var users = 0
        var lastUsed = System.currentTimeMillis()
        /** Last new frame from the host or key from the reader. */
        @Volatile var lastActivity = System.currentTimeMillis()
    }

    private val feeds = LinkedHashMap<String, Feed>()
    private var watching = false
    private var releaseJob: Job? = null

    /** The state for [ref]; it only updates while the feed runs (see [acquire]). */
    @MainThread
    fun flowFor(ref: SessionRef): StateFlow<TuiView> = feed(ref).state

    /** The reader sent keys to this terminal. */
    fun touch(ref: SessionRef) {
        feeds[ref.key]?.lastActivity = System.currentTimeMillis()
    }

    /** A session page shows this terminal: keep (or start) its stream. */
    @MainThread
    fun acquire(ref: SessionRef) {
        watchLifecycle()
        startIdleSweep()
        val f = feed(ref)
        f.users++
        f.lastUsed = System.currentTimeMillis()
        f.lastActivity = f.lastUsed
        // A stream that ended on its own (an error) is started again.
        if (f.job?.isActive != true) start(f)
        trim()
    }

    /** The page is gone; the stream stays up while the app is in front. */
    @MainThread
    fun release(ref: SessionRef) {
        val f = feeds[ref.key] ?: return
        f.users = (f.users - 1).coerceAtLeast(0)
        f.lastUsed = System.currentTimeMillis()
        trim()
    }

    private fun feed(ref: SessionRef) = feeds.getOrPut(ref.key) { Feed(ref) }

    private fun trim() {
        val idle = feeds.values.filter { it.users == 0 }.sortedByDescending { it.lastUsed }
        idle.drop(KEEP_IDLE).forEach { drop(it) }
    }

    private fun drop(f: Feed) {
        f.job?.cancel()
        f.job = null
        feeds.remove(f.ref.key)
    }

    private var sweeping = false

    /**
     * A stream nobody is looking at, whose pane has not changed and that has
     * had no key for five minutes, is let go: a still pane out of sight is
     * not worth a connection. It comes back the next time a page shows it.
     */
    private fun startIdleSweep() {
        if (sweeping) return
        sweeping = true
        scope.launch(Dispatchers.Main) {
            while (isActive) {
                delay(30_000)
                val now = System.currentTimeMillis()
                feeds.values.toList()
                    .filter { it.users == 0 && now - it.lastActivity > IDLE_RELEASE_MS }
                    .forEach { drop(it) }
            }
        }
    }

    private fun watchLifecycle() {
        if (watching) return
        watching = true
        ProcessLifecycleOwner.get().lifecycle.addObserver(LifecycleEventObserver { _, event ->
            when (event) {
                Lifecycle.Event.ON_STOP -> {
                    releaseJob?.cancel()
                    releaseJob = scope.launch(Dispatchers.Main) {
                        delay(BACKGROUND_RELEASE_MS)
                        // Nothing is on screen now: let every stream go. A
                        // page still open keeps its entry, and reconnects.
                        feeds.values.toList().forEach { f ->
                            if (f.users == 0) drop(f) else {
                                f.job?.cancel()
                                f.job = null
                                f.state.update { it.copy(live = false, status = "Paused") }
                            }
                        }
                    }
                }
                Lifecycle.Event.ON_START -> {
                    releaseJob?.cancel()
                    releaseJob = null
                    feeds.values.filter { it.users > 0 && it.job?.isActive != true }.forEach { start(it) }
                }
                else -> Unit
            }
        })
    }

    private fun start(f: Feed) {
        val ref = f.ref
        f.job = scope.launch {
            val client = repo.client(ref.profileId) ?: return@launch
            fun show(frame: TuiFrameDto) = f.state.update { v ->
                when {
                    !frame.attached -> v.copy(
                        error = null,
                        live = false,
                        status = frame.error.ifBlank { "No host TUI attached" },
                        text = if (v.seq == 0L) frame.error.ifBlank {
                            "No interactive TUI for this session. Start a turn in Interactive mode."
                        } else v.text,
                    )
                    frame.seq != v.seq -> v.also { f.lastActivity = System.currentTimeMillis() }.copy(
                        error = null,
                        seq = frame.seq,
                        cols = if (frame.cols > 0) frame.cols else v.cols,
                        live = true,
                        status = if (frame.jobId.isNotBlank()) "Live · job ${frame.jobId.take(8)}" else "Live",
                        text = frame.text.ifBlank { "(empty pane)" },
                    )
                    else -> v.copy(error = null, live = true)
                }
            }
            // Daemon ≥ 2.22 pushes a frame when the pane changes; the echo of
            // a key is then one hop away instead of up to a 400 ms poll.
            if (repo.profile(ref.profileId)?.caps?.tuiStream == true) {
                try {
                    TuiStream(client, ref.sessionId).events().collect { ev ->
                        when (ev) {
                            is TuiStream.Event.Frame -> show(ev.frame)
                            is TuiStream.Event.Down -> {
                                if (ev.unsupported) throw StopStream()
                                f.state.update { it.copy(live = false, status = "Reconnecting…") }
                            }
                        }
                    }
                    return@launch
                } catch (_: StopStream) {
                    // The daemon has no stream route: poll instead.
                }
            }
            // Older daemons: ask for the pane every 400 ms.
            while (isActive) {
                val frame = runCatching { client.tui(ref.sessionId) }.getOrElse { e ->
                    f.state.update { it.copy(error = repo.reason(e), live = false, status = "Error") }
                    delay(1200)
                    continue
                }
                show(frame)
                delay(400)
            }
        }
    }

    private class StopStream : Exception()

    private companion object {
        const val KEEP_IDLE = 3
        const val BACKGROUND_RELEASE_MS = 5 * 60 * 1000L
        const val IDLE_RELEASE_MS = 5 * 60 * 1000L
    }
}
