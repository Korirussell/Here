import SwiftUI

/// Guided walkthrough of README §23's 2-minute setup checklist, one step at a time, so the presenter
/// doesn't have to hold the five steps (and their order) in their head under a judging-table clock.
/// Steps 2 and 4 trigger the same actions Settings already exposes (Calibrate, belt test); step 3 shows
/// a live readout (relay, faces tracked, newest caption) so "check" landing on Person 1 is confirmable
/// right here instead of eyeballing the monitor.
struct DemoSetupView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var step = 0
    @State private var calibrated = false
    @State private var beltTested = false

    private let steps: [Step] = [
        Step(title: "Array on its mark", seconds: 10,
             body: "Set the ReSpeaker array on its taped spot on the table. Nothing to do in the app yet."),
        Step(title: "Wearer calibrates", seconds: 10,
             body: "Wearer stands on their mark, facing the array's front, then taps Calibrate below."),
        Step(title: "Speakers check in", seconds: 15,
             body: "Speakers to their marks. One of them says \"check\" from Person 1's mark -- "
                  + "watch for a ring and caption to land on them below."),
        Step(title: "Belt test pattern", seconds: 5,
             body: "Run the belt's test pattern -- each motor should buzz in turn, front to back to sides."),
        Step(title: "Mirror visible", seconds: 5,
             body: "Confirm the AirPlay (or QuickTime cable) mirror is live on the judges' monitor. That's the full 90 s."),
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 22) {
                HStack(spacing: 6) {
                    ForEach(steps.indices, id: \.self) { i in
                        Capsule()
                            .fill(i <= step ? Color(red: 0.25, green: 0.85, blue: 0.85) : Color.white.opacity(0.15))
                            .frame(height: 5)
                    }
                }
                .padding(.top, 4)

                VStack(alignment: .leading, spacing: 14) {
                    HStack {
                        Text("Step \(step + 1) of \(steps.count)")
                            .font(Typeface.small())
                            .foregroundStyle(.secondary)
                        Spacer()
                        Text("~\(steps[step].seconds)s")
                            .font(Typeface.small())
                            .foregroundStyle(.secondary)
                    }
                    Text(steps[step].title)
                        .font(.system(.title, design: .rounded).weight(.bold))
                    Text(steps[step].body)
                        .font(.system(.body, design: .rounded))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)

                    stepAction
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
                .hereGlass(cornerRadius: 20, tint: .black.opacity(0.5))

                Spacer()

                HStack {
                    Button("Back") { step = max(0, step - 1) }
                        .disabled(step == 0)
                    Spacer()
                    if step < steps.count - 1 {
                        Button("Next") { step += 1 }.buttonStyle(.borderedProminent)
                    } else {
                        Button("Done") { dismiss() }.buttonStyle(.borderedProminent)
                    }
                }
            }
            .padding(20)
            .navigationTitle("Demo Setup")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
    }

    @ViewBuilder private var stepAction: some View {
        switch step {
        case 1:
            VStack(alignment: .leading, spacing: 8) {
                LabeledContent("Heading", value: String(format: "%+.1f°  (%@)", model.headingDeg, model.headingQuality))
                Button {
                    model.calibrate()
                    calibrated = true
                } label: {
                    Label(calibrated ? "Calibrated" : "Calibrate", systemImage: calibrated ? "checkmark.circle.fill" : "scope")
                }
                .buttonStyle(.borderedProminent)
                .tint(calibrated ? .green : .accentColor)
            }
        case 2:
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Relay", value: relayText)
                LabeledContent("Faces tracked", value: "\(model.faces.count)")
                if let newest = model.visibleCaptions.last {
                    LabeledContent("Newest caption", value: newest.text.isEmpty ? "(typing…)" : newest.text)
                        .lineLimit(1)
                } else {
                    Text("Waiting for \"check\"…").font(.footnote).foregroundStyle(.secondary)
                }
            }
        case 3:
            Button {
                model.testBelt()
                beltTested = true
            } label: {
                Label(beltTested ? "Test pattern sent" : "Run belt test pattern",
                      systemImage: beltTested ? "checkmark.circle.fill" : "waveform.path")
            }
            .buttonStyle(.borderedProminent)
            .tint(beltTested ? .green : .accentColor)
        default:
            EmptyView()
        }
    }

    private var relayText: String {
        switch model.relayStatus {
        case .connected: "connected"
        case .connecting: "connecting"
        case .disconnected: "disconnected"
        case .rejected: "rejected"
        }
    }

    private struct Step {
        let title: String
        let seconds: Int
        let body: String
    }
}
