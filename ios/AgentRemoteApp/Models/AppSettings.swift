import Foundation

/// App-wide settings (Android AppSettings parity — subset used on iOS).
struct AppSettings: Codable, Equatable {
    /// "" / "default" = daemon default for next turn.
    var modelOverride: String = ""
    var effortOverride: String = ""
    /// interactive | headless (mapped to permissionMode on the wire).
    /// Default interactive so new installs match Android/web (host TUI).
    var permissionMode: String = "interactive"
    var soundCues: Bool = true
    var showAllSessions: Bool = false
    /// system | light | dark
    var theme: String = "system"
}

@MainActor
final class SettingsStore: ObservableObject {
    @Published var settings: AppSettings {
        didSet { save() }
    }

    private let key = "com.agentremote.settings"

    init() {
        if let data = UserDefaults.standard.data(forKey: key),
           let decoded = try? JSONDecoder().decode(AppSettings.self, from: data) {
            settings = decoded
            // One-shot: older builds defaulted to headless; flip to interactive
            // unless the user already changed settings after this migration.
            let migratedKey = key + ".interactiveDefault"
            if !UserDefaults.standard.bool(forKey: migratedKey) {
                if settings.permissionMode == "headless" || settings.permissionMode.isEmpty {
                    settings.permissionMode = "interactive"
                }
                UserDefaults.standard.set(true, forKey: migratedKey)
            }
        } else {
            settings = AppSettings()
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(settings) {
            UserDefaults.standard.set(data, forKey: key)
        }
    }
}
