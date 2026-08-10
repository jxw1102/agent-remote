import Foundation

/// One entry from `GET /api/projects`. `id` is the project-directory name
/// (path with `/` replaced by `-`) — pass it back as `?project=` on `/api/sessions`.
public struct Project: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let cwd: String
    public let name: String
    public let sessionCount: Int
    public let lastActive: FlexibleDate
    /// Multi-harness root tags each project with its provider.
    public let provider: String

    public init(
        id: String, cwd: String = "", name: String = "", sessionCount: Int = 0,
        lastActive: FlexibleDate = FlexibleDate(.distantPast), provider: String = ""
    ) {
        self.id = id; self.cwd = cwd; self.name = name
        self.sessionCount = sessionCount; self.lastActive = lastActive; self.provider = provider
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        cwd = try c.decodeIfPresent(String.self, forKey: .cwd) ?? ""
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? ""
        sessionCount = try c.decodeIfPresent(Int.self, forKey: .sessionCount) ?? 0
        lastActive = try c.decodeIfPresent(FlexibleDate.self, forKey: .lastActive) ?? FlexibleDate(.distantPast)
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? ""
    }

    private enum CodingKeys: String, CodingKey {
        case id, cwd, name, sessionCount, lastActive, provider
    }
}

public struct ProjectsResponse: Codable, Sendable, Equatable {
    public let projects: [Project]
}
