import SwiftUI

/// Debug/rehearsal-only controls (README §14's "Developer" settings) -- simulated inputs, the raw
/// overlay, BLE UUIDs, and no-laptop test triggers. Split out of the main Settings screen so a judge
/// or demo viewer paging through Settings never lands on "inject test caption" by accident; everything
/// here is for solo rehearsal, not the live demo.
struct DeveloperSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var store: SettingsStore

    var body: some View {
        Form {
            Section {
                NavigationLink { CalibrationDebugView().environmentObject(model).environmentObject(store) } label: {
                    Label("Calibration & audio check", systemImage: "scope")
                }
                Text("Where the wearer stands, a measured calibration check (GOOD / FAIR / BAD), and every stage of a "
                     + "sound's direction from the array to the screen.")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Label("Calibration", systemImage: "scope") }

            Section {
                Toggle("Show Person Recognition Debug", isOn: $store.value.personDebug)
                if store.value.personDebug {
                    Toggle("Face boxes", isOn: $store.value.personDebugFaceBoxes)
                    Toggle("Body boxes", isOn: $store.value.personDebugBodyBoxes)
                    Toggle("Identity scores", isOn: $store.value.personDebugIdentity)
                    Toggle("Speaker scores", isOn: $store.value.personDebugSpeaker)
                    Toggle("Audio associations", isOn: $store.value.personDebugAudio)
                }
                Toggle("Lip movement as speaker evidence", isOn: $store.value.mouthTracking)
                Text("Person Recognition Debug draws each person's face and body boxes in their identity color, with "
                     + "what tracking, re-identification and speaker attribution currently believe. For testing only: "
                     + "with it off there are no boxes or text on people, only the aura and captions.")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Label("Person recognition", systemImage: "person.crop.rectangle") }

            Section {
                Picker("Visual style", selection: $store.value.visualStyle) {
                    Text("Spatial V2").tag(SpatialVisualStyle.spatialV2)
                    Text("Legacy").tag(SpatialVisualStyle.legacy)
                }
                Text("Spatial V2: quiet until something happens, then auras on speakers and sound icons "
                     + "on an invisible path around the view. Legacy: rings, the top-right panel and edge glows.")
                    .font(.footnote).foregroundStyle(.secondary)
                if model.sessionPeople.contains(where: \.isSessionPinned) {
                    Button("Clear pins", role: .destructive) { model.clearPins() }
                }
                Toggle("Body fallback (hold a person when their face is lost)", isOn: $store.value.bodyFallback)
                Toggle("Object detection (quiet objects)", isOn: $store.value.objectDetection)
                Toggle("Scene Understanding (show recognised objects)", isOn: $store.value.sceneDebug)
                Text("Scene Understanding draws each recognised object's region, class, confidence, stable id and "
                     + "state. Normal use never shows these: objects are tracked silently and only appear when a "
                     + "sound is attributed to one.")
                    .font(.footnote).foregroundStyle(.secondary)
                Toggle("Recognise people in People (experimental)", isOn: $store.value.knownRecognition)
                Text("Recognition compares a general appearance print, not a face-recognition model: "
                     + "expect misses, and it only names someone on a strong, unambiguous match.")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Label("Presentation", systemImage: "sparkles") }

            Section {
                if let running = model.scenario {
                    Button("Stop scenario \(running.number): \(running.name)", role: .destructive) { model.debugRun(nil) }
                }
                ForEach(SpatialScenario.all) { s in
                    Button {
                        store.value.visualStyle = .spatialV2
                        model.debugRun(s)
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(s.number). \(s.name)")
                            Text(s.summary).font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
                Text("Scripted scenes on a loop: people, speech, sounds, alerts and turning, with no "
                     + "laptop, microphone or belt. Pick one, tap Done to watch it, come back here to stop.")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Label("Spatial scenarios", systemImage: "play.rectangle") }

            Section {
                Toggle("Debug overlay (stats and diagnostics)", isOn: $store.value.showDebug)
            } header: { Label("Overlay", systemImage: "square.dashed") }

            Section {
                if !store.value.simulatedFaces {
                    Toggle("Use front camera (testing)", isOn: $store.value.debugFrontCamera)
                    Text("Production is always the back camera. Flip this to see yourself and the "
                         + "screen at once when testing alone.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Toggle("Simulated faces (no camera)", isOn: $store.value.simulatedFaces)
                if store.value.simulatedFaces {
                    VStack(alignment: .leading) {
                        Text("Simulated heading \(Int(store.value.simulatedHeadingDeg))°")
                        Slider(value: $store.value.simulatedHeadingDeg, in: -180...180, step: 1)
                        Text("Rehearse the turn-around with no phone: drag toward 180° and the off-camera "
                             + "caption behind you should slide onto Person 3 as they come into view.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            } header: { Label("Simulated input", systemImage: "camera.metering.none") }

            Section {
                Picker("Simulated thermal state", selection: $store.value.debugThermalOverride) {
                    Text("Real (off)").tag("")
                    Text("Nominal").tag("nominal")
                    Text("Fair").tag("fair")
                    Text("Serious").tag("serious")
                    Text("Critical").tag("critical")
                }
                Text("Serious/critical halve the face-detection rate; critical also swaps captions to "
                     + "a flat plate instead of the glass (cheaper to render).")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Label("Thermal", systemImage: "thermometer.medium") }

            Section {
                Toggle("Send scene + attribution (extras)", isOn: $store.value.sendScene)
                DisclosureGroup("Belt BLE UUIDs") {
                    TextField("Service", text: $store.value.beltServiceUUID).font(.caption.monospaced())
                    TextField("Command", text: $store.value.beltCommandUUID).font(.caption.monospaced())
                }
            } header: { Label("Advanced", systemImage: "wrench.and.screwdriver") }

            Section {
                Button("Inject test caption") { model.debugInjectTestCaption() }
                Text("\"Hey, my name's Kori, how are you?\" -- lands on your face if one's tracked, "
                     + "off-camera otherwise. Tap Done first so it's visible.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Trigger test doorbell") { model.debugInjectTestAlert(kind: .doorbell) }
                Button("Trigger test name called") { model.debugInjectTestAlert(kind: .nameCalled) }
                Text("Alerts clear themselves after 6 s, same as a real one ending.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Inject test ambient sound") { model.debugInjectTestSound() }
                Text("Music, 60° off from wherever you're facing -- shows the on-screen \"shading\" "
                     + "glow if it's in view, or an edge glow + tag if it's not.")
                    .font(.footnote).foregroundStyle(.secondary)
            } header: { Label("Test triggers (no laptop needed)", systemImage: "bolt.badge.a") }
        }
        .navigationTitle("Developer")
        .navigationBarTitleDisplayMode(.inline)
    }
}
