import AgentRemoteKit
import Combine
import Foundation

/// Owns one `AgentRemoteClient` for a server: verifies the token via `ping`, then holds it —
/// there's no persistent session socket to lose the way the old SSH/WebSocket transport had, since
/// every call is an independent HTTP request. The only long-lived connection is `/ws/status`
/// (for the sessions-list activity indicators), which this restarts on foreground.
@MainActor
final class DaemonClient: ObservableObject {
    enum ConnectionState: Equatable {
        case disconnected
        case connecting
        case connected(PingResponse)
        case failed(String)
    }

    @Published private(set) var state: ConnectionState = .disconnected
    /// Currently-active jobs across every session on this server, keyed by job id — from the
    /// `/ws/status` push. Best-effort: absence doesn't mean a job isn't running, just that the
    /// status stream hasn't reported it (or is disconnected).
    @Published private(set) var activeJobs: [String: ActiveJobStatus] = [:]

    private(set) var agentClient: AgentRemoteClient?
    private var statusTask: Task<Void, Never>?

    var isConnected: Bool { if case .connected = state { return true }; return false }
    var caps: PingResponse.Capabilities? { if case .connected(let ping) = state { return ping.caps }; return nil }
    var availableModels: [String] { if case .connected(let ping) = state { return ping.models ?? [] }; return [] }
    var availableSlashCommands: [String] { if case .connected(let ping) = state { return ping.slashCommands ?? [] }; return [] }

    func connect(baseURLString: String, authToken: String) async {
        statusTask?.cancel()
        statusTask = nil
        activeJobs = [:]
        state = .connecting

        guard let url = URL(string: baseURLString), let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else {
            state = .failed("Server URL must start with http:// or https://")
            return
        }

        let client = AgentRemoteClient(baseURL: url, token: authToken)
        do {
            let ping = try await client.ping()
            agentClient = client
            state = .connected(ping)
            startStatusStream(client)
        } catch {
            agentClient = nil
            state = .failed(Self.describe(error))
        }
    }

    /// Surface a pre-connection failure (e.g. a missing Keychain secret) through the normal state
    /// machine so the UI shows it on the failed screen.
    func reportFailure(_ message: String) {
        state = .failed(message)
    }

    func pauseStatusStream() {
        statusTask?.cancel()
        statusTask = nil
    }

    func resumeStatusStreamIfNeeded() {
        guard statusTask == nil, let agentClient else { return }
        startStatusStream(agentClient)
    }

    private func startStatusStream(_ client: AgentRemoteClient) {
        statusTask = Task { [weak self] in
            do {
                for try await push in client.statusStream() {
                    guard let self, !Task.isCancelled else { return }
                    var map: [String: ActiveJobStatus] = [:]
                    for job in push.active { map[job.jobId] = job }
                    self.activeJobs = map
                }
            } catch {
                // Best-effort feed — a dropped status stream doesn't affect core chat functionality
                // (every other call is a plain request/response), so just clear the stale data and
                // let a later `resumeStatusStreamIfNeeded()` (e.g. on foreground) retry.
                guard let self else { return }
                self.activeJobs = [:]
            }
        }
    }

    nonisolated static func describe(_ error: Error) -> String {
        if let ar = error as? AgentRemoteError {
            return ar.errorDescription ?? String(describing: ar)
        }
        // Bridged NSError from AgentRemoteError (CustomNSError) — prefer its localized text.
        let ns = error as NSError
        if ns.domain == AgentRemoteError.errorDomain,
           let msg = ns.userInfo[NSLocalizedDescriptionKey] as? String,
           !msg.isEmpty {
            return msg
        }
        if let urlErr = error as? URLError {
            return urlErr.localizedDescription
        }
        let msg = error.localizedDescription
        // Avoid the useless "error 1" form if we can surface more.
        if msg.contains("AgentRemoteError error") {
            return "Daemon request failed: \(msg)"
        }
        return msg
    }
}
