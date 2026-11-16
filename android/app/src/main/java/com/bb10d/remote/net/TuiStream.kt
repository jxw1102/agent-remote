package com.bb10d.remote.net

import com.bb10d.remote.data.Json
import com.bb10d.remote.data.TuiFrameDto
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
 * Live TUI frames pushed by the daemon: `GET /sse/sessions/<id>/tui` writes a
 * frame the moment the host pane changes (agentremoted ≥ 2.22).
 *
 * The screen used to ask for the pane every 400 ms, so a key was echoed up to
 * 400 ms plus a round trip late. Pushed, the echo is one hop behind the key,
 * and a still pane costs nothing.
 */
class TuiStream(private val client: DaemonClient, private val sessionId: String) {

    sealed interface Event {
        data class Frame(val frame: TuiFrameDto) : Event

        /** [unsupported]: the daemon has no such route — poll instead. */
        data class Down(val reason: String, val unsupported: Boolean = false) : Event
    }

    fun events(): Flow<Event> = callbackFlow {
        // Long-lived read: a still pane sends only a keepalive every 15 s.
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
                .url(client.tuiStreamUrl(sessionId))
                .header(header, value)
                .header("Accept", "text/event-stream")
                .build()
            source = factory.newEventSource(
                request,
                object : EventSourceListener() {
                    var next = attempt

                    override fun onEvent(
                        eventSource: EventSource,
                        id: String?,
                        type: String?,
                        data: String,
                    ) {
                        val frame = runCatching {
                            Json.decodeFromString(TuiFrameDto.serializer(), data)
                        }.getOrNull() ?: return
                        next = 0
                        trySend(Event.Frame(frame))
                    }

                    override fun onClosed(eventSource: EventSource) {
                        if (closed) return
                        trySend(Event.Down("stream closed"))
                        retry(next + 1)
                    }

                    override fun onFailure(
                        eventSource: EventSource,
                        t: Throwable?,
                        response: Response?,
                    ) {
                        if (closed) return
                        val code = response?.code
                        response?.close()
                        if (code == 404 || code == 405) {
                            trySend(Event.Down("not supported", unsupported = true))
                            return
                        }
                        trySend(
                            Event.Down(
                                when {
                                    code == 401 -> "token rejected"
                                    code != null -> "HTTP $code"
                                    else -> t?.message ?: "connection lost"
                                },
                            ),
                        )
                        retry(if (code == 401) MAX_ATTEMPT else next + 1)
                    }

                    fun retry(n: Int) {
                        val delayMs = backoffMs(n)
                        http.dispatcher.executorService.execute {
                            try {
                                Thread.sleep(delayMs)
                            } catch (e: InterruptedException) {
                                Thread.currentThread().interrupt()
                                return@execute
                            }
                            connect(n)
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
        const val MAX_ATTEMPT = 5

        /** 0.5s, 1s, 2s … capped at 10s: someone is looking at this screen. */
        fun backoffMs(attempt: Int): Long =
            (500L shl attempt.coerceIn(0, MAX_ATTEMPT)).coerceAtMost(10_000L)
    }
}
