import AgentRemoteKit
import SwiftUI

struct ProfilesView: View {
    @EnvironmentObject private var profileStore: ProfileStore
    @EnvironmentObject private var appModel: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var isAdding = false
    @State private var editingProfile: ServerProfile?

    var body: some View {
        NavigationStack {
            List {
                if profileStore.profiles.isEmpty {
                    ContentUnavailableView(
                        "No servers",
                        systemImage: "server.rack",
                        description: Text("Add a daemon URL and token to merge its sessions into the list.")
                    )
                } else {
                    ForEach(profileStore.profiles) { profile in
                        Button {
                            editingProfile = profile
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(profile.name).font(.headline)
                                    Spacer()
                                    statusBadge(for: profile.id)
                                }
                                Text(profile.serverURLString)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                if let ping = appModel.ping(for: profile.id) {
                                    Text(harnessLabel(ping))
                                        .font(.caption2)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                        .buttonStyle(.plain)
                    }
                    .onDelete { indexSet in
                        for index in indexSet {
                            let p = profileStore.profiles[index]
                            profileStore.remove(p)
                        }
                        appModel.syncClients()
                        appModel.refreshEverything()
                    }
                }
            }
            .navigationTitle("Profiles")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Done") { dismiss() }
                }
                ToolbarItem(placement: .primaryAction) {
                    Button { isAdding = true } label: { Image(systemName: "plus") }
                }
            }
            .sheet(isPresented: $isAdding) {
                AddConnectionView()
                    .onDisappear {
                        appModel.syncClients()
                        appModel.refreshEverything()
                    }
            }
            .sheet(item: $editingProfile) { profile in
                AddConnectionView(editing: profile)
                    .onDisappear {
                        appModel.syncClients()
                        appModel.refreshEverything()
                    }
            }
        }
    }

    @ViewBuilder
    private func statusBadge(for id: UUID) -> some View {
        if let err = appModel.connectionErrors[id] {
            Text(err)
                .font(.caption2)
                .foregroundStyle(.orange)
                .lineLimit(1)
        } else if appModel.ping(for: id) != nil {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
                .font(.caption)
        } else {
            ProgressView().controlSize(.mini)
        }
    }

    private func harnessLabel(_ ping: PingResponse) -> String {
        let list = ping.harnesses
        if list.isEmpty { return ping.provider }
        return list.map { ProviderAccent.forProvider($0).label }.joined(separator: " · ")
            + " · v\(ping.version)"
    }
}
