import AgentRemoteKit
import Combine
import Foundation

/// Drives one session: job poll loop for new/continue, history load for resume.
@MainActor
final class ChatViewModel: ObservableObject, Identifiable, Hashable {
    nonisolated static func == (lhs: ChatViewModel, rhs: ChatViewModel) -> Bool { lhs === rhs }
    nonisolated func hash(into hasher: inout Hasher) { hasher.combine(localId) }

    enum Phase: Equatable {
        case idle
        case running
        case failed(String)
    }

    nonisolated let localId: String
    let resumeId: String?
    let profileId: UUID
    let cwd: String
    let sessionName: String?
    /// Harness for this session (claude|grok|codex).
    @Published private(set) var provider: String

    @Published private(set) var phase: Phase = .idle
    @Published private(set) var items: [TimelineItem] = []
    @Published var pendingPermission: PendingPermissionUI?
    @Published var pendingQuestion: PendingQuestionUI?
    @Published var draftText = ""
    @Published var selectedModel: String = ""
    @Published var selectedEffort: String = ""
    @Published var permissionMode: String = ""

    private(set) var sessionId: String
    @Published private(set) var model: String = ""

    /// Fired when a brand-new session gets a real daemon id (for list re-key).
    var onSessionIdResolved: ((String) -> Void)?

    private let client: DaemonClient
    /// Exposed for Live TUI sheet (same connection as this chat).
    var liveDaemonClient: DaemonClient { client }
    private weak var settings: SettingsStore?
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
            let harness = ProviderAccent.forProvider(provider).label
            let parts = [harness, Self.prettyModel(model), Self.prettyMode(permissionMode)].filter { !$0.isEmpty }
            return parts.isEmpty ? "Ready" : parts.joined(separator: " · ")
        }
    }

    var accent: ProviderAccent { ProviderAccent.forProvider(provider) }

    init(
        client: DaemonClient,
        profileId: UUID,
        cwd: String,
        sessionName: String?,
        resume: String?,
        provider: String,
        settings: SettingsStore?
    ) {
        self.client = client
        self.profileId = profileId
        self.cwd = cwd
        self.sessionName = sessionName
        self.resumeId = resume
        self.provider = provider.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        self.sessionId = resume ?? ""
        self.localId = UUID().uuidString
        self.settings = settings
        // Seed overrides from app settings.
        if let s = settings?.settings {
            selectedModel = s.modelOverride
            selectedEffort = s.effortOverride
            permissionMode = s.permissionMode == "interactive" ? "interactive" : ""
        }
        if resume != nil {
            loadHistory()
        }
    }

    // MARK: - Catalogues (per open harness)

    private var ping: PingResponse? {
        if case .connected(let p) = client.state { return p }
        return nil
    }

    var availableModels: [String] {
        ping?.models(for: provider) ?? client.availableModels
    }

    var availableEfforts: [String] {
        let list = ping?.efforts(for: provider) ?? []
        return list
    }

    var canSetModel: Bool {
        ping?.caps(for: provider).canSetModel ?? (ping?.caps.canSetModel ?? false)
    }

    var canSetEffort: Bool {
        if provider == "claude" { return false }
        return ping?.caps(for: provider).canSetEffort ?? false
    }

    var liveTuiEnabled: Bool {
        ping?.caps(for: provider).liveTuiEnabled ?? false
    }

    var availableSlashCommands: [String] {
        var list = ping?.slashCommands(for: provider) ?? client.availableSlashCommands
        // Always surface these so older daemons / every client can invoke them.
        for cmd in ["/rewind", "/goal"] where !list.contains(cmd) {
            list.append(cmd)
        }
        return list
    }

    func send() {
        let text = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, phase != .running else { return }
        items.append(.userText(id: UUID().uuidString, text: text))
        draftText = ""
        phase = .running

        // Persist last-used overrides into settings.
        if let settings {
            settings.settings.modelOverride = selectedModel
            settings.settings.effortOverride = selectedEffort
            if permissionMode == "interactive" {
                settings.settings.permissionMode = "interactive"
            }
        }

        Task {
            guard let agentClient = client.agentClient else {
                phase = .failed("Not connected.")
                return
            }
            do {
                let jobId: String
                if sessionId.isEmpty {
                    jobId = try await agentClient.startSession(NewSessionRequest(
                        cwd: cwd.isEmpty ? nil : cwd,
                        prompt: text,
                        provider: provider.isEmpty ? nil : provider,
                        permissionMode: permissionMode.isEmpty ? nil : permissionMode,
                        model: selectedModel.isEmpty ? nil : selectedModel,
                        effort: selectedEffort.isEmpty ? nil : selectedEffort
                    ))
                } else {
                    jobId = try await agentClient.continueSession(id: sessionId, ContinueSessionRequest(
                        prompt: text,
                        permissionMode: permissionMode.isEmpty ? nil : permissionMode,
                        model: selectedModel.isEmpty ? nil : selectedModel,
                        effort: selectedEffort.isEmpty ? nil : selectedEffort
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

    func respondToQuestion(answers: [[String]], notes: [String]?, cancel: Bool) {
        guard let q = pendingQuestion, let jobId = currentJobId else { return }
        pendingQuestion = nil
        Task {
            try? await client.agentClient?.resolveQuestion(
                jobId: jobId,
                QuestionAnswerRequest(requestId: q.requestId, answers: cancel ? nil : answers, notes: notes, cancel: cancel)
            )
        }
    }

    func interrupt() {
        guard let jobId = currentJobId, phase == .running else { return }
        Task { try? await client.agentClient?.stopJob(jobId: jobId) }
    }

    var commandSuggestions: [String] {
        guard draftText.hasPrefix("/") else { return [] }
        let query = String(draftText.dropFirst()).lowercased()
        guard !query.contains(" ") else { return [] }
        let all = availableSlashCommands.map { $0.hasPrefix("/") ? String($0.dropFirst()) : $0 }
        let matches = query.isEmpty ? all : all.filter { $0.lowercased().hasPrefix(query) }
        return Array(matches.prefix(30))
    }

    func applyCommand(_ name: String) {
        draftText = "/\(name) "
    }

    func selectModel(_ model: String) {
        selectedModel = model == "default" ? "" : model
    }

    func selectEffort(_ effort: String) {
        selectedEffort = effort == "default" ? "" : effort
    }

    func isCurrentModel(_ model: String) -> Bool {
        (selectedModel.isEmpty ? "default" : selectedModel) == model
    }

    func isCurrentEffort(_ effort: String) -> Bool {
        (selectedEffort.isEmpty ? "default" : selectedEffort) == effort
    }

    static func modelLabel(_ raw: String) -> String {
        if raw.isEmpty || raw == "default" { return "Default" }
        let withoutPrefix = raw
            .replacingOccurrences(of: "claude-", with: "")
            .replacingOccurrences(of: "gpt-", with: "")
        return withoutPrefix.split(separator: "-").map { $0.capitalized }.joined(separator: " ")
    }

    // MARK: - History

    private func loadHistory() {
        guard let resumeId else { return }
        Task {
            // Wait briefly for the multi-host pool to finish connecting this profile.
            var agentClient = client.agentClient
            if agentClient == nil {
                for _ in 0..<40 {
                    try? await Task.sleep(nanoseconds: 250_000_000)
                    agentClient = client.agentClient
                    if agentClient != nil { break }
                }
            }
            guard let agentClient else {
                items = [.systemNotice(id: UUID().uuidString, text: "Not connected — pull to refresh sessions and reopen.")]
                return
            }
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
                    switch message.role {
                    case "user":
                        loaded.append(.userText(id: message.id, text: message.text))
                    case "status":
                        loaded.append(.systemNotice(id: message.id, text: message.text))
                    default:
                        loaded.append(.assistantText(id: message.id, text: message.text))
                    }
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
            if !job.resolvedSessionId.isEmpty {
                let first = sessionId.isEmpty
                sessionId = job.resolvedSessionId
                if first { onSessionIdResolved?(sessionId) }
            }

            for event in job.events { apply(event, job: job) }

            pendingPermission = job.pendingPermission.map {
                PendingPermissionUI(requestId: $0.requestId, toolName: $0.toolName, detail: $0.detail)
            }
            if let q = job.pendingQuestion, !q.requestId.isEmpty {
                pendingQuestion = PendingQuestionUI(requestId: q.requestId, questions: q.questions)
            } else if job.pendingQuestion == nil {
                // Only clear when the server dropped it (not when we haven't answered yet).
                // Keep local sheet until user responds or job ends.
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
                pendingQuestion = nil
                if job.status == .error, !job.error.isEmpty, items.last.map({
                    if case .turnResult(_, _, let err) = $0 { return !err } else { return true }
                }) ?? true {
                    // ensure error is visible if no result event
                }
                phase = .idle
                return
            }
        }
    }

    private func apply(_ event: JobEvent, job: JobSnapshot) {
        switch event {
        case .initEvent(_, _, let model):
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

        case .question(_, let requestId, let questions):
            pendingQuestion = PendingQuestionUI(requestId: requestId, questions: questions)

        case .questionResolved, .unknown:
            break
        }
    }

    static func prettyModel(_ raw: String) -> String {
        guard !raw.isEmpty, raw != "default" else { return "" }
        return raw.replacingOccurrences(of: "claude-", with: "")
            .replacingOccurrences(of: "-", with: " ")
            .capitalized
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

struct PendingQuestionUI: Identifiable, Equatable {
    let requestId: String
    let questions: [QuestionItem]
    var id: String { requestId }
}
