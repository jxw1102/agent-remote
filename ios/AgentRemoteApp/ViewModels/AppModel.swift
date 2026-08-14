import AgentRemoteKit
import Combine
import Foundation

/// Multi-host app brain — merges sessions from every saved daemon into one list
/// (Android AgentRepository parity). Owns per-profile connections and open chats.
@MainActor
final class AppModel: ObservableObject {
    let profileStore: ProfileStore
    let settingsStore: SettingsStore

    @Published private(set) var rows: [SessionRow] = []
    @Published private(set) var feeds: [UUID: ProfileFeed] = [:]
    @Published private(set) var isLoading = false
    @Published var query: String = ""
    @Published var profileFilter: UUID?
    @Published private(set) var pings: [UUID: PingResponse] = [:]
    @Published private(set) var connectionErrors: [UUID: String] = [:]
    /// Active jobs per profile (from /ws/status).
    @Published private(set) var activeJobs: [UUID: [String: ActiveJobStatus]] = [:]

    /// Open chats keyed by SessionRef.key or "profileId/new-localId" for brand-new.
    @Published private(set) var chats: [String: ChatViewModel] = [:]
    @Published var selectedChatKey: String?

    private var clients: [UUID: DaemonClient] = [:]
    private var clientObservers: [UUID: AnyCancellable] = [:]
    private var chatObservers: [String: AnyCancellable] = [:]
    private var refreshTask: Task<Void, Never>?
    private var searchDebounce: Task<Void, Never>?
    private var profileObserver: AnyCancellable?
    private var settingsObserver: AnyCancellable?

    private let perProfileLimit = 80

    init(profileStore: ProfileStore? = nil, settingsStore: SettingsStore? = nil) {
        // Construct MainActor stores in the init body (not default args — those are nonisolated).
        let profiles = profileStore ?? ProfileStore()
        let settings = settingsStore ?? SettingsStore()
        self.profileStore = profiles
        self.settingsStore = settings
        profileObserver = profiles.objectWillChange.sink { [weak self] _ in
            Task { @MainActor in
                self?.objectWillChange.send()
                self?.syncClients()
                self?.refreshEverything()
            }
        }
        settingsObserver = settings.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }
        syncClients()
    }

    var selectedChat: ChatViewModel? {
        guard let key = selectedChatKey else { return nil }
        return chats[key]
    }

    var filteredRows: [SessionRow] {
        guard let profileFilter else { return rows }
        return rows.filter { $0.ref.profileId == profileFilter }
    }

    var problems: [(name: String, message: String)] {
        feeds.values.compactMap { feed in
            guard let err = feed.error else { return nil }
            let name = profileStore.profiles.first(where: { $0.id == feed.profileId })?.name ?? "Daemon"
            return (name, err)
        }
    }

    // MARK: - Clients

    func client(for profileId: UUID) -> DaemonClient? { clients[profileId] }

    func ping(for profileId: UUID) -> PingResponse? { pings[profileId] }

    func syncClients() {
        let profiles = profileStore.profiles
        let ids = Set(profiles.map(\.id))
        for id in clients.keys where !ids.contains(id) {
            clients[id]?.pauseStatusStream()
            clients.removeValue(forKey: id)
            clientObservers.removeValue(forKey: id)
            pings.removeValue(forKey: id)
            connectionErrors.removeValue(forKey: id)
            activeJobs.removeValue(forKey: id)
        }
        for profile in profiles {
            if clients[profile.id] == nil {
                let dc = DaemonClient()
                clients[profile.id] = dc
                clientObservers[profile.id] = dc.objectWillChange.sink { [weak self] _ in
                    Task { @MainActor in
                        self?.ingestClient(profile.id)
                        self?.objectWillChange.send()
                    }
                }
            }
        }
    }

    func connectAll() {
        syncClients()
        for profile in profileStore.profiles {
            Task { await connect(profile) }
        }
    }

    func connect(_ profile: ServerProfile) async {
        guard let dc = clients[profile.id] else { return }
        guard let token = KeychainStore.get(account: KeychainStore.account(profileId: profile.id, kind: .authToken)) else {
            connectionErrors[profile.id] = "No auth token saved. Delete and re-add this server."
            feeds[profile.id] = ProfileFeed(profileId: profile.id, error: connectionErrors[profile.id])
            return
        }
        await dc.connect(baseURLString: profile.serverURLString, authToken: token)
        ingestClient(profile.id)
    }

    private func ingestClient(_ profileId: UUID) {
        guard let dc = clients[profileId] else { return }
        switch dc.state {
        case .connected(let ping):
            pings[profileId] = ping
            connectionErrors.removeValue(forKey: profileId)
            activeJobs[profileId] = dc.activeJobs
        case .failed(let message):
            connectionErrors[profileId] = message
            activeJobs[profileId] = [:]
        default:
            break
        }
        // Mirror active jobs map continuously
        activeJobs[profileId] = dc.activeJobs
    }

    func appDidBecomeActive() {
        for dc in clients.values { dc.resumeStatusStreamIfNeeded() }
        connectAll()
        refreshEverything()
    }

    func appDidResignActive() {
        for dc in clients.values { dc.pauseStatusStream() }
    }

    // MARK: - Session list

    func setQuery(_ value: String) {
        query = value
        searchDebounce?.cancel()
        searchDebounce = Task {
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }
            await refreshSessions()
        }
    }

    func clearQuery() {
        query = ""
        Task { await refreshSessions() }
    }

    func refreshEverything() {
        refreshTask?.cancel()
        refreshTask = Task {
            await withTaskGroup(of: Void.self) { group in
                for profile in profileStore.profiles {
                    group.addTask { await self.connect(profile) }
                }
            }
            await refreshSessions()
        }
    }

    func refreshSessions() async {
        let profiles = profileStore.profiles
        guard !profiles.isEmpty else {
            rows = []
            feeds = [:]
            isLoading = false
            return
        }
        isLoading = true
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let showAll = settingsStore.settings.showAllSessions
        var nextFeeds: [UUID: ProfileFeed] = [:]
        for p in profiles {
            nextFeeds[p.id] = ProfileFeed(profileId: p.id, loading: true)
        }
        feeds = nextFeeds

        await withTaskGroup(of: (UUID, Result<[SessionRow], Error>).self) { group in
            for profile in profiles {
                group.addTask {
                    let result: Result<[SessionRow], Error>
                    do {
                        let rows = try await self.fetchRows(profile: profile, query: q, all: showAll)
                        result = .success(rows)
                    } catch {
                        result = .failure(error)
                    }
                    return (profile.id, result)
                }
            }
            var collected: [SessionRow] = []
            for await (profileId, result) in group {
                switch result {
                case .success(let list):
                    collected.append(contentsOf: list)
                    nextFeeds[profileId] = ProfileFeed(profileId: profileId, count: list.count)
                case .failure(let error):
                    nextFeeds[profileId] = ProfileFeed(
                        profileId: profileId,
                        error: DaemonClient.describe(error)
                    )
                }
            }
            // Dedupe by session id (same UUID shouldn't appear twice across hosts normally).
            var seen = Set<String>()
            var unique: [SessionRow] = []
            for row in collected.sorted(by: { $0.sortKey > $1.sortKey }) {
                if seen.insert(row.session.id).inserted {
                    unique.append(row)
                }
            }
            rows = unique
            feeds = nextFeeds
            isLoading = false
        }
    }

    private func fetchRows(profile: ServerProfile, query: String, all: Bool) async throws -> [SessionRow] {
        guard let dc = clients[profile.id], let agent = dc.agentClient else {
            // Try connect once
            await connect(profile)
            guard let agent = clients[profile.id]?.agentClient else {
                throw AgentRemoteError.daemon(status: 0, message: connectionErrors[profile.id] ?? "Not connected")
            }
            return try await load(agent: agent, profile: profile, query: query, all: all)
        }
        return try await load(agent: agent, profile: profile, query: query, all: all)
    }

    private func load(agent: AgentRemoteClient, profile: ServerProfile, query: String, all: Bool) async throws -> [SessionRow] {
        let sessions: [SessionSummary]
        if !query.isEmpty {
            sessions = try await agent.searchSessionSummaries(query: query, limit: perProfileLimit, all: all)
        } else if settingsStore.settings.focusMode {
            // Focus mode: only daemons that keep a focus list contribute — a profile without it
            // contributes nothing rather than dumping its whole session list into the view.
            guard pings[profile.id]?.focus == true else { return [] }
            sessions = try await agent.focusList().sessions
        } else {
            sessions = try await agent.sessions(limit: perProfileLimit, all: all)
        }
        let defaultProvider = pings[profile.id]?.provider ?? ""
        return sessions.map { s in
            let provider = s.provider.isEmpty ? defaultProvider : s.provider
            return SessionRow(
                ref: SessionRef(profileId: profile.id, sessionId: s.id),
                profileName: profile.name,
                provider: provider,
                session: s
            )
        }
    }

    // MARK: - Focus / titles

    /// Whether any connected daemon keeps a focus list (gates the Focus filter chip).
    var focusAvailable: Bool {
        pings.values.contains { $0.focus == true }
    }

    var focusMode: Bool { settingsStore.settings.focusMode }

    func setFocusMode(_ enabled: Bool) {
        guard settingsStore.settings.focusMode != enabled else { return }
        settingsStore.settings.focusMode = enabled
        Task { await refreshSessions() }
    }

    /// Enroll a session on the focus list (`member: true`) or take it off (`done`).
    func setFocusMember(_ row: SessionRow, member: Bool) async throws {
        guard let agent = clients[row.ref.profileId]?.agentClient else {
            throw AgentRemoteError.daemon(status: 0, message: "Not connected")
        }
        _ = try await agent.setFocusDone(sessionId: row.ref.sessionId, done: !member)
        await refreshSessions()
    }

    /// Read cursor — a finished turn the user just opened stops glowing on the focus list.
    /// Fire-and-forget; skipped entirely when the daemon has no focus support.
    func markSeen(_ row: SessionRow) {
        guard pings[row.ref.profileId]?.focus == true else { return }
        guard let agent = clients[row.ref.profileId]?.agentClient else { return }
        let sessionId = row.ref.sessionId
        Task { try? await agent.markFocusSeen(sessionId: sessionId) }
    }

    /// Rename a session (empty title reverts to the derived one). Returns the stored title.
    @discardableResult
    func renameSession(_ row: SessionRow, title: String) async throws -> String {
        guard let agent = clients[row.ref.profileId]?.agentClient else {
            throw AgentRemoteError.daemon(status: 0, message: "Not connected")
        }
        let response = try await agent.setTitle(sessionId: row.ref.sessionId, title: title)
        await refreshSessions()
        return response.title
    }

    /// Ask the daemon's model to re-derive a title from the transcript — returns the suggestion
    /// (not yet saved; pass it to `renameSession` to keep it).
    func regenerateTitle(_ row: SessionRow) async throws -> String {
        guard let agent = clients[row.ref.profileId]?.agentClient else {
            throw AgentRemoteError.daemon(status: 0, message: "Not connected")
        }
        return try await agent.regenerateTitle(sessionId: row.ref.sessionId).title
    }

    func focusSupported(_ row: SessionRow) -> Bool {
        pings[row.ref.profileId]?.focus == true
    }

    // MARK: - Working state

    private func activeJob(ref: SessionRef) -> ActiveJobStatus? {
        guard let jobs = activeJobs[ref.profileId]?.values else { return nil }
        return jobs.first {
            $0.sessionId == ref.sessionId || $0.resolvedSessionId == ref.sessionId
        }
    }

    func isWorking(ref: SessionRef) -> Bool {
        activeJob(ref: ref) != nil
    }

    /// A running job waiting on the human — permission prompt or AskUserQuestion.
    func isBlocked(ref: SessionRef) -> Bool {
        guard let job = activeJob(ref: ref) else { return false }
        return job.pendingPermission || job.pendingQuestion
    }

    func isWorking(chat: ChatViewModel) -> Bool {
        chat.isBusy || (!chat.sessionId.isEmpty && isWorking(ref: SessionRef(profileId: chat.profileId, sessionId: chat.sessionId)))
    }

    /// The status-stream frame for this chat's turn: by job id first (a brand-new session has no
    /// session id yet), then by session id (a turn started from the desktop or a queued chain).
    func activeStatus(for chat: ChatViewModel) -> ActiveJobStatus? {
        guard let jobs = activeJobs[chat.profileId]?.values else { return nil }
        if let jobId = chat.currentJobId, let match = jobs.first(where: { $0.jobId == jobId }) {
            return match
        }
        guard !chat.sessionId.isEmpty else { return nil }
        return jobs.first {
            $0.sessionId == chat.sessionId || $0.resolvedSessionId == chat.sessionId
        }
    }

    // MARK: - Open chat

    func open(row: SessionRow) -> ChatViewModel {
        let key = row.ref.key
        markSeen(row)
        if let existing = chats[key] {
            selectedChatKey = key
            return existing
        }
        guard let dc = clients[row.ref.profileId] else {
            // Create a shell that will fail gracefully
            let chat = ChatViewModel(
                client: DaemonClient(),
                profileId: row.ref.profileId,
                cwd: row.session.cwd,
                sessionName: row.session.displayTitle,
                resume: row.session.id,
                provider: row.resolvedProvider,
                settings: settingsStore
            )
            cache(chat, key: key)
            selectedChatKey = key
            return chat
        }
        let chat = ChatViewModel(
            client: dc,
            profileId: row.ref.profileId,
            cwd: row.session.cwd,
            sessionName: row.session.displayTitle,
            resume: row.session.id,
            provider: row.resolvedProvider,
            settings: settingsStore
        )
        cache(chat, key: key)
        selectedChatKey = key
        return chat
    }

    /// Brand-new session (no resume id yet).
    func openNew(profileId: UUID, cwd: String, provider: String, name: String? = nil) -> ChatViewModel {
        let dc = clients[profileId] ?? DaemonClient()
        let chat = ChatViewModel(
            client: dc,
            profileId: profileId,
            cwd: cwd,
            sessionName: name,
            resume: nil,
            provider: provider,
            settings: settingsStore
        )
        let key = "\(profileId.uuidString)/new-\(chat.localId)"
        cache(chat, key: key)
        selectedChatKey = key
        // When the first job assigns a real session id, re-key the cache.
        chat.onSessionIdResolved = { [weak self, weak chat] sid in
            guard let self, let chat, !sid.isEmpty else { return }
            let newKey = SessionRef(profileId: profileId, sessionId: sid).key
            if self.chats[newKey] == nil {
                self.chats[newKey] = chat
                if self.selectedChatKey == key {
                    self.selectedChatKey = newKey
                }
                self.chats.removeValue(forKey: key)
                self.chatObservers[newKey] = self.chatObservers.removeValue(forKey: key)
            }
            Task { await self.refreshSessions() }
        }
        return chat
    }

    private func cache(_ chat: ChatViewModel, key: String) {
        chats[key] = chat
        chatObservers[key] = chat.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }
    }

    func selectChat(key: String?) {
        selectedChatKey = key
    }
}
