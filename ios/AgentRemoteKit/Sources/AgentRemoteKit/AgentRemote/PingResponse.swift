import Foundation

/// `GET /api/ping` — authenticated when a token is sent. Multi-harness daemons add
/// `multi` / `providers` / `providerDetails`; single-harness still uses the root fields.
public struct PingResponse: Codable, Sendable, Equatable {
    public let ok: Bool
    public let app: String
    public let version: String
    public let host: String
    /// Single-harness default; multi-harness daemons may omit or leave empty.
    public let provider: String
    public let caps: Capabilities
    /// Present only when authenticated.
    public let slashCommands: [String]?
    public let models: [String]?
    public let efforts: [String]?
    public let dropPath: String?
    /// Multi-harness root: one daemon fronts several CLIs.
    public let multi: Bool?
    public let providers: [String]?
    public let providerDetails: [String: ProviderDetail]?
    /// Aggregate harness login snapshot (daemon ≥ 2.5.3).
    public let auth: AuthHealth?
    /// Focus-list support — gates the Focus filter, rename, and regenerate (daemon ≥ 2.6).
    public let focus: Bool?
    public let focusStates: [String]?

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        ok = try c.decodeIfPresent(Bool.self, forKey: .ok) ?? true
        app = try c.decodeIfPresent(String.self, forKey: .app) ?? "agentremoted"
        version = try c.decodeIfPresent(String.self, forKey: .version) ?? ""
        host = try c.decodeIfPresent(String.self, forKey: .host) ?? ""
        provider = try c.decodeIfPresent(String.self, forKey: .provider) ?? ""
        caps = try c.decodeIfPresent(Capabilities.self, forKey: .caps) ?? Capabilities()
        slashCommands = try c.decodeIfPresent([String].self, forKey: .slashCommands)
        models = try c.decodeIfPresent([String].self, forKey: .models)
        efforts = try c.decodeIfPresent([String].self, forKey: .efforts)
        dropPath = try c.decodeIfPresent(String.self, forKey: .dropPath)
        multi = try c.decodeIfPresent(Bool.self, forKey: .multi)
        providers = try c.decodeIfPresent([String].self, forKey: .providers)
        providerDetails = try c.decodeIfPresent([String: ProviderDetail].self, forKey: .providerDetails)
        auth = try c.decodeIfPresent(AuthHealth.self, forKey: .auth)
        focus = try c.decodeIfPresent(Bool.self, forKey: .focus)
        focusStates = try c.decodeIfPresent([String].self, forKey: .focusStates)
    }

    private enum CodingKeys: String, CodingKey {
        case ok, app, version, host, provider, caps, slashCommands, models, efforts
        case dropPath, multi, providers, providerDetails, auth, focus, focusStates
    }

    /// Harnesses this daemon exposes (multi list, else the single root provider).
    public var harnesses: [String] {
        if let providers, !providers.isEmpty { return providers }
        let p = provider.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return p.isEmpty ? [] : [p]
    }

    public var isMulti: Bool {
        if multi == true { return true }
        return harnesses.count > 1
    }

    /// Models for a harness; falls back to root catalogue.
    public func models(for harness: String?) -> [String] {
        let h = (harness ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !h.isEmpty, let list = providerDetails?[h]?.models, !list.isEmpty {
            return list
        }
        return models ?? []
    }

    public func efforts(for harness: String?) -> [String] {
        let h = (harness ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !h.isEmpty, let detail = providerDetails?[h] {
            return detail.efforts ?? []
        }
        if h == "claude" { return [] }
        return efforts ?? []
    }

    public func slashCommands(for harness: String?) -> [String] {
        let h = (harness ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !h.isEmpty, let list = providerDetails?[h]?.slashCommands, !list.isEmpty {
            return list
        }
        return slashCommands ?? []
    }

    public func caps(for harness: String?) -> Capabilities {
        let h = (harness ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if !h.isEmpty, let map = providerDetails?[h]?.caps {
            return Capabilities(fromCapMap: map, fallback: caps)
        }
        return caps
    }

    /// Every field defaults to `false` when absent — missing key must never be assumed true.
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
        /// Absent on older daemons — treat `nil` as false.
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

        /// Build from a `provider_details[h].caps` map, filling gaps from root caps.
        ///
        /// The wire keys are snake_case, but `.convertFromSnakeCase` rewrites **dictionary keys
        /// too**, so a map decoded through the client arrives camelCased. Accept both spellings —
        /// looking up only `"can_set_effort"` here silently fell back to root caps for every
        /// multi-word capability.
        public init(fromCapMap map: [String: Bool], fallback: Capabilities) {
            func flag(_ camel: String, _ snake: String) -> Bool? {
                map[camel] ?? map[snake]
            }
            queue = map["queue"] ?? fallback.queue
            stop = map["stop"] ?? fallback.stop
            projects = map["projects"] ?? fallback.projects
            wsStatus = flag("wsStatus", "ws_status") ?? fallback.wsStatus
            permissions = map["permissions"] ?? fallback.permissions
            permissionModes = flag("permissionModes", "permission_modes") ?? fallback.permissionModes
            requiresCwd = flag("requiresCwd", "requires_cwd") ?? fallback.requiresCwd
            canSetModel = flag("canSetModel", "can_set_model") ?? fallback.canSetModel
            canSetEffort = flag("canSetEffort", "can_set_effort") ?? fallback.canSetEffort
            canShowUsage = flag("canShowUsage", "can_show_usage") ?? fallback.canShowUsage
            interactive = map["interactive"] ?? fallback.interactive
            liveTui = flag("liveTui", "live_tui") ?? fallback.liveTui
            rewind = map["rewind"] ?? fallback.rewind
        }

        public var liveTuiEnabled: Bool { liveTui ?? false }
        public var rewindEnabled: Bool { rewind ?? false }
    }
}

public struct ProviderDetail: Codable, Sendable, Equatable {
    public let caps: [String: Bool]?
    public let slashCommands: [String]?
    public let models: [String]?
    public let efforts: [String]?
    public let auth: AuthHealth?
}

/// Harness login health from `/api/ping` (daemon ≥ 2.5.3).
public struct AuthHealth: Codable, Sendable, Equatable {
    public let cli: String?
    public let cliOnPath: Bool?
    /// subscription | api_key | none | unknown
    public let mode: String?
    /// ok | warning | expired | missing | unknown
    public let status: String?
    public let detail: String?
}
