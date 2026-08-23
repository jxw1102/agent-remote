package com.bb10d.remote.data

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.longOrNull
import java.time.Instant

/*
 * Wire types for the agentremoted HTTP API (daemon/agentremoted/server.py).
 * The API is the same for every harness; /api/ping reports which ones answer
 * and what they can do, so every feature here is gated on caps, never on a
 * build flavour.
 *
 * Everything is defaulted: an older daemon that predates a field must degrade
 * to "feature off", not to a parse error.
 */

/**
 * Multi /api/projects mixes float epochs (claude/grok) with ISO strings
 * (codex). Accept either as a Double epoch so one bad row never kills the list.
 */
object FlexibleEpochSerializer : KSerializer<Double> {
    override val descriptor: SerialDescriptor =
        PrimitiveSerialDescriptor("FlexibleEpoch", PrimitiveKind.DOUBLE)

    override fun deserialize(decoder: Decoder): Double {
        val json = decoder as? JsonDecoder
        if (json != null) {
            val el = json.decodeJsonElement()
            val p = el as? JsonPrimitive ?: return 0.0
            p.doubleOrNull?.let { return it }
            p.longOrNull?.let { return it.toDouble() }
            val s = p.content.trim()
            if (s.isEmpty()) return 0.0
            s.toDoubleOrNull()?.let { return it }
            return runCatching {
                Instant.parse(
                    if (s.endsWith("Z") || s.contains('+') || s.lastIndexOf('-') > 10) s
                    else s + "Z",
                ).epochSecond.toDouble()
            }.getOrElse {
                runCatching {
                    // "2026-08-04T13:08:59Z" style without Instant if needed
                    Instant.parse(s.replace(" ", "T").let {
                        if (it.endsWith("Z") || it.contains('+')) it else "${it}Z"
                    }).toEpochMilli() / 1000.0
                }.getOrDefault(0.0)
            }
        }
        return runCatching { decoder.decodeDouble() }.getOrDefault(0.0)
    }

    override fun serialize(encoder: Encoder, value: Double) {
        encoder.encodeDouble(value)
    }
}

@Serializable
data class AuthHealthDto(
    val cli: String = "",
    @SerialName("cli_on_path") val cliOnPath: Boolean = false,
    /** subscription | api_key | none | unknown */
    val mode: String = "",
    /** ok | warning | expired | missing | unknown */
    val status: String = "",
    val detail: String = "",
)

@Serializable
data class ProviderDetailDto(
    val caps: Map<String, Boolean> = emptyMap(),
    @SerialName("slash_commands") val slashCommands: List<String> = emptyList(),
    val models: List<String> = emptyList(),
    val efforts: List<String> = emptyList(),
    val auth: AuthHealthDto? = null,
)

@Serializable
data class PingDto(
    val ok: Boolean = false,
    val app: String = "",
    val version: String = "",
    val host: String = "",
    val provider: String = "",
    val caps: Map<String, Boolean> = emptyMap(),
    @SerialName("slash_commands") val slashCommands: List<String> = emptyList(),
    val models: List<String> = emptyList(),
    val efforts: List<String> = emptyList(),
    @SerialName("drop_path") val dropPath: String = "",
    /** agentremoted multi-harness: one profile fronts several providers. */
    val multi: Boolean = false,
    /** Focus support (agentremoted ≥ 2.6); gates Focus mode. */
    val focus: Boolean = false,
    /** Session share (agentremoted ≥ 2.7); gates the share-link action. */
    val share: Boolean = false,
    /** Chunked POST /api/attachments (agentremoted ≥ 2.8.6). */
    @SerialName("chunked_upload") val chunkedUpload: Boolean = false,
    @SerialName("max_upload_mb") val maxUploadMb: Int = 16,
    @SerialName("focus_states") val focusStates: List<String> = emptyList(),
    val providers: List<String> = emptyList(),
    @SerialName("provider_details")
    val providerDetails: Map<String, ProviderDetailDto> = emptyMap(),
    /** Aggregate harness login snapshot (daemon ≥ 2.5.3). */
    val auth: AuthHealthDto? = null,
)

/**
 * [lastActive] may be a float epoch (claude/grok) or an ISO string (older
 * codex). [FlexibleEpochSerializer] accepts both so multi /api/projects
 * never fails the whole list for one harness.
 */
@Serializable
data class ProjectDto(
    val id: String = "",
    val cwd: String = "",
    val name: String = "",
    @SerialName("session_count") val sessionCount: Int = 0,
    @SerialName("last_active")
    @Serializable(with = FlexibleEpochSerializer::class)
    val lastActive: Double = 0.0,
    /** Multi-harness root tags each project with its provider. */
    val provider: String = "",
)

@Serializable
data class ProjectsDto(val projects: List<ProjectDto> = emptyList())

@Serializable
data class SessionDto(
    val id: String = "",
    @SerialName("project_id") val projectId: String = "",
    val cwd: String = "",
    @SerialName("git_branch") val gitBranch: String = "",
    val title: String = "",
    val started: String = "",
    @SerialName("last_active") val lastActive: String = "",
    @SerialName("last_role") val lastRole: String = "",
    @SerialName("last_text") val lastText: String = "",
    val model: String = "",
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    /** Only present on /api/sessions/search rows. */
    val snippet: String = "",
    /** Multi-harness daemon tags each session with its provider. */
    val provider: String = "",
    /** Focus: is this a session the human is tracking. */
    val focus: Boolean = false,
    /**
     * Focus state tag, derived by the daemon from live job state:
     * needs_answer | failed | working | turn_finished. Empty for sessions that
     * are not in Focus.
     */
    @SerialName("focus_state") val focusState: String = "",
    /**
     * Cosmetic companion to [focusState]: a finished turn you have not opened
     * is drawn lit, one you have is drawn dim. Never a state of its own.
     */
    @SerialName("focus_unread") val focusUnread: Boolean = false,
    /** True when the title is a manual rename rather than a derived one. */
    @SerialName("title_manual") val titleManual: Boolean = false,
)

@Serializable
data class SessionsDto(val sessions: List<SessionDto> = emptyList())

/** GET /api/focus — the enrolled sessions only, most urgent first. */
@Serializable
data class FocusDto(
    val sessions: List<SessionDto> = emptyList(),
    val counts: Map<String, Int> = emptyMap(),
    val total: Int = 0,
)

/** POST /api/sessions/<id>/title and .../title/regenerate. */
@Serializable
data class TitleDto(
    val ok: Boolean = false,
    val id: String = "",
    val title: String = "",
    val manual: Boolean = false,
)

/** POST /api/sessions/<id>/share — a 7-day read-only URL hosted by the daemon. */
@Serializable
data class ShareDto(
    val ok: Boolean = false,
    val token: String = "",
    val url: String = "",
    val path: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("expires_at") val expiresAt: Double = 0.0,
    @SerialName("expires_in") val expiresIn: Long = 0,
)

/** POST /api/focus/<key>/{done,restore,seen}. */
@Serializable
data class FocusActionDto(
    val ok: Boolean = false,
    val changed: Boolean = false,
    val key: String = "",
    val focus: Boolean = false,
)

@Serializable
data class SearchDto(
    val query: String = "",
    val results: List<SessionDto> = emptyList(),
)

@Serializable
data class MessageDto(
    val uuid: String = "",
    /** "user" | "assistant" | "status" (grok thought/worked lines). */
    val role: String = "",
    val ts: String = "",
    val text: String = "",
    @SerialName("metaKind") val metaKind: String = "",
    /** Process view only (`?detail=steps`): the work between the messages. */
    val steps: List<StepDto> = emptyList(),
)

/**
 * One process-view step, hung off the message it followed. Kinds:
 * `tool_use` (name/detail), `tool_result` (ok), `thinking` (recorded=false
 * means the CLI stored ciphertext only). Previews are capped daemon-side at
 * ~512 bytes; `truncated` rows fetch the rest from /steps/<ref> on expand.
 */
@Serializable
data class StepDto(
    val kind: String = "",
    val ref: String = "",
    val ts: String = "",
    val name: String = "",
    val detail: String = "",
    val ok: Boolean = true,
    val recorded: Boolean = true,
    val preview: String = "",
    val bytes: Long = 0,
    val truncated: Boolean = false,
    /** Syntax-highlight hint (python/diff/…); empty = plain text. */
    val lang: String = "",
)

@Serializable
data class StepTextDto(val text: String = "")

@Serializable
data class MessagesDto(
    @SerialName("session_id") val sessionId: String = "",
    val total: Int = 0,
    val offset: Int = 0,
    val messages: List<MessageDto> = emptyList(),
)

@Serializable
data class QueuedDto(val id: String = "", val prompt: String = "")

@Serializable
data class PendingPermissionDto(
    @SerialName("request_id") val requestId: String = "",
    @SerialName("tool_name") val toolName: String = "",
    val detail: String = "",
)

@Serializable
data class QuestionOptionDto(
    val label: String = "",
    val description: String = "",
)

@Serializable
data class QuestionDto(
    val question: String = "",
    val header: String = "",
    val options: List<QuestionOptionDto> = emptyList(),
    @SerialName("multi_select") val multiSelect: Boolean = false,
    /** Option label that opens a free-text field (grok "Request changes"). */
    @SerialName("note_for") val noteFor: String = "",
    @SerialName("note_hint") val noteHint: String = "",
)

@Serializable
data class PendingQuestionDto(
    @SerialName("request_id") val requestId: String = "",
    val questions: List<QuestionDto> = emptyList(),
)

/**
 * One job event. `kind` is text | tool | init | result | permission |
 * permission_resolved | question | question_resolved; the fields that matter
 * differ per kind, and unknown kinds are simply ignored by the UI.
 */
@Serializable
data class JobEventDto(
    val seq: Int = 0,
    val kind: String = "",
    val text: String = "",
    val name: String = "",
    val detail: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val model: String = "",
    @SerialName("is_error") val isError: Boolean = false,
    @SerialName("request_id") val requestId: String = "",
    val allow: Boolean = false,
    val cancelled: Boolean = false,
    @SerialName("tool_name") val toolName: String = "",
    val questions: List<QuestionDto> = emptyList(),
    /** Present but unused: the phone renders `text` as markdown itself. */
    val blocks: JsonElement? = null,
)

@Serializable
data class JobSnapshotDto(
    val id: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("new_session_id") val newSessionId: String = "",
    /** starting | running | done | error | stopped */
    val status: String = "",
    val error: String = "",
    @SerialName("result_text") val resultText: String = "",
    @SerialName("pending_permission") val pendingPermission: PendingPermissionDto? = null,
    @SerialName("pending_question") val pendingQuestion: PendingQuestionDto? = null,
    val queued: List<QueuedDto> = emptyList(),
    @SerialName("next_job_id") val nextJobId: String = "",
    @SerialName("dropped_queued") val droppedQueued: Int = 0,
    @SerialName("next_seq") val nextSeq: Int = 0,
    val events: List<JobEventDto> = emptyList(),
) {
    val finished: Boolean get() = status == "done" || status == "error" || status == "stopped"
}

/** One entry of the /sse/status frame's `active` list. */
@Serializable
data class ActiveJobDto(
    @SerialName("job_id") val jobId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("new_session_id") val newSessionId: String = "",
    val status: String = "",
    val prompt: String = "",
    @SerialName("elapsed_s") val elapsedS: Int = 0,
    @SerialName("queued_count") val queuedCount: Int = 0,
    val tool: String = "",
    @SerialName("tool_detail") val toolDetail: String = "",
    val phase: String = "",
    @SerialName("phase_detail") val phaseDetail: String = "",
    @SerialName("pending_permission") val pendingPermission: Boolean = false,
    @SerialName("pending_question") val pendingQuestion: Boolean = false,
    /** Doorbell: fetch /api/jobs/<id>?since=N only when this passes the cursor. */
    @SerialName("next_seq") val nextSeq: Int = -1,
) {
    /** Every session id this job could be filed under. */
    fun sessionIds(): List<String> =
        listOf(sessionId, newSessionId).filter { it.isNotEmpty() }
}

@Serializable
data class StatusFrameDto(
    val type: String = "",
    val active: List<ActiveJobDto> = emptyList(),
)

@Serializable
data class UsageBucketDto(
    val title: String = "",
    val percent: Int = 0,
    @SerialName("resets_text") val resetsText: String = "",
    /** normal | warning | critical */
    val severity: String = "normal",
    /** Multi-daemon root tags each bucket with the harness name. */
    val provider: String = "",
    /** Logged-in account label (email when known) for cross-host dedup. */
    val account: String = "",
    @SerialName("account_id") val accountId: String = "",
)

@Serializable
data class UsageSectionDto(
    val provider: String = "",
    val ok: Boolean = true,
    val error: String = "",
    val buckets: List<UsageBucketDto> = emptyList(),
    /** Display label for the seat (email preferred). */
    val account: String = "",
    /** Stable seat id for merging the same login across hosts. */
    @SerialName("account_id") val accountId: String = "",
)

@Serializable
data class UsageDto(
    val ok: Boolean = true,
    val error: String = "",
    val buckets: List<UsageBucketDto> = emptyList(),
    /** Multi-harness host: one section per provider (claude / grok / …). */
    val multi: Boolean = false,
    val sections: List<UsageSectionDto> = emptyList(),
    /** Single-provider hosts stamp the harness + account on the root too. */
    val provider: String = "",
    val account: String = "",
    @SerialName("account_id") val accountId: String = "",
)

@Serializable
data class DropFileDto(
    val name: String = "",
    val size: Long = 0,
    val mtime: Long = 0,
)

@Serializable
data class DropListDto(
    val path: String = "",
    val files: List<DropFileDto> = emptyList(),
)

@Serializable
data class JobStartedDto(@SerialName("job_id") val jobId: String = "")

/** GET /api/sessions/<id>/tui — host tmux pane frame. */
@Serializable
data class TuiFrameDto(
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("job_id") val jobId: String = "",
    val attached: Boolean = false,
    val text: String = "",
    val seq: Long = 0,
    val cols: Int = 0,
    val rows: Int = 0,
    /** True when the client requested ?ansi=1 and the daemon kept SGR. */
    val ansi: Boolean = false,
    val error: String = "",
    val ts: Double = 0.0,
)

@Serializable
data class AttachmentDto(
    val ok: Boolean = false,
    val path: String = "",
    val size: Long = 0,
    val complete: Boolean = true,
)

@Serializable
data class ShellResultDto(
    val ok: Boolean = false,
    val output: String = "",
    @SerialName("exit_code") val exitCode: Int = 0,
    val cwd: String = "",
)

@Serializable
data class ErrorDto(val error: String = "")

/** Capability keys /api/ping reports. Read through [Caps]. */
object Cap {
    const val QUEUE = "queue"
    const val STOP = "stop"
    const val PROJECTS = "projects"
    const val PERMISSIONS = "permissions"
    const val PERMISSION_MODES = "permission_modes"
    const val REQUIRES_CWD = "requires_cwd"
    const val CAN_SET_MODEL = "can_set_model"
    const val CAN_SET_EFFORT = "can_set_effort"
    const val CAN_SHOW_USAGE = "can_show_usage"
    const val INTERACTIVE = "interactive"
    const val LIVE_TUI = "live_tui"
    const val REWIND = "rewind"
}

/**
 * Everything /api/ping told us about one daemon, cached so the UI does not
 * flicker between app start and the first reply.
 */
@Serializable
data class Caps(
    val version: String = "",
    val host: String = "",
    val provider: String = "",
    val flags: Map<String, Boolean> = emptyMap(),
    val slashCommands: List<String> = emptyList(),
    val models: List<String> = emptyList(),
    val efforts: List<String> = emptyList(),
    val dropPath: String = "",
    val fetchedAtMs: Long = 0,
    val multi: Boolean = false,
    /** Focus support (agentremoted >= 2.6); gates Focus mode. */
    val focus: Boolean = false,
    /** Session share (agentremoted >= 2.7). */
    val share: Boolean = false,
    /** Chunked attachments (agentremoted >= 2.8.6). */
    val chunkedUpload: Boolean = false,
    val providers: List<String> = emptyList(),
    val providerDetails: Map<String, ProviderDetailDto> = emptyMap(),
    val auth: AuthHealthDto? = null,
) {
    operator fun get(key: String): Boolean = flags[key] == true

    val queue: Boolean get() = this[Cap.QUEUE]
    val permissions: Boolean get() = this[Cap.PERMISSIONS]
    val requiresCwd: Boolean get() = flags[Cap.REQUIRES_CWD] ?: (provider != "grok")
    val canSetModel: Boolean get() = this[Cap.CAN_SET_MODEL]
    val canSetEffort: Boolean get() = this[Cap.CAN_SET_EFFORT]
    val canShowUsage: Boolean
        get() {
            if (multi && providerDetails.isNotEmpty()) {
                return providerDetails.values.any { it.caps[Cap.CAN_SHOW_USAGE] == true }
            }
            return this[Cap.CAN_SHOW_USAGE]
        }
    val interactive: Boolean get() = this[Cap.INTERACTIVE]
    val liveTui: Boolean get() = this[Cap.LIVE_TUI] || interactive
    val rewind: Boolean get() = this[Cap.REWIND]

    fun liveTuiFor(harness: String?): Boolean {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        val flag = detail?.caps?.get(Cap.LIVE_TUI)
        if (flag != null) return flag
        return detail?.caps?.get(Cap.INTERACTIVE) ?: liveTui
    }

    /** Harnesses this host fronts (multi) or the single known provider. */
    fun harnesses(): List<String> {
        // Prefer the multi catalogue even if an older cache omitted multi=true.
        if (providers.size > 1) return providers
        if (multi && providers.isNotEmpty()) return providers
        if (providerDetails.size > 1) return providerDetails.keys.sorted()
        if (provider.isNotBlank()) return listOf(provider)
        if (providers.isNotEmpty()) return providers
        return emptyList()
    }

    val isMulti: Boolean
        get() = multi || providers.size > 1 || providerDetails.size > 1

    fun requiresCwd(harness: String?): Boolean {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        val flag = detail?.caps?.get(Cap.REQUIRES_CWD)
        if (flag != null) return flag
        return flags[Cap.REQUIRES_CWD] ?: (h != "grok" && provider != "grok")
    }

    /**
     * Can THIS harness rewind (daemon ≥ 2.5 does it via session-file
     * surgery, so both exec modes qualify)? A multi-harness host reports a
     * union at its root — gating a session's UI on the root flag would
     * offer an action an old daemon cannot perform.
     */
    fun rewindFor(harness: String?): Boolean {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        val flag = detail?.caps?.get(Cap.REWIND)
        if (flag != null) return flag
        return rewind
    }

    /** Slash commands THIS harness advertises (the only whitelist there is). */
    fun slashFor(harness: String?): List<String> {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        return detail?.slashCommands?.takeIf { it.isNotEmpty() } ?: slashCommands
    }

    fun modelsFor(harness: String?): List<String> {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        return detail?.models?.takeIf { it.isNotEmpty() } ?: models
    }

    fun effortsFor(harness: String?): List<String> {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        return detail?.efforts?.takeIf { it.isNotEmpty() } ?: efforts
    }

    /** Multi: read can_set_model from the harness detail, not only the root union. */
    fun canSetModelFor(harness: String?): Boolean {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        val flag = detail?.caps?.get(Cap.CAN_SET_MODEL)
        if (flag != null) return flag
        // Fall back: if this harness (or root) advertises a model list, show the picker.
        if (modelsFor(h.ifBlank { null }).isNotEmpty()) return true
        return canSetModel
    }

    fun canSetEffortFor(harness: String?): Boolean {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        val flag = detail?.caps?.get(Cap.CAN_SET_EFFORT)
        if (flag != null) return flag
        if (effortsFor(h.ifBlank { null }).isNotEmpty()) return true
        return canSetEffort
    }

    fun interactiveFor(harness: String?): Boolean {
        val h = harness?.lowercase().orEmpty()
        val detail = if (h.isNotEmpty()) providerDetails[h] else null
        return detail?.caps?.get(Cap.INTERACTIVE) ?: interactive
    }

    companion object {
        fun from(ping: PingDto, nowMs: Long) = Caps(
            version = ping.version,
            host = ping.host,
            provider = ping.provider,
            flags = ping.caps,
            slashCommands = ping.slashCommands,
            models = ping.models,
            efforts = ping.efforts,
            dropPath = ping.dropPath,
            fetchedAtMs = nowMs,
            multi = ping.multi,
            focus = ping.focus,
            share = ping.share,
            chunkedUpload = ping.chunkedUpload || ping.caps["chunked_upload"] == true,
            providers = ping.providers,
            providerDetails = ping.providerDetails,
            auth = ping.auth,
        )
    }
}

/**
 * Execution modes the composer can send with a prompt.
 *
 * UI is only **Interactive** (host TUI) or **Headless** (CLI). Both always
 * bypass tool permissions — no acceptEdits / ask / plan picker.
 * Wire API still uses `bypassPermissions` for headless (daemon contract).
 */
object ExecMode {
    const val INTERACTIVE = "interactive"
    const val HEADLESS = "headless"
    /** @deprecated Wire value for headless; prefer [HEADLESS] in UI storage. */
    const val BYPASS = "bypassPermissions"

    /** Modes shown in pickers (Interactive only if the harness supports TUI). */
    fun options(canInteractive: Boolean): List<String> =
        if (canInteractive) listOf(INTERACTIVE, HEADLESS) else listOf(HEADLESS)

    /** Normalize stored / legacy values to interactive | headless. */
    fun normalize(mode: String?, canInteractive: Boolean = true): String {
        val m = mode?.trim().orEmpty()
        if (m == INTERACTIVE && canInteractive) return INTERACTIVE
        // Legacy: bypassPermissions | acceptEdits | default | plan | Auto …
        return HEADLESS
    }

    /** Value for `permission_mode` on the HTTP API. */
    fun wire(mode: String?): String =
        if (normalize(mode) == INTERACTIVE) INTERACTIVE else BYPASS

    fun label(mode: String): String = when (normalize(mode)) {
        INTERACTIVE -> "Interactive"
        else -> "Headless"
    }

    fun short(mode: String): String = label(mode)
}
