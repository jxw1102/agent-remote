import Foundation

/// `POST /api/sessions/new` body. `cwd` is required whenever `caps.requiresCwd` is true (always,
/// for the claude provider). `permissionMode`: `""` (daemon default), `"acceptEdits"`,
/// `"bypassPermissions"`, `"plan"`, `"interactive"` (hosts a tmux TUI instead of running headless).
public struct NewSessionRequest: Codable, Sendable, Equatable {
    public var cwd: String?
    public var prompt: String
    public var provider: String?
    public var permissionMode: String?
    public var model: String?
    public var effort: String?

    public init(cwd: String? = nil, prompt: String, provider: String? = nil, permissionMode: String? = nil, model: String? = nil, effort: String? = nil) {
        self.cwd = cwd
        self.prompt = prompt
        self.provider = provider
        self.permissionMode = permissionMode
        self.model = model
        self.effort = effort
    }
}

/// `POST /api/sessions/<id>/continue` body — "resume and send a message" in one call. There is no
/// bare "open with no message" endpoint; viewing an existing session needs only `GET .../messages`.
public struct ContinueSessionRequest: Codable, Sendable, Equatable {
    public var prompt: String
    public var permissionMode: String?
    public var model: String?
    public var effort: String?

    public init(prompt: String, permissionMode: String? = nil, model: String? = nil, effort: String? = nil) {
        self.prompt = prompt
        self.permissionMode = permissionMode
        self.model = model
        self.effort = effort
    }
}

public struct JobIdResponse: Codable, Sendable, Equatable {
    public let jobId: String
}

public struct QueuePromptRequest: Codable, Sendable, Equatable {
    public var prompt: String
    public init(prompt: String) { self.prompt = prompt }
}

public struct QueueResponse: Codable, Sendable, Equatable {
    public let queued: [QueuedPrompt]
}

public struct CancelQueuedResponse: Codable, Sendable, Equatable {
    public let ok: Bool
    public let queued: [QueuedPrompt]
    public let prompt: String
}

public struct PermissionDecisionRequest: Codable, Sendable, Equatable {
    public var requestId: String
    public var allow: Bool
    public init(requestId: String, allow: Bool) {
        self.requestId = requestId
        self.allow = allow
    }
}

/// `POST /api/jobs/<id>/question`. `answers` is one list of chosen option labels per question
/// (single-select carries exactly one label) — omit and set `cancel: true` to dismiss the panel
/// without answering. `notes` is optional free text per question. Verified against
/// `_handle_question_answer` in server.py.
public struct QuestionAnswerRequest: Codable, Sendable, Equatable {
    public var requestId: String
    public var answers: [[String]]?
    public var notes: [String]?
    public var cancel: Bool?

    public init(requestId: String, answers: [[String]]? = nil, notes: [String]? = nil, cancel: Bool? = nil) {
        self.requestId = requestId
        self.answers = answers
        self.notes = notes
        self.cancel = cancel
    }
}

public struct StopJobResponse: Codable, Sendable, Equatable {
    public let ok: Bool
}

public struct InputPromptRequest: Codable, Sendable, Equatable {
    public var prompt: String
    public init(prompt: String) { self.prompt = prompt }
}

/// `{"error": "..."}` — the daemon's uniform error body shape across every endpoint (a 404 on an
/// unknown route uses the identical shape to a 404 on a known-but-missing resource; the client
/// can only tell them apart by which endpoint it called, not by this body).
public struct DaemonErrorBody: Codable, Sendable, Equatable {
    public let error: String
}
