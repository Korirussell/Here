import SwiftUI

/// Top-right panel (README §14): icon + label ("Doorbell", "Name called") + direction ring +
/// "behind you / to your left". Fades after a few seconds; repeats stack as ×N.
struct SoundPanel: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .trailing, spacing: 10) {
            ForEach(model.visibleAlerts.prefix(3)) { alert in
                AlertCard(alert: alert, relative: alert.bearing.map { Geo.relative($0, heading: model.headingDeg) },
                          simple: model.simplifyGlass)
                    .opacity(alert.active ? 1 : max(0, 1 - (model.now - alert.updated) / Tunables.alertFadeS))
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .animation(.smooth(duration: 0.3), value: model.visibleAlerts.map(\.alertId))
    }
}

struct AlertCard: View {
    let alert: AlertState
    let relative: Double?
    var simple = false

    var color: Color { alert.priority >= 3 ? Palette.emergency : Palette.alert }

    var body: some View {
        HStack(spacing: 12) {
            DirectionRing(relative: relative, color: color, emoji: alert.emoji)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(alert.label).font(Typeface.caption(.title3))
                    if alert.count > 1 { Text("×\(alert.count)").font(Typeface.header()).foregroundStyle(color) }
                }
                Text(relative.map(Geo.describe(relative:)) ?? "direction unknown")
                    .font(Typeface.small(.footnote))
                    .foregroundStyle(.white.opacity(0.85))
            }
            .foregroundStyle(.white)
            .legible()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(minWidth: 220, alignment: .leading)
        .hereGlass(tint: color.opacity(0.28), simple: simple)
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).stroke(color.opacity(0.5), lineWidth: 1))
        .shadow(color: color.opacity(simple ? 0 : 0.4), radius: 14, x: 0, y: 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }

    /// `.combine` would read the visual pieces in layout order ("Doorbell", "×2", "behind you") with no
    /// framing; the direction -- the entire point of the card -- lives in `DirectionRing`'s arc angle,
    /// which has no text at all and would be silently dropped. Same spirit as `CaptionView`'s explicit
    /// label: say plainly what a sighted person reads from the ring and the count badge.
    private var accessibilityText: String {
        var s = "\(alert.label) alert"
        if alert.count > 1 { s += ", repeated \(alert.count) times" }
        s += ", " + (relative.map(Geo.describe(relative:)) ?? "direction unknown")
        return s
    }
}

/// Toggleable debug layer (README §14): face boxes + bearings, source markers, latency, heading.
struct DebugOverlay: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    let size: CGSize

    var body: some View {
        let proj = model.projector(size: size)
        ZStack(alignment: .topLeading) {
            ForEach(Array(model.sources.enumerated()), id: \.offset) { _, s in
                if let x = proj.x(forBearing: s.bearingDeg) {
                    Rectangle().fill(s.speech ? Color.red : Color.gray).frame(width: 4, height: 36)
                        .position(x: x, y: 18)
                    Text("#\(s.id) \(s.bearingDeg, specifier: "%+.0f")°").font(Typeface.small(.caption2))
                        .foregroundStyle(.white).position(x: x, y: 46)
                }
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("heading \(model.headingDeg, specifier: "%+.1f")° (raw \(model.headingRaw, specifier: "%+.1f"), \(model.headingQuality))")
                Text("HFOV \(model.hfovDeg, specifier: "%.0f")°  \(model.camera.lensName)  capture \(Int(model.camera.captureAngle))°")
                Text("faces \(model.faces.count)  sources \(model.sources.map { String(format: "%+.0f%@", $0.bearingDeg, $0.speech ? "*" : "") }.joined(separator: " "))")
                Text("lag \(model.lagMs.map { "\(Int($0)) ms" } ?? "-")  stages \(model.stageMs.map { "\($0.key) \(Int($0.value))" }.sorted().joined(separator: ", "))")
                Text("relay \(model.relayRoute == .primary ? "primary" : "fallback")  clock rtt \(model.relay.clock.bestRtt.isFinite ? "\(Int(model.relay.clock.bestRtt * 1000)) ms" : "-")  thermal \(String(describing: model.thermal))")
                diagnostics
                if let d = model.laptopDetail { Text("laptop: \(d)").lineLimit(1) }
                if let e = model.camera.error { Text("camera: \(e)").foregroundStyle(.orange) }
            }
            .font(Typeface.small(.caption2))
            .foregroundStyle(.white)
            .padding(8)
            .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 8))
            .position(x: 250, y: size.height - 120)
        }
        .frame(width: size.width, height: size.height)
        .allowsHitTesting(false)
    }

    /// Where does it fail? Face detection / tracking / size, body detection, audio, attribution,
    /// re-identification, presentation -- one line each, so a failure at range can be pinned on a stage.
    @ViewBuilder private var diagnostics: some View {
        ForEach(Array(diagnosticLines.enumerated()), id: \.offset) { _, line in Text(line).lineLimit(1) }
    }

    private var diagnosticLines: [String] {
        let d = model.trackerDiagnostics
        var lines = ["FACE detected \(d.facesDetected)  tracked \(model.faces.count)  BODY detected \(d.bodies.count)  held \(d.bodyHeld)"]
        if !d.lastDrop.isEmpty { lines.append("last drop: " + d.lastDrop) }
        let sources = model.sources.map { String(format: "#%d %+.0f° e%.2f%@", $0.id, $0.bearingDeg, $0.energy, $0.speech ? "*" : "") }
        lines.append("AUDIO ids " + (model.sourceIdsMeaningful ? "stable (ODAS)" : "not stable (built-in: all 0)") + "  " + sources.joined(separator: " "))
        if let a = model.attributionDecision {
            let err = a.angularError.map { String(format: "%.0f°", $0) } ?? "-"
            lines.append("ATTRIB \(a.candidate ?? "-") err \(err) " + (a.accepted ? "accepted: " : "rejected: ") + a.reason)
        }
        for r in model.people.recentDecisions.suffix(2) {
            var line = "REID " + r.reason + " -> " + (r.personId.map { "person \($0)" } ?? "new")
            line += String(format: "  score %.2f next %.2f", r.score, r.runnerUp)
            if let e = r.evidence {
                let vis = e.visualSimilarity.map { String(format: "%.2f", $0) } ?? "-"
                line += String(format: "  vis %@ d%.0f° %.0fs", vis, e.bearingDeltaDeg, e.secondsSinceSeen)
                if e.bodyContinuity { line += " body" }
                if e.audioContinuity { line += " audio" }
            }
            lines.append(line)
        }
        let people = model.sessionPeople.map { p -> String in
            var t = p.displayName + (p.isSessionPinned ? " [pin]" : "")
            if !p.isVisible { t += String(format: " (away, spatial %.1f)", p.spatialConfidence(now: model.now)) }
            return t
        }
        lines.append("PEOPLE " + people.joined(separator: ", "))
        lines.append("UI shown \(model.scene.presences.count)  queued \(model.scene.queued.count)  below threshold \(model.scene.suppressed.count)")
        return lines
    }
}
