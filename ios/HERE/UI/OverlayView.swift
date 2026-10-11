import SwiftUI

/// The AR layer over the camera (README §14): rings on whoever is talking, captions above heads,
/// off-camera captions at the screen edge with a chevron, sound tags, and edge glows toward alerts.
struct OverlayView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let size: CGSize

    var body: some View {
        let proj = model.projector(size: size)
        let rects = Dictionary(uniqueKeysWithValues: model.faces.map { ($0.id, proj.rect(forVisionBox: $0.box)) })
        let placements = Layout.captions(model.visibleCaptions, faces: model.faces, rects: rects, proj: proj, size: size)
        let simple = model.simplifyGlass

        ZStack(alignment: .topLeading) {
            // Edge glow toward off-screen alerts.
            ForEach(model.visibleAlerts.filter(\.active)) { a in
                if let b = a.bearing, proj.x(forBearing: b) == nil {
                    EdgeGlow(leading: proj.relative(b) < 0, color: a.priority >= 3 ? Palette.emergency : Palette.alert)
                        .frame(height: size.height)
                        .frame(maxWidth: .infinity, alignment: proj.relative(b) < 0 ? .leading : .trailing)
                }
            }

            // Rings around active speakers.
            if settings.value.features.rings {
                ForEach(model.faces) { face in
                    if let loud = model.activeSpeakers[face.id], let r = rects[face.id] {
                        SpeakerRing(color: Palette.person(face.colorIndex), loudness: loud)
                            .frame(width: r.width * 1.55, height: r.height * 1.45)
                            .position(x: r.midX, y: r.midY)
                    }
                }
            }

            // Ambient (non-alert) sounds, hidden in focus mode: a soft "shading" glow at the
            // direction we think it's coming from -- on screen if it's in view, an edge glow +
            // minimal tag toward it otherwise. Reuses the alert panel's direction language
            // (EdgeGlow) at lower visual weight, so an actual alert still reads as more urgent.
            ForEach(model.visibleSounds) { s in
                let x = s.bearing.flatMap { proj.x(forBearing: $0) }
                let relative = s.bearing.map { proj.relative($0) }
                if let x {
                    SoundGlow(tag: s).position(x: x, y: size.height * 0.4)
                } else if let relative {
                    EdgeGlow(leading: relative < 0, color: Palette.category(s.category),
                             width: 40, peakOpacity: 0.5, restOpacity: 0.18)
                        .frame(height: size.height)
                        .frame(maxWidth: .infinity, alignment: relative < 0 ? .leading : .trailing)
                    SoundTagView(tag: s, x: nil, relative: relative, size: size)
                }
            }

            // Captions.
            if settings.value.features.captions {
                ForEach(placements) { p in
                    HStack(spacing: 6) {
                        if p.chevron == .leading { Chevron(leading: true, color: p.color) }
                        CaptionView(caption: p.caption, color: p.color, title: p.title, tail: p.tail,
                                    simpleGlass: simple, offCameraHint: p.hint)
                        if p.chevron == .trailing { Chevron(leading: false, color: p.color) }
                    }
                    .fixedSize()
                    .position(p.point)
                    // Short enough to still smooth out single-frame jitter (README §14 "no jitter") without
                    // visibly lagging behind the face: the ring and box below track the same data with no
                    // animation at all (they snap every frame), so anything much slower than that reads as
                    // the caption detaching from the person during normal head/camera movement.
                    .animation(reduceMotion ? nil : .smooth(duration: 0.08), value: p.point)
                    .transition(.opacity.combined(with: .scale(scale: 0.96)))
                }
            }
        }
        .frame(width: size.width, height: size.height)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.2), value: placements.map(\.id))
        .allowsHitTesting(false)
    }
}

struct Chevron: View {
    let leading: Bool
    let color: Color
    var body: some View {
        Image(systemName: leading ? "chevron.left" : "chevron.right")
            .font(.system(size: 26, weight: .heavy))
            .foregroundStyle(color)
            .shadow(color: .black.opacity(0.6), radius: 3)
    }
}

/// On-screen ambient sound: a soft pulsing radial glow -- "shading" where we think it's coming from
/// -- with the emoji inside and a small label below, instead of a plain floating text pill.
struct SoundGlow: View {
    let tag: SoundTag
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        let color = Palette.category(tag.category)
        VStack(spacing: 5) {
            ZStack {
                Circle()
                    .fill(RadialGradient(colors: [color.opacity(0.5), color.opacity(0)],
                                         center: .center, startRadius: 2, endRadius: 36))
                    .frame(width: 72, height: 72)
                    .scaleEffect(reduceMotion ? 1 : (pulse ? 1.12 : 0.88))
                    .opacity(reduceMotion ? 0.7 : (pulse ? 0.55 : 0.95))
                Circle()
                    .stroke(color.opacity(0.85), lineWidth: 1.5)
                    .frame(width: 38, height: 38)
                Text(tag.emoji).font(.system(size: 17))
            }
            Text(tag.count > 1 ? "\(tag.text) ×\(tag.count)" : tag.text)
                .font(Typeface.small(.footnote))
                .foregroundStyle(.white)
                .lineLimit(1)
                .padding(.horizontal, 8).padding(.vertical, 3)
                .hereGlass(cornerRadius: 10, tint: color.opacity(0.4))
        }
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 1.3).repeatForever(autoreverses: true)) { pulse = true }
        }
        .allowsHitTesting(false)
    }
}

struct SoundTagView: View {
    let tag: SoundTag
    let x: CGFloat?
    let relative: Double?
    let size: CGSize

    var body: some View {
        let edgeLeading = (relative ?? 0) < 0
        HStack(spacing: 4) {
            if x == nil, relative != nil, edgeLeading { Image(systemName: "chevron.left") }
            Text(tag.emoji)
            Text(tag.count > 1 ? "\(tag.text) ×\(tag.count)" : tag.text)
            if x == nil, relative != nil, !edgeLeading { Image(systemName: "chevron.right") }
        }
        .font(Typeface.small(.footnote))
        .foregroundStyle(.white)
        .padding(.horizontal, 10).padding(.vertical, 5)
        .background(Palette.category(tag.category).opacity(0.35), in: Capsule())
        .overlay(Capsule().stroke(Palette.category(tag.category), lineWidth: 1.5))
        .position(x: x ?? (edgeLeading ? 90 : size.width - 90), y: size.height * 0.16)
    }
}

/// Where each caption goes. Pure layout; kept out of the view so it's easy to reason about.
enum Layout {
    enum ChevronSide { case none, leading, trailing }

    struct Placement: Identifiable {
        let id: String
        let caption: CaptionState
        let title: String
        let color: Color
        let point: CGPoint
        let tail: CaptionView.TailEdge
        let chevron: ChevronSide
        let hint: String?
    }

    static let boxHeight: CGFloat = 96
    static let boxWidth: CGFloat = Tunables.captionMaxWidth
    static let topSafe: CGFloat = 64         // status pill row
    static let margin: CGFloat = 16

    static func captions(_ captions: [CaptionState], faces: [TrackedFace], rects: [Int: CGRect],
                         proj: Projector, size: CGSize) -> [Placement] {
        var out: [Placement] = []
        var edgeY: [Bool: CGFloat] = [true: size.height * 0.34, false: size.height * 0.34]
        let colorByLabel = Dictionary(faces.map { ($0.label, $0.colorIndex) }, uniquingKeysWith: { a, _ in a })

        for c in captions {
            let title: String
            let color: Color
            var faceRect: CGRect?
            switch c.speaker {
            case let .person(id, label):
                title = label
                color = Palette.person(colorByLabel[label] ?? labelIndex(label))
                faceRect = rects[id]
            case .offCamera:
                title = "Someone"
                color = Palette.offCamera
            }

            if let r = faceRect {
                // Above the head; below the chin only if there's no room. Never over the mouth.
                let above = r.minY - 14 - boxHeight / 2
                let below = r.maxY + 14 + boxHeight / 2
                let useAbove = above - boxHeight / 2 >= topSafe || below + boxHeight / 2 > size.height
                out.append(Placement(id: c.uttId, caption: c, title: title, color: color,
                                     point: CGPoint(x: clampX(r.midX, size), y: useAbove ? above : below),
                                     tail: useAbove ? .bottom : .top, chevron: .none, hint: nil))
            } else if let x = proj.x(forBearing: c.bearing) {
                // In view but no face found (turned away, too far): at its screen x.
                out.append(Placement(id: c.uttId, caption: c, title: title, color: color,
                                     point: CGPoint(x: clampX(x, size), y: size.height * 0.3),
                                     tail: .bottom, chevron: .none, hint: nil))
            } else {
                // Off camera: at the matching screen edge with a chevron toward the bearing.
                let rel = proj.relative(c.bearing)
                let leading = rel < 0
                let y = edgeY[leading]!
                edgeY[leading] = y + boxHeight + 12
                let x = leading ? margin + 34 + boxWidth / 2 : size.width - margin - 34 - boxWidth / 2
                out.append(Placement(id: c.uttId, caption: c, title: title, color: color,
                                     point: CGPoint(x: x, y: min(y, size.height - boxHeight / 2 - margin)),
                                     tail: .none, chevron: leading ? .leading : .trailing,
                                     hint: Geo.describe(relative: rel)))
            }
        }
        return separate(out)
    }

    /// Two captions that would overlap: lift the later one above the earlier one.
    private static func separate(_ items: [Placement]) -> [Placement] {
        var placed: [Placement] = []
        for var p in items {
            for q in placed where abs(q.point.x - p.point.x) < boxWidth && abs(q.point.y - p.point.y) < boxHeight + 6 {
                p = Placement(id: p.id, caption: p.caption, title: p.title, color: p.color,
                              point: CGPoint(x: p.point.x, y: q.point.y - boxHeight - 8),
                              tail: p.tail, chevron: p.chevron, hint: p.hint)
            }
            placed.append(p)
        }
        return placed
    }

    private static func clampX(_ x: CGFloat, _ size: CGSize) -> CGFloat {
        min(max(x, margin + boxWidth / 2), size.width - margin - boxWidth / 2)
    }

    private static func labelIndex(_ label: String) -> Int {
        (Int(label.split(separator: " ").last ?? "1") ?? 1) - 1
    }
}
