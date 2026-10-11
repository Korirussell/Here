import SwiftUI

/// The glasses view (SPEC §14): full-screen camera, AR overlay, status pill top-left, sound panel
/// top-right, belt indicator bottom-right, settings bottom-left. Mirrored to the judges' monitor.
struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @State private var showSettings = false
    @State private var before = Settings()

    private var spatial: Bool { settings.value.visualStyle == .spatialV2 }

    var body: some View {
        GeometryReader { geo in
            ZStack {
                if model.usesBackdrop {
                    SimulatedBackdrop()
                } else {
                    CameraPreview(camera: model.camera) { layer in model.previewLayer = layer }
                }

                // Spatial V2 is quiet until something happens; the legacy layer stays available
                // behind Settings > Developer > Visual style until V2 is proven on the rig.
                if spatial {
                    SpatialOverlayView(size: geo.size)
                } else {
                    OverlayView(size: geo.size)
                }

                if settings.value.sceneDebug {
                    SceneUnderstandingOverlay(objects: model.objects, proj: model.projector(size: geo.size), size: geo.size,
                                              now: model.now, scanMs: model.objectScanMs, detectorName: model.objectDetectorName,
                                              people: model.sessionPeople,
                                              faces: model.faces, scene: model.scene)
                }

                if settings.value.personDebug {
                    PersonRecognitionOverlay(size: geo.size)
                }

                if settings.value.showDebug {
                    DebugOverlay(size: geo.size)
                }

                VStack {
                    HStack(alignment: .top) {
                        // Spatial V2: the pill appears when the wearer needs to know something
                        // (not connected, no laptop, hot), not as a permanent "live" light.
                        if !spatial || model.statusNeedsAttention || settings.value.showDebug { StatusPill() }
                        if settings.value.focusMode { ModeBadge(text: "Focus") }
                        if settings.value.beltOnlyMode { ModeBadge(text: "Belt only") }
                        if let scenario = model.scenario { ModeBadge(text: "Scenario \(scenario.number)") }
                        Spacer()
                        if !spatial { SoundPanel() }      // V2 shows alerts on the path, at their bearing
                    }
                    Spacer()
                    HStack(alignment: .bottom) {
                        Button {
                            before = settings.value
                            showSettings = true
                        } label: {
                            Image(systemName: "gearshape.fill")
                                .font(.system(size: 18))
                                .foregroundStyle(.white.opacity(0.8))
                                .padding(10)
                                .hereGlass(cornerRadius: 22, simple: model.simplifyGlass)
                                .opacity(spatial ? Tunables.quietChromeOpacity : 1)
                        }
                        .accessibilityLabel("Settings")
                        Spacer()
                        if spatial {
                            TransientBeltIndicator(belt: model.belt)
                        } else {
                            BeltIndicator(belt: model.belt)
                        }
                    }
                }
                .padding(.horizontal, 18)
                .padding(.vertical, 12)
            }
        }
        .ignoresSafeArea()
        .statusBarHidden()
        .persistentSystemOverlays(.hidden)
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSettings, onDismiss: { model.applySettings(previous: before) }) {
            SettingsView()
                .environmentObject(model)
                .environmentObject(settings)
        }
    }
}

/// Spatial V2: the belt indicator shows what the belt just did (SPEC §23: visible even if BLE
/// drops), then gets out of the way instead of sitting in the corner all the time.
struct TransientBeltIndicator: View {
    @ObservedObject var belt: BeltController

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.25)) { timeline in
            let fresh = belt.lastCommandAt.map { timeline.date.timeIntervalSince($0) < Tunables.beltIndicatorHoldS } ?? false
            BeltIndicator(belt: belt)
                .opacity(fresh ? 1 : 0)
                .animation(.easeOut(duration: Tunables.presenceFadeOutS), value: fresh)
                .accessibilityHidden(!fresh)
        }
    }
}

struct ModeBadge: View {
    let text: String
    var body: some View {
        Text(text)
            .font(Typeface.small())
            .foregroundStyle(.white)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .hereGlass(cornerRadius: 14, tint: .blue.opacity(0.3))
    }
}

/// Stand-in for the camera in the Simulator / simulated-faces mode: a dim room with two "people".
struct SimulatedBackdrop: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        GeometryReader { geo in
            let proj = model.projector(size: geo.size)
            ZStack {
                LinearGradient(colors: [Color(white: 0.20), Color(white: 0.07)], startPoint: .top, endPoint: .bottom)
                ForEach(model.faces) { face in
                    let r = proj.rect(forVisionBox: face.box)
                    VStack(spacing: 4) {
                        Circle().fill(Color(white: 0.55)).frame(width: r.width, height: r.height)
                        RoundedRectangle(cornerRadius: r.width / 2).fill(Color(white: 0.4))
                            .frame(width: r.width * 1.8, height: r.height * 2.2)
                    }
                    .position(x: r.midX, y: r.midY + r.height * 1.25)
                }
            }
        }
    }
}
