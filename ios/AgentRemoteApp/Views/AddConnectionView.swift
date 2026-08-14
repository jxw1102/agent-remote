import AgentRemoteKit
import SwiftUI

/// Add or edit one daemon profile. "Test connection" pings AND then makes one authenticated call
/// (`/api/projects`) — `/api/ping` alone is unauthenticated, so a wrong token would otherwise
/// pass here and only surface later as an empty session list.
struct AddConnectionView: View {
    @EnvironmentObject private var profileStore: ProfileStore
    @Environment(\.dismiss) private var dismiss

    /// nil = add a new server; set = edit this one in place (same id, token overwritten).
    var editing: ServerProfile?

    @State private var name = ""
    @State private var serverURLString = ""
    @State private var authToken = ""
    @State private var showToken = false
    @State private var saveError: String?
    @State private var testing = false
    @State private var testResult: TestResult?

    private struct TestResult {
        let ok: Bool
        let lines: [String]
    }

    private var isValid: Bool {
        guard !name.trimmingCharacters(in: .whitespaces).isEmpty, !authToken.isEmpty else { return false }
        guard let url = URL(string: serverURLString), let scheme = url.scheme?.lowercased() else { return false }
        return scheme == "http" || scheme == "https"
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Server") {
                    TextField("Name", text: $name)
                        .textInputAutocapitalization(.words)
                    TextField("https://your-daemon.example.com", text: $serverURLString)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.URL)
                }

                Section {
                    HStack {
                        if showToken {
                            TextField("Daemon auth token", text: $authToken)
                                .textInputAutocapitalization(.never)
                                .autocorrectionDisabled()
                        } else {
                            SecureField("Daemon auth token", text: $authToken)
                        }
                        Button {
                            showToken.toggle()
                        } label: {
                            Image(systemName: showToken ? "eye.slash" : "eye")
                                .foregroundStyle(.secondary)
                        }
                        .buttonStyle(.plain)
                    }
                } footer: {
                    Text("From ~/.agentremoted/token (or ~/.bb10d/token) on the server, or run: python3 -m agentremoted --print-token")
                }

                Section {
                    Button {
                        Task { await testConnection() }
                    } label: {
                        if testing {
                            HStack(spacing: 8) { ProgressView(); Text("Testing…") }
                        } else {
                            Text("Test connection")
                        }
                    }
                    .disabled(testing || !isValid)
                    if let result = testResult {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(result.lines, id: \.self) { line in
                                Text(line)
                                    .font(.caption)
                                    .foregroundStyle(result.ok ? Color.green : Color.orange)
                            }
                        }
                    }
                }
            }
            .navigationTitle(editing == nil ? "Add Server" : "Edit Server")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(!isValid)
                }
            }
            .onAppear { prefill() }
            .alert(
                "Couldn't save this server",
                isPresented: Binding(get: { saveError != nil }, set: { if !$0 { saveError = nil } }),
                presenting: saveError
            ) { _ in
                Button("OK") { saveError = nil }
            } message: { Text($0) }
        }
    }

    private func prefill() {
        guard let editing else { return }
        name = editing.name
        serverURLString = editing.serverURLString
        authToken = KeychainStore.get(account: KeychainStore.account(profileId: editing.id, kind: .authToken)) ?? ""
    }

    private func testConnection() async {
        guard let url = URL(string: serverURLString.trimmingCharacters(in: .whitespaces)) else { return }
        testing = true
        defer { testing = false }
        let client = AgentRemoteClient(baseURL: url, token: authToken)
        let ping: PingResponse
        do {
            ping = try await client.ping()
        } catch {
            testResult = TestResult(ok: false, lines: [DaemonClient.describe(error)])
            return
        }
        guard ping.app == "agentremoted" else {
            testResult = TestResult(ok: false, lines: [
                "That address answered, but it is not an agentremoted host.",
            ])
            return
        }
        // Ping is unauthenticated on purpose — verify the token with a real call.
        do {
            _ = try await client.projects()
        } catch let error as AgentRemoteError {
            if case .daemon(let status, _) = error, status == 401 || status == 403 {
                let who = ping.harnesses.map { ProviderAccent.forProvider($0).label }.joined(separator: " · ")
                testResult = TestResult(ok: false, lines: [
                    "Reached \(who.isEmpty ? ping.host : who), but the token was rejected.",
                ])
            } else {
                testResult = TestResult(ok: false, lines: [DaemonClient.describe(error)])
            }
            return
        } catch {
            testResult = TestResult(ok: false, lines: [DaemonClient.describe(error)])
            return
        }
        var lines: [String] = []
        let harnesses = ping.harnesses.map { ProviderAccent.forProvider($0).label }.joined(separator: " · ")
        lines.append("\(harnesses.isEmpty ? "Agent" : harnesses) on \(ping.host)")
        lines.append("agentremoted \(ping.version) · \(capsSummary(ping.caps))")
        if let auth = ping.auth, let detail = auth.detail, !detail.isEmpty {
            lines.append("Auth: \(detail)")
        }
        testResult = TestResult(ok: true, lines: lines)
    }

    private func capsSummary(_ caps: PingResponse.Capabilities) -> String {
        var parts: [String] = []
        if caps.interactive { parts.append("interactive") }
        if caps.permissions { parts.append("permissions") }
        if caps.canSetModel { parts.append("models") }
        if caps.canSetEffort { parts.append("effort") }
        if caps.rewindEnabled { parts.append("rewind") }
        if caps.canShowUsage { parts.append("usage") }
        return parts.isEmpty ? "basic" : parts.joined(separator: ", ")
    }

    /// Writes the auth token to Keychain, returning false (and setting `saveError`) if it failed —
    /// callers must stop and not persist a profile whose token didn't actually save.
    private func writeSecret(_ value: String, profileId: UUID, kind: SecretKind) -> Bool {
        let status = KeychainStore.set(value, account: KeychainStore.account(profileId: profileId, kind: kind))
        guard status == errSecSuccess else {
            saveError = "Saving the token to Keychain failed: \(KeychainStore.errorDescription(status))"
            return false
        }
        return true
    }

    private func save() {
        if let editing {
            let updated = ServerProfile(
                id: editing.id,
                name: name.trimmingCharacters(in: .whitespaces),
                serverURLString: serverURLString.trimmingCharacters(in: .whitespaces)
            )
            guard writeSecret(authToken, profileId: editing.id, kind: .authToken) else { return }
            profileStore.update(updated)
        } else {
            let profile = ServerProfile(
                name: name.trimmingCharacters(in: .whitespaces),
                serverURLString: serverURLString.trimmingCharacters(in: .whitespaces)
            )
            guard writeSecret(authToken, profileId: profile.id, kind: .authToken) else { return }
            profileStore.add(profile)
        }
        dismiss()
    }
}
