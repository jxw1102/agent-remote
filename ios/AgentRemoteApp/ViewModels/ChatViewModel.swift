import AgentRemoteKit
import Combine
import Foundation

/// Drives one session: job poll loop for new/continue, history load for resume. Also attaches to
/// turns started elsewhere (desktop TUI, queued chain, another client) via the status stream, so
/// a pending permission/question can always be answered from this phone.
@MainActor
final class ChatViewModel: ObservableObject, Identifiable, Hashable {
    nonisolated static func == (lhs: ChatViewModel, rhs: ChatViewModel) -> Bool { lhs === rhs }
    nonisolated func hash(into hasher: inout Hasher) { hasher.combine(localId) }

    enum Phase: Equatable {
        case idle
        case running
        case failed(String)
    }

    struct AttachmentChip: Identifiable, Equatable {
        let id: String
        let name: String
        /// Host path once uploaded; empty while the upload is in flight.
        var hostPath: String = ""
        var uploading: Bool = true
        var error: String?
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
    /// A gate the user swiped away without answering — still pending on the daemon; the chat shows
    /// an "Answer" banner instead of the sheet until they reopen or the daemon resolves it.
    @Published private(set) var parkedQuestion: PendingQuestionUI?
    @Published private(set) var parkedPermission: PendingPermissionUI?
    @Published var draftText = ""
    @Published var selectedModel: String = ""
    @Published var selectedEffort: String = ""
    @Published var permissionMode: String = ""
    /// Prompts queued behind the running turn (daemon-owned; cancellable one by one).
    @Published private(set) var queued: [QueuedPrompt] = []
    /// Composer attachment chips (uploading or uploaded to the host).
    @Published private(set) var attachments: [AttachmentChip] = []
    /// One-line acknowledgement under the composer ("Queued", "Stopping…"); self-explanatory
    /// problems stick until the next action replaces them.
    @Published var statusLine: String?
    /// Process view: show the agent's working steps under each message (this session only,
    /// persisted, off by default — web/Android parity).
    @Published private(set) var processView = false
    /// Expanded steps: ref → full body once fetched ("" while only the preview is available).
    @Published private(set) var openSteps: [String: String] = [:]

    private(set) var sessionId: String
    @Published private(set) var model: String = ""
    /// Exposed so the app model can match this chat against the status stream.
    @Published private(set) var currentJobId: String?

    /// Fired when a brand-new session gets a real daemon id (for list re-key).
    var onSessionIdResolved: ((String) -> Void)?

    private let client: DaemonClient
    /// Exposed for Live TUI sheet (same connection as this chat).
    var liveDaemonClient: DaemonClient { client }
    private weak var settings: SettingsStore?
    private var pollTask: Task<Void, Never>?
    private var permissionIndex: [String: Int] = [:]
    /// Gates the user swiped away — show the "Answer" banner instead of re-opening the sheet.
    private var dismissedQuestionIds: Set<String> = []
    private var dismissedPermissionIds: Set<String> = []
    /// Gates already answered/cancelled — show nothing while the daemon catches up.
    private var answeredGateIds: Set<String> = []
    /// True while watching a turn we didn't start (events were skipped, not replayed).
    private var attachedMidTurn = false
    /// Live process-view steps: refs already on screen, so mid-turn journal
    /// pulls only append what is new.
    private var shownStepRefs: Set<String> = []
    private var liveStepsAt = Date.distantPast
    private var liveStepsInFlight = false

    nonisolated var id: String { localId }
    var isBusy: Bool { phase == .running }
    var isUploadingAttachment: Bool { attachments.contains { $0.uploading } }

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
            processView = resume.map { s.processViewSessions.contains($0) } ?? false
        }
        if resume != nil {
            loadHistory()
        }
    }

    // MARK: - Process view

    func setProcessView(_ on: Bool) {
        guard !sessionId.isEmpty else { return }
        processView = on
        if on {
            settings?.settings.processViewSessions.insert(sessionId)
        } else {
            settings?.settings.processViewSessions.remove(sessionId)
            openSteps = [:]
        }
        // Steps only arrive with ?detail=steps — refetch either way so toggling
        // off also drops them.
        if phase != .running { loadHistory() }
    }

    /// Mid-turn journal pull: append steps the transcript has not shown yet. A small tail
    /// window is enough (only the running turn's messages grow steps; previews are capped
    /// daemon-side), and the turn-end reload replaces everything with the canonical order.
    private func maybeFetchLiveSteps() {
        guard processView, !sessionId.isEmpty, !liveStepsInFlight,
              Date().timeIntervalSince(liveStepsAt) > 1.2,
              let agentClient = client.agentClient else { return }
        liveStepsAt = Date()
        liveStepsInFlight = true
        let sid = sessionId
        Task {
            defer { liveStepsInFlight = false }
            guard let response = try? await agentClient.messages(sessionId: sid, limit: 5, steps: true),
                  sid == sessionId, processView else { return }
            var fresh: [TimelineItem] = []
            for message in response.messages {
                for step in message.steps where !step.ref.isEmpty && !shownStepRefs.contains(step.ref) {
                    shownStepRefs.insert(step.ref)
                    fresh.append(.step(id: "step-\(step.ref)", step: step))
                }
            }
            if !fresh.isEmpty { items.append(contentsOf: fresh) }
        }
    }

    /// Expand/collapse one step. First expand of a truncated step fetches the full body —
    /// that is what keeps a 200KB tool result out of every window fetch.
    func toggleStep(_ step: ProcessStep) {
        if openSteps[step.ref] != nil {
            openSteps.removeValue(forKey: step.ref)
            return
        }
        openSteps[step.ref] = ""
        guard step.truncated, let agentClient = client.agentClient, !sessionId.isEmpty else { return }
        let sid = sessionId
        Task {
            do {
                let full = try await agentClient.stepText(sessionId: sid, ref: step.ref)
                if openSteps[step.ref] != nil, !full.text.isEmpty {
                    openSteps[step.ref] = full.text
                }
            } catch {
                statusLine = DaemonClient.describe(error)
            }
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
        guard let caps = ping?.caps(for: provider) else { return false }
        // Same fallback chain as Android: live_tui, else "interactive implies a host TUI exists".
        return caps.liveTuiEnabled || caps.interactive
    }

    /// Per-harness, never the multi root's union — a union would offer rewind on a harness whose
    /// daemon can't perform it.
    var canRewind: Bool {
        ping?.caps(for: provider).rewindEnabled ?? false
    }

    var availableSlashCommands: [String] {
        var list = ping?.slashCommands(for: provider) ?? client.availableSlashCommands
        // Always surface these so older daemons / every client can invoke them.
        for cmd in ["/rewind", "/goal"] where !list.contains(cmd) {
            list.append(cmd)
        }
        return list
    }

    // MARK: - Send

    func send() {
        let text = draftText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        if isUploadingAttachment {
            statusLine = "Still uploading an attachment — one moment."
            return
        }
        if text.hasPrefix("!") {
            draftText = ""
            runShellCommand(String(text.dropFirst()).trimmingCharacters(in: .whitespaces))
            return
        }
        if let refusal = slashRefusal(text) {
            statusLine = refusal
            return
        }
        var outgoing = text
        for chip in attachments where !chip.hostPath.isEmpty {
            outgoing += "\n[attached: \(chip.hostPath)]"
        }
        draftText = ""
        attachments = []
        statusLine = nil

        // Mid-turn: type into the interactive TUI, or queue behind the headless job.
        if phase == .running, let jobId = currentJobId {
            items.append(.userText(id: UUID().uuidString, text: outgoing))
            Task {
                guard let agentClient = client.agentClient else { return }
                do {
                    if permissionMode == "interactive" {
                        try await agentClient.typeIntoTui(jobId: jobId, prompt: outgoing)
                        statusLine = "Typed into the session."
                    } else {
                        let list = try await agentClient.queuePrompt(jobId: jobId, prompt: outgoing)
                        queued = list
                        statusLine = "Queued behind the running turn."
                    }
                } catch {
                    statusLine = DaemonClient.describe(error)
                }
            }
            return
        }

        sendTurn(outgoing, echo: true)
    }

    /// Refuse slash commands the daemon can't run — a swallowed `/compact` looks like a hang.
    private func slashRefusal(_ text: String) -> String? {
        guard text.hasPrefix("/") else { return nil }
        let name = text.split(separator: " ", maxSplits: 1).first.map(String.init) ?? text
        // Only things shaped like a command; "/path/to/file" is a message.
        guard name.range(of: "^/[A-Za-z][A-Za-z0-9_-]*$", options: .regularExpression) != nil else {
            return nil
        }
        if permissionMode != "interactive" && name != "/rewind" {
            return "\(name) needs interactive execution — switch Execution to Interactive first."
        }
        let known = availableSlashCommands
        if known.isEmpty { return "This daemon does not advertise any slash commands." }
        if !known.contains(name) {
            let sample = known.prefix(6).joined(separator: " ")
            return "Unknown command \(name) — try: \(sample)"
        }
        return nil
    }

    private func sendTurn(_ prompt: String, echo: Bool) {
        if echo {
            items.append(.userText(id: UUID().uuidString, text: prompt))
        }
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
                        prompt: prompt,
                        provider: provider.isEmpty ? nil : provider,
                        permissionMode: permissionMode.isEmpty ? nil : permissionMode,
                        model: selectedModel.isEmpty ? nil : selectedModel,
                        effort: selectedEffort.isEmpty ? nil : selectedEffort
                    ))
                } else {
                    jobId = try await agentClient.continueSession(id: sessionId, ContinueSessionRequest(
                        prompt: prompt,
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

    // MARK: - Shell (`!command`)

    /// Runs one host shell command, echoes its output, then re-sends the exchange as a silent
    /// context turn so it persists in the transcript (same format the Android client writes).
    private func runShellCommand(_ command: String) {
        guard !command.isEmpty else { return }
        items.append(.userText(id: UUID().uuidString, text: "! \(command)"))
        statusLine = "Running on the host…"
        Task {
            guard let agentClient = client.agentClient else {
                statusLine = "Not connected."
                return
            }
            do {
                let result = try await agentClient.runShell(
                    command: command,
                    sessionId: sessionId.isEmpty ? nil : sessionId,
                    cwd: sessionId.isEmpty && !cwd.isEmpty ? cwd : nil
                )
                var output = result.output
                if output.count > 8000 { output = String(output.prefix(8000)) + "\n…(truncated)" }
                let display = output.isEmpty ? "(no output)" : output
                items.append(.assistantText(
                    id: UUID().uuidString,
                    text: "```\n\(display)\n```"
                ))
                statusLine = result.ok ? nil : "Exit code \(result.exitCode)"
                // Persist into the conversation so the agent sees it — only once a session exists.
                if !sessionId.isEmpty {
                    let turn = "[shell] ! \(command)\n[output]\n```\n\(display)\n```\n"
                        + "[silent] Shell result for context only. Do not reply or acknowledge this message."
                    sendTurn(turn, echo: false)
                }
            } catch {
                statusLine = DaemonClient.describe(error)
            }
        }
    }

    // MARK: - Rewind

    /// How many of the user's messages a rewind to this row would drop (the row itself included).
    func rewindDropCount(itemId: String) -> Int? {
        guard let index = items.firstIndex(where: { $0.id == itemId }),
              case .userText = items[index] else { return nil }
        var count = 0
        for item in items[index...] {
            if case .userText(_, let text) = item, !text.hasPrefix("[shell] !") {
                count += 1
            }
        }
        return count
    }

    /// Drops the last `count` user messages and everything after them (conversation only — file
    /// changes on the host are not reverted). Callers must confirm with the user first.
    func rewind(dropping count: Int) {
        guard count > 0, !sessionId.isEmpty, phase != .running else { return }
        sendTurn("/rewind \(count)", echo: true)
    }

    // MARK: - Attachments

    func addAttachment(name: String, data: Data) {
        let chip = AttachmentChip(id: UUID().uuidString, name: name)
        attachments.append(chip)
        Task {
            guard let agentClient = client.agentClient else {
                markAttachment(chip.id) { $0.uploading = false; $0.error = "Not connected." }
                return
            }
            do {
                let response = try await agentClient.uploadAttachment(name: name, data: data)
                markAttachment(chip.id) { $0.uploading = false; $0.hostPath = response.path }
            } catch {
                markAttachment(chip.id) { $0.uploading = false; $0.error = DaemonClient.describe(error) }
                statusLine = DaemonClient.describe(error)
            }
        }
    }

    func removeAttachment(id: String) {
        attachments.removeAll { $0.id == id }
    }

    private func markAttachment(_ id: String, _ change: (inout AttachmentChip) -> Void) {
        guard let idx = attachments.firstIndex(where: { $0.id == id }) else { return }
        var chip = attachments[idx]
        change(&chip)
        attachments[idx] = chip
    }

    // MARK: - Gates (permission / question)

    func respondToPermission(approved: Bool) {
        guard let prompt = pendingPermission ?? parkedPermission, let jobId = currentJobId else { return }
        pendingPermission = nil
        parkedPermission = nil
        answeredGateIds.insert(prompt.requestId)
        Task {
            try? await client.agentClient?.resolvePermission(jobId: jobId, requestId: prompt.requestId, allow: approved)
        }
    }

    /// Swipe-dismiss: hide the sheet but leave the gate pending on the daemon — the "Answer"
    /// banner takes over. Dismissing must never silently deny.
    func parkPermission() {
        guard let prompt = pendingPermission else { return }
        dismissedPermissionIds.insert(prompt.requestId)
        parkedPermission = prompt
        pendingPermission = nil
    }

    func reopenPermission() {
        guard let prompt = parkedPermission else { return }
        dismissedPermissionIds.remove(prompt.requestId)
        parkedPermission = nil
        pendingPermission = prompt
    }

    func respondToQuestion(answers: [[String]], notes: [String]?, cancel: Bool) {
        guard let q = pendingQuestion ?? parkedQuestion, let jobId = currentJobId else { return }
        pendingQuestion = nil
        parkedQuestion = nil
        answeredGateIds.insert(q.requestId)
        Task {
            do {
                try await client.agentClient?.resolveQuestion(
                    jobId: jobId,
                    QuestionAnswerRequest(requestId: q.requestId, answers: cancel ? nil : answers, notes: notes, cancel: cancel)
                )
            } catch {
                // The gate is still open on the daemon — un-suppress so the next poll re-offers it.
                answeredGateIds.remove(q.requestId)
                statusLine = DaemonClient.describe(error)
            }
        }
    }

    func parkQuestion() {
        guard let q = pendingQuestion else { return }
        dismissedQuestionIds.insert(q.requestId)
        parkedQuestion = q
        pendingQuestion = nil
    }

    func reopenQuestion() {
        guard let q = parkedQuestion else { return }
        dismissedQuestionIds.remove(q.requestId)
        parkedQuestion = nil
        pendingQuestion = q
    }

    func interrupt() {
        guard let jobId = currentJobId, phase == .running else { return }
        statusLine = "Stopping…"
        Task { try? await client.agentClient?.stopJob(jobId: jobId) }
    }

    func cancelQueued(id: String) {
        guard let jobId = currentJobId else { return }
        Task {
            do {
                let response = try await client.agentClient?.cancelQueued(jobId: jobId, queuedId: id)
                if let response { queued = response.queued }
            } catch {
                statusLine = DaemonClient.describe(error)
            }
        }
    }

    // MARK: - Attach to a turn started elsewhere

    /// Adopt a job the status stream reports for this session (desktop TUI turn, queued chain,
    /// or a finished job holding an open question gate — daemon ≥ 2.6.5 keeps those in the feed).
    ///
    /// Polls from the job's current cursor, not 0 — replaying events would double every text
    /// block the history load already brought in. The text this skips is backfilled by a full
    /// history reload when the turn ends.
    func attach(to job: ActiveJobStatus) {
        guard currentJobId == nil, phase != .running else { return }
        currentJobId = job.jobId
        phase = .running
        attachedMidTurn = true
        pollTask?.cancel()
        let cursor = max(0, job.nextSeq)
        pollTask = Task { await pollJob(job.jobId, from: cursor) }
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

    /// Plain-text export of the visible conversation ("You:" / "Agent:" rows only).
    var plainTranscript: String {
        items.compactMap { item -> String? in
            switch item {
            case .userText(_, let text): return "You: \(text)"
            case .assistantText(_, let text): return "Agent: \(text)"
            default: return nil
            }
        }.joined(separator: "\n\n")
    }

    /// Re-fetch the transcript tail (toolbar Refresh) — safe on a session that got its id mid-chat.
    func reloadHistory() {
        guard phase != .running else { return }
        loadHistory()
    }

    private func loadHistory() {
        let historyId = sessionId.isEmpty ? resumeId : sessionId
        guard let resumeId = historyId else { return }
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
                let response = try await agentClient.messages(sessionId: resumeId, steps: processView)
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
                        loaded.append(contentsOf: Self.splitStoredShellTurn(message))
                    case "status":
                        loaded.append(.systemNotice(id: message.id, text: message.text))
                    default:
                        loaded.append(.assistantText(id: message.id, text: message.text))
                    }
                    // Steps hang under the message they FOLLOWED — the daemon
                    // attaches after, so top-to-bottom is the order it happened.
                    for step in message.steps where !step.ref.isEmpty {
                        loaded.append(.step(id: "step-\(step.ref)", step: step))
                    }
                }
                // Re-seed the live-steps dedupe with what this fetch painted.
                shownStepRefs = Set(response.messages.flatMap { $0.steps.map(\.ref) })
                items = loaded
            } catch {
                items = [.systemNotice(id: UUID().uuidString, text: "Couldn't load history: \(DaemonClient.describe(error))")]
            }
        }
    }

    /// A stored `!command` turn ("[shell] ! …\n[output]\n```…```\n[silent] …") replays as the
    /// command row plus its output row, with the [silent] directive stripped — matches Android.
    private static func splitStoredShellTurn(_ message: SessionMessage) -> [TimelineItem] {
        let text = message.text
        guard text.hasPrefix("[shell] !") else {
            return [.userText(id: message.id, text: text)]
        }
        var lines = text.components(separatedBy: "\n")
        let command = String(lines.removeFirst().dropFirst("[shell] ".count))
        if lines.first == "[output]" { lines.removeFirst() }
        if let silent = lines.lastIndex(where: { $0.hasPrefix("[silent]") }) {
            lines.removeSubrange(silent...)
        }
        let output = lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        var result: [TimelineItem] = [.userText(id: message.id, text: command)]
        if !output.isEmpty {
            result.append(.assistantText(id: message.id + "-out", text: output))
        }
        return result
    }

    // MARK: - Job polling

    private func startPolling(_ jobId: String) {
        pollTask?.cancel()
        currentJobId = jobId
        pollTask = Task { await pollJob(jobId) }
    }

    private func pollJob(_ initialJobId: String, from startSeq: Int = 0) async {
        var jobId = initialJobId
        var since = startSeq
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
            queued = job.queued
            if !job.resolvedSessionId.isEmpty {
                let first = sessionId.isEmpty
                sessionId = job.resolvedSessionId
                if first { onSessionIdResolved?(sessionId) }
            }

            for event in job.events { apply(event, job: job) }

            syncPermissionGate(job.pendingPermission)
            syncQuestionGate(job.pendingQuestion)

            switch job.status {
            case .starting, .running:
                // Process view: tool_use/tool_result live in the journal, not
                // this event stream — pull the tail (throttled) so they appear
                // while the turn runs instead of all at once at the end.
                maybeFetchLiveSteps()
                try? await Task.sleep(nanoseconds: 700_000_000)
                continue
            case .done, .error, .stopped:
                // Claude can fire Stop while AskUserQuestion is still on screen — the daemon
                // holds the gate open past job end (2.6.5). Keep the Answer UI alive and keep
                // polling until the gate resolves; finishing now would strand the waiter.
                if job.pendingQuestion?.requestId.isEmpty == false
                    || job.pendingPermission?.requestId.isEmpty == false {
                    try? await Task.sleep(nanoseconds: 700_000_000)
                    continue
                }
                if job.droppedQueued > 0 {
                    items.append(.systemNotice(
                        id: UUID().uuidString,
                        text: "\(job.droppedQueued) queued message(s) were dropped because this turn didn't finish cleanly."
                    ))
                }
                if !job.nextJobId.isEmpty {
                    // The daemon auto-started the next queued prompt — follow the chain in place.
                    jobId = job.nextJobId
                    currentJobId = jobId
                    since = 0
                    continue
                }
                finishTurn(job)
                return
            }
        }
    }

    private func syncPermissionGate(_ pending: PendingPermission?) {
        guard let pending, !pending.requestId.isEmpty else {
            pendingPermission = nil
            parkedPermission = nil
            return
        }
        if answeredGateIds.contains(pending.requestId) { return }
        let ui = PendingPermissionUI(requestId: pending.requestId, toolName: pending.toolName, detail: pending.detail)
        if dismissedPermissionIds.contains(pending.requestId) {
            if pendingPermission == nil { parkedPermission = ui }
        } else {
            parkedPermission = nil
            pendingPermission = ui
        }
    }

    private func syncQuestionGate(_ pending: PendingQuestion?) {
        guard let pending, !pending.requestId.isEmpty else {
            // The daemon dropped the gate (answered elsewhere / cancelled / turn moved on).
            pendingQuestion = nil
            parkedQuestion = nil
            return
        }
        if answeredGateIds.contains(pending.requestId) { return }
        let ui = PendingQuestionUI(requestId: pending.requestId, questions: pending.questions)
        if dismissedQuestionIds.contains(pending.requestId) {
            if pendingQuestion == nil { parkedQuestion = ui }
        } else {
            parkedQuestion = nil
            pendingQuestion = ui
        }
    }

    private func finishTurn(_ job: JobSnapshot) {
        currentJobId = nil
        pendingQuestion = nil
        parkedQuestion = nil
        pendingPermission = nil
        parkedPermission = nil
        queued = []
        // A job that ends .error without a result event otherwise surfaces nothing at all.
        if job.status == .error {
            let lastShowsError: Bool = {
                if case .turnResult(_, _, let isError) = items.last { return isError }
                return false
            }()
            if !lastShowsError {
                items.append(.turnResult(
                    id: UUID().uuidString,
                    summary: job.error.isEmpty ? "The turn ended with an error." : job.error,
                    isError: true
                ))
            }
        } else if job.status == .stopped {
            items.append(.systemNotice(id: UUID().uuidString, text: "Stopped."))
        }
        if statusLine == "Stopping…" { statusLine = nil }
        phase = .idle
        // An attached watch skipped this turn's earlier events — swap in the durable
        // transcript. Process view reloads too: the finished turn's steps live in the
        // journal, which only `?detail=steps` carries.
        if attachedMidTurn || processView {
            attachedMidTurn = false
            loadHistory()
        }
    }

    private func apply(_ event: JobEvent, job: JobSnapshot) {
        switch event {
        case .initEvent(_, _, let model):
            if model != "interactive" { self.model = model }

        case .text(_, let text):
            // Attaching to a mid-flight job replays its events from 0 — drop exact repeats of a
            // block that's already on screen instead of doubling the transcript.
            if case .assistantText(_, let last) = items.last, last == text { break }
            items.append(.assistantText(id: UUID().uuidString, text: text))

        case .tool(_, let name, let detail):
            if case .toolCall(_, let lastName, let lastDetail) = items.last,
               lastName == name, lastDetail == detail { break }
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
            if permissionIndex[requestId] == nil {
                permissionIndex[requestId] = items.count
                items.append(.permissionRequest(id: requestId, toolName: toolName, detail: detail, resolution: nil))
            }

        case .permissionResolved(_, let requestId, let allow, let reason):
            if let index = permissionIndex[requestId], case .permissionRequest(let id, let toolName, let detail, _) = items[index] {
                items[index] = .permissionRequest(
                    id: id, toolName: toolName, detail: detail,
                    resolution: allow ? .allowed : .denied(reason: reason)
                )
            }

        case .question(_, let requestId, let questions):
            if !dismissedQuestionIds.contains(requestId), !answeredGateIds.contains(requestId) {
                pendingQuestion = PendingQuestionUI(requestId: requestId, questions: questions)
            }

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
