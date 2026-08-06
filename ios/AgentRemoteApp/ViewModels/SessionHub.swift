import AgentRemoteKit
import Combine
import Foundation

/// One per connected server. Owns the `DaemonClient` and caches open chats keyed by session id, so
/// switching between sessions is instant and preserves state. Unlike the old SSH/WebSocket
/// transport, there's no persistent per-session connection to lose — every REST call is
/// independent — so there's nothing to "reestablish" on foreground; only the best-effort
/// `/ws/status` stream gets restarted.
@MainActor
final class SessionHub: ObservableObject {
    let client = DaemonClient()
    let profile: ServerProfile

    /// Open chats keyed by their resume id (a resumed session) or their localId (a brand-new one).
    @Published private(set) var chats: [String: ChatViewModel] = [:]

    private var statusObservers: [String: AnyCancellable] = [:]
    private var clientObserver: AnyCancellable?

    init(profile: ServerProfile) {
        self.profile = profile
        clientObserver = client.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
    }

    /// Returns the already-open chat for a resumed session, or creates and caches a new one.
    func chat(resumeId: String?, cwd: String, name: String?) -> ChatViewModel {
        if let resumeId, let existing = chats[resumeId] { return existing }
        let chat = ChatViewModel(client: client, cwd: cwd, sessionName: name, resume: resumeId)
        let key = resumeId ?? chat.localId
        chats[key] = chat
        statusObservers[key] = chat.objectWillChange.sink { [weak self] _ in self?.objectWillChange.send() }
        return chat
    }

    /// The open chat for a session row in the picker, if one exists (keyed by the row's session id).
    func openChat(forResumeId id: String) -> ChatViewModel? {
        chats[id]
    }

    // MARK: - Connection lifecycle

    func connectIfNeeded() async {
        switch client.state {
        case .connected, .connecting:
            return
        case .disconnected, .failed:
            await connect()
        }
    }

    func retry() async {
        await connect()
    }

    func appDidBecomeActive() {
        client.resumeStatusStreamIfNeeded()
        Task { await connectIfNeeded() }
    }

    func appDidResignActive() {
        client.pauseStatusStream()
    }

    private func connect() async {
        guard let authToken = KeychainStore.get(account: KeychainStore.account(profileId: profile.id, kind: .authToken)) else {
            client.reportFailure("No auth token saved for this server. Delete it and add it again.")
            return
        }
        await client.connect(baseURLString: profile.serverURLString, authToken: authToken)
    }
}
