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
    @State private var renameTarget: SessionRow?
    @State private var renameText = ""
    @State private var regeneratingKey: String?

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

            if profileStore.profiles.count > 1 || appModel.focusAvailable {
                Section {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            if appModel.focusAvailable {
                                FilterChip(
                                    title: appModel.focusMode ? "Focus · \(rows.count)" : "Focus",
                                    selected: appModel.focusMode
                                ) { appModel.setFocusMode(!appModel.focusMode) }
                            }
                            FilterChip(
                                title: "All",
                                selected: appModel.profileFilter == nil && !appModel.focusMode
                            ) {
                                appModel.profileFilter = nil
                                appModel.setFocusMode(false)
                            }
                            if profileStore.profiles.count > 1 {
                                ForEach(profileStore.profiles) { profile in
                                    FilterChip(
                                        title: profile.name,
                                        selected: appModel.profileFilter == profile.id
                                    ) {
                                        appModel.profileFilter =
                                            appModel.profileFilter == profile.id ? nil : profile.id
                                    }
                                }
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
                            // Opening from search: the push is swallowed both
                            // while the search overlay is presented AND while
                            // it is dismissing — so dismiss first and open once
                            // the transition has settled.
                            if searching {
                                searching = false
                                DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                                    onOpen(row)
                                }
                            } else {
                                onOpen(row)
                            }
                        } label: {
                            SessionRowView(
                                row: row,
                                isWorking: appModel.isWorking(ref: row.ref),
                                isBlocked: appModel.isBlocked(ref: row.ref),
                                showFocusState: appModel.focusMode,
                                isSelected: appModel.selectedChatKey == row.ref.key,
                                isRegenerating: regeneratingKey == row.ref.key
                            )
                        }
                        .buttonStyle(.plain)
                        .contextMenu { rowMenu(for: row) }
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
        .alert("Rename session", isPresented: Binding(
            get: { renameTarget != nil },
            set: { if !$0 { renameTarget = nil } }
        )) {
            TextField("Title", text: $renameText)
            Button("Save") {
                if let row = renameTarget {
                    let title = String(renameText.prefix(120))
                    Task { try? await appModel.renameSession(row, title: title) }
                }
                renameTarget = nil
            }
            Button("Cancel", role: .cancel) { renameTarget = nil }
        } message: {
            Text("Leave empty to go back to the derived title.")
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
                .accessibilityIdentifier("sessions.more")
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
                    .accessibilityIdentifier("sessions.new")
                    .padding()
                }
            }
        }
    }
}

extension SessionsListView {
    @ViewBuilder
    fileprivate func rowMenu(for row: SessionRow) -> some View {
        Button {
            renameText = row.session.titleManual ? row.session.displayTitle : ""
            renameTarget = row
        } label: {
            Label("Rename…", systemImage: "pencil")
        }
        Button {
            regeneratingKey = row.ref.key
            Task {
                defer { regeneratingKey = nil }
                if let suggestion = try? await appModel.regenerateTitle(row) {
                    renameText = suggestion
                    renameTarget = row
                }
            }
        } label: {
            Label("Suggest a title", systemImage: "sparkles")
        }
        if appModel.focusSupported(row) {
            Divider()
            if row.session.focus {
                Button {
                    Task { try? await appModel.setFocusMember(row, member: false) }
                } label: {
                    Label("Done — take it off Focus", systemImage: "checkmark.circle")
                }
            } else {
                Button {
                    Task { try? await appModel.setFocusMember(row, member: true) }
                } label: {
                    Label("Track in Focus", systemImage: "scope")
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
    var isBlocked: Bool = false
    var showFocusState: Bool = false
    let isSelected: Bool
    var isRegenerating: Bool = false

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
                    if isWorking || isRegenerating {
                        ProgressView().controlSize(.mini)
                    }
                }
                if isBlocked {
                    Text("Waiting for your answer")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.orange.opacity(0.18), in: Capsule())
                        .foregroundStyle(.orange)
                } else if showFocusState, let pill = focusPill {
                    Text(pill.text)
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(pill.color.opacity(0.16), in: Capsule())
                        .foregroundStyle(pill.color)
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
                    if !row.session.gitBranch.isEmpty {
                        Text("⑂ \(row.session.gitBranch)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
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

    /// Live-corrected focus pill: the working/blocked truth from the status stream wins over the
    /// stored state (the pill flips the instant a turn finishes, blocks, or resumes).
    private var focusPill: (text: String, color: Color)? {
        var state = row.session.focusState
        if isWorking { state = "working" }
        switch state {
        case "needs_answer": return ("needs answer", .orange)
        case "failed": return ("failed", .red)
        case "working": return ("working", .green)
        case "turn_finished":
            return row.session.focusUnread
                ? ("turn finished", accent.tint)
                : ("turn finished", .secondary)
        default: return nil
        }
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
