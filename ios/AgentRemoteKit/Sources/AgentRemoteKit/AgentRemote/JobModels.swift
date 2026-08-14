import Foundation

public enum JobStatus: String, Codable, Sendable, Equatable {
    case starting, running, done, error, stopped

    /// A status string this client doesn't know must not fail the whole snapshot/status-frame
    /// decode — treat it as still-running (the daemon's own status stream does the same coercion).
    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? ""
        self = JobStatus(rawValue: raw) ?? .running
    }
}

/// One entry in `queued` — a prompt chained behind a still-running job via `POST /api/jobs/<id>/queue`.
public struct QueuedPrompt: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let prompt: String

    public init(id: String, prompt: String) {
        self.id = id
        self.prompt = prompt
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        prompt = try c.decodeIfPresent(String.self, forKey: .prompt) ?? ""
    }

    private enum CodingKeys: String, CodingKey { case id, prompt }
}

/// Mirrors the job's `pending_permission` field: set for as long as a `canUseTool`-style approval
/// is outstanding, `nil` once resolved. The identical data is also emitted as a `permission` event.
public struct PendingPermission: Codable, Sendable, Equatable {
    public let requestId: String
    public let toolName: String
    /// One pre-formatted display string (already clipped/whitespace-collapsed server-side) —
    /// there is no structured tool input in this protocol, unlike the old daemon's `toolInput`.
    public let detail: String

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        requestId = try c.decodeIfPresent(String.self, forKey: .requestId) ?? ""
        toolName = try c.decodeIfPresent(String.self, forKey: .toolName) ?? ""
        detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
    }

    private enum CodingKeys: String, CodingKey { case requestId, toolName, detail }
}

public struct QuestionOption: Codable, Sendable, Equatable {
    public let label: String
    public let description: String

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? ""
        description = try c.decodeIfPresent(String.self, forKey: .description) ?? ""
    }

    private enum CodingKeys: String, CodingKey { case label, description }
}

public struct QuestionItem: Codable, Sendable, Equatable {
    public let question: String
    public let header: String
    public let options: [QuestionOption]
    public let multiSelect: Bool
    /// Option label that opens a free-text field (e.g. Grok "Request changes").
    public let noteFor: String
    public let noteHint: String

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        question = try c.decodeIfPresent(String.self, forKey: .question) ?? ""
        header = try c.decodeIfPresent(String.self, forKey: .header) ?? ""
        options = try c.decodeIfPresent([QuestionOption].self, forKey: .options) ?? []
        multiSelect = try c.decodeIfPresent(Bool.self, forKey: .multiSelect) ?? false
        noteFor = try c.decodeIfPresent(String.self, forKey: .noteFor) ?? ""
        noteHint = try c.decodeIfPresent(String.self, forKey: .noteHint) ?? ""
    }

    private enum CodingKeys: String, CodingKey {
        case question, header, options, multiSelect, noteFor, noteHint
    }
}

/// Mirrors the job's `pending_question` field (AskUserQuestion).
public struct PendingQuestion: Codable, Sendable, Equatable {
    public let requestId: String
    public let questions: [QuestionItem]

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        requestId = try c.decodeIfPresent(String.self, forKey: .requestId) ?? ""
        // Prefer structured list; fall back to empty if an old shape arrives.
        if let list = try? c.decode([QuestionItem].self, forKey: .questions) {
            questions = list
        } else {
            questions = []
        }
    }

    private enum CodingKeys: String, CodingKey { case requestId, questions }
}

/// `GET /api/jobs/<id>?since=<seq>` — polling, not a real long-poll: returns whatever is buffered
/// right now. `since` is a plain 0-based event-array index; poll again with `since: nextSeq`.
public struct JobSnapshot: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    /// The session this job resumed (empty for a brand-new session).
    public let sessionId: String
    /// The real Claude session id once known, from the job's `init` event. Track "the session id"
    /// as `newSessionId.isEmpty ? sessionId : newSessionId` — the exact rule the daemon itself uses.
    public let newSessionId: String
    public let status: JobStatus
    public let error: String
    /// Only populated once `status` is `.done`/`.error` — empty string while running.
    public let resultText: String
    public let pendingPermission: PendingPermission?
    public let pendingQuestion: PendingQuestion?
    public let queued: [QueuedPrompt]
    /// Set when this job finished with a non-empty queue — the daemon auto-starts the next queued
    /// prompt as a brand-new job resuming the same session. Follow this to keep watching the chain;
    /// this job's own polling dead-ends once it's set.
    public let nextJobId: String
    /// How many queued prompts were dropped because this job ended in error/stopped instead of
    /// finishing cleanly — surface this to the user rather than silently losing queued messages.
    public let droppedQueued: Int
    public let nextSeq: Int
    public let events: [JobEvent]

    public var resolvedSessionId: String { newSessionId.isEmpty ? sessionId : newSessionId }

    public var finished: Bool { status == .done || status == .error || status == .stopped }

    /// Defensive throughout: a missing field on an older/newer daemon must degrade to a default,
    /// not fail the poll loop with a decode error the user can't act on.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decodeIfPresent(String.self, forKey: .id) ?? ""
        sessionId = try c.decodeIfPresent(String.self, forKey: .sessionId) ?? ""
        newSessionId = try c.decodeIfPresent(String.self, forKey: .newSessionId) ?? ""
        status = try c.decodeIfPresent(JobStatus.self, forKey: .status) ?? .running
        error = try c.decodeIfPresent(String.self, forKey: .error) ?? ""
        resultText = try c.decodeIfPresent(String.self, forKey: .resultText) ?? ""
        pendingPermission = try c.decodeIfPresent(PendingPermission.self, forKey: .pendingPermission)
        pendingQuestion = try c.decodeIfPresent(PendingQuestion.self, forKey: .pendingQuestion)
        queued = try c.decodeIfPresent([QueuedPrompt].self, forKey: .queued) ?? []
        nextJobId = try c.decodeIfPresent(String.self, forKey: .nextJobId) ?? ""
        droppedQueued = try c.decodeIfPresent(Int.self, forKey: .droppedQueued) ?? 0
        nextSeq = try c.decodeIfPresent(Int.self, forKey: .nextSeq) ?? 0
        events = try c.decodeIfPresent([JobEvent].self, forKey: .events) ?? []
    }

    private enum CodingKeys: String, CodingKey {
        case id, sessionId, newSessionId, status, error, resultText
        case pendingPermission, pendingQuestion, queued, nextJobId, droppedQueued
        case nextSeq, events
    }
}

/// One event in a job's `events` array. Every case carries `seq`. `blocks` fields present in the
/// daemon's `text`/`tool` events (a bespoke HTML-ish rendering dialect for the BlackBerry client)
/// are intentionally not modeled — always prefer the plain-text fields here.
public enum JobEvent: Sendable, Equatable {
    case initEvent(seq: Int, sessionId: String, model: String)
    /// One complete markdown text block (headless `stream-json`, not token deltas — can fire more
    /// than once per turn if the model emits multiple text blocks).
    case text(seq: Int, text: String)
    /// A tool call with no structured input and (in headless mode) no corresponding result event —
    /// `detail` is one pre-formatted, already-clipped display string, not raw arguments.
    case tool(seq: Int, name: String, detail: String)
    /// Terminal, sentinel-style: at most one per job, always last if the job actually ran a turn.
    case result(seq: Int, isError: Bool, durationMs: Int, costUsd: Double)
    case permission(seq: Int, requestId: String, toolName: String, detail: String)
    /// `allow == false` with `reason == "timeout"` when the phone never answered within the
    /// daemon's `permission_timeout` (default 300s).
    case permissionResolved(seq: Int, requestId: String, allow: Bool, reason: String?)
    case question(seq: Int, requestId: String, questions: [QuestionItem])
    case questionResolved(seq: Int, requestId: String, cancelled: Bool, answers: JSONValue?)
    /// Forward-compat catch-all for an event kind this client doesn't know about yet.
    case unknown(seq: Int, kind: String, payload: JSONValue)

    public var seq: Int {
        switch self {
        case .initEvent(let seq, _, _), .text(let seq, _), .tool(let seq, _, _),
             .result(let seq, _, _, _), .permission(let seq, _, _, _),
             .permissionResolved(let seq, _, _, _), .question(let seq, _, _),
             .questionResolved(let seq, _, _, _), .unknown(let seq, _, _):
            return seq
        }
    }
}

extension JobEvent: Codable {
    private enum CodingKeys: String, CodingKey {
        case seq, kind, sessionId, model, text, name, detail, isError, durationMs, costUsd,
             requestId, toolName, allow, reason, questions, cancelled, answers
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let seq = try c.decodeIfPresent(Int.self, forKey: .seq) ?? 0
        let kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? ""
        switch kind {
        case "init":
            self = .initEvent(
                seq: seq,
                sessionId: try c.decodeIfPresent(String.self, forKey: .sessionId) ?? "",
                model: try c.decodeIfPresent(String.self, forKey: .model) ?? ""
            )
        case "text":
            self = .text(seq: seq, text: try c.decodeIfPresent(String.self, forKey: .text) ?? "")
        case "tool":
            self = .tool(
                seq: seq,
                name: try c.decodeIfPresent(String.self, forKey: .name) ?? "",
                detail: try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
            )
        case "result":
            self = .result(
                seq: seq,
                isError: try c.decodeIfPresent(Bool.self, forKey: .isError) ?? false,
                durationMs: try c.decodeIfPresent(Int.self, forKey: .durationMs) ?? 0,
                costUsd: try c.decodeIfPresent(Double.self, forKey: .costUsd) ?? 0
            )
        case "permission":
            self = .permission(
                seq: seq,
                requestId: try c.decodeIfPresent(String.self, forKey: .requestId) ?? "",
                toolName: try c.decodeIfPresent(String.self, forKey: .toolName) ?? "",
                detail: try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
            )
        case "permission_resolved":
            self = .permissionResolved(
                seq: seq,
                requestId: try c.decodeIfPresent(String.self, forKey: .requestId) ?? "",
                allow: try c.decodeIfPresent(Bool.self, forKey: .allow) ?? false,
                reason: try c.decodeIfPresent(String.self, forKey: .reason)
            )
        case "question":
            let requestId = try c.decodeIfPresent(String.self, forKey: .requestId) ?? ""
            let questions = (try? c.decode([QuestionItem].self, forKey: .questions)) ?? []
            self = .question(seq: seq, requestId: requestId, questions: questions)
        case "question_resolved":
            self = .questionResolved(
                seq: seq,
                requestId: try c.decodeIfPresent(String.self, forKey: .requestId) ?? "",
                cancelled: try c.decodeIfPresent(Bool.self, forKey: .cancelled) ?? false,
                answers: try c.decodeIfPresent(JSONValue.self, forKey: .answers)
            )
        default:
            self = .unknown(seq: seq, kind: kind, payload: try JSONValue(from: decoder))
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(seq, forKey: .seq)
        switch self {
        case .initEvent(_, let sessionId, let model):
            try c.encode("init", forKey: .kind)
            try c.encode(sessionId, forKey: .sessionId)
            try c.encode(model, forKey: .model)
        case .text(_, let text):
            try c.encode("text", forKey: .kind)
            try c.encode(text, forKey: .text)
        case .tool(_, let name, let detail):
            try c.encode("tool", forKey: .kind)
            try c.encode(name, forKey: .name)
            try c.encode(detail, forKey: .detail)
        case .result(_, let isError, let durationMs, let costUsd):
            try c.encode("result", forKey: .kind)
            try c.encode(isError, forKey: .isError)
            try c.encode(durationMs, forKey: .durationMs)
            try c.encode(costUsd, forKey: .costUsd)
        case .permission(_, let requestId, let toolName, let detail):
            try c.encode("permission", forKey: .kind)
            try c.encode(requestId, forKey: .requestId)
            try c.encode(toolName, forKey: .toolName)
            try c.encode(detail, forKey: .detail)
        case .permissionResolved(_, let requestId, let allow, let reason):
            try c.encode("permission_resolved", forKey: .kind)
            try c.encode(requestId, forKey: .requestId)
            try c.encode(allow, forKey: .allow)
            try c.encodeIfPresent(reason, forKey: .reason)
        case .question(_, let requestId, let questions):
            try c.encode("question", forKey: .kind)
            try c.encode(requestId, forKey: .requestId)
            // questions is [QuestionItem]; encode as JSON array of objects via JSONEncoder cascade
            try c.encode(questions, forKey: .questions)
        case .questionResolved(_, let requestId, let cancelled, let answers):
            try c.encode("question_resolved", forKey: .kind)
            try c.encode(requestId, forKey: .requestId)
            try c.encode(cancelled, forKey: .cancelled)
            try c.encodeIfPresent(answers, forKey: .answers)
        case .unknown(_, let kind, _):
            try c.encode(kind, forKey: .kind)
        }
    }
}
