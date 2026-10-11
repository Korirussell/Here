import SwiftUI

/// SPEC §14 settings: relay URL + token + session, user name + aliases, HFOV, Calibrate,
/// focus mode, belt-only mode, belt strength, belt connect/test, feature toggles.
/// Changes save immediately; transport changes apply when the sheet closes.
struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var store: SettingsStore
    @Environment(\.dismiss) private var dismiss
    @State private var aliases = ""
    @State private var showDemoSetup = false
    @State private var confirmReset = false

    private var relayStatusText: String {
        relayStateText + (model.relayRoute == .primary ? " (primary)" : " (fallback)")
    }

    private var relayStateText: String {
        switch model.relayStatus {
        case .connected: "connected"
        case .connecting: "connecting"
        case .disconnected: "disconnected"
        case .rejected(let code): "rejected (HTTP \(code), bad token)"
        }
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Button { showDemoSetup = true } label: {
                        Label("Demo Setup", systemImage: "checklist")
                    }
                    Text("Walks through SPEC §23's 2-minute checklist, one step at a time: array, "
                         + "calibrate, speaker check, belt test, mirror.")
                        .font(.footnote).foregroundStyle(.secondary)
                }

                Section {
                    LabeledContent("Heading", value: String(format: "%+.1f°  (%@)", model.headingDeg, model.headingQuality))
                    Button {
                        model.calibrate()
                    } label: {
                        Label("Calibrate: wearer faces the array's front", systemImage: "scope")
                    }
                    Toggle("Flip heading sign", isOn: $store.value.headingSignFlip)
                    Text("Turn clockwise: the heading must go up. If it goes down, flip it.")
                        .font(.footnote).foregroundStyle(.secondary)
                    HStack {
                        Text("Field of view")
                        Spacer()
                        Text(store.value.hfovOverride > 0 ? String(format: "%.0f°", store.value.hfovOverride)
                             : String(format: "auto %.0f°", model.hfovDeg))
                            .foregroundStyle(.secondary)
                    }
                    Slider(value: $store.value.hfovOverride, in: 0...130, step: 1) { Text("HFOV") }
                    Text("0 = use the camera's reported FOV. Measure in the harness and set it here (SPEC §8).")
                        .font(.footnote).foregroundStyle(.secondary)
                } header: { Label("Calibrate (every demo)", systemImage: "scope") }

                Section {
                    TextField("Your name (for name called)", text: $store.value.userName)
                        .textInputAutocapitalization(.words)
                    TextField("Nicknames / misspellings, comma separated", text: $aliases)
                        .onChange(of: aliases) { _, v in
                            store.value.nameAliases = v.split(separator: ",")
                                .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
                        }
                } header: { Label("You", systemImage: "person.fill") }

                Section {
                    NavigationLink { PeopleSettingsView(known: model.known).environmentObject(model) } label: {
                        Label("People (\(model.known.people.count))", systemImage: "person.2.fill")
                    }
                    Text("People you've added keep their name, color and priority across sessions. Add someone "
                         + "by tapping their face, then the add button. This is separate from \"pin for now\", "
                         + "which only lasts for the current session.")
                        .font(.footnote).foregroundStyle(.secondary)
                } header: { Label("People", systemImage: "person.2.fill") }

                Section {
                    Toggle("Focus mode (speech + alerts only)", isOn: $store.value.focusMode)
                    Toggle("Belt-only mode (heading locked to 0°)", isOn: $store.value.beltOnlyMode)
                } header: { Label("Modes", systemImage: "slider.horizontal.3") }

                Section {
                    Toggle("Captions", isOn: $store.value.features.captions)
                    Toggle("Tone", isOn: $store.value.features.tone)
                    Toggle("Rings", isOn: $store.value.features.rings)
                    Toggle("Alerts", isOn: $store.value.features.alerts)
                    Toggle("Belt", isOn: $store.value.features.belt)
                } header: { Label("Show", systemImage: "eye") }

                Section {
                    ForEach(AlertKind.allCases, id: \.self) { kind in
                        Toggle(kind.rawValue.replacingOccurrences(of: "_", with: " ").capitalized,
                               isOn: Binding(get: { store.value.alertsEnabled[kind.rawValue] ?? true },
                                             set: { store.value.alertsEnabled[kind.rawValue] = $0 }))
                    }
                } header: { Label("Alerts", systemImage: "bell.fill") }

                Section {
                    BeltStatusRow(belt: model.belt)
                    HStack {
                        Button("Connect") { model.belt.connect() }
                        Spacer()
                        Button("Disconnect", role: .destructive) { model.belt.disconnect() }
                    }
                    .buttonStyle(.borderless)
                    VStack(alignment: .leading) {
                        Text("Strength \(store.value.beltStrength)")
                        Slider(value: Binding(get: { Double(store.value.beltStrength) },
                                              set: { store.value.beltStrength = Int($0) }), in: 60...255, step: 5)
                    }
                    Button("Test pattern (each motor in turn)") { model.testBelt() }
                    HStack {
                        ForEach([("Front", 1), ("Right", 3), ("Back", 5), ("Left", 7)], id: \.1) { name, motor in
                            Button(name) { model.testBelt(motor: motor) }.buttonStyle(.bordered)
                        }
                    }
                    Toggle("Connect automatically", isOn: $store.value.beltAutoConnect)
                } header: { Label("Belt", systemImage: "waveform.path") }

                Section {
                    TextField("Primary: ws://<laptop-ip>:8080/ws", text: $store.value.relayURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    TextField("Fallback (optional): wss://host/ws", text: $store.value.relayFallbackURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    SecureField("Token", text: $store.value.relayToken)
                    TextField("Session", text: $store.value.session)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    LabeledContent("Status", value: relayStatusText)
                    if case .rejected = model.relayStatus {
                        Text("The relay rejected this token (HTTP 401/403) and won't accept it on retry -- "
                             + "check Token above against the relay's HERE_RELAY_TOKEN, then it'll reconnect.")
                            .font(.footnote).foregroundStyle(.red)
                    }
                    Text("Primary = the laptop's local relay, fallback = Vultr; same stream on both. If the primary "
                         + "doesn't connect within \(Int(Tunables.relayPrimaryConnectS)) s or drops, HERE uses the "
                         + "fallback and retries the primary every \(Int(Tunables.relayPrimaryRetryS)) s (SPEC §7).")
                        .font(.footnote).foregroundStyle(.secondary)
                } header: { Label("Relay", systemImage: "antenna.radiowaves.left.and.right") }

                Section {
                    NavigationLink { DeveloperSettingsView() } label: {
                        Label("Developer", systemImage: "hammer.fill")
                    }
                    Text("Simulated faces/heading, debug overlay, thermal rehearsal, BLE UUIDs, "
                         + "no-laptop test triggers. Rehearsal-only, not for the live demo.")
                        .font(.footnote).foregroundStyle(.secondary)
                }

                Section {
                    Button(role: .destructive) { confirmReset = true } label: {
                        Label("Reset display settings", systemImage: "arrow.counterclockwise")
                    }
                    Text("Resets Modes, Show and Alerts toggles and belt strength to defaults -- for "
                         + "undoing a wrong tap fast between setup and a demo. Leaves Relay, calibration "
                         + "and your name alone.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("HERE")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .onAppear { aliases = store.value.nameAliases.joined(separator: ", ") }
            .sheet(isPresented: $showDemoSetup) { DemoSetupView().environmentObject(model) }
            .confirmationDialog("Reset display settings to defaults?", isPresented: $confirmReset, titleVisibility: .visible) {
                Button("Reset", role: .destructive) { resetDisplayDefaults() }
                Button("Cancel", role: .cancel) {}
            }
        }
    }

    private func resetDisplayDefaults() {
        let d = Settings()
        store.value.focusMode = d.focusMode
        store.value.beltOnlyMode = d.beltOnlyMode
        store.value.features = d.features
        store.value.alertsEnabled = d.alertsEnabled
        store.value.beltStrength = d.beltStrength
    }
}

/// Observes the belt directly so its connection state updates live in the sheet.
struct BeltStatusRow: View {
    @ObservedObject var belt: BeltController
    var body: some View {
        LabeledContent("Status", value: belt.state.rawValue + (belt.peripheralName.map { " (\($0))" } ?? ""))
    }
}

/// People the wearer explicitly added. Everything here lives on this phone only and is deleted with
/// the entry, including the stored appearance descriptor.
struct PeopleSettingsView: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var known: KnownPeopleStore

    var body: some View {
        Form {
            if known.people.isEmpty {
                Text("Nobody added yet. In the camera view, tap a person's face and choose the add button.")
                    .foregroundStyle(.secondary)
            }
            ForEach($known.people) { $person in
                Section {
                    TextField("Name", text: $person.chosenName).textInputAutocapitalization(.words)
                    Picker("Color", selection: $person.preferredColor) {
                        ForEach(Palette.people.indices, id: \.self) { i in
                            Label { Text("Color \(i + 1)") } icon: { Circle().fill(Palette.person(i)).frame(width: 14, height: 14) }.tag(i)
                        }
                    }
                    Picker("Priority", selection: $person.priority) {
                        ForEach(KnownPerson.Priority.allCases, id: \.self) { Text($0.title).tag($0) }
                    }
                    LabeledContent("Appearance stored", value: person.recognitionProfile.isEmpty ? "no" : "yes, on this phone")
                    Button("Remove from People", role: .destructive) {
                        known.people.removeAll { $0.id == person.id }
                    }
                }
            }
        }
        .navigationTitle("People")
        .navigationBarTitleDisplayMode(.inline)
        .onDisappear { model.knownPeopleChanged() }
    }
}
