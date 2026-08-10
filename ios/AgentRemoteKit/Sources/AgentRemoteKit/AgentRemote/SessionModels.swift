import Foundation

/// Decode `size_bytes` whether the daemon emits an int or a float JSON number.
private func decodeSizeBytes<Key: CodingKey>(_ c: KeyedDecodingContainer<Key>, key: Key) -> Int {
    if let n = try? c.decodeIfPresent(Int.self, forKey: key) { return n ?? 0 }
    if let d = try? c.decodeIfPresent(Double.self, forKey: key) { return Int(d ?? 0) }
    return 0
}

/// One row from `GET /api/sessions`, `GET /api/sessions/<id>`, or a search result (which adds
/// `snippet`). Multi-harness daemons tag each row with `provider`.
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
    /// Multi-harness: claude | grok | codex. Empty on single-provider older daemons.
    public let provider: String
    /// Only on search results.
    public let snippet: String

    public init(
        id: String, projectId: String = "", cwd: String = "", gitBranch: String = "",
        title: String = "", started: FlexibleDate = FlexibleDate(.distantPast),
        lastActive: FlexibleDate = FlexibleDate(.distantPast), lastRole: String = "",
        lastText: String = "", model: String = "", sizeBytes: Int = 0,
        provider: String = "", snippet: String = ""
    ) {
        self.id = id; self.projectId = projectId; self.cwd = cwd; self.gitBranch = gitBranch
        self.title = title; self.started = started; self.lastActive = lastActive
        self.lastRole = lastRole; self.lastText = lastText; self.model = model
        self.sizeBytes = sizeBytes; self.provider = provider; self.snippet = snippet
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        projectId = try c.decodeIfPresent(String.self, forKey: .projectId) ?? ""
        cwd = try c.decodeIfPresent(String.self, forKey: .cwd) ?? ""
        gitBranch = try c.decodeIfPresent(String.self, forKey: .gitBranch) ?? ""
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        started = try c.decodeIfPresent(FlexibleDate.self, forKey: .started) ?? FlexibleDate(.distantPast)
        lastActive = try c.decodeIfPresent(FlexibleDate.self, forKey: .lastActive) ?? FlexibleDate(.distantPast)
        lastRole = try c.decodeIfPresent(String.self, forKey: .lastRole) ?? ""
        lastText = try c.decodeIfPresent(String.self, forKey: .lastText) ?? ""
        model = try c.decodeIfPresent(String.self, forKey: .model) ?? ""
        sizeBytes = decodeSizeBytes(c, key: .sizeBytes)
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? ""
        snippet = try c.decodeIfPresent(String.self, forKey: .snippet) ?? ""
    }

    private enum CodingKeys: String, CodingKey {
        case id, projectId, cwd, gitBranch, title, started, lastActive, lastRole
        case lastText, model, sizeBytes, provider, snippet
    }

    public var displayTitle: String {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed.lowercased() == "(no content)" { return "Untitled session" }
        return trimmed
    }
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
    public let snippet: String
    public let provider: String

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        projectId = try c.decodeIfPresent(String.self, forKey: .projectId) ?? ""
        cwd = try c.decodeIfPresent(String.self, forKey: .cwd) ?? ""
        gitBranch = try c.decodeIfPresent(String.self, forKey: .gitBranch) ?? ""
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        started = try c.decodeIfPresent(FlexibleDate.self, forKey: .started) ?? FlexibleDate(.distantPast)
        lastActive = try c.decodeIfPresent(FlexibleDate.self, forKey: .lastActive) ?? FlexibleDate(.distantPast)
        lastRole = try c.decodeIfPresent(String.self, forKey: .lastRole) ?? ""
        lastText = try c.decodeIfPresent(String.self, forKey: .lastText) ?? ""
        model = try c.decodeIfPresent(String.self, forKey: .model) ?? ""
        sizeBytes = decodeSizeBytes(c, key: .sizeBytes)
        snippet = try c.decodeIfPresent(String.self, forKey: .snippet) ?? ""
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? ""
    }

    private enum CodingKeys: String, CodingKey {
        case id, projectId, cwd, gitBranch, title, started, lastActive, lastRole
        case lastText, model, sizeBytes, snippet, provider
    }

    public func asSummary() -> SessionSummary {
        SessionSummary(
            id: id, projectId: projectId, cwd: cwd, gitBranch: gitBranch, title: title,
            started: started, lastActive: lastActive, lastRole: lastRole, lastText: lastText,
            model: model, sizeBytes: sizeBytes, provider: provider, snippet: snippet
        )
    }
}

public struct SessionSearchResponse: Codable, Sendable, Equatable {
    public let query: String
    public let results: [SessionSearchResult]
}

/// One turn from `GET /api/sessions/<id>/messages`. `role` is `"user"`, `"assistant"`, or
/// occasionally `"status"` (grok thought/worked lines). Tool activity is not persisted.
public struct SessionMessage: Codable, Sendable, Equatable, Identifiable {
    public let uuid: String
    public let role: String
    public let ts: FlexibleDate
    public let text: String
    public let metaKind: String?

    public var id: String { uuid }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        uuid = try c.decodeIfPresent(String.self, forKey: .uuid) ?? UUID().uuidString
        role = try c.decodeIfPresent(String.self, forKey: .role) ?? "assistant"
        ts = try c.decodeIfPresent(FlexibleDate.self, forKey: .ts) ?? FlexibleDate(.distantPast)
        text = try c.decodeIfPresent(String.self, forKey: .text) ?? ""
        metaKind = try c.decodeIfPresent(String.self, forKey: .metaKind)
    }

    private enum CodingKeys: String, CodingKey {
        case uuid, role, ts, text, metaKind
    }
}

public struct MessagesResponse: Codable, Sendable, Equatable {
    public let sessionId: String
    public let total: Int
    public let offset: Int
    public let messages: [SessionMessage]
}
