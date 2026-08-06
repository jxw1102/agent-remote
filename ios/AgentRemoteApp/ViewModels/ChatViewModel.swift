import AgentRemoteKit
import Combine
import Foundation

/// Drives one session against the daemon's job model: `send()` starts a new job (via
/// `POST /api/sessions/new` the first time, `.../continue` after) and polls it to completion,
/// following `nextJobId` if the daemon auto-chains a queued prompt. There's no persisted tool-call
/// history — resuming a session shows only past user/assistant text, by daemon design (tool
/// activity is transient job state, never written to the transcript).
@MainActor
final class ChatViewModel: ObservableObject, Identifiable, Hashable {
    nonisolated static func == (lhs: ChatViewModel, rhs: ChatViewModel) -> Bool { lhs === rhs }
    nonisolated func hash(into hasher: inout Hasher) { hasher.combine(localId) }

    enum Phase: Equatable {
        case idle
        case running
        case failed(String)
    }

    /// Stable identity assigned at creation — a brand-new session has no daemon-side id yet, so
    /// navigation and the session hub's cache key off this instead.
    nonisolated let localId: String
    /// The session id this chat was opened to resume (nil for a brand-new session).
    let resumeId: String?
    let cwd: String
    /// Human-facing session name (the summary from the picker); nil for a brand-new session.
    let sessionName: String?

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var items: [TimelineItem] = []
    @Published var pendingPermission: PendingPermissionUI?
    @Published var draftText = ""
    @Published var selectedModel: String = ""
    @Published var permissionMode: String = ""

    /// The real daemon-side session id — empty until either resumed or the first job's `init`
    /// event reports it. Track as `newSessionId.isEmpty ? sessionId : newSessionId`, same rule the
    /// daemon itself uses.
    private(set) var sessionId: String
    /// The model reported by the most recent job's `init` event (or "" before any turn has run).
    @Published private(set) var model: String = ""

    private let client: DaemonClient
    private var currentJobId: String?
    private var pollTask: Task<Void, Never>?
    private var permissionIndex: [String: Int] = [:]

    nonisolated var id: String { localId }
    var isBusy: Bool { phase == .running }

    var displayTitle: String {
        if let sessionName, !sessionName.isEmpty { return sessionName }
        return (cwd as NSString).lastPathComponent
    }

    var subtitle: String {
        switch phase {
        case .failed: return "Couldn't continue"
        case .idle, .running:
            let parts = [Self.prettyModel(model), Self.prettyMode(permissionMode)].filter { !$0.isEmpty }
            return parts.isEmpty ? "Ready" : parts.joined(separator: " · ")
        }
    }

    init(client: DaemonClient, cwd: String, sessionName: String?, resume: String?) {
        self.client = client
        self.cwd = cwd
        self.sessionName = sessionName
        self.resumeId = resume
        self.sessionId = resume ?? ""
        self.localId = UUID().uuidString
        if resume != nil {
            loadHistory()
        }
    }

    func send() {
        let text = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, phase != .running else { return }
        items.append(.userText(id: UUID().uuidString, text: text))
        draftText = ""
        phase = .running

        Task {
            guard let agentClient = client.agentClient else {
                phase = .failed("Not connected.")
                return
            }
            do {
                let jobId: String
                if sessionId.isEmpty {
                    jobId = try await agentClient.startSession(NewSessionRequest(
                        cwd: cwd, prompt: text,
                        permissionMode: permissionMode.isEmpty ? nil : permissionMode,
                        model: selectedModel.isEmpty ? nil : selectedModel
                    ))
                } else {
                    jobId = try await agentClient.continueSession(id: sessionId, ContinueSessionRequest(
                        prompt: text,
                        permissionMode: permissionMode.isEmpty ? nil : permissionMode,
                        model: selectedModel.isEmpty ? nil : selectedModel
                    ))
                }
                startPolling(jobId)
            } catch {
                phase = .failed(DaemonClient.describe(error))
            }
        }
    }

    func respondToPermission(approved: Bool) {
        guard let prompt = pendingPermission, let jobId = currentJobId else { return }
        pendingPermission = nil
        Task {
            try? await client.agentClient?.resolvePermission(jobId: jobId, requestId: prompt.requestId, allow: approved)
        }
    }

    func interrupt() {
        guard let jobId = currentJobId, phase == .running else { return }
        Task { try? await client.agentClient?.stopJob(jobId: jobId) }
    }

    /// Slash commands (bare names — this daemon's `/api/ping` doesn't provide per-command
    /// descriptions/argument hints) matching what's currently typed.
    var commandSuggestions: [String] {
        guard draftText.hasPrefix("/") else { return [] }
        let query = String(draftText.dropFirst()).lowercased()
        guard !query.contains(" ") else { return [] }
        let all = client.availableSlashCommands
        let matches = query.isEmpty ? all : all.filter { $0.lowercased().hasPrefix(query) }
        return Array(matches.prefix(30))
    }

    func applyCommand(_ name: String) {
        draftText = "/\(name) "
    }

    // MARK: - Model selection

    /// This daemon has no "change the running session's model" call — `model` is only a field on
    /// the next `new`/`continue` request, so selecting one here just changes what the *next*
    /// message will ask for.
    var availableModels: [String] { client.availableModels }

    func selectModel(_ model: String) {
        selectedModel = model == "default" ? "" : model
    }

    func isCurrentModel(_ model: String) -> Bool {
        (selectedModel.isEmpty ? "default" : selectedModel) == model
    }

    static func modelLabel(_ raw: String) -> String {
        if raw.isEmpty || raw == "default" { return "Default" }
        let withoutPrefix = raw.replacingOccurrences(of: "claude-", with: "")
        return withoutPrefix.split(separator: "-").map { $0.capitalized }.joined(separator: " ")
    }

    // MARK: - History (resumed sessions only)

    private func loadHistory() {
        guard let resumeId else { return }
        Task {
            guard let agentClient = client.agentClient else { return }
            do {
                let response = try await agentClient.messages(sessionId: resumeId)
                var loaded: [TimelineItem] = []
                if response.total > response.messages.count {
                    loaded.append(.systemNotice(
                        id: "history-truncated",
                        text: "Showing the most recent \(response.messages.count) of \(response.total) messages."
                    ))
                }
                for message in response.messages {
                    loaded.append(message.role == "user"
                        ? .userText(id: message.id, text: message.text)
                        : .assistantText(id: message.id, text: message.text))
                }
                items = loaded
            } catch {
                items = [.systemNotice(id: UUID().uuidString, text: "Couldn't load history: \(DaemonClient.describe(error))")]
            }
        }
    }

    // MARK: - Job polling

    private func startPolling(_ jobId: String) {
        pollTask?.cancel()
        currentJobId = jobId
        pollTask = Task { await pollJob(jobId) }
    }

    private func pollJob(_ initialJobId: String) async {
        var jobId = initialJobId
        var since = 0
        while !Task.isCancelled {
            guard let agentClient = client.agentClient else {
                phase = .failed("Not connected.")
                return
            }
            let job: JobSnapshot
            do {
                job = try await agentClient.job(id: jobId, since: since)
            } catch {
                phase = .failed(DaemonClient.describe(error))
                return
            }
            since = job.nextSeq
            if !job.resolvedSessionId.isEmpty { sessionId = job.resolvedSessionId }

            for event in job.events { apply(event, job: job) }

            pendingPermission = job.pendingPermission.map {
                PendingPermissionUI(requestId: $0.requestId, toolName: $0.toolName, detail: $0.detail)
            }

            switch job.status {
            case .starting, .running:
                try? await Task.sleep(nanoseconds: 700_000_000)
                continue
            case .done, .error, .stopped:
                if job.droppedQueued > 0 {
                    items.append(.systemNotice(
                        id: UUID().uuidString,
                        text: "\(job.droppedQueued) queued message(s) were dropped because this turn didn't finish cleanly."
                    ))
                }
                if !job.nextJobId.isEmpty {
                    jobId = job.nextJobId
                    currentJobId = jobId
                    since = 0
                    continue
                }
                currentJobId = nil
                phase = .idle
                return
            }
        }
    }

    private func apply(_ event: JobEvent, job: JobSnapshot) {
        switch event {
        case .initEvent(_, _, let model):
            // "interactive" is a literal placeholder in interactive-TUI mode, not a real model id.
            if model != "interactive" { self.model = model }

        case .text(_, let text):
            items.append(.assistantText(id: UUID().uuidString, text: text))

        case .tool(_, let name, let detail):
            items.append(.toolCall(id: UUID().uuidString, name: name, detail: detail))

        case .result(_, let isError, _, _):
            if isError {
                items.append(.turnResult(
                    id: UUID().uuidString,
                    summary: job.error.isEmpty ? "The turn ended with an error." : job.error,
                    isError: true
                ))
            }

        case .permission(_, let requestId, let toolName, let detail):
            permissionIndex[requestId] = items.count
            items.append(.permissionRequest(id: requestId, toolName: toolName, detail: detail, resolution: nil))

        case .permissionResolved(_, let requestId, let allow, let reason):
            if let index = permissionIndex[requestId], case .permissionRequest(let id, let toolName, let detail, _) = items[index] {
                items[index] = .permissionRequest(
                    id: id, toolName: toolName, detail: detail,
                    resolution: allow ? .allowed : .denied(reason: reason)
                )
            }

        case .question, .questionResolved, .unknown:
            // AskUserQuestion flows through `pendingQuestion`/interactive mode — not modeled in the
            // v1 chat timeline; the structured shape isn't pinned down yet (see PROTOCOL_SPEC.md).
            break
        }
    }

    // MARK: - Display formatting

    static func prettyModel(_ raw: String) -> String {
        guard !raw.isEmpty, raw != "default" else { return "" }
        return raw.replacingOccurrences(of: "claude-", with: "").replacingOccurrences(of: "-", with: " ").capitalized
    }

    static func prettyMode(_ raw: String) -> String {
        switch raw {
        case "", "default": return ""
        case "acceptEdits": return "Accept Edits"
        case "bypassPermissions": return "Bypass Permissions"
        case "plan": return "Plan"
        case "interactive": return "Interactive"
        default: return raw.capitalized
        }
    }
}
