import SwiftUI

/// Edits the local transport target plus the wire `config` message
/// (HERE_PLAN.md §8.4): user name + aliases, feature toggles, alert
/// toggles, belt strength, caption scale. Saving pushes `config` to the
/// hub and reconnects if the hub host/port changed.
struct SettingsView: View {
    @ObservedObject var config: AppConfig
    var onSave: (_ transportChanged: Bool) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var aliasesText: String = ""
    @State private var originalHost: String = ""
    @State private var originalPort: Int = 0

    var body: some View {
        NavigationStack {
            Form {
                transportSection
                cameraSection
                headingSection
                identitySection
                featuresSection
                alertsSection
                beltAndCaptionsSection
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                }
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .onAppear {
                aliasesText = config.nameAliases.joined(separator: ", ")
                originalHost = config.hubHost
                originalPort = config.hubPort
            }
        }
    }

    private var transportSection: some View {
        Section("Laptop link (USB)") {
            TextField("Laptop IP", text: $config.hubHost)
                .keyboardType(.decimalPad)
                .autocorrectionDisabled()
            Stepper(value: $config.hubPort, in: 1...65535) {
                HStack {
                    Text("Port")
                    Spacer()
                    Text("\(config.hubPort)").foregroundStyle(.secondary)
                }
            }
            Text("Personal Hotspot over USB: this is the laptop's IP on the Apple network adapter. For the iproxy fallback, use 127.0.0.1 and the forwarded port.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var cameraSection: some View {
        Section("Camera") {
            Picker("Lens", selection: $config.lens) {
                ForEach(AppConfig.Lens.allCases) { lens in
                    Text(lens.rawValue.capitalized).tag(lens)
                }
            }
            Stepper(value: $config.targetFps, in: 10...30, step: 1) {
                HStack {
                    Text("Target fps")
                    Spacer()
                    Text("\(Int(config.targetFps))").foregroundStyle(.secondary)
                }
            }
            VStack(alignment: .leading) {
                Text("JPEG quality: \(String(format: "%.2f", config.jpegQuality))")
                Slider(value: $config.jpegQuality, in: 0.1...1.0)
            }
            HStack {
                Text("HFOV (\(config.lens.rawValue))")
                Spacer()
                Text("\(Int(config.hfovDeg))°").foregroundStyle(.secondary)
            }
            Text("HFOV default is a placeholder (tune) — replace after the harness measurement in ios_here/SPIKE.md.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var headingSection: some View {
        Section("Heading") {
            Toggle("Flip sign (clockwise should increase heading)", isOn: $config.headingSignFlip)
            Text("Turn the phone clockwise while facing the harness forward. If the on-screen heading decreases, enable this.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var identitySection: some View {
        Section("Wearer") {
            Picker("Profile", selection: $config.profile) {
                Text("Deaf / hard-of-hearing").tag("deaf")
                Text("Custom").tag("custom")
            }
            TextField("Name (for name-called detection)", text: $config.userName)
            TextField("Aliases, comma-separated", text: $aliasesText)
                .autocorrectionDisabled()
        }
    }

    private var featuresSection: some View {
        Section("Features") {
            Toggle("Captions", isOn: $config.features.captions)
            Toggle("Tone", isOn: $config.features.tone)
            Toggle("Sound labels", isOn: $config.features.soundLabels)
            Toggle("Alerts", isOn: $config.features.alerts)
            Toggle("Belt", isOn: $config.features.belt)
            Toggle("Memory / search", isOn: $config.features.memory)
            Toggle("Looking-away summary (stretch)", isOn: $config.features.lookingAwaySummary)
            Toggle("Type-to-speak (stretch)", isOn: $config.features.typeToSpeak)
        }
    }

    private var alertsSection: some View {
        Section("Alerts enabled") {
            ForEach(config.alertsEnabled.keys.sorted(), id: \.self) { key in
                Toggle(key.replacingOccurrences(of: "_", with: " ").capitalized, isOn: Binding(
                    get: { config.alertsEnabled[key] ?? false },
                    set: { config.alertsEnabled[key] = $0 }
                ))
            }
        }
    }

    private var beltAndCaptionsSection: some View {
        Section("Belt & captions") {
            VStack(alignment: .leading) {
                Text("Belt strength: \(config.beltStrength)")
                Slider(value: Binding(
                    get: { Double(config.beltStrength) },
                    set: { config.beltStrength = Int($0) }
                ), in: 0...255)
            }
            VStack(alignment: .leading) {
                Text("Caption scale: \(String(format: "%.2f", config.captionScale))")
                Slider(value: $config.captionScale, in: 0.75...1.5)
            }
        }
    }

    private func save() {
        config.nameAliases = aliasesText
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        let transportChanged = config.hubHost != originalHost || config.hubPort != originalPort
        onSave(transportChanged)
        dismiss()
    }
}

#Preview {
    SettingsView(config: AppConfig()) { _ in }
}
