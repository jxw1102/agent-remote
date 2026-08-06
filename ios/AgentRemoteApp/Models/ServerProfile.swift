import Foundation

/// A saved agentremoted/bb10d daemon: just a name and base URL. The auth token lives in Keychain,
/// keyed by `id` — plain HTTP + token auth is the whole transport story now (the daemon typically
/// sits behind its own TLS termination, e.g. a Cloudflare Tunnel, same as this app's server does).
struct ServerProfile: Codable, Hashable, Identifiable {
    var id: UUID
    var name: String
    var serverURLString: String

    init(id: UUID = UUID(), name: String, serverURLString: String) {
        self.id = id
        self.name = name
        self.serverURLString = serverURLString
    }

    var subtitle: String { serverURLString }
}
