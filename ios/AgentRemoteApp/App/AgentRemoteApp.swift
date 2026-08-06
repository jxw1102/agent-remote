import SwiftUI

@main
struct AgentRemoteApp: App {
    @StateObject private var profileStore = ProfileStore()

    var body: some Scene {
        WindowGroup {
            ConnectionListView()
                .environmentObject(profileStore)
                .tint(.brand)
        }
    }
}
