import SwiftUI

/// The on-phone status screen. Mirrors what `mock_hub.py` prints on the
/// laptop side (heading, quality, fps, clock offset) so the wearer/operator
/// can sanity-check the link without needing the laptop in view — this is
/// also the very first thing CLAUDE.md's suggested build order asks for:
/// "heading-only app showing the number on screen."
struct ContentView: View {
    @EnvironmentObject private var appState: AppState
    @State private var showingSettings = false

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            VStack(spacing: 28) {
                header

                Spacer(minLength: 0)

                headingDial

                Spacer(minLength: 0)

                statsGrid

                if let error = appState.cameraErrorMessage {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .multilineTextAlignment(.center)
                }

                calibrateButton
            }
            .padding()
            .padding(.top, 8)
        }
        .sheet(isPresented: $showingSettings) {
            SettingsView(config: appState.config) { transportChanged in
                appState.applySettingsChange(transportChanged: transportChanged)
            }
        }
    }

    private var header: some View {
        HStack {
            Circle()
                .fill(statusColor)
                .frame(width: 10, height: 10)
            Text("HERE · ios_here")
                .font(.headline)
                .foregroundStyle(.white)
            Spacer()
            Text(statusText)
                .font(.caption)
                .foregroundStyle(.secondary)
            Button {
                showingSettings = true
            } label: {
                Image(systemName: "gearshape.fill")
                    .foregroundStyle(.white)
            }
        }
    }

    private var headingDial: some View {
        VStack(spacing: 10) {
            ZStack {
                Circle()
                    .stroke(Color.white.opacity(0.15), lineWidth: 2)
                    .frame(width: 180, height: 180)
                Image(systemName: "location.north.fill")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 42, height: 42)
                    .foregroundStyle(headingQualityColor)
                    .rotationEffect(.degrees(appState.lastHeadingDeg ?? 0))
                    .animation(.easeOut(duration: 0.15), value: appState.lastHeadingDeg)
            }

            Text(headingText)
                .font(.system(size: 44, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
                .monospacedDigit()

            Text(appState.headingQuality)
                .font(.caption)
                .foregroundStyle(headingQualityColor)
        }
    }

    private var statsGrid: some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 12) {
            statTile("Frames", String(format: "%.0f fps", appState.fps))
            statTile("Heading", String(format: "%.0f Hz", appState.headingHz))
            statTile("Clock offset", String(format: "%.0f ms", appState.clockOffsetMs))
            statTile("RTT", String(format: "%.0f ms", appState.clockRttMs))
        }
    }

    private func statTile(_ label: String, _ value: String) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.system(.title3, design: .rounded)).bold()
                .foregroundStyle(.white)
                .monospacedDigit()
            Text(label)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Color.white.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private var calibrateButton: some View {
        Button {
            appState.calibrateHeadingZero()
        } label: {
            Text("Calibrate — facing array front")
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
        }
        .buttonStyle(.borderedProminent)
        .tint(.blue)
    }

    private var headingText: String {
        guard let h = appState.lastHeadingDeg else { return "—" }
        return String(format: "%.0f°", h)
    }

    private var headingQualityColor: Color {
        switch appState.headingQuality {
        case "good": return .green
        case "settling": return .yellow
        default: return .red
        }
    }

    private var statusColor: Color {
        switch appState.connectionStatus {
        case .connected: return .green
        case .connecting: return .yellow
        case .disconnected: return .red
        }
    }

    private var statusText: String {
        switch appState.connectionStatus {
        case .connected: return "connected"
        case .connecting: return "connecting…"
        case .disconnected: return "disconnected"
        }
    }
}

#Preview {
    ContentView().environmentObject(AppState())
}
