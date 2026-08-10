import SwiftUI

@main
struct AgentRemoteApp: App {
    @StateObject private var appModel = AppModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(appModel)
                .environmentObject(appModel.profileStore)
                .environmentObject(appModel.settingsStore)
                .tint(ProviderAccent.neutral.tint)
                .preferredColorScheme(colorScheme)
                .onChange(of: scenePhase) { _, phase in
                    switch phase {
                    case .active: appModel.appDidBecomeActive()
                    case .background, .inactive: appModel.appDidResignActive()
                    @unknown default: break
                    }
                }
                .task {
                    appModel.connectAll()
                    appModel.refreshEverything()
                }
        }
    }

    private var colorScheme: ColorScheme? {
        switch appModel.settingsStore.settings.theme {
        case "light": return .light
        case "dark": return .dark
        default: return nil
        }
    }
}
