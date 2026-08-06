import SwiftUI

struct ProjectView: View {
    let profile: ServerProfile
    @ObservedObject var hub: SessionHub
    @StateObject private var viewModel: ProjectViewModel
    /// The chat chosen here. On iPad (regular width) this drives the split view's detail column.
    @Binding var selectedChat: ChatViewModel?
    /// On iPhone (compact width) the chat is pushed onto this column's stack instead.
    @State private var pushedChat: ChatViewModel?
    @Environment(\.horizontalSizeClass) private var sizeClass

    /// Folders start collapsed so connecting shows a short, scannable list of folders rather than
    /// every session at once. Expanding is per-folder and remembered here.
    @State private var expandedFolders: Set<String> = []
    /// Folders the user asked to see in full; others are capped to `sessionCap` rows.
    @State private var expandedInFull: Set<String> = []
    @State private var isShowingUsage = false
    @State private var isShowingDrop = false

    private let sessionCap = 6

    init(profile: ServerProfile, hub: SessionHub, selectedChat: Binding<ChatViewModel?>) {
        self.profile = profile
        self.hub = hub
        self._selectedChat = selectedChat
        _viewModel = StateObject(wrappedValue: ProjectViewModel(client: hub.client))
    }

    var body: some View {
        Form {
            Section("New session") {
                HStack(spacing: 10) {
                    TextField("/home/you/project", text: $viewModel.newSessionCwd)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.subheadline)
                        .submitLabel(.go)
                        .onSubmit { startNewSession() }

                    let canStart = !viewModel.newSessionCwd.trimmingCharacters(in: .whitespaces).isEmpty
                    Button {
                        startNewSession()
                    } label: {
                        Image(systemName: "plus.circle.fill")
                            .font(.system(size: 27))
                            .foregroundStyle(canStart ? Color.brand : Color.secondary.opacity(0.4))
                    }
                    .buttonStyle(.plain)
                    .disabled(!canStart)
                }
            }

            if let errorMessage = viewModel.errorMessage {
                Section {
                    Text(errorMessage).foregroundStyle(.red)
                }
            }

            if viewModel.isLoadingSessions {
                Section {
                    HStack { Spacer(); ProgressView(); Spacer() }
                }
            } else if viewModel.folders.isEmpty {
                Section {
                    Text("No sessions yet. Start a new one above.")
                        .foregroundStyle(.secondary)
                }
            } else {
                Section("Sessions") {
                    ForEach(viewModel.folders) { folder in
                        DisclosureGroup(isExpanded: expansion(of: folder.cwd)) {
                            folderContents(folder)
                        } label: {
                            FolderHeader(cwd: folder.cwd, count: folder.sessions.count)
                        }
                    }
                }
            }
        }
        .navigationTitle(profile.name)
        .onAppear { if viewModel.folders.isEmpty { viewModel.loadAllSessions() } }
        .navigationDestination(item: $pushedChat) { chat in
            ChatView(viewModel: chat)
        }
        .toolbar {
            // A single ellipsis button directly in the nav bar (primaryAction) — `.secondaryAction`
            // would bury this menu under iOS's own overflow "…", making it a redundant two-tap menu.
            ToolbarItem(placement: .primaryAction) {
                Menu {
                    if hub.client.caps?.canShowUsage == true {
                        Button { isShowingUsage = true } label: { Label("Usage", systemImage: "chart.bar") }
                    }
                    Button { isShowingDrop = true } label: { Label("Drop Files", systemImage: "tray.and.arrow.down") }
                } label: {
                    Image(systemName: "ellipsis.circle")
                }
            }
        }
        .sheet(isPresented: $isShowingUsage) { UsageView(client: hub.client) }
        .sheet(isPresented: $isShowingDrop) { DropView(client: hub.client) }
    }

    @ViewBuilder
    private func folderContents(_ folder: ProjectViewModel.FolderGroup) -> some View {
        let showAll = expandedInFull.contains(folder.cwd)
        let visible = showAll ? folder.sessions : Array(folder.sessions.prefix(sessionCap))

        ForEach(visible) { session in
            Button {
                open(hub.chat(resumeId: session.id, cwd: folder.cwd, name: session.title))
            } label: {
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(Self.sessionTitle(session.title)).lineLimit(2)
                        Text(Self.relativeTime(session.lastActive.date))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 6)
                    SessionActivityBadge(phase: hub.openChat(forResumeId: session.id)?.phase)
                }
            }
        }

        if !showAll && folder.sessions.count > sessionCap {
            Button("Show all \(folder.sessions.count) sessions") {
                expandedInFull.insert(folder.cwd)
            }
            .font(.callout)
        }

        Button {
            open(hub.chat(resumeId: nil, cwd: folder.cwd, name: nil))
        } label: {
            Label("New session here", systemImage: "plus").font(.callout)
        }
    }

    private func startNewSession() {
        let trimmed = viewModel.newSessionCwd.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        open(hub.chat(resumeId: nil, cwd: trimmed, name: nil))
    }

    /// Route an opened chat: to the split view's detail column on iPad, or pushed on iPhone.
    private func open(_ chat: ChatViewModel) {
        if sizeClass == .compact {
            pushedChat = chat
        } else {
            selectedChat = chat
        }
    }

    private static let relativeFormatter: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()

    /// A one-shot relative time string ("3d ago") — computed once per render rather than a live
    /// `Text(_, style: .relative)`, which ticks every second.
    private static func relativeTime(_ date: Date) -> String {
        relativeFormatter.localizedString(for: date, relativeTo: Date())
    }

    /// Display name for a session row: a fresh session has no title yet, which the daemon reports
    /// as "(no content)" — show a friendly placeholder instead.
    private static func sessionTitle(_ title: String) -> String {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        return (trimmed.isEmpty || trimmed.lowercased() == "(no content)") ? "Untitled session" : title
    }

    private func expansion(of cwd: String) -> Binding<Bool> {
        Binding(
            get: { expandedFolders.contains(cwd) },
            set: { isOpen in
                if isOpen { expandedFolders.insert(cwd) } else { expandedFolders.remove(cwd) }
            }
        )
    }
}

/// A small live indicator next to a session row: spinner when running, filled dot when open-idle,
/// nothing when the session isn't open in this app.
private struct SessionActivityBadge: View {
    let phase: ChatViewModel.Phase?

    var body: some View {
        switch phase {
        case .running:
            ProgressView().controlSize(.mini)
        case .idle:
            Circle().fill(Color.green).frame(width: 8, height: 8)
        case .failed:
            Image(systemName: "exclamationmark.circle.fill").font(.caption2).foregroundStyle(.orange)
        case nil:
            EmptyView()
        }
    }
}

/// Collapsed-row label for a folder group: the folder's own name, its full path underneath (so
/// two same-named folders in different trees stay distinguishable), and a session count.
private struct FolderHeader: View {
    let cwd: String
    let count: Int

    private var folderName: String {
        let trimmed = cwd.hasSuffix("/") ? String(cwd.dropLast()) : cwd
        return trimmed.split(separator: "/").last.map(String.init) ?? cwd
    }

    var body: some View {
        HStack(spacing: 11) {
            Image(systemName: "folder.fill")
                .font(.system(size: 13))
                .foregroundStyle(Color.brand)
                .frame(width: 30, height: 30)
                .background(Color.brandSoft, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(folderName)
                    .font(.headline)
                    .textCase(nil)
                Text(cwd)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .textCase(nil)
                    .lineLimit(1)
                    .truncationMode(.head)
            }
            Spacer(minLength: 8)
            Text("\(count)")
                .font(.caption.weight(.medium)).monospacedDigit()
                .foregroundStyle(.secondary)
                .padding(.horizontal, 8).padding(.vertical, 2)
                .background(Color.secondary.opacity(0.12), in: Capsule())
        }
        .padding(.vertical, 3)
    }
}
