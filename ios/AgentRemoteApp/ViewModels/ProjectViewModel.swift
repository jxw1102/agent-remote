import AgentRemoteKit
import Combine
import Foundation

@MainActor
final class ProjectViewModel: ObservableObject {
    /// Sessions grouped by their working directory, newest-active folder first. This is what the
    /// picker renders after connecting: every resumable session on the server, under its folder.
    struct FolderGroup: Identifiable, Equatable {
        let cwd: String
        let sessions: [SessionSummary]
        var id: String { cwd }
    }

    @Published private(set) var folders: [FolderGroup] = []
    @Published private(set) var isLoadingSessions = false
    /// Directory for the "start a session in a new folder" affordance — folders that have no
    /// sessions yet won't appear in `folders`, so this is the only way to reach them.
    @Published var newSessionCwd: String = ""
    @Published var errorMessage: String?

    private let client: DaemonClient

    init(client: DaemonClient) {
        self.client = client
    }

    /// Fetch every resumable session on the server (no `project` → every folder). Called on appear.
    /// There's no session-delete endpoint in this daemon's API (sessions are just Claude Code's own
    /// transcript files) — the old app's swipe-to-delete doesn't carry over.
    func loadAllSessions() {
        guard let agentClient = client.agentClient else { return }
        isLoadingSessions = true
        errorMessage = nil
        Task {
            do {
                let sessions = try await agentClient.sessions(limit: 200)
                isLoadingSessions = false
                folders = Self.group(sessions)
                if newSessionCwd.isEmpty, let mostRecent = folders.first?.cwd {
                    newSessionCwd = mostRecent
                }
            } catch {
                isLoadingSessions = false
                errorMessage = DaemonClient.describe(error)
            }
        }
    }

    /// Bucket sessions by `cwd`, sort each folder newest-first, and order folders by their most
    /// recently touched session so the folder you were just in floats to the top.
    static func group(_ sessions: [SessionSummary]) -> [FolderGroup] {
        let byFolder = Dictionary(grouping: sessions) { $0.cwd }
        return byFolder
            .map { cwd, group in
                FolderGroup(cwd: cwd, sessions: group.sorted { $0.lastActive.date > $1.lastActive.date })
            }
            .sorted { ($0.sessions.first?.lastActive.date ?? .distantPast) > ($1.sessions.first?.lastActive.date ?? .distantPast) }
    }
}
