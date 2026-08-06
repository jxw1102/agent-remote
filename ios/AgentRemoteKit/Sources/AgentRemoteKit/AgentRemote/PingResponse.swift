import Foundation

/// `GET /api/ping` — the only unauthenticated endpoint. Single-provider shape; multi-provider root
/// adds `multi`/`providers`/`providerDetails` fields this type doesn't model (not exercised against
/// this daemon's deployment, which is single-provider).
public struct PingResponse: Codable, Sendable, Equatable {
    public let ok: Bool
    public let app: String
    public let version: String
    public let host: String
    public let provider: String
    public let caps: Capabilities
    /// Present only when authenticated (ping additionally returns these with a valid token).
    public let slashCommands: [String]?
    public let models: [String]?
    public let efforts: [String]?
    public let dropPath: String?

    /// Every field defaults to `false` when absent — the daemon's own server.py explicitly unions
    /// caps across providers specifically so an unrecognized/missing key must never be assumed true.
    public struct Capabilities: Codable, Sendable, Equatable {
        public let queue: Bool
        public let stop: Bool
        public let projects: Bool
        public let wsStatus: Bool
        public let permissions: Bool
        public let permissionModes: Bool
        public let requiresCwd: Bool
        public let canSetModel: Bool
        public let canSetEffort: Bool
        public let canShowUsage: Bool
        public let interactive: Bool
        /// Absent entirely on daemon builds older than the one that introduced Live TUI — decode
        /// as optional and treat `nil` the same as `false`, never assume true from another cap.
        public let liveTui: Bool?
        public let rewind: Bool?

        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            queue = try c.decodeIfPresent(Bool.self, forKey: .queue) ?? false
            stop = try c.decodeIfPresent(Bool.self, forKey: .stop) ?? false
            projects = try c.decodeIfPresent(Bool.self, forKey: .projects) ?? false
            wsStatus = try c.decodeIfPresent(Bool.self, forKey: .wsStatus) ?? false
            permissions = try c.decodeIfPresent(Bool.self, forKey: .permissions) ?? false
            permissionModes = try c.decodeIfPresent(Bool.self, forKey: .permissionModes) ?? false
            requiresCwd = try c.decodeIfPresent(Bool.self, forKey: .requiresCwd) ?? false
            canSetModel = try c.decodeIfPresent(Bool.self, forKey: .canSetModel) ?? false
            canSetEffort = try c.decodeIfPresent(Bool.self, forKey: .canSetEffort) ?? false
            canShowUsage = try c.decodeIfPresent(Bool.self, forKey: .canShowUsage) ?? false
            interactive = try c.decodeIfPresent(Bool.self, forKey: .interactive) ?? false
            liveTui = try c.decodeIfPresent(Bool.self, forKey: .liveTui)
            rewind = try c.decodeIfPresent(Bool.self, forKey: .rewind)
        }

        public init(
            queue: Bool = false, stop: Bool = false, projects: Bool = false, wsStatus: Bool = false,
            permissions: Bool = false, permissionModes: Bool = false, requiresCwd: Bool = false,
            canSetModel: Bool = false, canSetEffort: Bool = false, canShowUsage: Bool = false,
            interactive: Bool = false, liveTui: Bool? = false, rewind: Bool? = false
        ) {
            self.queue = queue; self.stop = stop; self.projects = projects; self.wsStatus = wsStatus
            self.permissions = permissions; self.permissionModes = permissionModes; self.requiresCwd = requiresCwd
            self.canSetModel = canSetModel; self.canSetEffort = canSetEffort; self.canShowUsage = canShowUsage
            self.interactive = interactive; self.liveTui = liveTui; self.rewind = rewind
        }

        public var liveTuiEnabled: Bool { liveTui ?? false }
    }
}
