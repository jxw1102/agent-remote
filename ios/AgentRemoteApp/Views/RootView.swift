import SwiftUI

/// App shell: NavigationSplitView with unified sessions (sidebar) + transcript (detail).
/// Collapses to a stack on iPhone; two-column master-detail on iPad.
struct RootView: View {
    @EnvironmentObject private var appModel: AppModel
    @EnvironmentObject private var profileStore: ProfileStore
    @Environment(\.horizontalSizeClass) private var sizeClass

    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    @State private var showNewSession = false
    @State private var showProfiles = false
    @State private var showSettings = false
    @State private var showUsage = false
    @State private var showDrop = false

    var body: some View {
        navigationShell
        .sheet(isPresented: $showNewSession) {
            NewSessionView { profileId, cwd, provider in
                showNewSession = false
                _ = appModel.openNew(profileId: profileId, cwd: cwd, provider: provider)
            }
        }
        .sheet(isPresented: $showProfiles) {
            ProfilesView()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .sheet(isPresented: $showUsage) {
            UsageView()
        }
        .sheet(isPresented: $showDrop) {
            DropView()
        }
    }

    /// iPad (regular width): two-column split view. iPhone (compact): a plain NavigationStack —
    /// NavigationSplitView ignores programmatic `columnVisibility` in compact width, so the
    /// split-view variant could show a session ONLY on iPad; the push has to be a real
    /// `navigationDestination` driven by the selected chat.
    @ViewBuilder
    private var navigationShell: some View {
        if sizeClass == .compact {
            NavigationStack {
                sessionsList
                    .navigationDestination(isPresented: Binding(
                        get: { appModel.selectedChat != nil },
                        set: { if !$0 { appModel.selectChat(key: nil) } }
                    )) {
                        if let chat = appModel.selectedChat {
                            ChatView(viewModel: chat)
                                .id(chat.id)
                        }
                    }
            }
        } else {
            NavigationSplitView(columnVisibility: $columnVisibility) {
                sessionsList
                    .navigationSplitViewColumnWidth(min: 280, ideal: 340, max: 480)
            } detail: {
                detailColumn
            }
            .navigationSplitViewStyle(.balanced)
        }
    }

    private var sessionsList: some View {
        SessionsListView(
            onOpen: { row in
                _ = appModel.open(row: row)
            },
            onNewSession: { showNewSession = true },
            onProfiles: { showProfiles = true },
            onSettings: { showSettings = true },
            onUsage: { showUsage = true },
            onDrop: { showDrop = true }
        )
    }

    @ViewBuilder
    private var detailColumn: some View {
        if let chat = appModel.selectedChat {
            NavigationStack {
                ChatView(viewModel: chat)
                    .id(chat.id)
            }
        } else {
            ContentUnavailableView(
                "Pick a session",
                systemImage: "bubble.left.and.text.bubble.right",
                description: Text("Choose a session from the list, or start a new one.")
            )
        }
    }
}
