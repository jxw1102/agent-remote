import SwiftUI

struct AddConnectionView: View {
    @EnvironmentObject private var profileStore: ProfileStore
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var serverURLString = ""
    @State private var authToken = ""
    @State private var saveError: String?

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
                    SecureField("Daemon auth token", text: $authToken)
                } footer: {
                    Text("From ~/.agentremoted/token (or ~/.bb10d/token) on the server, or run: python3 -m agentremoted --print-token")
                }
            }
            .navigationTitle("Add Server")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .disabled(!isValid)
                }
            }
            .alert(
                "Couldn't save this server",
                isPresented: Binding(get: { saveError != nil }, set: { if !$0 { saveError = nil } }),
                presenting: saveError
            ) { _ in
                Button("OK") { saveError = nil }
            } message: { Text($0) }
        }
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
        let profile = ServerProfile(
            name: name.trimmingCharacters(in: .whitespaces),
            serverURLString: serverURLString.trimmingCharacters(in: .whitespaces)
        )
        guard writeSecret(authToken, profileId: profile.id, kind: .authToken) else { return }
        profileStore.add(profile)
        dismiss()
    }
}
