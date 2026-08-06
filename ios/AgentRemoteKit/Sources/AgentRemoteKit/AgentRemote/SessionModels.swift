import Foundation

/// One row from `GET /api/sessions`, `GET /api/sessions/<id>`, or a search result (which adds
/// `snippet`). `title` is an AI-generated short summary that can change on a later poll of the
/// same session (starts as a first-message preview until the real title finishes generating).
public struct SessionSummary: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let projectId: String
    public let cwd: String
    public let gitBranch: String
    public let title: String
    public let started: FlexibleDate
    public let lastActive: FlexibleDate
    public let lastRole: String
    /// Truncated preview of the most recent message — not the full text.
    public let lastText: String
    public let model: String
    public let sizeBytes: Int
}

public struct SessionsResponse: Codable, Sendable, Equatable {
    public let sessions: [SessionSummary]
}

public struct SessionSearchResult: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let projectId: String
    public let cwd: String
    public let gitBranch: String
    public let title: String
    public let started: FlexibleDate
    public let lastActive: FlexibleDate
    public let lastRole: String
    public let lastText: String
    public let model: String
    public let sizeBytes: Int
    /// The matched line/snippet that made this session show up in the search.
    public let snippet: String
}

public struct SessionSearchResponse: Codable, Sendable, Equatable {
    public let query: String
    public let results: [SessionSearchResult]
}

/// One turn from `GET /api/sessions/<id>/messages`. `role` is only ever `"user"` or `"assistant"` —
/// the daemon explicitly drops every other JSONL record type and never persists tool-call
/// activity (it's treated as transient job state, not part of the durable transcript). There is no
/// way to recover a past turn's tool calls/results once its job is pruned — resuming a session
/// shows text-only history, by daemon design, not a client limitation to work around.
public struct SessionMessage: Codable, Sendable, Equatable, Identifiable {
    public let uuid: String
    public let role: String
    public let ts: FlexibleDate
    public let text: String
    // `blocks` (a bespoke HTML-ish dialect for the BlackBerry client) is intentionally not modeled
    // — always use `text` (plain markdown) with a native renderer.

    public var id: String { uuid }
}

public struct MessagesResponse: Codable, Sendable, Equatable {
    public let sessionId: String
    public let total: Int
    public let offset: Int
    public let messages: [SessionMessage]
}
