package com.bb10d.remote.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/*
 * Wire types for the agentremoted HTTP API (daemon/agentremoted/server.py).
 * The API is the same for every harness; /api/ping reports which ones answer
 * and what they can do, so every feature here is gated on caps, never on a
 * build flavour.
 *
 * Everything is defaulted: an older daemon that predates a field must degrade
 * to "feature off", not to a parse error.
 */

@Serializable
data class ProviderDetailDto(
    val caps: Map<String, Boolean> = emptyMap(),
    @SerialName("slash_commands") val slashCommands: List<String> = emptyList(),
    val models: List<String> = emptyList(),
    val efforts: List<String> = emptyList(),
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
    val providers: List<String> = emptyList(),
    @SerialName("provider_details")
    val providerDetails: Map<String, ProviderDetailDto> = emptyMap(),
)

@Serializable
data class ProjectDto(
    val id: String = "",
    val cwd: String = "",
    val name: String = "",
    @SerialName("session_count") val sessionCount: Int = 0,
    @SerialName("last_active") val lastActive: Double = 0.0,
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
)

@Serializable
data class SessionsDto(val sessions: List<SessionDto> = emptyList())

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
)

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
)

@Serializable
data class UsageSectionDto(
    val provider: String = "",
    val ok: Boolean = true,
    val error: String = "",
    val buckets: List<UsageBucketDto> = emptyList(),
)

@Serializable
data class UsageDto(
    val ok: Boolean = true,
    val error: String = "",
    val buckets: List<UsageBucketDto> = emptyList(),
    /** Multi-harness host: one section per provider (claude / grok / …). */
    val multi: Boolean = false,
    val sections: List<UsageSectionDto> = emptyList(),
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

@Serializable
data class AttachmentDto(
    val ok: Boolean = false,
    val path: String = "",
    val size: Long = 0,
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
    val providers: List<String> = emptyList(),
    val providerDetails: Map<String, ProviderDetailDto> = emptyMap(),
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
    val rewind: Boolean get() = this[Cap.REWIND]

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
     * Can THIS harness rewind? claude and grok can, codex cannot, and a
     * multi-harness host reports a union at its root — so gating a session's
     * UI on the root flag offers an action that would fail.
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
            providers = ping.providers,
            providerDetails = ping.providerDetails,
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
