package com.bb10d.remote.net

import com.bb10d.remote.data.ActiveJobDto
import com.bb10d.remote.data.Json
import com.bb10d.remote.data.StatusFrameDto
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.util.concurrent.TimeUnit

/**
 * The daemon's push channel: `GET /sse/status` emits the whole active-job list
 * about once a second (and a `:` keepalive when nothing changed).
 *
 * This is what makes the app feel live without hammering the daemon: job
 * polling only fires when a frame's `next_seq` proves there is something new
 * (see JobWatcher), and the session list marks working sessions straight from
 * these frames — including turns started from another device.
 *
 * SSE rather than the daemon's WebSocket: it rides plain HTTP, so it survives
 * the HTTPS/Cloudflare path the VPS profile uses.
 */
class StatusStream(private val client: DaemonClient) {

    sealed interface Event {
        data class Frame(val active: List<ActiveJobDto>) : Event
        data class Down(val reason: String) : Event
        data object Up : Event
    }

    fun events(): Flow<Event> = callbackFlow {
        // Long-lived read: the stream is idle between pushes, so the normal
        // 30s read timeout would tear it down every half minute.
        val http: OkHttpClient = DaemonClient.shared.newBuilder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .retryOnConnectionFailure(false)
            .build()
        val factory = EventSources.createFactory(http)
        var source: EventSource? = null
        var closed = false

        fun connect(attempt: Int) {
            if (closed) return
            val (header, value) = client.authHeader()
            val request = Request.Builder()
                .url(client.statusUrl())
                .header(header, value)
                .header("Accept", "text/event-stream")
                .build()
            source = factory.newEventSource(
                request,
                object : EventSourceListener() {
                    override fun onOpen(eventSource: EventSource, response: Response) {
                        trySend(Event.Up)
                    }

                    override fun onEvent(
                        eventSource: EventSource,
                        id: String?,
                        type: String?,
                        data: String,
                    ) {
                        val frame = runCatching {
                            Json.decodeFromString(StatusFrameDto.serializer(), data)
                        }.getOrNull() ?: return
                        trySend(Event.Frame(frame.active))
                    }

                    override fun onClosed(eventSource: EventSource) {
                        if (closed) return
                        trySend(Event.Down("stream closed"))
                        retry(attempt + 1)
                    }

                    override fun onFailure(
                        eventSource: EventSource,
                        t: Throwable?,
                        response: Response?,
                    ) {
                        if (closed) return
                        val reason = when {
                            response?.code == 401 -> "token rejected"
                            response != null -> "HTTP ${response.code}"
                            else -> t?.message ?: "connection lost"
                        }
                        response?.close()
                        trySend(Event.Down(reason))
                        // A rejected token will not fix itself by reconnecting
                        // in a tight loop; back off to the ceiling immediately.
                        retry(if (response?.code == 401) MAX_ATTEMPT else attempt + 1)
                    }

                    fun retry(next: Int) {
                        val delayMs = backoffMs(next)
                        http.dispatcher.executorService.execute {
                            try {
                                Thread.sleep(delayMs)
                            } catch (e: InterruptedException) {
                                Thread.currentThread().interrupt()
                                return@execute
                            }
                            connect(next)
                        }
                    }
                },
            )
        }

        connect(0)

        awaitClose {
            closed = true
            source?.cancel()
        }
    }

    private companion object {
        const val MAX_ATTEMPT = 6

        /** 1s, 2s, 4s … capped at 30s — a sleeping laptop must not be spammed. */
        fun backoffMs(attempt: Int): Long =
            (1000L shl attempt.coerceIn(0, MAX_ATTEMPT)).coerceAtMost(30_000L)
    }
}
