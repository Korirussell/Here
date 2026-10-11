import SwiftUI

/// "Show Person Recognition Debug" (Settings > Developer): what Vision and attribution believe about
/// each person, drawn on them. Boxes use the person's identity color, so an identity switch shows as
/// a box changing color. Testing only; nothing here exists in normal use.
struct PersonRecognitionOverlay: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    let size: CGSize

    var body: some View {
        let proj = model.projector(size: size)
        let s = settings.value
        ZStack(alignment: .topLeading) {
            ForEach(model.faces) { face in
                let color = Palette.person(face.colorIndex)
                let r = proj.rect(forVisionBox: face.box)
                if s.personDebugBodyBoxes, let body = face.bodyBox {
                    let b = proj.rect(forVisionBox: body)
                    Rectangle().stroke(color.opacity(0.7), style: StrokeStyle(lineWidth: 1.5, dash: [7, 5]))
                        .frame(width: b.width, height: b.height)
                        .position(x: b.midX, y: b.midY)
                }
                if s.personDebugFaceBoxes {
                    // Solid: a detected face. Dashed: an estimated head on a body-held track.
                    Rectangle().stroke(color, style: StrokeStyle(lineWidth: 2.5, dash: face.viaBody ? [4, 4] : []))
                        .frame(width: r.width, height: r.height)
                        .position(x: r.midX, y: r.midY)
                }
                let lines = Self.lines(for: face, model: model, identity: s.personDebugIdentity, speaker: s.personDebugSpeaker,
                                       audio: s.personDebugAudio)
                Text(lines.joined(separator: "\n"))
                    .font(Typeface.small(.caption2)).foregroundStyle(.white)
                    .padding(4)
                    .background(.black.opacity(0.65), in: RoundedRectangle(cornerRadius: 5))
                    .overlay(RoundedRectangle(cornerRadius: 5).stroke(color, lineWidth: 1))
                    .fixedSize()
                    .position(x: min(max(r.midX, 150), size.width - 150), y: min(size.height - 50, r.maxY + 14 + CGFloat(lines.count) * 7))
            }
        }
        .frame(width: size.width, height: size.height)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    /// Everything known about one visible person, grouped by what the toggles ask for.
    static func lines(for face: TrackedFace, model: AppModel, identity: Bool, speaker: Bool, audio: Bool) -> [String] {
        let person = model.sessionPeople.first { $0.id == face.personId }
        let tier = ReID.tier(facePixels: face.pixelHeight)
        var lines = [String(format: "%@  session #%@  color %d%@", face.label, person.map { String($0.id) } ?? "-", face.colorIndex,
                            person?.isSessionPinned == true ? "  pinned" : "")]
        lines.append(String(format: "face track %d  conf %.2f  %dpx (%@)%@", face.id, face.confidence, Int(face.pixelHeight), tier.rawValue,
                            face.viaBody ? "  BODY-HELD" : ""))
        lines.append(face.bodyBox == nil ? "body: none linked" : String(format: "body conf %.2f", face.bodyConfidence))
        if identity, let p = person {
            lines.append(String(format: "identity %.2f  descriptors %d  state %@", p.identityConfidence, p.descriptors, p.state(now: model.now).rawValue))
            if let d = model.people.lastDecision(personId: p.id) {
                let vis = d.evidence?.visualSimilarity.map { String(format: "%.2f", $0) } ?? "-"
                lines.append(String(format: "reid %@: visual %@ place %.2f score %.2f (next %.2f)", d.reason, vis, d.spatial, d.score, d.runnerUp))
            }
        }
        let lock = model.speakerLocks.associations.first { $0.personId != nil && $0.personId == face.personId }
        if speaker {
            lines.append(String(format: "mouth %.2f  speaker %@", face.mouthActivity,
                                lock.map { String(format: "%.2f [%@]", $0.confidence, $0.state.rawValue) } ?? "-"))
        }
        if audio {
            if let lock {
                lines.append(String(format: "audio source %d (stream %d)  bearing error %+.0f°", lock.id, lock.sourceId,
                                    Geo.angDiff(lock.smoothedBearing, model.micBearing(of: face))))
            } else {
                lines.append("audio: no source" + (person?.recentAudioSources.isEmpty == false
                                                    ? "  (held: " + person!.recentAudioSources.map(String.init).joined(separator: ",") + ")" : ""))
            }
        }
        return lines
    }
}
