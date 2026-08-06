import Foundation

/// Persists the (non-secret) list of servers. Each profile's daemon auth token lives in Keychain
/// under the profile's id, via `KeychainStore`.
@MainActor
final class ProfileStore: ObservableObject {
    @Published private(set) var profiles: [ServerProfile] = []

    private let defaultsKey = "com.claudereremote.profiles"

    init() {
        load()
    }

    func add(_ profile: ServerProfile) {
        profiles.append(profile)
        save()
    }

    func update(_ profile: ServerProfile) {
        guard let index = profiles.firstIndex(where: { $0.id == profile.id }) else { return }
        profiles[index] = profile
        save()
    }

    func remove(_ profile: ServerProfile) {
        profiles.removeAll { $0.id == profile.id }
        KeychainStore.delete(account: KeychainStore.account(profileId: profile.id, kind: .authToken))
        save()
    }

    private func load() {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey) else { return }
        profiles = (try? JSONDecoder().decode([ServerProfile].self, from: data)) ?? []
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(profiles) else { return }
        UserDefaults.standard.set(data, forKey: defaultsKey)
    }
}
