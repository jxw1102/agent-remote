import AgentRemoteKit
import Foundation

/// A session always travels with the daemon it lives on (matches Android SessionRef).
struct SessionRef: Hashable, Codable, Identifiable {
    let profileId: UUID
    let sessionId: String

    var id: String { key }
    var key: String { "\(profileId.uuidString)/\(sessionId)" }

    static func parse(_ key: String) -> SessionRef? {
        guard let slash = key.firstIndex(of: "/") else { return nil }
        let profilePart = String(key[..<slash])
        let sessionPart = String(key[key.index(after: slash)...])
        guard let pid = UUID(uuidString: profilePart), !sessionPart.isEmpty else { return nil }
        return SessionRef(profileId: pid, sessionId: sessionPart)
    }
}

/// One row of the unified multi-host session list.
/// Hash/equality are by `ref` only — `SessionSummary` is not Hashable in the kit.
struct SessionRow: Identifiable, Hashable {
    let ref: SessionRef
    let profileName: String
    let provider: String
    let session: SessionSummary

    var id: String { ref.key }

    var sortKey: Date { session.lastActive.date }

    var resolvedProvider: String {
        let p = provider.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !p.isEmpty { return p }
        return session.provider.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
    }

    static func == (lhs: SessionRow, rhs: SessionRow) -> Bool {
        lhs.ref == rhs.ref
            && lhs.profileName == rhs.profileName
            && lhs.provider == rhs.provider
            && lhs.session.id == rhs.session.id
            && lhs.session.title == rhs.session.title
            && lhs.session.lastActive.date == rhs.session.lastActive.date
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(ref)
        hasher.combine(session.id)
        hasher.combine(session.lastActive.date)
    }
}

struct ProfileFeed: Equatable {
    var profileId: UUID
    var loading: Bool = false
    var error: String? = nil
    var count: Int = 0
}
