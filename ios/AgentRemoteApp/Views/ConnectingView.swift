import SwiftUI

struct ConnectingView: View {
    let profile: ServerProfile
    /// Bubbled up to the split view's detail column when a session is opened (iPad); ignored on iPhone.
    @Binding var selectedChat: ChatViewModel?

    /// Persists for as long as this server is selected — owns the connection and the open chats, so
    /// returning to the session list never reconnects.
    @StateObject private var hub: SessionHub
    @Environment(\.scenePhase) private var scenePhase
    /// Once connected at least once, keep the session UI mounted (no persistent connection to lose
    /// in this REST-based protocol, so there's nothing to reconnect defensively for).
    @State private var hasConnectedOnce = false

    init(profile: ServerProfile, selectedChat: Binding<ChatViewModel?>) {
        self.profile = profile
        self._selectedChat = selectedChat
        self._hub = StateObject(wrappedValue: SessionHub(profile: profile))
    }

    private var client: DaemonClient { hub.client }

    var body: some View {
        Group {
            if hasConnectedOnce {
                ProjectView(profile: profile, hub: hub, selectedChat: $selectedChat)
            } else {
                initialConnectView
            }
        }
        .navigationTitle(profile.name)
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: client.state) { _, state in
            if case .connected = state { hasConnectedOnce = true }
        }
        .task { await hub.connectIfNeeded() }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: hub.appDidBecomeActive()
            case .background, .inactive: hub.appDidResignActive()
            @unknown default: break
            }
        }
    }

    /// Full-screen state shown before the first successful connect.
    @ViewBuilder
    private var initialConnectView: some View {
        switch client.state {
        case .failed(let message):
            VStack(spacing: 16) {
                Image(systemName: "exclamationmark.triangle")
                    .font(.largeTitle)
                    .foregroundStyle(.orange)
                Text(message)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal)
                Button("Retry") { Task { await hub.retry() } }
            }
        default:
            VStack(spacing: 16) {
                ProgressView()
                Text("Connecting…").foregroundStyle(.secondary)
            }
        }
    }
}
