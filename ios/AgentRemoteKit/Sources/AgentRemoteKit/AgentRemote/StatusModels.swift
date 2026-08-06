import Foundation

/// One entry in a `/ws/status` or `/sse/status` push — only `starting`/`running` jobs are listed;
/// a finished job vanishes from `active` the instant it's done. This is a "what's happening right
/// now" feed, not history. `nextSeq` is the "doorbell": compare against the last-polled cursor for
/// this job id and only `GET /api/jobs/<id>?since=` when it has grown, instead of polling every
/// open session's full detail on a fixed timer.
public struct ActiveJobStatus: Codable, Sendable, Equatable, Identifiable {
    public let jobId: String
    public let sessionId: String
    public let newSessionId: String
    public let status: JobStatus
    /// First ~120 characters of the prompt that started/continued this job.
    public let prompt: String
    public let elapsedS: Int
    public let queuedCount: Int
    public let tool: String?
    public let toolDetail: String?
    public let phase: String?
    public let phaseDetail: String?
    public let pendingPermission: Bool
    public let pendingQuestion: Bool
    public let nextSeq: Int

    public var id: String { jobId }
    public var resolvedSessionId: String { newSessionId.isEmpty ? sessionId : newSessionId }
}

/// Both `/ws/status` and `/sse/status` push this identical payload, ~1x/sec, only when it changes.
public struct StatusPush: Codable, Sendable, Equatable {
    public let type: String
    public let active: [ActiveJobStatus]
}
