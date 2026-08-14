import Foundation

/// One ready-to-render usage bucket from `GET /api/usage`. `resetsText` is pre-formatted in the
/// host's timezone — no client-side date math needed. `severity` is normal | warning | critical
/// but arrives as a free-form string — render generically rather than hardcoding.
public struct UsageBucket: Codable, Sendable, Equatable, Identifiable {
    public let title: String
    public let percent: Int
    public let resetsText: String
    public let severity: String

    public var id: String { title }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        percent = try c.decodeIfPresent(Int.self, forKey: .percent) ?? 0
        resetsText = try c.decodeIfPresent(String.self, forKey: .resetsText) ?? ""
        severity = try c.decodeIfPresent(String.self, forKey: .severity) ?? "normal"
    }

    private enum CodingKeys: String, CodingKey { case title, percent, resetsText, severity }
}

/// One harness's usage on a multi daemon (`sections` array), stamped with the account so two
/// daemons reaching the same subscription can be merged client-side.
public struct UsageSection: Codable, Sendable, Equatable {
    public let provider: String
    public let ok: Bool
    public let error: String
    public let buckets: [UsageBucket]
    public let account: String
    public let accountId: String

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? ""
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        error = try c.decodeIfPresent(String.self, forKey: .error) ?? ""
        buckets = try c.decodeIfPresent([UsageBucket].self, forKey: .buckets) ?? []
        account = try c.decodeIfPresent(String.self, forKey: .account) ?? ""
        accountId = try c.decodeIfPresent(String.self, forKey: .accountId) ?? ""
    }

    private enum CodingKeys: String, CodingKey { case provider, ok, error, buckets, account, accountId }
}

/// `{"ok": false, "error": "not supported"}` when the provider has no usage function.
/// Multi-harness daemons answer with `sections` (one per harness); single-harness hosts also
/// stamp `provider`/`account` on the root.
public struct UsageResponse: Codable, Sendable, Equatable {
    public let ok: Bool
    public let buckets: [UsageBucket]?
    public let error: String?
    public let multi: Bool
    public let sections: [UsageSection]
    public let provider: String
    public let account: String
    public let accountId: String

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? false
        buckets = try c.decodeIfPresent([UsageBucket].self, forKey: .buckets)
        error = try c.decodeIfPresent(String.self, forKey: .error)
        multi = try c.decodeIfPresent(Bool.self, forKey: .multi) ?? false
        sections = try c.decodeIfPresent([UsageSection].self, forKey: .sections) ?? []
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? ""
        account = try c.decodeIfPresent(String.self, forKey: .account) ?? ""
        accountId = try c.decodeIfPresent(String.self, forKey: .accountId) ?? ""
    }

    private enum CodingKeys: String, CodingKey {
        case ok, buckets, error, multi, sections, provider, account, accountId
    }
}
