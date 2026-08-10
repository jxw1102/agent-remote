import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var settingsStore: SettingsStore
    @EnvironmentObject private var appModel: AppModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("Appearance") {
                    Picker("Theme", selection: $settingsStore.settings.theme) {
                        Text("System").tag("system")
                        Text("Light").tag("light")
                        Text("Dark").tag("dark")
                    }
                }

                Section("Sessions list") {
                    Toggle("Show all sessions", isOn: $settingsStore.settings.showAllSessions)
                        .onChange(of: settingsStore.settings.showAllSessions) { _, _ in
                            Task { await appModel.refreshSessions() }
                        }
                    Text("When off, the daemon may limit how many historical sessions are returned.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                Section("Next turn defaults") {
                    Picker("Execution", selection: Binding(
                        get: {
                            settingsStore.settings.permissionMode == "interactive" ? "interactive" : "headless"
                        },
                        set: { settingsStore.settings.permissionMode = $0 }
                    )) {
                        Text("Headless").tag("headless")
                        Text("Interactive (host TUI)").tag("interactive")
                    }
                    TextField("Model override (empty = default)", text: $settingsStore.settings.modelOverride)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Effort override (Grok/Codex)", text: $settingsStore.settings.effortOverride)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }

                Section("About") {
                    LabeledContent("App", value: "Agent Remote")
                    Text("Talks to your self-hosted agentremoted daemons. Sessions from every profile appear in one list.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
