import Foundation

/// One entry from `GET /api/projects`. `id` is the raw Claude Code project-directory name
/// (path with `/` replaced by `-`) — pass it back verbatim as `?project=` on `/api/sessions`.
public struct Project: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let cwd: String
    public let name: String
    public let sessionCount: Int
    public let lastActive: FlexibleDate
}

public struct ProjectsResponse: Codable, Sendable, Equatable {
    public let projects: [Project]
}
