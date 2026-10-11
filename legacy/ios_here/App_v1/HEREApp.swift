import SwiftUI

@main
struct HEREApp: App {
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appState)
                .preferredColorScheme(.dark)
                .task { appState.start() }
        }
    }
}
