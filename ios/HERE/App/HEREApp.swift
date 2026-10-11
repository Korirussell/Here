import SwiftUI

@main
struct HEREApp: App {
    @StateObject private var settings: SettingsStore
    @StateObject private var model: AppModel

    init() {
        let s = SettingsStore()
        #if targetEnvironment(simulator)
        if !s.value.simulatedFaces { s.value.simulatedFaces = true }      // no camera in the Simulator
        #endif
        _settings = StateObject(wrappedValue: s)
        _model = StateObject(wrappedValue: AppModel(settings: s))
    }

    var body: some SwiftUI.Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .environmentObject(settings)
                .task {
                    model.start()
                    // Rehearsal / screenshots: `-scenario 13` starts a debug scenario at launch,
                    // `-scenarioFreeze 3.0` holds it at that moment.
                    let args = UserDefaults.standard
                    if let s = SpatialScenario.all.first(where: { $0.number == args.integer(forKey: "scenario") }) {
                        settings.value.visualStyle = .spatialV2
                        let freeze = args.double(forKey: "scenarioFreeze")
                        model.debugRun(s, freezeAt: freeze > 0 ? freeze : nil)
                    }
                }
        }
    }
}
