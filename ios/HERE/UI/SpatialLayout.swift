import SwiftUI

/// Where each presence and caption goes in the Spatial V2 layer. Pure layout, like `Layout`: no
/// state, no views, so it can be unit-tested (PresentationTests).
enum SpatialLayout {
    struct Placed: Identifiable, Equatable {
        enum Anchor: Equatable {
            case corner              // a notification in the top-right stack; its green dot says which way
            case perimeter           // on the invisible path, at its relative bearing
            case inView              // the wearer is facing it: at its direction of arrival
            case face                // on the tracked face it belongs to
            case object              // on the known object that made the sound, while that object is in view
            case unknownDirection    // no bearing: top center, says so
        }

        let presence: SpatialPresence
        let relative: Double?
        let point: CGPoint
        let anchor: Anchor
        let inward: Double           // 0 on the path ... 1 where the sound is
        var id: String { presence.id }
    }

    struct CaptionPlacement: Identifiable {
        let id: String
        let caption: CaptionState
        let title: String
        let color: Color
        let point: CGPoint
        let tail: CaptionView.TailEdge
        let hint: String?
        let faceId: Int?
        let personLabel: String?     // set when the speaker is a tracked person in view (pin control)
    }

    // MARK: presences

    /// `presences` arrive in priority order; an icon that would land on a higher-priority one at the
    /// same bearing stacks inward along that bearing rather than moving off it.
    static func place(_ presences: [SpatialPresence], proj: Projector, faceRects: [Int: CGRect], size: CGSize,
                      reduceMotion: Bool, placement: IndicatorPlacement = Tunables.indicatorPlacement) -> [Placed] {
        if placement == .corner { return placeInCorner(presences, proj: proj, faceRects: faceRects, size: size, reduceMotion: reduceMotion) }
        var out: [Placed] = []
        var used: [Double] = []
        var unknown = 0
        let unknownTotal = presences.filter { $0.worldBearing == nil && $0.associatedTrackedFaceID == nil }.count
        let threshold = min(Tunables.inwardThresholdDeg, proj.hfovDeg / 2 - 1)

        for p in presences {
            let faceRect = p.associatedTrackedFaceID.flatMap { faceRects[$0] }
            let rel = p.worldBearing.map { proj.relative($0) }

            // An alert that belongs to someone in view sits beside their head, not over their face.
            if let r = faceRect, p.sourceType == .alert {
                let d = Tunables.iconSize * Relevance.iconScale(p.level)
                out.append(Placed(presence: p, relative: rel,
                                  point: CGPoint(x: max(d / 2 + Layout.margin, r.minX - d * 0.6), y: r.minY),
                                  anchor: .face, inward: 1))
                continue
            }

            guard let bearing = p.worldBearing, let rel else {
                if let r = faceRect {
                    out.append(Placed(presence: p, relative: nil, point: CGPoint(x: r.midX, y: r.midY),
                                      anchor: .face, inward: 1))
                    continue
                }
                let x = size.width / 2 + (CGFloat(unknown) - CGFloat(unknownTotal - 1) / 2) * Tunables.unknownDirectionSpacing
                unknown += 1
                out.append(Placed(presence: p, relative: nil, point: CGPoint(x: x, y: Layout.topSafe),
                                  anchor: .unknownDirection, inward: 0))
                continue
            }

            let stack = Perimeter.stackIndex(relative: rel, placed: used)
            used.append(rel)
            let onPath = Perimeter.point(relative: rel, size: size, stack: stack)

            // Handing off to a tracked face: travel from the path to the face. With Reduce Motion the
            // icon stays where it is and fades while the aura fades in.
            if let r = faceRect, let k = p.handoffProgress {
                let target = CGPoint(x: r.midX, y: r.midY)
                out.append(Placed(presence: p, relative: rel,
                                  point: reduceMotion ? onPath : Perimeter.lerp(onPath, target, Perimeter.smooth(k)),
                                  anchor: reduceMotion ? .perimeter : .face, inward: reduceMotion ? 0 : k))
                continue
            }

            // No visual track: as the wearer turns to face it, move inward toward where it came from.
            // That's a direction, not an object, so it lands on the horizon line at that bearing.
            let inward = reduceMotion ? 0 : Perimeter.inwardProgress(relative: rel, thresholdDeg: threshold)
            if inward > 0, let x = proj.x(forBearing: bearing) {
                let target = CGPoint(x: x, y: size.height * Tunables.inViewAnchorY)
                out.append(Placed(presence: p, relative: rel, point: Perimeter.lerp(onPath, target, inward),
                                  anchor: inward >= 1 ? .inView : .perimeter, inward: inward))
            } else {
                out.append(Placed(presence: p, relative: rel, point: onPath, anchor: .perimeter, inward: 0))
            }
        }
        return out
    }

    /// One notification per sound, stacked down from the top-right in priority order so the middle
    /// of the view stays clear. Direction is the green dot on each notification's ring, not position.
    static func cornerPoint(slot: Int, size: CGSize) -> CGPoint {
        CGPoint(x: size.width - Tunables.cornerTrailing - Tunables.cornerSlot / 2,
                y: Tunables.cornerTop + Tunables.cornerSlot / 2 + CGFloat(slot) * Tunables.cornerSlot)
    }

    private static func placeInCorner(_ presences: [SpatialPresence], proj: Projector, faceRects: [Int: CGRect],
                                      size: CGSize, reduceMotion: Bool) -> [Placed] {
        var out: [Placed] = []
        var slot = 0
        for p in presences {
            let faceRect = p.associatedTrackedFaceID.flatMap { faceRects[$0] }
            let rel = p.worldBearing.map { proj.relative($0) }
            if let r = faceRect, p.sourceType == .alert {
                let d = Tunables.iconSize * Relevance.iconScale(p.level)
                out.append(Placed(presence: p, relative: rel,
                                  point: CGPoint(x: max(d / 2 + Layout.margin, r.minX - d * 0.6), y: r.minY),
                                  anchor: .face, inward: 1))
                continue
            }
            // The sound's source is a known object and it is in view: indicate it there.
            if let box = p.objectBox {
                let r = proj.rect(forVisionBox: box)
                let d = Tunables.iconSize * Relevance.iconScale(p.level)
                // Beside the thing, not on top of it: above a boxed object, at the middle of a region.
                let y = p.objectPrecise ? max(d / 2 + Layout.margin, r.minY - d / 2 - 4) : r.midY
                out.append(Placed(presence: p, relative: rel, point: CGPoint(x: r.midX, y: y), anchor: .object, inward: 1))
                continue
            }
            let home = cornerPoint(slot: slot, size: size)
            slot += 1
            if let r = faceRect, let k = p.handoffProgress, !reduceMotion {
                out.append(Placed(presence: p, relative: rel,
                                  point: Perimeter.lerp(home, CGPoint(x: r.midX, y: r.midY), Perimeter.smooth(k)),
                                  anchor: .face, inward: k))
            } else {
                // Handing off with Reduce Motion: it fades in its place while the aura fades in.
                out.append(Placed(presence: p, relative: faceRect == nil || reduceMotion ? rel : nil, point: home,
                                  anchor: rel == nil ? .unknownDirection : .corner, inward: 0))
            }
        }
        return out
    }

    // MARK: captions

    /// A caption belongs on the face of whoever said it: above their head (below the chin only if
    /// there's no room, never over the mouth). Speech from anyone not in view gets no floating caption;
    /// it is a notification, and only a pinned person's notification carries their words.
    static func captions(_ captions: [CaptionState], scene: SpatialScene, placed: [Placed], faces: [TrackedFace],
                         rects: [Int: CGRect], proj: Projector, size: CGSize, reduceMotion: Bool) -> [CaptionPlacement] {
        let byId = Dictionary(faces.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let byCaption = Dictionary(placed.compactMap { p in p.presence.captionId.map { ($0, p) } },
                                   uniquingKeysWith: { a, _ in a })
        var out: [CaptionPlacement] = []
        for c in captions {
            guard case let .person(id, label) = c.speaker, let r = rects[id] else { continue }
            let face = byId[id]
            let above = r.minY - 14 - Layout.boxHeight / 2
            let below = r.maxY + 14 + Layout.boxHeight / 2
            let useAbove = above - Layout.boxHeight / 2 >= Layout.topSafe || below + Layout.boxHeight / 2 > size.height
            var point = CGPoint(x: clampX(r.midX, size), y: useAbove ? above : below)
            // Coming back into view: glide in from the notification that was standing in for them.
            if !reduceMotion, let icon = byCaption[c.uttId], let k = icon.presence.handoffProgress,
               let slot = placed.firstIndex(of: icon) {
                point = Perimeter.lerp(attach(to: cornerPoint(slot: slot, size: size), inward: 0, size: size), point,
                                       Perimeter.smooth(k))
            }
            out.append(CaptionPlacement(id: c.uttId, caption: c, title: face?.label ?? label,
                                        color: Palette.person(face?.colorIndex ?? labelIndex(label)), point: point,
                                        tail: useAbove ? .bottom : .top, hint: nil, faceId: id,
                                        personLabel: face?.label ?? label))
        }
        return settle(out, rects: rects, size: size)
    }

    /// Where a caption sits beside a notification at `icon`: on the side facing the middle of the view.
    static func attach(to icon: CGPoint, inward: Double, size: CGSize) -> CGPoint {
        let center = Perimeter.center(size: size)
        var dx = Double(center.x - icon.x), dy = Double(center.y - icon.y)
        let length = max(hypot(dx, dy), 1e-6)
        dx /= length
        dy /= length
        let halfW = Double(Layout.boxWidth) / 2, halfH = Double(Layout.boxHeight) / 2
        let reach = min(abs(dx) > 1e-6 ? halfW / abs(dx) : .infinity, abs(dy) > 1e-6 ? halfH / abs(dy) : .infinity)
        let d = reach + Double(Tunables.iconSize * Tunables.iconScaleSpeech) / 2 + Double(Tunables.captionAttachGap)
        return clamp(CGPoint(x: icon.x + CGFloat(dx * d), y: icon.y + CGFloat(dy * d)), size)
    }

    /// SPEC §14: never cover a mouth, and never draw one caption over another. Captions are settled
    /// in order; each takes the first of these spots that is on screen, clear of every face and clear
    /// of the captions already settled: where it wants to be, just above or below any face it would
    /// cover, above or below a caption it would hit, then beside that caption.
    static func settle(_ items: [CaptionPlacement], rects: [Int: CGRect], size: CGSize) -> [CaptionPlacement] {
        let faces = rects.values.sorted { $0.minX < $1.minX }
        let h = Layout.boxHeight, w = Layout.boxWidth
        func box(_ p: CGPoint) -> CGRect { CGRect(x: p.x - w / 2, y: p.y - h / 2, width: w, height: h) }
        func onScreen(_ p: CGPoint) -> Bool {
            p.y - h / 2 >= Layout.margin && p.y + h / 2 <= size.height - Layout.margin
                && p.x - w / 2 >= 0 && p.x + w / 2 <= size.width
        }
        func coversFace(_ p: CGPoint) -> Bool { faces.contains { box(p).intersects($0.insetBy(dx: -4, dy: -4)) } }

        var done: [CaptionPlacement] = []
        for item in items {
            func hitsCaption(_ p: CGPoint) -> Bool {
                done.contains { abs($0.point.x - p.x) < w && abs($0.point.y - p.y) < h + 6 }
            }
            var spots = [item.point]
            for r in faces where box(item.point).intersects(r.insetBy(dx: -4, dy: -4)) {
                spots.append(CGPoint(x: item.point.x, y: r.minY - 14 - h / 2))
                spots.append(CGPoint(x: item.point.x, y: r.maxY + 14 + h / 2))
            }
            for q in done where abs(q.point.x - item.point.x) < w && abs(q.point.y - item.point.y) < h + 6 {
                spots.append(CGPoint(x: item.point.x, y: q.point.y - h - 8))
                spots.append(CGPoint(x: item.point.x, y: q.point.y + h + 8))
                let away: CGFloat = item.point.x <= q.point.x ? -1 : 1
                spots.append(CGPoint(x: q.point.x + away * (w + 8), y: item.point.y))
                spots.append(CGPoint(x: q.point.x - away * (w + 8), y: item.point.y))
            }
            let spot = spots.first { onScreen($0) && !coversFace($0) && !hitsCaption($0) }
                ?? spots.first { onScreen($0) && !coversFace($0) }      // a face matters more than a caption
                ?? item.point
            done.append(moved(item, to: spot))
        }
        return done
    }

    private static func moved(_ p: CaptionPlacement, to point: CGPoint) -> CaptionPlacement {
        CaptionPlacement(id: p.id, caption: p.caption, title: p.title, color: p.color, point: point, tail: p.tail,
                         hint: p.hint, faceId: p.faceId, personLabel: p.personLabel)
    }

    private static func clampX(_ x: CGFloat, _ size: CGSize) -> CGFloat {
        min(max(x, Layout.margin + Layout.boxWidth / 2), size.width - Layout.margin - Layout.boxWidth / 2)
    }

    private static func clamp(_ p: CGPoint, _ size: CGSize) -> CGPoint {
        CGPoint(x: clampX(p.x, size),
                y: min(max(p.y, Layout.margin + Layout.boxHeight / 2), size.height - Layout.margin - Layout.boxHeight / 2))
    }

    private static func labelIndex(_ label: String) -> Int {
        (Int(label.split(separator: " ").last ?? "1") ?? 1) - 1
    }
}
