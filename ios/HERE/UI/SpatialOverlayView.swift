import SwiftUI

/// The Spatial V2 AR layer (SpatialVisualStyle.spatialV2). Quiet until something happens: then a
/// speaker gets an aura and a caption that belongs to them, and a sound gets an icon on an invisible
/// path around the view, at the bearing it came from.
///
/// This wrapper only pulls live state out of `AppModel`; everything is drawn by `SpatialSceneView`,
/// which takes plain values so the same view renders previews and debug scenarios.
struct SpatialOverlayView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var settings: SettingsStore
    let size: CGSize

    var body: some View {
        SpatialSceneView(scene: model.scene, captions: model.visibleCaptions, faces: model.faces,
                         proj: model.projector(size: size), size: size, now: model.now,
                         simpleGlass: model.simplifyGlass,
                         showCaptions: settings.value.features.captions,
                         showAuras: settings.value.features.rings,
                         showDebug: settings.value.showDebug && settings.value.debugBoxes,
                         forceReduceMotion: model.scenarioReduceMotion,
                         onTogglePin: { model.togglePin(personId: $0) },
                         onEnroll: { model.enroll(personId: $0) },
                         enrolled: Set(model.sessionPeople.filter { $0.knownPersonID != nil }.map(\.id)))
    }
}

struct SpatialSceneView: View {
    let scene: SpatialScene
    let captions: [CaptionState]
    let faces: [TrackedFace]
    let proj: Projector
    let size: CGSize
    var now = 0.0
    var simpleGlass = false
    var showCaptions = true
    var showAuras = true
    var showDebug = false
    var forceReduceMotion = false
    var onTogglePin: (Int) -> Void = { _ in }
    var onEnroll: ((Int) -> Void)? = nil

    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    /// Faces the wearer tapped: label -> when its name tag goes away again.
    @State private var revealed: [String: Double] = [:]

    private var reduceMotion: Bool { systemReduceMotion || forceReduceMotion }

    var body: some View {
        let rects = Dictionary(faces.map { ($0.id, proj.rect(forVisionBox: $0.box)) }, uniquingKeysWith: { a, _ in a })
        let placed = SpatialLayout.place(scene.presences, proj: proj, faceRects: rects, size: size,
                                         reduceMotion: reduceMotion)
        let placements = showCaptions
            ? SpatialLayout.captions(captions, scene: scene, placed: placed, faces: faces, rects: rects,
                                     proj: proj, size: size, reduceMotion: reduceMotion)
            : []
        let captioned = Set(placements.compactMap(\.faceId))

        ZStack(alignment: .topLeading) {
            if showDebug, Tunables.indicatorPlacement == .perimeter { PerimeterDebug(scene: scene, proj: proj, size: size) }
            if showAuras {
                objectAuras
                auras(rects)
            }
            icons(placed)
            faceTargets(rects)
            tags(rects, captioned: captioned)
            captionCards(placements)
        }
        .frame(width: size.width, height: size.height)
        .animation(.easeOut(duration: Tunables.presenceFadeInS), value: placed.map(\.id))
        .animation(.easeOut(duration: Tunables.presenceFadeInS), value: placements.map(\.id))
        .animation(.easeOut(duration: Tunables.presenceFadeInS), value: scene.speakers.map(\.id))
        .animation(.easeOut(duration: Tunables.presenceFadeInS), value: scene.objectAuras.map(\.id))
    }

    // MARK: layers

    /// Objects that are making sound right now.
    private var objectAuras: some View {
        ForEach(scene.objectAuras) { aura in
            let r = proj.rect(forVisionBox: aura.box)
            ObjectAuraView(aura: aura, rect: r, reduceMotion: reduceMotion)
                .position(x: r.midX, y: r.midY)
                .transition(.opacity)
        }
    }

    private func auras(_ rects: [Int: CGRect]) -> some View {
        ForEach(scene.speakers) { s in
            if let r = rects[s.faceId], s.activity > 0 || s.resting > 0 {
                let color = Palette.person(s.colorIndex)
                SpeakerAura(color: color, rect: r, activity: s.activity, resting: s.resting, intensity: s.intensity,
                            reduceMotion: reduceMotion)
                    .position(x: r.midX, y: r.midY)
                    .transition(.opacity)
                if s.activity > 0 {
                    SpeakingBadge(color: color, activity: s.activity, reduceMotion: reduceMotion)
                        .position(x: r.maxX + Tunables.speakingBadgeSize * 0.4, y: r.minY)
                        .transition(.opacity)
                }
            }
        }
    }

    private func icons(_ placed: [SpatialLayout.Placed]) -> some View {
        ForEach(placed) { p in
            SpatialNotification(presence: p.presence, relative: p.relative,
                          directionKnown: p.anchor != .unknownDirection, reduceMotion: reduceMotion,
                          labelSide: p.point.x < Tunables.iconLabelEdge ? .leading
                              : (p.point.x > size.width - Tunables.iconLabelEdge ? .trailing : .center),
                          compass: Tunables.indicatorPlacement == .corner && p.anchor != .face && p.anchor != .object)
                .position(p.point)
                // Following the wearer's turn is spatial, so it stays even with Reduce Motion, just
                // unsmoothed; the travel inward and onto a face is what Reduce Motion replaces with fades.
                .animation(reduceMotion ? nil : .smooth(duration: Tunables.pathFollowS), value: p.point)
                .animation(.linear(duration: Tunables.pathFollowS), value: p.presence.opacity)
                .transition(.opacity)
            if p.presence.captionText != nil, p.anchor != .face {
                PinnedCaptionCard(presence: p.presence, relative: p.relative)
                    .position(x: p.point.x - Tunables.cornerSlot / 2 - 8 - Tunables.pinnedCaptionWidth / 2, y: p.point.y)
                    .transition(.opacity)
            }
        }
    }

    /// Tap a face to see who it is and pin them. Invisible; nothing is drawn until tapped.
    private func faceTargets(_ rects: [Int: CGRect]) -> some View {
        ForEach(faces) { face in
            if let r = rects[face.id] {
                Color.clear
                    .contentShape(Rectangle())
                    .frame(width: r.width, height: r.height)
                    .position(x: r.midX, y: r.midY)
                    .onTapGesture { revealed[face.label] = now + Tunables.nametagRevealS }
                    .accessibilityElement()
                    .accessibilityLabel(face.label)
                    .accessibilityHint("Shows the name tag and pin control")
                    .accessibilityAddTraits(.isButton)
            }
        }
    }

    private func tags(_ rects: [Int: CGRect], captioned: Set<Int>) -> some View {
        ForEach(faces) { face in
            let pid = personId(of: face)
            let pinned = pid.map { scene.pinnedPersonIds.contains($0) } ?? false
            let tapped = (revealed[face.label] ?? 0) > now
            if let r = rects[face.id], let pid, pinned || tapped, !captioned.contains(face.id) {
                PersonTag(label: face.label, color: Palette.person(face.colorIndex), pinned: pinned,
                          canEnroll: tapped && onEnroll != nil && !enrolled.contains(pid),
                          onPin: { onTogglePin(pid) }, onEnroll: { onEnroll?(pid) })
                .fixedSize()
                .opacity(tapped ? 1 : Tunables.pinnedTagOpacity)
                .position(x: r.midX, y: max(Layout.topSafe, r.minY - 14 - Tunables.pinHitSize / 2))
                .transition(.opacity)
            }
        }
    }

    private func captionCards(_ placements: [SpatialLayout.CaptionPlacement]) -> some View {
        ForEach(placements) { p in
            CaptionView(caption: p.caption, color: p.color, title: p.title, tail: p.tail, simpleGlass: simpleGlass,
                        offCameraHint: p.hint, spatial: true,
                        pinned: pinState(p.faceId),
                        onPin: p.faceId.flatMap { id in faces.first { $0.id == id } }.flatMap(personId(of:)).map { pid in { onTogglePin(pid) } })
                .fixedSize()
                .position(p.point)
                .animation(reduceMotion ? nil : .smooth(duration: 0.08), value: p.point)
                .transition(.opacity.combined(with: .scale(scale: 0.96)))
        }
    }

    /// SessionPerson ids already enrolled in People (no "Add to People" for them).
    var enrolled: Set<Int> = []

    private func personId(of face: TrackedFace) -> Int? {
        face.personId ?? scene.speakers.first { $0.faceId == face.id }?.personId
    }

    private func pinState(_ faceId: Int?) -> Bool? {
        guard let face = faceId.flatMap({ id in faces.first { $0.id == id } }), let pid = personId(of: face) else { return nil }
        return scene.pinnedPersonIds.contains(pid)
    }
}

/// Debug overlay only: draws the path that is otherwise invisible, plus what was received but not
/// shown, so relevance decisions can be checked on screen.
struct PerimeterDebug: View {
    let scene: SpatialScene
    let proj: Projector
    let size: CGSize

    var body: some View {
        let c = Perimeter.center(size: size)
        ZStack(alignment: .topLeading) {
            Ellipse()
                .stroke(.white.opacity(0.25), style: StrokeStyle(lineWidth: 1, dash: [4, 6]))
                .frame(width: size.width * Tunables.perimeterRadiusX * 2, height: size.height * Tunables.perimeterRadiusY * 2)
                .position(c)
            ForEach(scene.suppressed) { p in
                if let b = p.worldBearing {
                    Circle().stroke(.white.opacity(0.5), lineWidth: 1).frame(width: 10, height: 10)
                        .position(Perimeter.point(relative: proj.relative(b), size: size))
                }
            }
            Text("presence shown \(scene.presences.count)  suppressed \(scene.suppressed.count)  baseline "
                 + (scene.baseline.map { String(format: "%.2f", $0) } ?? "-"))
                .font(Typeface.small(.caption2)).foregroundStyle(.white).padding(4)
                .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 6))
                .position(x: size.width / 2, y: size.height - 16)
        }
        .frame(width: size.width, height: size.height)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// "Scene Understanding" (Settings > Developer): what HERE currently recognises in the room, shown on
/// request to demonstrate the vision layer. Never part of the normal view: quiet objects are tracked
/// silently and only surface when a sound is attributed to one.
struct SceneUnderstandingOverlay: View {
    let objects: [SceneObject]
    let proj: Projector
    let size: CGSize
    let now: Double
    var scanMs = 0.0
    var detectorName: String? = nil
    var people: [SessionPerson] = []
    var faces: [TrackedFace] = []
    var scene = SpatialScene.empty

    /// PEOPLE / AUDIO / ASSOCIATIONS, in words: who is known, what is sounding, and what each sound
    /// has been attributed to.
    var entityLines: [String] {
        var lines = people.map { p -> String in
            let face = faces.first { $0.personId == p.id }
            let seen = face.map { "track \($0.id)\($0.viaBody ? " body" : " face")" } ?? String(format: "away, spatial %.1f", p.spatialConfidence(now: now))
            return String(format: "PERSON %@  #%d  %@  id %.2f  color %d%@  descriptors %d", p.displayName, p.id, seen,
                          p.identityConfidence, p.colorIndex, p.isSessionPinned ? "  pinned" : "", p.descriptors)
        }
        for a in scene.presences + scene.queued where a.sourceType != .person {
            var to = "unattached"
            if let o = a.objectId, let object = objects.first(where: { $0.id == o }) { to = "\(object.cls.name) #\(o)" }
            else if let f = a.associatedTrackedFaceID, let face = faces.first(where: { $0.id == f }) { to = face.label }
            lines.append(String(format: "AUDIO %@  %@  intensity %.2f  ->  %@", a.id,
                                a.worldBearing.map { String(format: "%+.0f°", $0) } ?? "no bearing", a.intensity, to))
        }
        for s in scene.speakers where s.activity > 0 || s.resting > 0 {
            lines.append(String(format: "AURA person #%@ %@  intensity %.2f  color person %d  opacity %.2f  %@",
                                s.personId.map(String.init) ?? "-", s.label, s.intensity, s.colorIndex,
                                SoundContinuity.auraOpacity(activity: s.activity, resting: s.resting, intensity: s.intensity),
                                s.activity >= 1 ? "active" : (s.activity > 0 ? "releasing" : "resting")))
        }
        for a in scene.objectAuras {
            lines.append(String(format: "AURA object #%d %@  <- %@  conf %.2f  intensity %.2f  color %@  opacity %.2f  %@",
                                a.objectId, a.cls.name, a.soundId, a.confidence, a.intensity, String(describing: a.tone), a.opacity,
                                a.releasing ? "releasing" : "active"))
        }
        return lines
    }

    var body: some View {
        ZStack(alignment: .topLeading) {
            if !entityLines.isEmpty {
                Text(entityLines.joined(separator: "\n"))
                    .font(Typeface.small(.caption2)).foregroundStyle(.white).padding(6)
                    .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 8))
                    .fixedSize()
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
                    .padding(.leading, 70).padding(.bottom, 14)
            }
            ForEach(objects) { object in
                let inView = proj.x(forBearing: object.worldBearing) != nil
                let state = object.state(now: now, inView: inView)
                if inView, let box = object.box, state != .dormant {
                    let r = proj.rect(forVisionBox: box)
                    if object.precise {
                        // A detector's box: this is where the object is.
                        RoundedRectangle(cornerRadius: 6)
                            .stroke(Palette.environment.opacity(state == .temporarilyLost ? 0.35 : 0.9), lineWidth: 2)
                            .frame(width: r.width, height: r.height)
                            .position(x: r.midX, y: r.midY)
                    } else {
                        // A classified region: it is somewhere in here.
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(.white.opacity(state == .temporarilyLost ? 0.25 : 0.6), style: StrokeStyle(lineWidth: 1, dash: [6, 5]))
                            .frame(width: r.width, height: r.height * 0.6)
                            .position(x: r.midX, y: r.midY)
                    }
                    Label(Self.line(object, state), systemImage: object.cls.symbol)
                        .font(Typeface.small(.caption2)).foregroundStyle(.white)
                        .padding(.horizontal, 6).padding(.vertical, 3)
                        .background(.black.opacity(Tunables.captionPlateOpacity), in: Capsule())
                        .fixedSize()
                        .position(x: r.midX, y: r.midY + r.height * (object.precise ? 0.5 : 0.3) + 14)
                }
            }
            Text(summary)
                .font(Typeface.small(.caption2)).foregroundStyle(.white).padding(6)
                .background(.black.opacity(0.55), in: RoundedRectangle(cornerRadius: 8))
                .fixedSize()
                .position(x: size.width / 2, y: 22)
        }
        .frame(width: size.width, height: size.height)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    static func line(_ o: SceneObject, _ state: SceneObject.State) -> String {
        String(format: "%@ %.2f  #%d  id %.2f  %@%@", o.cls.name, o.confidence, o.id, o.identityConfidence, state.rawValue,
               o.associatedSoundID == nil ? "" : "  ♪")
    }

    private var summary: String {
        let known = objects.filter(\.isConfirmed)
        let away = known.filter { proj.x(forBearing: $0.worldBearing) == nil }
        return "Scene: \(known.count) objects known, \(away.count) out of view" + (scanMs > 0 ? String(format: "  scan %.0f ms", scanMs) : "")
            + "  detector: " + (detectorName ?? "none (classifier regions only)")
            + (away.isEmpty ? "" : "  (" + away.map { String(format: "%@ #%d %+.0f°", $0.cls.name, $0.id, $0.worldBearing) }.joined(separator: ", ") + ")")
    }
}
