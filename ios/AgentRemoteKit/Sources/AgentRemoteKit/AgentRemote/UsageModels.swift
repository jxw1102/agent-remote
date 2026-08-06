import Foundation

/// One ready-to-render usage bucket from `GET /api/usage`. `resetsText` is pre-formatted in the
/// host's timezone — no client-side date math needed. `severity` is a free-form string (only
/// `"normal"` observed live) — render generically rather than hardcoding a fixed set of values.
public struct UsageBucket: Codable, Sendable, Equatable, Identifiable {
    public let title: String
    public let percent: Int
    public let resetsText: String
    public let severity: String

    public var id: String { title }
}

/// `{"ok": false, "error": "not supported"}` when the provider has no usage function.
public struct UsageResponse: Codable, Sendable, Equatable {
    public let ok: Bool
    public let buckets: [UsageBucket]?
    public let error: String?
}
