import Foundation
import Security

/// Thin wrapper over Keychain Services for the one secret this app holds per server: its daemon
/// auth token. On the Simulator (or any build that cannot use Keychain), falls back to a
/// UserDefaults map so Add Server still works.
enum KeychainStore {
    private static let service = "com.agentremote.app"
    /// Simulator / entitlement-fallback token map (never use on device when Keychain works).
    private static let simTokensKey = "com.agentremote.simTokens"
    /// Legacy seed key from earlier simulator tooling.
    private static let legacySimTokensKey = "com.claudereremote.simTokens"

    /// Returns the raw `OSStatus` (`errSecSuccess` on success) rather than swallowing it, since a
    /// silent write failure here (e.g. `errSecMissingEntitlement` on a personal-team signed build)
    /// otherwise only surfaces later, confusingly, as "no token saved" at connect time.
    @discardableResult
    static func set(_ value: String, account: String) -> OSStatus {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)

        var attributes = query
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(attributes as CFDictionary, nil)
        if status == errSecSuccess {
            // Prefer Keychain; drop any stale simulator fallback for this account.
            removeSimToken(account: account)
            return errSecSuccess
        }
        #if targetEnvironment(simulator)
        // -34018 errSecMissingEntitlement is common on unsigned/ad-hoc simulator runs.
        if setSimToken(value, account: account) {
            return errSecSuccess
        }
        #endif
        return status
    }

    static func get(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        if SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
           let data = result as? Data,
           let string = String(data: data, encoding: .utf8) {
            return string
        }
        #if targetEnvironment(simulator)
        return simToken(account: account)
        #else
        // Device: still try fallback if Keychain was blocked at save time (rare).
        return simToken(account: account)
        #endif
    }

    @discardableResult
    static func delete(account: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        removeSimToken(account: account)
        return status == errSecSuccess || status == errSecItemNotFound
    }

    // MARK: - UserDefaults fallback (simulator / missing entitlement)

    private static func simToken(account: String) -> String? {
        for key in [simTokensKey, legacySimTokensKey] {
            if let map = UserDefaults.standard.dictionary(forKey: key) as? [String: String],
               let value = map[account], !value.isEmpty {
                return value
            }
        }
        return nil
    }

    @discardableResult
    private static func setSimToken(_ value: String, account: String) -> Bool {
        var map = (UserDefaults.standard.dictionary(forKey: simTokensKey) as? [String: String]) ?? [:]
        map[account] = value
        UserDefaults.standard.set(map, forKey: simTokensKey)
        return true
    }

    private static func removeSimToken(account: String) {
        for key in [simTokensKey, legacySimTokensKey] {
            guard var map = UserDefaults.standard.dictionary(forKey: key) as? [String: String] else { continue }
            map.removeValue(forKey: account)
            if map.isEmpty {
                UserDefaults.standard.removeObject(forKey: key)
            } else {
                UserDefaults.standard.set(map, forKey: key)
            }
        }
    }
}

extension KeychainStore {
    static func errorDescription(_ status: OSStatus) -> String {
        if let message = SecCopyErrorMessageString(status, nil) as String? {
            return "\(message) (\(status))"
        }
        return "Keychain error \(status)"
    }
}

enum SecretKind: String {
    case authToken
}

extension KeychainStore {
    static func account(profileId: UUID, kind: SecretKind) -> String {
        "\(profileId.uuidString).\(kind.rawValue)"
    }
}
