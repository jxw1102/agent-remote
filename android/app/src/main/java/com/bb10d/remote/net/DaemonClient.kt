package com.bb10d.remote.net

import com.bb10d.remote.data.AttachmentDto
import com.bb10d.remote.data.FocusActionDto
import com.bb10d.remote.data.FocusDto
import com.bb10d.remote.data.DropListDto
import com.bb10d.remote.data.ErrorDto
import com.bb10d.remote.data.JobSnapshotDto
import com.bb10d.remote.data.JobStartedDto
import com.bb10d.remote.data.Json
import com.bb10d.remote.data.MessagesDto
import com.bb10d.remote.data.PingDto
import com.bb10d.remote.data.ProjectsDto
import com.bb10d.remote.data.SearchDto
import com.bb10d.remote.data.StepTextDto
import com.bb10d.remote.data.SessionDto
import com.bb10d.remote.data.SessionsDto
import com.bb10d.remote.data.ShareDto
import com.bb10d.remote.data.ShellResultDto
import com.bb10d.remote.data.TitleDto
import com.bb10d.remote.data.TuiFrameDto
import com.bb10d.remote.data.UsageDto
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Headers
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit

/** A failed call, already turned into something worth showing a user. */
class DaemonException(
    val status: Int,
    override val message: String,
    val transport: Boolean = false,
) : Exception(message) {
    /** Wrong/absent token — the UI offers "check the profile" instead of retry. */
    val unauthorized: Boolean get() = status == 401
    val notFound: Boolean get() = status == 404
    /** Daemon refused because the job moved on (queue closed, TUI gone). */
    val conflict: Boolean get() = status == 409
}

/** One downloaded drop entry: its bytes plus the name the daemon served it as. */
class DropPayload(val name: String, val bytes: ByteArray)

/**
 * HTTP access to one agentremoted host.
 *
 * Timeouts are per call kind, because the daemon's own costs differ by two
 * orders of magnitude: a ping is instant, but grok's /api/usage resumes a tmux
 * TUI and reads its `/usage` output, and a cold transcript can be megabytes.
 * One global timeout would either abort usage or hang the UI on a dead host.
 */
class DaemonClient(
    baseUrl: String,
    private val token: String,
    private val http: OkHttpClient = shared,
) {
    private val root: HttpUrl? = normalize(baseUrl)

    companion object {
        val shared: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .callTimeout(0, TimeUnit.MILLISECONDS)
            .retryOnConnectionFailure(true)
            .build()

        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
        private val OCTET_MEDIA = "application/octet-stream".toMediaType()

        /** Multi-MB uploads over cellular / CF need a long *write* budget. */
        private const val UPLOAD_TIMEOUT_SEC = 180

        /**
         * Accept what a person types: "10.0.0.5", "10.0.0.5:8473",
         * "http://host:2095/", "https://nerd.example.com".
         */
        fun normalize(raw: String): HttpUrl? {
            val trimmed = raw.trim().trimEnd('/')
            if (trimmed.isEmpty()) return null
            val withScheme =
                if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) trimmed
                else "http://$trimmed"
            return withScheme.toHttpUrlOrNull()
        }

        /** Default port hint shown in the profile editor. */
        const val DEFAULT_PORT = 8473
    }

    private fun url(path: String, query: Map<String, String> = emptyMap()): HttpUrl {
        val base = root ?: throw DaemonException(0, "This profile has no server address")
        val builder = base.newBuilder()
        path.trim('/').split('/').filter { it.isNotEmpty() }.forEach {
            builder.addPathSegment(it)
        }
        query.forEach { (k, v) -> if (v.isNotEmpty()) builder.addQueryParameter(k, v) }
        return builder.build()
    }

    private fun request(url: HttpUrl): Request.Builder =
        Request.Builder().url(url).header("X-Auth-Token", token)
            .header("Accept", "application/json")

    /**
     * Per-call timeouts. Upload paths must raise **write** as well as read —
     * the shared client defaults to 30s write, which aborts multi-MB posts on
     * cellular and surfaces as daemon "empty upload" / "truncated".
     */
    private fun clientFor(
        readTimeoutSeconds: Int = 0,
        writeTimeoutSeconds: Int = -1,
    ): OkHttpClient {
        val writeSec = when {
            writeTimeoutSeconds >= 0 -> writeTimeoutSeconds
            readTimeoutSeconds > 0 -> readTimeoutSeconds
            else -> -1
        }
        if (readTimeoutSeconds <= 0 && writeSec < 0) return http
        val b = http.newBuilder()
        if (readTimeoutSeconds > 0) {
            b.readTimeout(readTimeoutSeconds.toLong(), TimeUnit.SECONDS)
        }
        if (writeSec > 0) {
            b.writeTimeout(writeSec.toLong(), TimeUnit.SECONDS)
        }
        return b.build()
    }

    private suspend fun <T> call(
        req: Request,
        serializer: KSerializer<T>,
        timeoutSeconds: Int = 0,
    ): T = withContext(Dispatchers.IO) {
        val body = raw(req, timeoutSeconds)
        val text = body.decodeToString()
        try {
            Json.decodeFromString(serializer, text)
        } catch (e: DaemonException) {
            throw e
        } catch (e: Exception) {
            throw DaemonException(0, unreadMessage(text))
        }
    }

    private suspend fun raw(
        req: Request,
        timeoutSeconds: Int = 0,
        writeTimeoutSeconds: Int = -1,
    ): ByteArray = rawResponse(req, timeoutSeconds, writeTimeoutSeconds).second

    private suspend fun rawResponse(
        req: Request,
        timeoutSeconds: Int = 0,
        writeTimeoutSeconds: Int = -1,
    ): Pair<Headers, ByteArray> =
        withContext(Dispatchers.IO) {
            val response: Response = try {
                clientFor(timeoutSeconds, writeTimeoutSeconds).newCall(req).execute()
            } catch (e: SocketTimeoutException) {
                throw DaemonException(
                    0,
                    "Upload timed out — try again on a stronger network, or a smaller file",
                    transport = true,
                )
            } catch (e: IOException) {
                throw DaemonException(
                    0,
                    e.message?.takeIf { it.isNotBlank() } ?: "Could not reach the daemon",
                    transport = true,
                )
            }
            response.use {
                val bytes = it.body?.bytes() ?: ByteArray(0)
                // 2xx includes 202 Accepted (job started) from agentremoted.
                if (it.code !in 200..299) {
                    throw DaemonException(it.code, errorText(it.code, bytes))
                }
                it.headers to bytes
            }
        }

    private fun errorText(code: Int, body: ByteArray): String {
        val text = body.decodeToString()
        val parsed = runCatching {
            Json.decodeFromString(ErrorDto.serializer(), text).error
        }.getOrNull()
        if (!parsed.isNullOrBlank()) return parsed
        // Multi root often returns plain {"error":"…"}; also surface a snippet
        // so misrouted HTML/proxy pages are diagnosable.
        val snip = text.trim().replace(Regex("\\s+"), " ").take(160)
        return when {
            snip.isNotEmpty() && !snip.startsWith("{") ->
                "Daemon returned HTTP $code: $snip"
            code == 401 -> "Token rejected by the daemon"
            code == 404 -> "Not found on the daemon"
            else -> "Daemon returned HTTP $code"
        }
    }

    private fun unreadMessage(text: String): String {
        val snip = text.trim().replace(Regex("\\s+"), " ").take(160)
        return if (snip.isEmpty()) {
            "The daemon sent an empty reply"
        } else {
            "The daemon sent something this app cannot read: $snip"
        }
    }

    /**
     * POST /api/sessions/new and /continue return `{"job_id":"…"}` (HTTP 202).
     * Parse the id without depending only on the generated serializer, so a
     * R8/minifier quirk or an extra field cannot blank the start flow.
     */
    private fun parseJobId(body: ByteArray): String {
        val text = body.decodeToString()
        val obj = runCatching {
            Json.parseToJsonElement(text) as? JsonObject
        }.getOrNull()
        if (obj != null) {
            val id = primitiveString(obj["job_id"])
                ?: primitiveString(obj["jobId"])
            if (!id.isNullOrBlank()) return id
            val err = primitiveString(obj["error"])
            if (!err.isNullOrBlank()) throw DaemonException(0, err)
        }
        // Last resort: typed DTO (same payload shape as older clients).
        val typed = runCatching {
            Json.decodeFromString(JobStartedDto.serializer(), text).jobId
        }.getOrNull()
        if (!typed.isNullOrBlank()) return typed
        throw DaemonException(0, unreadMessage(text))
    }

    private fun primitiveString(el: kotlinx.serialization.json.JsonElement?): String? =
        (el as? JsonPrimitive)?.content?.takeIf { it.isNotBlank() }

    private fun jsonBody(build: JsonObject): RequestBody =
        Json.encodeToString(JsonObject.serializer(), build).toRequestBody(JSON_MEDIA)

    // -- discovery ---------------------------------------------------------

    /** /api/ping is unauthenticated, but the token unlocks models + commands. */
    suspend fun ping(): PingDto =
        call(request(url("api/ping")).get().build(), PingDto.serializer(), timeoutSeconds = 10)

    suspend fun usage(): UsageDto = call(
        request(url("api/usage")).get().build(),
        UsageDto.serializer(),
        // grok has no usage endpoint: it resumes a TUI and scrapes /usage.
        timeoutSeconds = 90,
    )

    suspend fun projects(): ProjectsDto =
        call(request(url("api/projects")).get().build(), ProjectsDto.serializer())

    // -- sessions ----------------------------------------------------------

    suspend fun sessions(projectId: String = "", limit: Int = 40, all: Boolean = false):
        SessionsDto = call(
        request(
            url(
                "api/sessions",
                buildMap {
                    if (projectId.isNotEmpty()) put("project", projectId)
                    put("limit", limit.toString())
                    if (all) put("all", "1")
                },
            ),
        ).get().build(),
        SessionsDto.serializer(),
    )

    suspend fun search(query: String, projectId: String = "", limit: Int = 30, all: Boolean = false):
        SearchDto = call(
        request(
            url(
                "api/sessions/search",
                buildMap {
                    put("q", query)
                    if (projectId.isNotEmpty()) put("project", projectId)
                    put("limit", limit.toString())
                    if (all) put("all", "1")
                },
            ),
        ).get().build(),
        SearchDto.serializer(),
    )

    suspend fun session(id: String): SessionDto =
        call(request(url("api/sessions/$id")).get().build(), SessionDto.serializer())

    // -- focus list --------------------------------------------------------
    //
    // Focus mode asks the daemon for the rows rather than filtering
    // /api/sessions here: a project untouched for weeks falls outside the
    // recency window, and that is exactly the row that must not be lost.

    suspend fun focus(): FocusDto =
        call(request(url("api/focus")).get().build(), FocusDto.serializer())

    /** Take a row off Focus (done) or put it back (restore). */
    suspend fun focusDone(sessionId: String, done: Boolean): FocusActionDto = call(
        request(url("api/focus/$sessionId/" + if (done) "done" else "restore"))
            .post(jsonBody(buildJsonObject {})).build(),
        FocusActionDto.serializer(),
    )

    /**
     * Mark a session looked at. Cosmetic only — it dims a finished turn's tag
     * and changes no state.
     */
    suspend fun focusSeen(sessionId: String): FocusActionDto = call(
        request(url("api/focus/$sessionId/seen"))
            .post(jsonBody(buildJsonObject {})).build(),
        FocusActionDto.serializer(),
    )

    /** Rename a session; an empty title drops back to the derived name. */
    suspend fun setTitle(sessionId: String, title: String): TitleDto = call(
        request(url("api/sessions/$sessionId/title")).post(
            jsonBody(buildJsonObject { put("title", title) }),
        ).build(),
        TitleDto.serializer(),
    )

    /** Ask the daemon to derive a fresh title from the transcript. */
    suspend fun regenerateTitle(sessionId: String): TitleDto = call(
        request(url("api/sessions/$sessionId/title/regenerate"))
            .post(jsonBody(buildJsonObject {})).build(),
        TitleDto.serializer(),
        timeoutSeconds = 60,
    )

    /** Mint a 7-day read-only share URL hosted by this daemon. */
    suspend fun shareSession(sessionId: String): ShareDto = call(
        request(url("api/sessions/$sessionId/share"))
            .post(jsonBody(buildJsonObject {})).build(),
        ShareDto.serializer(),
    )

    /** offset < 0 asks for the tail, which is what a phone wants first. */
    suspend fun messages(id: String, offset: Int, limit: Int, steps: Boolean = false): MessagesDto = call(
        request(
            url(
                "api/sessions/$id/messages",
                buildMap {
                    if (offset >= 0) put("offset", offset.toString())
                    put("limit", limit.toString())
                    // Process view: attach tool_use/tool_result/thinking steps
                    // to each message. Off = byte-identical to the old reply.
                    if (steps) put("detail", "steps")
                },
            ),
        ).get().build(),
        MessagesDto.serializer(),
        timeoutSeconds = 60,
    )

    /** Full text behind a truncated process step — fetched only on expand. */
    suspend fun stepText(sessionId: String, ref: String): StepTextDto = call(
        request(url("api/sessions/$sessionId/steps/$ref")).get().build(),
        StepTextDto.serializer(),
        timeoutSeconds = 60,
    )

    // -- turns -------------------------------------------------------------

    suspend fun continueSession(
        sessionId: String,
        prompt: String,
        execMode: String,
        model: String,
        effort: String,
    ): String = parseJobId(
        raw(
            request(url("api/sessions/$sessionId/continue")).post(
                jsonBody(
                    buildJsonObject {
                        put("prompt", prompt)
                        // Interactive | Headless UI → daemon permission_mode.
                        put("permission_mode", com.bb10d.remote.data.ExecMode.wire(execMode))
                        put("model", model)
                        put("effort", effort)
                    },
                ),
            ).build(),
        ),
    )

    suspend fun newSession(
        cwd: String,
        prompt: String,
        execMode: String,
        model: String,
        effort: String,
        /** Multi-harness root requires this so the daemon routes the turn. */
        provider: String = "",
    ): String = parseJobId(
        raw(
            request(url("api/sessions/new")).post(
                jsonBody(
                    buildJsonObject {
                        put("cwd", cwd)
                        put("prompt", prompt)
                        put("permission_mode", com.bb10d.remote.data.ExecMode.wire(execMode))
                        put("model", model)
                        put("effort", effort)
                        if (provider.isNotBlank()) put("provider", provider)
                    },
                ),
            ).build(),
        ),
    )

    suspend fun job(jobId: String, since: Int): JobSnapshotDto = call(
        request(url("api/jobs/$jobId", mapOf("since" to since.toString()))).get().build(),
        JobSnapshotDto.serializer(),
    )

    suspend fun stop(jobId: String) {
        raw(request(url("api/jobs/$jobId/stop")).post(jsonBody(buildJsonObject {})).build())
    }

    /** Interactive turns: type straight into the host TUI, no daemon queue. */
    suspend fun input(jobId: String, prompt: String) {
        raw(
            request(url("api/jobs/$jobId/input"))
                .post(jsonBody(buildJsonObject { put("prompt", prompt) })).build(),
        )
    }

    /**
     * Live TUI pane capture for a session.
     * [ansi] requests coloured SGR (web/Android). Default plain is for BB.
     */
    suspend fun tui(sessionId: String, ansi: Boolean = true): TuiFrameDto = call(
        request(
            url(
                "api/sessions/$sessionId/tui",
                if (ansi) mapOf("ansi" to "1") else emptyMap(),
            ),
        ).get().build(),
        TuiFrameDto.serializer(),
        timeoutSeconds = 15,
    )

    /** Inject keys and/or literal text into the session's Live TUI. */
    suspend fun tuiKeys(sessionId: String, keys: List<String> = emptyList(), text: String = "") {
        raw(
            request(url("api/sessions/$sessionId/tui/keys")).post(
                jsonBody(
                    buildJsonObject {
                        if (keys.isNotEmpty()) {
                            put(
                                "keys",
                                kotlinx.serialization.json.JsonArray(
                                    keys.map { kotlinx.serialization.json.JsonPrimitive(it) },
                                ),
                            )
                        }
                        if (text.isNotEmpty()) put("text", text)
                    },
                ),
            ).build(),
        )
    }

    suspend fun queue(jobId: String, prompt: String) {
        raw(
            request(url("api/jobs/$jobId/queue"))
                .post(jsonBody(buildJsonObject { put("prompt", prompt) })).build(),
        )
    }

    suspend fun cancelQueued(jobId: String, queueId: String) {
        raw(
            request(url("api/jobs/$jobId/queue/$queueId/cancel"))
                .post(jsonBody(buildJsonObject {})).build(),
        )
    }

    suspend fun answerPermission(jobId: String, requestId: String, allow: Boolean, message: String = "") {
        raw(
            request(url("api/jobs/$jobId/permission")).post(
                jsonBody(
                    buildJsonObject {
                        put("request_id", requestId)
                        put("allow", allow)
                        put("message", message)
                    },
                ),
            ).build(),
        )
    }

    /** answers: one list of chosen labels per question; null cancels the panel. */
    suspend fun answerQuestion(
        jobId: String,
        requestId: String,
        answers: List<List<String>>?,
        notes: List<String> = emptyList(),
    ) {
        val body = buildJsonObject {
            put("request_id", requestId)
            if (answers == null) {
                put("cancel", true)
            } else {
                put(
                    "answers",
                    JsonArray(
                        answers.map { picks -> JsonArray(picks.map { JsonPrimitive(it) }) },
                    ),
                )
                if (notes.isNotEmpty()) {
                    put("notes", JsonArray(notes.map { JsonPrimitive(it) }))
                }
            }
        }
        raw(request(url("api/jobs/$jobId/question")).post(jsonBody(body)).build())
    }

    suspend fun shell(command: String, sessionId: String, cwd: String): ShellResultDto = call(
        request(url("api/shell")).post(
            jsonBody(
                buildJsonObject {
                    put("command", command)
                    if (sessionId.isNotEmpty()) put("session_id", sessionId)
                    if (cwd.isNotEmpty()) put("cwd", cwd)
                },
            ),
        ).build(),
        ShellResultDto.serializer(),
        timeoutSeconds = 40,
    )

    // -- files -------------------------------------------------------------

    /**
     * POST raw bytes to /api/attachments?name=… → host path for the prompt.
     * Parses path defensively (same idea as [parseJobId]) so R8/extra fields
     * cannot leave the composer without an `[attached: …]` marker.
     *
     * Always sets a fixed [Content-Length] body (no chunked encoding) so the
     * daemon never sees "empty upload", and uses a long write timeout for
     * multi-MB photos over cellular. One automatic retry on transport errors.
     */
    suspend fun uploadAttachment(name: String, bytes: ByteArray): AttachmentDto {
        val safe = name.ifBlank { "file" }
        require(bytes.isNotEmpty()) { "empty file" }
        // Fixed-length body: Content-Length = size. Chunked uploads hit the
        // daemon as length 0 ("empty upload") behind some proxies.
        val body = object : RequestBody() {
            override fun contentType() = OCTET_MEDIA
            override fun contentLength() = bytes.size.toLong()
            override fun writeTo(sink: okio.BufferedSink) {
                sink.write(bytes)
            }
        }
        val req = request(url("api/attachments", mapOf("name" to safe)))
            .post(body)
            .header("Content-Type", "application/octet-stream")
            .header("Content-Length", bytes.size.toString())
            .build()
        var last: Exception? = null
        repeat(2) { attempt ->
            try {
                val raw = raw(
                    req,
                    timeoutSeconds = UPLOAD_TIMEOUT_SEC,
                    writeTimeoutSeconds = UPLOAD_TIMEOUT_SEC,
                )
                return parseAttachment(raw)
            } catch (e: DaemonException) {
                last = e
                // Retry only transport / timeout; 4xx means the daemon understood.
                if (!e.transport || attempt == 1) throw e
            } catch (e: Exception) {
                last = e
                if (attempt == 1) throw e
            }
        }
        throw last ?: DaemonException(0, "Upload failed", transport = true)
    }

    private fun parseAttachment(body: ByteArray): AttachmentDto {
        val text = body.decodeToString()
        val obj = runCatching {
            Json.parseToJsonElement(text) as? JsonObject
        }.getOrNull()
        if (obj != null) {
            val path = primitiveString(obj["path"]).orEmpty()
            if (path.isNotBlank()) {
                val size = (obj["size"] as? JsonPrimitive)?.content?.toLongOrNull() ?: 0L
                val ok = (obj["ok"] as? JsonPrimitive)?.content?.toBooleanStrictOrNull() ?: true
                return AttachmentDto(ok = ok, path = path, size = size)
            }
            val err = primitiveString(obj["error"])
            if (!err.isNullOrBlank()) throw DaemonException(0, err)
        }
        val typed = runCatching {
            Json.decodeFromString(AttachmentDto.serializer(), text)
        }.getOrNull()
        if (typed != null && typed.path.isNotBlank()) return typed
        throw DaemonException(0, unreadMessage(text))
    }

    suspend fun dropList(): DropListDto =
        call(request(url("api/drop")).get().build(), DropListDto.serializer())

    /**
     * The daemon names what it actually served in X-Drop-Name — a folder
     * arrives zipped as `<name>.zip` — so the caller saves under that, not
     * under the entry name it asked for.
     */
    suspend fun dropDownload(name: String): DropPayload {
        val (headers, bytes) =
            rawResponse(request(url("api/drop/$name")).get().build(), timeoutSeconds = 180)
        val served = headers["X-Drop-Name"]?.takeIf { it.isNotBlank() } ?: name
        return DropPayload(served, bytes)
    }

    suspend fun dropDelete(name: String) {
        raw(request(url("api/drop/$name/delete")).post(jsonBody(buildJsonObject {})).build())
    }

    /** Fire-and-forget diagnostics; never surfaced to the user. */
    suspend fun clientLog(line: String) {
        runCatching {
            raw(
                request(url("api/clientlog")).post(
                    jsonBody(
                        buildJsonObject {
                            put("app", "android")
                            put("line", line)
                        },
                    ),
                ).build(),
            )
        }
    }

    /** The URL the SSE status stream lives on, for [StatusStream]. */
    fun statusUrl(): HttpUrl = url("sse/status")

    fun authHeader(): Pair<String, String> = "X-Auth-Token" to token
}
