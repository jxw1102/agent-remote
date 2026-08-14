import AgentRemoteKit
import SwiftUI

/// Start a session: pick daemon, harness, cwd/project, then open chat (prompt sent on first message).
struct NewSessionView: View {
    @EnvironmentObject private var appModel: AppModel
    @EnvironmentObject private var profileStore: ProfileStore
    @EnvironmentObject private var settingsStore: SettingsStore
    @Environment(\.dismiss) private var dismiss

    /// profileId, cwd, provider
    var onStarted: (UUID, String, String) -> Void

    @State private var profileId: UUID?
    @State private var harness: String = ""
    @State private var cwd: String = ""
    @State private var projects: [Project] = []
    @State private var loadingProjects = false
    @State private var errorMessage: String?
    @State private var interactive = true

    private var profiles: [ServerProfile] { profileStore.profiles }

    private var selectedProfile: ServerProfile? {
        if let profileId { return profiles.first { $0.id == profileId } }
        return profiles.first
    }

    private var ping: PingResponse? {
        guard let id = selectedProfile?.id else { return nil }
        return appModel.ping(for: id)
    }

    private var harnesses: [String] {
        let list = ping?.harnesses ?? []
        if list.isEmpty, let p = ping?.provider, !p.isEmpty { return [p] }
        return list.isEmpty ? ["claude"] : list
    }

    private var activeHarness: String {
        let h = harness.isEmpty ? (harnesses.first ?? "claude") : harness
        return h.lowercased()
    }

    private var cwdRequired: Bool {
        ping?.caps(for: activeHarness).requiresCwd ?? true
    }

    private var visibleProjects: [Project] {
        if (ping?.isMulti ?? false) {
            return projects.filter {
                $0.provider.isEmpty || $0.provider.lowercased() == activeHarness
            }
        }
        return projects
    }

    private var canStart: Bool {
        selectedProfile != nil && (!cwdRequired || !cwd.trimmingCharacters(in: .whitespaces).isEmpty)
    }

    var body: some View {
        NavigationStack {
            Form {
                if profiles.count > 1 {
                    Section("Daemon") {
                        Picker("Server", selection: Binding(
                            get: { profileId ?? profiles.first?.id },
                            set: { profileId = $0; Task { await loadProjects() } }
                        )) {
                            ForEach(profiles) { p in
                                Text(p.name).tag(Optional(p.id))
                            }
                        }
                    }
                }

                if harnesses.count > 1 {
                    Section("Agent") {
                        Picker("Harness", selection: $harness) {
                            ForEach(harnesses, id: \.self) { h in
                                Text(ProviderAccent.forProvider(h).label).tag(h)
                            }
                        }
                        .pickerStyle(.segmented)
                        .onChange(of: harness) { _, _ in
                            // Clear cwd when switching harness so projects re-filter cleanly.
                            cwd = ""
                        }
                    }
                }

                Section("Working directory") {
                    if loadingProjects {
                        ProgressView()
                    }
                    if !visibleProjects.isEmpty {
                        ForEach(visibleProjects.prefix(20)) { project in
                            Button {
                                cwd = project.cwd
                            } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(project.name.isEmpty ? project.cwd : project.name)
                                            .foregroundStyle(.primary)
                                        Text(project.cwd)
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(1)
                                            .truncationMode(.head)
                                    }
                                    Spacer()
                                    if cwd == project.cwd {
                                        Image(systemName: "checkmark.circle.fill")
                                            .foregroundStyle(ProviderAccent.forProvider(activeHarness).tint)
                                    }
                                }
                            }
                        }
                    }
                    TextField(cwdRequired ? "/path/to/project (required)" : "/path/to/project (optional)", text: $cwd)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.subheadline.monospaced())
                }

                Section("Execution") {
                    Toggle("Interactive (host TUI)", isOn: $interactive)
                    Text(interactive
                         ? "Runs in a tmux TUI on the host. Tools auto-run."
                         : "One-shot CLI turn. Tools auto-run.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                if let errorMessage {
                    Section {
                        Text(errorMessage).foregroundStyle(.red)
                    }
                }
            }
            .navigationTitle("New session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Start") { start() }
                        .disabled(!canStart)
                }
            }
            .task {
                if profileId == nil { profileId = profiles.first?.id }
                if harness.isEmpty { harness = harnesses.first ?? "claude" }
                interactive = settingsStore.settings.permissionMode == "interactive"
                await loadProjects()
            }
            .onChange(of: profileId) { _, _ in
                Task { await loadProjects() }
            }
        }
    }

    private func loadProjects() async {
        guard let profile = selectedProfile else { return }
        loadingProjects = true
        errorMessage = nil
        defer { loadingProjects = false }
        // Ensure connected
        await appModel.connect(profile)
        guard let agent = appModel.client(for: profile.id)?.agentClient else {
            errorMessage = appModel.connectionErrors[profile.id] ?? "Not connected"
            return
        }
        do {
            projects = try await agent.projects()
            if cwd.isEmpty, let first = visibleProjects.first?.cwd {
                cwd = first
            }
        } catch {
            errorMessage = DaemonClient.describe(error)
            projects = []
        }
    }

    private func start() {
        guard let profile = selectedProfile else { return }
        let trimmed = cwd.trimmingCharacters(in: .whitespacesAndNewlines)
        if cwdRequired && trimmed.isEmpty {
            errorMessage = "Working directory is required for \(ProviderAccent.forProvider(activeHarness).label)."
            return
        }
        settingsStore.settings.permissionMode = interactive ? "interactive" : "headless"
        onStarted(profile.id, trimmed, activeHarness)
        dismiss()
    }
}
