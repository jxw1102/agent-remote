import SwiftUI

struct ConnectionListView: View {
    @EnvironmentObject private var profileStore: ProfileStore
    @State private var isAddingProfile = false
    @State private var selectedServer: ServerProfile?
    @State private var selectedChat: ChatViewModel?

    var body: some View {
        // Three columns on iPad — Servers, the selected server's folders/sessions, and the chat —
        // collapsing to a single push-navigation stack on iPhone.
        NavigationSplitView {
            List(selection: $selectedServer) {
                ForEach(profileStore.profiles) { profile in
                    NavigationLink(value: profile) {
                        ServerRow(profile: profile)
                    }
                }
                .onDelete { indexSet in
                    for index in indexSet {
                        let removed = profileStore.profiles[index]
                        if removed == selectedServer { selectedServer = nil }
                        profileStore.remove(removed)
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Servers")
            .navigationSplitViewColumnWidth(min: 220, ideal: 300, max: 520)
            .overlay {
                if profileStore.profiles.isEmpty {
                    ContentUnavailableView(
                        "No servers yet",
                        systemImage: "server.rack",
                        description: Text("Add a server to connect to Claude Code running there.")
                    )
                }
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        isAddingProfile = true
                    } label: {
                        Label("Add Server", systemImage: "plus")
                    }
                }
            }
            .sheet(isPresented: $isAddingProfile) {
                AddConnectionView()
            }
        } content: {
            if let selectedServer {
                // A fresh connection per selected server; `.id` resets it (and its DaemonClient)
                // when you switch servers.
                ConnectingView(profile: selectedServer, selectedChat: $selectedChat)
                    .id(selectedServer.id)
                    .navigationSplitViewColumnWidth(min: 300, ideal: 380, max: 620)
            } else {
                ContentUnavailableView(
                    "Select a server",
                    systemImage: "sidebar.left",
                    description: Text("Choose a server to connect and browse its sessions.")
                )
            }
        } detail: {
            if let selectedChat {
                ChatView(viewModel: selectedChat)
                    .id(selectedChat.id)
            } else {
                ContentUnavailableView(
                    "Pick a session",
                    systemImage: "bubble.left.and.text.bubble.right",
                    description: Text("Choose a session on the left, or start a new one.")
                )
            }
        }
        // Switching servers invalidates the previous server's chat (its connection is gone).
        .onChange(of: selectedServer) { _, _ in selectedChat = nil }
    }
}

/// One server in the sidebar: a branded tile, name, and its address.
private struct ServerRow: View {
    let profile: ServerProfile

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "server.rack")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(Color.brand)
                .frame(width: 38, height: 38)
                .background(Color.brandSoft, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            VStack(alignment: .leading, spacing: 3) {
                Text(profile.name).font(.headline)
                Text(profile.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
        }
        .padding(.vertical, 4)
    }
}
