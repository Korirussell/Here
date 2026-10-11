import SwiftUI

/// Thin ring around whoever is talking, in their color, pulsing gently with loudness (SPEC §14).
struct SpeakerRing: View {
    let color: Color
    let loudness: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        Ellipse()
            .stroke(AngularGradient(colors: [color.opacity(0.5), color, color.opacity(0.5), color],
                                    center: .center), lineWidth: 3)
            .shadow(color: color.opacity(0.8), radius: 6)
            .scaleEffect(reduceMotion ? 1 : (pulse ? 1 + 0.06 + 0.10 * loudness : 1))
            .opacity(0.9)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.55).repeatForever(autoreverses: true)) { pulse = true }
            }
            .allowsHitTesting(false)
    }
}

/// Top-down direction ring: wearer in the middle, front at the top, a glowing arc toward the sound
/// (SPEC §14: like a video-game damage indicator).
struct DirectionRing: View {
    let relative: Double?          // degrees, body frame; nil = unknown direction
    var color: Color = Palette.alert
    var arcDeg = 70.0
    var emoji: String? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pinged = false

    var body: some View {
        ZStack {
            // One-shot ripple the instant a new alert card mounts -- an "impact" cue distinct from the
            // continuous pulses elsewhere, since this only ever plays once per alert (`onAppear`, not
            // repeating), so it doesn't add to the steady-state render cost.
            if !reduceMotion {
                Circle()
                    .stroke(color, lineWidth: 2)
                    .scaleEffect(pinged ? 1.9 : 1)
                    .opacity(pinged ? 0 : 0.8)
            }
            Circle().stroke(.white.opacity(0.25), lineWidth: 2)
            Capsule().fill(.white.opacity(0.6)).frame(width: 2, height: 7).offset(y: -24)   // "front" tick
            if let rel = relative {
                Circle()
                    .trim(from: 0, to: arcDeg / 360)
                    .stroke(color, style: StrokeStyle(lineWidth: 6, lineCap: .round))
                    .rotationEffect(.degrees(rel - 90 - arcDeg / 2))          // trim starts at 3 o'clock
                    .shadow(color: color, radius: 6)
            } else {
                Circle().stroke(color.opacity(0.6), style: StrokeStyle(lineWidth: 3, dash: [3, 5]))
            }
            if let emoji { Text(emoji).font(.system(size: 20)) }
            else { Circle().fill(.white).frame(width: 6, height: 6) }
        }
        .frame(width: 56, height: 56)
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeOut(duration: 0.6)) { pinged = true }
        }
    }
}

/// Glow on the screen edge toward an off-screen alert or sound. `width`/`peakOpacity` default to the
/// original alert look; ambient sounds (OverlayView) pass a slimmer, dimmer variant so an alert still
/// reads as more urgent than "there's some sound over there."
struct EdgeGlow: View {
    let leading: Bool
    let color: Color
    var width: CGFloat = 70
    var peakOpacity: Double = 0.95
    var restOpacity: Double = 0.45
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var on = false

    var body: some View {
        LinearGradient(colors: [color.opacity(0.75), color.opacity(0)],
                       startPoint: leading ? .leading : .trailing, endPoint: leading ? .trailing : .leading)
            .frame(width: width)
            .opacity(reduceMotion ? restOpacity + (peakOpacity - restOpacity) * 0.7 : (on ? peakOpacity : restOpacity))
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) { on = true }
            }
            .allowsHitTesting(false)
    }
}

/// Small top-left pill: relay, transcription engine, thermal, latency (SPEC §14).
struct StatusPill: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        HStack(spacing: 8) {
            Circle().fill(relayColor).frame(width: 9, height: 9)
            Text(relayText)
            if model.relayRoute == .fallback { Text("cloud") }   // on the Vultr fallback, not the local relay
            if let engine = model.laptopEngine {
                Text(engine == .scribeRealtime ? "Scribe" : "Whisper")
            }
            if let lag = model.lagMs, lag > 0, lag < 5000 { Text("\(Int(lag)) ms") }
            if model.thermal == .serious || model.thermal == .critical {
                Image(systemName: "thermometer.high").foregroundStyle(.orange)
            }
        }
        .font(Typeface.small())
        .foregroundStyle(.white)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .hereGlass(cornerRadius: 14, simple: model.simplifyGlass)
        // Without this, VoiceOver treats the dot/text/icon as separate stops to swipe through one at a
        // time instead of one "connection status" announcement.
        .accessibilityElement(children: .combine)
    }

    private var laptopQuiet: Bool { model.laptopQuiet }

    private var relayColor: Color {
        switch model.relayStatus {
        case .connected: laptopQuiet ? .yellow : (model.laptopStatus == .degraded ? .yellow : .green)
        case .connecting: .yellow
        case .disconnected: .red
        case .rejected: .red
        }
    }

    private var relayText: String {
        switch model.relayStatus {
        case .connected: laptopQuiet ? "no laptop" : "live"
        case .connecting: "connecting"
        case .disconnected: "offline"
        case .rejected: "bad token"   // SPEC §7/§12: 401 before the WebSocket opens -- won't fix itself
        }
    }
}

/// Bottom-right: the 8 motors around the waist, the last one fired lit, so the audience (and the
/// wearer's helpers) can see what the belt does even if BLE drops (SPEC §23 backup).
struct BeltIndicator: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var belt: BeltController

    var body: some View {
        TimelineView(.periodic(from: .now, by: 0.1)) { timeline in
            let fresh = belt.lastCommandAt.map { timeline.date.timeIntervalSince($0) < 1.2 } ?? false
            ZStack {
                ForEach(1...8, id: \.self) { motor in
                    let lit = fresh && (belt.lastCommand?.motor == motor || belt.lastCommand?.motor2 == motor
                                        || belt.lastCommand?.pattern == .test)
                    Circle()
                        .fill(lit ? Palette.alert : .white.opacity(0.25))
                        .frame(width: lit ? 11 : 7, height: lit ? 11 : 7)
                        .offset(y: -24)
                        .rotationEffect(.degrees(Double(motor - 1) * 45))
                }
                Image(systemName: "figure.stand").font(.system(size: 16)).foregroundStyle(.white.opacity(0.8))
            }
            .frame(width: 64, height: 64)
            .overlay(alignment: .bottom) {
                Text(belt.state == .ready ? (fresh ? (belt.lastCommand?.pattern.label ?? "") : "belt") : "belt: \(belt.state.rawValue)")
                    .font(Typeface.small(.caption2))
                    .foregroundStyle(.white.opacity(0.8))
                    .offset(y: 14)
            }
        }
        .padding(10)
        .hereGlass(cornerRadius: 16, simple: model.simplifyGlass)
    }
}
