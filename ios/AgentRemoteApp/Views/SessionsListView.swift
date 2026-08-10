import SwiftUI

/// Unified multi-host session list — Android SessionsScreen parity.
struct SessionsListView: View {
    @EnvironmentObject private var appModel: AppModel
    @EnvironmentObject private var profileStore: ProfileStore

    var onOpen: (SessionRow) -> Void
    var onNewSession: () -> Void
    var onProfiles: () -> Void
    var onSettings: () -> Void
    var onUsage: () -> Void
    var onDrop: () -> Void

    @State private var searching = false
    @State private var searchText = ""

    private var rows: [SessionRow] { appModel.filteredRows }

    var body: some View {
        List {
            if !appModel.problems.isEmpty {
                Section {
                    ForEach(appModel.problems, id: \.name) { problem in
                        Label {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(problem.name).font(.subheadline.weight(.semibold))
                                Text(problem.message).font(.caption).foregroundStyle(.secondary)
                            }
                        } icon: {
                            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        }
                    }
                }
            }

            if profileStore.profiles.count > 1 {
                Section {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            FilterChip(
                                title: "All",
                                selected: appModel.profileFilter == nil
                            ) { appModel.profileFilter = nil }
                            ForEach(profileStore.profiles) { profile in
                                FilterChip(
                                    title: profile.name,
                                    selected: appModel.profileFilter == profile.id
                                ) { appModel.profileFilter = profile.id }
                            }
                        }
                        .padding(.vertical, 2)
                    }
                    .listRowInsets(EdgeInsets(top: 8, leading: 16, bottom: 8, trailing: 16))
                }
            }

            if rows.isEmpty && !appModel.isLoading {
                Section {
                    if profileStore.profiles.isEmpty {
                        ContentUnavailableView(
                            "No servers yet",
                            systemImage: "server.rack",
                            description: Text("Add a daemon profile to see sessions from every host.")
                        )
                        .listRowBackground(Color.clear)
                    } else {
                        ContentUnavailableView(
                            searchText.isEmpty ? "No sessions" : "No matches",
                            systemImage: "bubble.left.and.bubble.right",
                            description: Text(searchText.isEmpty
                                ? "Pull to refresh, or start a new session."
                                : "Try a different search.")
                        )
                        .listRowBackground(Color.clear)
                    }
                }
            } else {
                Section {
                    ForEach(rows) { row in
                        Button {
                            onOpen(row)
                        } label: {
                            SessionRowView(
                                row: row,
                                isWorking: appModel.isWorking(ref: row.ref),
                                isSelected: appModel.selectedChatKey == row.ref.key
                            )
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Sessions")
        .overlay {
            if appModel.isLoading && rows.isEmpty {
                ProgressView()
            }
        }
        .refreshable {
            appModel.refreshEverything()
            // Allow the refresh control a moment to settle.
            try? await Task.sleep(nanoseconds: 400_000_000)
        }
        .searchable(text: $searchText, isPresented: $searching, prompt: "Search sessions")
        .onChange(of: searchText) { _, value in
            appModel.setQuery(value)
        }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    Button { onNewSession() } label: { Label("New session", systemImage: "plus") }
                    Divider()
                    Button { onProfiles() } label: { Label("Profiles", systemImage: "server.rack") }
                    Button { onUsage() } label: { Label("Usage", systemImage: "chart.bar") }
                    Button { onDrop() } label: { Label("Files from host", systemImage: "tray.and.arrow.down") }
                    Button { onSettings() } label: { Label("Settings", systemImage: "gearshape") }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .safeAreaInset(edge: .bottom) {
            if !profileStore.profiles.isEmpty {
                HStack {
                    Spacer()
                    Button(action: onNewSession) {
                        Label("New session", systemImage: "plus")
                            .font(.subheadline.weight(.semibold))
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                            .background(ProviderAccent.neutral.tint, in: Capsule())
                            .foregroundStyle(.white)
                    }
                    .padding()
                }
            }
        }
    }
}

private struct FilterChip: View {
    let title: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.caption.weight(.medium))
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(selected ? ProviderAccent.neutral.tint.opacity(0.2) : Color.secondary.opacity(0.12),
                            in: Capsule())
                .foregroundStyle(selected ? ProviderAccent.neutral.tint : .primary)
        }
        .buttonStyle(.plain)
    }
}

private struct SessionRowView: View {
    let row: SessionRow
    let isWorking: Bool
    let isSelected: Bool

    private var accent: ProviderAccent { ProviderAccent.forProvider(row.resolvedProvider) }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            RoundedRectangle(cornerRadius: 3, style: .continuous)
                .fill(accent.tint)
                .frame(width: 4)
                .padding(.vertical, 2)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(row.session.displayTitle)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.primary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 4)
                    if isWorking {
                        ProgressView().controlSize(.mini)
                    }
                }
                HStack(spacing: 6) {
                    Text(accent.label)
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(accent.soft, in: Capsule())
                        .foregroundStyle(accent.tint)
                    if !row.profileName.isEmpty {
                        Text(row.profileName)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 4)
                    Text(Self.relativeTime(row.session.lastActive.date))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .monospacedDigit()
                }
                let preview = row.session.snippet.isEmpty ? row.session.lastText : row.session.snippet
                if !preview.isEmpty {
                    Text(preview)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                if !row.session.cwd.isEmpty {
                    Text(row.session.cwd)
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                        .truncationMode(.head)
                }
            }
        }
        .padding(.vertical, 4)
        .listRowBackground(isSelected ? accent.soft : nil)
    }

    private static let relativeFormatter: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()

    private static func relativeTime(_ date: Date) -> String {
        relativeFormatter.localizedString(for: date, relativeTo: Date())
    }
}
