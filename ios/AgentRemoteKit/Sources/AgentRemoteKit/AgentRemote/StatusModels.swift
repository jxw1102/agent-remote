import Foundation

/// One entry in a `/ws/status` or `/sse/status` push. Normally only `starting`/`running` jobs are
/// listed, but a job that finished while an AskUserQuestion / permission gate is still open stays
/// in the feed (daemon ≥ 2.6.5, forced to `running` + `phase: asking`) so clients keep the Answer
/// UI alive. `nextSeq` is the "doorbell": compare against the last-polled cursor for this job id
/// and only `GET /api/jobs/<id>?since=` when it has grown, instead of polling every open
/// session's full detail on a fixed timer.
public struct ActiveJobStatus: Codable, Sendable, Equatable, Identifiable {
    public let jobId: String
    public let sessionId: String
    public let newSessionId: String
    public let status: JobStatus
    /// First ~120 characters of the prompt that started/continued this job.
    public let prompt: String
    public let cwd: String
    public let provider: String
    public let elapsedS: Int
    public let queuedCount: Int
    /// Last tool name (banner line 2 lead) — the raw command/path lives in `toolDetail`.
    public let tool: String?
    public let toolDetail: String?
    /// Coarse activity phase (working | thinking | tool | asking | permission | …).
    public let phase: String?
    /// Human description for the banner's first line (daemon ≥ 2.6.4 sends description here and
    /// the raw command via `toolDetail`).
    public let phaseDetail: String?
    public let pendingPermission: Bool
    public let pendingQuestion: Bool
    /// -1 when the daemon is too old to send it — fall back to timed polling.
    public let nextSeq: Int

    public var id: String { jobId }
    public var resolvedSessionId: String { newSessionId.isEmpty ? sessionId : newSessionId }

    /// Defensive: one odd frame must not silently kill the status stream.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobId = try c.decodeIfPresent(String.self, forKey: .jobId) ?? ""
        sessionId = try c.decodeIfPresent(String.self, forKey: .sessionId) ?? ""
        newSessionId = try c.decodeIfPresent(String.self, forKey: .newSessionId) ?? ""
        status = try c.decodeIfPresent(JobStatus.self, forKey: .status) ?? .running
        prompt = try c.decodeIfPresent(String.self, forKey: .prompt) ?? ""
        cwd = try c.decodeIfPresent(String.self, forKey: .cwd) ?? ""
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? ""
        elapsedS = try c.decodeIfPresent(Int.self, forKey: .elapsedS) ?? 0
        queuedCount = try c.decodeIfPresent(Int.self, forKey: .queuedCount) ?? 0
        tool = try c.decodeIfPresent(String.self, forKey: .tool)
        toolDetail = try c.decodeIfPresent(String.self, forKey: .toolDetail)
        phase = try c.decodeIfPresent(String.self, forKey: .phase)
        phaseDetail = try c.decodeIfPresent(String.self, forKey: .phaseDetail)
        pendingPermission = try c.decodeIfPresent(Bool.self, forKey: .pendingPermission) ?? false
        pendingQuestion = try c.decodeIfPresent(Bool.self, forKey: .pendingQuestion) ?? false
        nextSeq = try c.decodeIfPresent(Int.self, forKey: .nextSeq) ?? -1
    }

    private enum CodingKeys: String, CodingKey {
        case jobId, sessionId, newSessionId, status, prompt, cwd, provider
        case elapsedS, queuedCount, tool, toolDetail, phase, phaseDetail
        case pendingPermission, pendingQuestion, nextSeq
    }
}

/// Both `/ws/status` and `/sse/status` push this identical payload, ~1x/sec, only when it changes.
public struct StatusPush: Codable, Sendable, Equatable {
    public let type: String
    public let active: [ActiveJobStatus]

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        type = try c.decodeIfPresent(String.self, forKey: .type) ?? ""
        active = try c.decodeIfPresent([ActiveJobStatus].self, forKey: .active) ?? []
    }

    private enum CodingKeys: String, CodingKey { case type, active }
}
