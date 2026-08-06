import Foundation
import Security

/// Thin wrapper over Keychain Services for the one secret this app holds per server: its daemon
/// auth token. Nothing here ever touches UserDefaults or disk directly — Keychain is the only
/// place secrets live.
enum KeychainStore {
    private static let service = "com.claudereremote.app"

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
        return SecItemAdd(attributes as CFDictionary, nil)
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
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let string = String(data: data, encoding: .utf8)
        else { return nil }
        return string
    }

    @discardableResult
    static func delete(account: String) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        return status == errSecSuccess || status == errSecItemNotFound
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
