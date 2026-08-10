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
    @State private var usageProfileId: UUID?
    @State private var dropProfileId: UUID?

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            SessionsListView(
                onOpen: { row in
                    _ = appModel.open(row: row)
                    if sizeClass == .compact {
                        columnVisibility = .detailOnly
                    }
                },
                onNewSession: { showNewSession = true },
                onProfiles: { showProfiles = true },
                onSettings: { showSettings = true },
                onUsage: {
                    usageProfileId = profileStore.profiles.first?.id
                    showUsage = true
                },
                onDrop: {
                    dropProfileId = profileStore.profiles.first?.id
                    showDrop = true
                }
            )
            .navigationSplitViewColumnWidth(min: 280, ideal: 340, max: 480)
        } detail: {
            detailColumn
        }
        .navigationSplitViewStyle(.balanced)
        .sheet(isPresented: $showNewSession) {
            NewSessionView { profileId, cwd, provider in
                showNewSession = false
                _ = appModel.openNew(profileId: profileId, cwd: cwd, provider: provider)
                if sizeClass == .compact {
                    columnVisibility = .detailOnly
                }
            }
        }
        .sheet(isPresented: $showProfiles) {
            ProfilesView()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
        }
        .sheet(isPresented: $showUsage) {
            if let pid = usageProfileId, let client = appModel.client(for: pid) {
                UsageView(client: client)
            } else {
                NavigationStack {
                    ContentUnavailableView("No servers", systemImage: "chart.bar",
                                           description: Text("Add a server first."))
                        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { showUsage = false } } }
                }
            }
        }
        .sheet(isPresented: $showDrop) {
            if let pid = dropProfileId, let client = appModel.client(for: pid) {
                DropView(client: client)
            } else {
                NavigationStack {
                    ContentUnavailableView("No servers", systemImage: "tray",
                                           description: Text("Add a server first."))
                        .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { showDrop = false } } }
                }
            }
        }
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
