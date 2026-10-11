import UIKit
import XCTest
@testable import HERE

/// Spatial V2: notifications (direction ring, color grammar, intensity -> opacity, life cycle,
/// capacity), SessionPerson identity and re-identification, session pins, known people, layout, and
/// the 30 debug scenarios. All pure: no camera, relay, laptop or belt.
final class PresentationTests: XCTestCase {
    let size = CGSize(width: 874, height: 402)          // iPhone 17, landscape

    private func proj(heading: Double = 0, hfovDeg: Double = 106) -> Projector {
        Projector(layer: nil, captureAngle: 0, hfovDeg: hfovDeg, heading: heading, size: size)
    }

    private func face(_ id: Int, _ label: String, bearing: Double, color: Int = 0, person: Int? = nil) -> TrackedFace {
        TrackedFace(id: id, label: label, colorIndex: color,
                    box: CGRect(x: 0.45, y: 0.52, width: 0.09, height: 0.16), bearing: bearing, personId: person)
    }

    private func caption(_ id: String, _ speaker: Speaker, bearing: Double, at t: Double = 0, final: Bool = false,
                         loudness: Double = 0.6, owner: Int? = nil) -> CaptionState {
        var c = CaptionState(uttId: id, speaker: speaker, bearing: bearing, started: t, updated: t)
        c.text = "hello there"
        c.final = final
        c.loudness = loudness
        c.ownerPersonId = owner
        return c
    }

    private func sound(_ id: String, _ label: SoundLabel, bearing: Double?, at t: Double = 0, text: String? = nil) -> SoundTag {
        SoundTag(eventId: id, label: label, category: .other, emoji: "", text: text ?? label.rawValue, bearing: bearing,
                 count: 1, updated: t, confidence: 0.85)
    }

    private func alert(_ id: String, _ kind: AlertKind, bearing: Double?, priority: Int = 2) -> AlertState {
        AlertState(alertId: id, kind: kind, label: kind.rawValue, emoji: "", priority: priority, bearing: bearing,
                   active: true, count: 1, updated: 0, lastBuzz: 0)
    }

    private func src(_ bearing: Double, _ energy: Double) -> Source {
        Source(id: 0, bearingDeg: bearing, energy: energy, speech: false, confidence: 0.8)
    }

    private let quiet = [Source(id: 0, bearingDeg: 0, energy: 0.05, speech: false, confidence: 0.5)]

    private func input(now: Double = 0, heading: Double = 0, captions: [CaptionState] = [], alerts: [AlertState] = [],
                       sounds: [SoundTag] = [], faces: [TrackedFace] = [], people: [SessionPerson] = [],
                       sources: [Source] = [], focus: Bool = false,
                       enrichments: [String: EnrichmentState] = [:]) -> PresenceInput {
        PresenceInput(now: now, heading: heading, hfovDeg: 106, captions: captions, alerts: alerts, sounds: sounds,
                      faces: faces, people: people, sources: sources, baseline: 0.05, focusMode: focus,
                      enrichments: enrichments)
    }

    private func judged(_ attention: Attention, directed: Double? = nil) -> EnrichmentState {
        EnrichmentState(attention: attention, confidence: 0.8, directedAtUser: directed, updated: 0)
    }

    private func person(_ id: Int, bearing: Double, track: Int? = nil, seen: Double = 0, pinned: Bool = false) -> SessionPerson {
        var p = SessionPerson(id: id, displayName: "Person \(id)", colorIndex: id - 1, lastKnownWorldBearing: bearing, lastSeenAt: seen)
        p.currentTrackID = track
        p.isSessionPinned = pinned
        return p
    }

    // MARK: direction ring = belt bearing

    func testRingMarkerPosition() {
        let r: CGFloat = 20
        func at(_ rel: Double) -> CGSize { Perimeter.ringMarker(relative: rel, radius: r) }
        XCTAssertEqual(at(0).width, 0, accuracy: 1e-9);   XCTAssertEqual(at(0).height, -r, accuracy: 1e-9)      // ahead = top
        XCTAssertEqual(at(90).width, r, accuracy: 1e-9);  XCTAssertEqual(at(90).height, 0, accuracy: 1e-9)      // right
        XCTAssertEqual(at(-90).width, -r, accuracy: 1e-9)                                                        // left
        XCTAssertEqual(at(180).height, r, accuracy: 1e-9)                                                        // behind = bottom
        XCTAssertEqual(at(180).width, at(-180).width, accuracy: 1e-9)
    }

    func testRingMarkerNeverDisagreesWithTheBelt() {
        // The marker is continuous, the belt rounds to 8 motors. Both read the same relative bearing,
        // so the marker always lies inside the 45-degree sector of the motor that fires.
        for world in stride(from: -180.0, through: 180.0, by: 7) {
            for heading in [0.0, 33, -120, 179] {
                let rel = Geo.relative(world, heading: heading)
                let m = Perimeter.ringMarker(relative: rel, radius: 1)
                let markerAngle = atan2(Double(m.width), Double(-m.height)) * 180 / .pi
                let motorCenter = Double(Geo.motor(forRelative: rel) - 1) * 45
                XCTAssertLessThanOrEqual(abs(Geo.angDiff(markerAngle, motorCenter)), 22.5 + 1e-6, "world \(world) heading \(heading)")
            }
        }
        // The layout hands the view exactly Geo.relative.
        var p = SpatialPresence(id: "a", sourceType: .sound, worldBearing: 100, category: .other, icon: "x", tone: .environment,
                                label: nil, prominence: 1, urgency: 0, persistence: 0, score: 1, level: .relevant, firstSeen: 0, lastSeen: 0)
        p.opacity = 1
        let placed = SpatialLayout.place([p], proj: proj(heading: 30), faceRects: [:], size: size, reduceMotion: false, placement: .corner)
        XCTAssertEqual(placed[0].relative, Geo.relative(100, heading: 30))
    }

    // MARK: color grammar, unknown

    func testCategoryColorMapping() {
        for label in [SoundLabel.knock, .dogBarking, .phoneRinging, .music, .tv, .doorbell] {
            XCTAssertEqual(Relevance.tone(label: label), .environment, "\(label) is environmental: green")
        }
        XCTAssertEqual(Relevance.tone(label: .other, text: "Footsteps"), .environment)
        XCTAssertEqual(Relevance.tone(label: .other, text: "Car horn"), .environment)
        XCTAssertEqual(Relevance.tone(label: .alarm), .danger)
        XCTAssertEqual(Relevance.tone(label: .loudBang), .danger)
        XCTAssertEqual(Relevance.tone(label: .speech), .human)
        XCTAssertEqual(Relevance.tone(label: .babyCrying), .human)
        XCTAssertEqual(Relevance.tone(alert: .alarm, priority: 3), .danger)
        XCTAssertEqual(Relevance.tone(alert: .nameCalled, priority: 2), .human)
        XCTAssertEqual(Relevance.tone(alert: .doorbell, priority: 2), .environment)
        // Labels added to the contract on main: each has a color and an icon, none falls through.
        XCTAssertEqual(Relevance.tone(label: .siren), .danger)
        XCTAssertEqual(Relevance.tone(label: .carHorn), .environment)
        XCTAssertEqual(Relevance.tone(label: .vehicle), .environment)
        XCTAssertEqual(Relevance.tone(label: .door), .environment)
        XCTAssertEqual(Relevance.tone(label: .unknown), .neutral)
        XCTAssertEqual(SoundIcon.symbol(label: .unknown), "questionmark")
        XCTAssertEqual(Relevance.tone(alert: .siren, priority: 2), .danger)
        for label in SoundLabel.allCases { XCTAssertNotNil(Tunables.semanticImportance[label], "\(label) has no importance") }
        XCTAssertEqual(PresenceTone.environment.color, Palette.environment)
        XCTAssertEqual(PresenceTone.danger.color, Palette.emergency)
        XCTAssertEqual(PresenceTone.human.color, Palette.human)
        for label in SoundLabel.allCases {
            if case .person = Relevance.tone(label: label) { XCTFail("a sound took an identity color") }
        }
    }

    func testUnknownSoundIsAQuestionMarkWithNoInventedMeaning() {
        XCTAssertEqual(SoundIcon.symbol(label: .other, text: "Sound"), "questionmark")
        XCTAssertEqual(Relevance.tone(label: .other, text: "Sound"), .neutral)
        let scene = PresenceEngine().step(input(sounds: [sound("x", .other, bearing: 120, text: "Sound")], sources: [src(120, 0.6)]))
        XCTAssertEqual(scene.presences.first?.icon, "questionmark")
        XCTAssertEqual(scene.presences.first?.tone, .neutral)
        XCTAssertEqual(scene.presences.first?.worldBearing, 120, "it still has its direction")
    }

    func testEverySoundIconIsARealSymbol() {
        for name in SoundIcon.all + ["person.crop.circle.badge.plus", "waveform"] {
            XCTAssertNotNil(UIImage(systemName: name), "\(name) is not an SF Symbol on this OS")
        }
        XCTAssertEqual(SoundIcon.symbol(label: .other, text: "Footsteps"), "shoeprints.fill")
    }

    // MARK: intensity -> opacity, and nothing else

    func testIntensityToOpacityMapping() {
        XCTAssertEqual(Relevance.opacity(intensity: 0), Tunables.soundOpacityMin)
        XCTAssertEqual(Relevance.opacity(intensity: 1), Tunables.soundOpacityMax)
        XCTAssertEqual(Relevance.opacity(intensity: 5), Tunables.soundOpacityMax)
        XCTAssertLessThan(Relevance.opacity(intensity: 0.2), Relevance.opacity(intensity: 0.8))
        XCTAssertEqual(Relevance.opacity(intensity: 0.1, floor: Tunables.dangerOpacityFloor), Tunables.dangerOpacityFloor,
                       "urgency holds a floor")
        XCTAssertGreaterThan(Relevance.opacity(intensity: 1, floor: Tunables.dangerOpacityFloor), Tunables.dangerOpacityFloor,
                             "and never caps intensity")
        // Raw level -> 0...1: above the room's baseline, clamped.
        XCTAssertEqual(Relevance.prominence(level: 0.2, baseline: 0.3), 0)
        XCTAssertEqual(Relevance.prominence(level: 1, baseline: 0), 1)
    }

    func testIntensitySmoothingAttacksFasterThanItReleases() {
        let up = Relevance.smooth(0, toward: 1, dt: 0.1)
        let down = 1 - Relevance.smooth(1, toward: 0, dt: 0.1)
        XCTAssertGreaterThan(up, down)
        XCTAssertGreaterThan(up, 0.6, "responsive when a sound starts")
        var v = 1.0
        for _ in 0..<15 { v = Relevance.smooth(v, toward: 0, dt: 1.0 / 15) }
        XCTAssertLessThan(v, 0.15, "not sluggish coming down either: about a second")
    }

    func testOpacityTracksCurrentIntensityContinuously() {
        let engine = PresenceEngine()
        var t = 0.0
        func step(_ energy: Double) -> Double {
            t += 1.0 / 15
            return engine.step(input(now: t, sounds: [sound("d", .dogBarking, bearing: -80, at: t)], sources: [src(-80, energy)]))
                .presences[0].opacity
        }
        var last = step(0.1)
        for e in stride(from: 0.12, through: 0.4, by: 0.02) {
            let o = step(e)
            XCTAssertGreaterThanOrEqual(o, last)
            last = o
        }
        let loud = last
        for _ in 0..<20 { last = step(0.1) }
        XCTAssertLessThan(last, loud - 0.3, "and comes back down when the sound does")
    }

    func testIntensityDoesNotAffectLifetime() {
        func removalTime(energy: Double) -> Double {
            let engine = PresenceEngine()
            _ = engine.step(input(now: 0, sounds: [sound("k", .knock, bearing: 100)], sources: [src(100, energy)]))
            var t = 0.0
            while t < 10 {
                t += 0.05
                if engine.step(input(now: t, sounds: [sound("k", .knock, bearing: 100)], sources: quiet)).presences.isEmpty { return t }
            }
            return .infinity
        }
        let loud = removalTime(energy: 0.9), soft = removalTime(energy: 0.16)
        XCTAssertEqual(loud, soft, accuracy: 1e-9, "a loud sound does not outstay a quiet one")
        XCTAssertEqual(loud, Tunables.soundReleaseHoldS + Tunables.notificationFadeS, accuracy: 0.06)
        XCTAssertEqual(Relevance.lifecycleFade(sinceActive: 0.5, hold: Tunables.soundReleaseHoldS), 1, "held through a short pause")
    }

    func testSimultaneousSoundsAreIndependent() {
        let engine = PresenceEngine()
        let tags = [sound("steps", .other, bearing: -90, text: "Footsteps"), sound("door", .knock, bearing: 90),
                    sound("phone", .phoneRinging, bearing: 170)]
        let a = engine.step(input(now: 0, sounds: tags, sources: [src(-90, 0.2), src(90, 0.3), src(170, 0.25)]))
        XCTAssertEqual(Set(a.presences.map(\.id)), ["snd:steps", "snd:door", "snd:phone"], "all three at once")
        XCTAssertEqual(Set(a.presences.compactMap(\.worldBearing)), [-90, 90, 170])
        XCTAssertEqual(Set(a.presences.map(\.icon)).count, 3)
        // The door gets much louder. The others are neither removed nor changed.
        let moved = tags.map { sound($0.eventId, $0.label, bearing: $0.bearing, at: 0.5, text: $0.text) }
        let b = engine.step(input(now: 0.5, sounds: moved, sources: [src(-90, 0.2), src(90, 0.9), src(170, 0.25)]))
        func opacity(_ s: SpatialScene, _ id: String) -> Double { s.presences.first { $0.id == id }!.opacity }
        XCTAssertEqual(b.presences.count, 3)
        XCTAssertGreaterThan(opacity(b, "snd:door"), opacity(a, "snd:door"))
        XCTAssertEqual(opacity(b, "snd:steps"), opacity(a, "snd:steps"), accuracy: 1e-9)
        XCTAssertEqual(opacity(b, "snd:phone"), opacity(a, "snd:phone"), accuracy: 1e-9)
    }

    func testPriorityOnlySuppressesWhenCapacityIsExceeded() {
        let bearings: [Double] = [-160, -120, -80, -40, 40, 80, 120, 160]
        func scene(_ n: Int, _ engine: PresenceEngine = PresenceEngine(), now: Double = 0) -> SpatialScene {
            engine.step(input(now: now, sounds: (0..<n).map { sound("s\($0)", .knock, bearing: bearings[$0], at: now) },
                              sources: (0..<n).map { src(bearings[$0], 0.4 + 0.05 * Double($0)) }))
        }
        let five = scene(Tunables.maxPerimeterIcons)
        XCTAssertEqual(five.presences.count, Tunables.maxPerimeterIcons, "at capacity: nothing is held back")
        XCTAssertTrue(five.queued.isEmpty)

        let engine = PresenceEngine()
        let eight = scene(8, engine)
        XCTAssertEqual(eight.presences.count, Tunables.maxPerimeterIcons)
        XCTAssertEqual(eight.queued.count, 8 - Tunables.maxPerimeterIcons, "the rest wait; they are not discarded")
        // Danger always gets a place, whatever else is going on.
        let withAlarm = PresenceEngine().step(input(alerts: [alert("a", .alarm, bearing: 0, priority: 3)],
                                                    sounds: (0..<8).map { sound("s\($0)", .knock, bearing: bearings[$0]) },
                                                    sources: (0..<8).map { src(bearings[$0], 0.6) }))
        XCTAssertEqual(withAlarm.presences.first?.id, "alr:a")
        // Higher-priority sounds end: the waiting ones take their places.
        let waiting = Set(eight.queued.map(\.id))
        let later = engine.step(input(now: 0.1, sounds: waiting.sorted().map { id in
            let i = Int(id.dropFirst(5))!
            return sound("s\(i)", .knock, bearing: bearings[i], at: 0.1)
        }, sources: quiet))
        XCTAssertEqual(Set(later.presences.map(\.id)), waiting)
        XCTAssertTrue(later.queued.isEmpty)
    }

    func testWeakBackgroundIsSuppressedButTrackedAndDangerNeverIs() {
        let scene = PresenceEngine().step(input(sounds: [sound("m", .music, bearing: 80)], sources: [src(80, 0.07)]))
        XCTAssertTrue(scene.presences.isEmpty)
        XCTAssertEqual(scene.suppressed.map(\.id), ["snd:m"])
        let danger = PresenceEngine().step(input(alerts: [alert("a", .alarm, bearing: 170, priority: 3)], sources: quiet))
        XCTAssertGreaterThanOrEqual(danger.presences[0].opacity, Tunables.dangerOpacityFloor)
        XCTAssertEqual(danger.presences[0].tone, .danger)
        let name = PresenceEngine().step(input(alerts: [alert("n", .nameCalled, bearing: nil)], focus: true))
        XCTAssertEqual(name.presences.count, 1, "the wearer's name always shows")
    }

    func testFacedEnvironmentalSoundResolvesAndIsRemovedButDangerIsNot() {
        let engine = PresenceEngine()
        XCTAssertEqual(engine.step(input(now: 0, sounds: [sound("k", .knock, bearing: 0)], sources: [src(0, 0.6)])).presences.count, 1)
        let after = Tunables.resolveHoldS + Tunables.resolveDissolveS + 0.05
        XCTAssertTrue(engine.step(input(now: after, sounds: [sound("k", .knock, bearing: 0, at: after)], sources: [src(0, 0.6)])).presences.isEmpty,
                      "the wearer is facing it: the directional job is done")
        XCTAssertNil(engine.step(input(now: 0, sounds: [sound("k", .knock, bearing: 0)])).presences.first?.associatedTrackedFaceID)
        let alarm = PresenceEngine()
        _ = alarm.step(input(now: 0, alerts: [alert("a", .alarm, bearing: 0, priority: 3)]))
        XCTAssertEqual(alarm.step(input(now: 5, alerts: [alert("a", .alarm, bearing: 0, priority: 3)])).presences.count, 1)
    }

    // MARK: speech

    func testUnattributedOffscreenSpeechIsBlueAndBecomesThePersonsColor() {
        let c = caption("u1", .offCamera, bearing: 160)
        let blue = PresenceEngine().step(input(captions: [c])).presences[0]
        XCTAssertEqual(blue.tone, .human)
        XCTAssertEqual(blue.icon, SoundIcon.speech)
        XCTAssertNil(blue.label, "nobody is named until it's known who it is")
        // The same voice once it belongs to a SessionPerson.
        let owned = caption("u1", .offCamera, bearing: 160, owner: 2)
        let theirs = PresenceEngine().step(input(captions: [owned], people: [person(2, bearing: 158)])).presences[0]
        XCTAssertEqual(theirs.tone, .person(1))
        XCTAssertEqual(theirs.label, "Person 2", "identity in words, not color alone")
        XCTAssertNil(theirs.captionText, "an unpinned person out of view gets no floating caption")
    }

    func testVisibleSpeakerGetsAuraAndCaptionNotANotification() {
        let f = face(7, "Person 1", bearing: 20, color: 3, person: 1)
        let c = caption("u1", .person(id: 7, label: "Person 1"), bearing: 21, loudness: 0.7)
        let scene = PresenceEngine().step(input(captions: [c], faces: [f], people: [person(1, bearing: 20, track: 7)]))
        XCTAssertTrue(scene.presences.isEmpty)
        XCTAssertEqual(scene.speakers.first?.activity, 1)
        XCTAssertEqual(scene.speakers.first?.colorIndex, 3)
        let rect = CGRect(x: 400, y: 220, width: 70, height: 90)
        let out = SpatialLayout.captions([c], scene: scene, placed: [], faces: [f], rects: [7: rect], proj: proj(), size: size, reduceMotion: false)
        XCTAssertEqual(out.count, 1)
        XCTAssertLessThanOrEqual(out[0].point.y + Layout.boxHeight / 2, rect.minY, "above the head, clear of the mouth")
        XCTAssertEqual(out[0].color, Palette.person(3), "caption and aura share the person's color")
    }

    func testSpeechFromOutOfViewGetsNoFloatingCaption() {
        let c = caption("u1", .offCamera, bearing: 160)
        let scene = PresenceEngine().step(input(captions: [c]))
        let placed = SpatialLayout.place(scene.presences, proj: proj(), faceRects: [:], size: size, reduceMotion: false, placement: .corner)
        XCTAssertTrue(SpatialLayout.captions([c], scene: scene, placed: placed, faces: [], rects: [:], proj: proj(), size: size,
                                             reduceMotion: false).isEmpty)
    }

    func testSpeakerLeavingViewBecomesANotificationAtOnce() {
        // Their face just left view; attribution hasn't let go yet. No blue flash in between.
        let c = caption("u1", .person(id: 7, label: "Person 1"), bearing: 22)
        let scene = PresenceEngine().step(input(heading: -95, captions: [c], people: [person(1, bearing: 22)]))
        XCTAssertEqual(scene.presences.map(\.id), ["person:1"])
        XCTAssertEqual(scene.presences[0].tone, .person(0))
        XCTAssertTrue(scene.presences[0].speaking)
    }

    func testSpeechNotificationLifetimeIgnoresLoudness() {
        func gone(_ loudness: Double) -> Double {
            let engine = PresenceEngine()
            let c = caption("u", .offCamera, bearing: 150, at: 0, final: true, loudness: loudness)
            var t = 0.0
            while t < 10 { t += 0.05; if engine.step(input(now: t, captions: [c])).presences.isEmpty { return t } }
            return .infinity
        }
        XCTAssertEqual(gone(0.1), gone(1.0), accuracy: 1e-9)
    }

    // MARK: SessionPerson, re-identification

    private func evidence(_ id: Int = 1, visual: Double? = nil, delta: Double = 0, seconds: Double = 1, body: Bool = false,
                          audio: Bool = false, pinned: Bool = false) -> ReIDEvidence {
        ReIDEvidence(personId: id, visualSimilarity: visual, bearingDeltaDeg: delta, secondsSinceSeen: seconds,
                     bodyContinuity: body, audioContinuity: audio, pinned: pinned)
    }

    func testVisualSimilarityContributesToMatching() {
        // Their old bearing is stale (a minute gone, other side of the room): only appearance can speak.
        XCTAssertGreaterThan(ReID.score(evidence(visual: 0.9, delta: 150, seconds: 60)), ReID.score(evidence(visual: 0.5, delta: 150, seconds: 60)))
        XCTAssertGreaterThanOrEqual(ReID.score(evidence(visual: 0.7, delta: 150, seconds: 60)), Tunables.reidAccept, "looks like them: it's them")
        XCTAssertLessThan(ReID.score(evidence(visual: 0.4, delta: 150, seconds: 60)), Tunables.reidAccept, "a vague resemblance is not enough")
        XCTAssertEqual(ReID.score(evidence(visual: 0.1, delta: 2, seconds: 3)), 0, "clearly someone else, even near where they were")
        // Feature-print distance -> similarity is monotonic and bounded.
        XCTAssertEqual(ReID.similarity(printDistance: 0), 1)
        XCTAssertEqual(ReID.similarity(printDistance: 5), 0)
        XCTAssertGreaterThan(ReID.similarity(printDistance: 0.5), ReID.similarity(printDistance: 0.9))
    }

    func testBearingContinuity() {
        XCTAssertGreaterThan(ReID.score(evidence(delta: 5, seconds: 2)), ReID.score(evidence(delta: 28, seconds: 2)))
        XCTAssertGreaterThanOrEqual(ReID.score(evidence(delta: 5, seconds: 2)), Tunables.reidAccept, "a brief drop at the same place")
        XCTAssertLessThan(ReID.score(evidence(delta: 45, seconds: 1)), Tunables.reidAccept)
        XCTAssertGreaterThan(ReID.spatial(evidence(delta: 35, seconds: 8)), ReID.spatial(evidence(delta: 35, seconds: 1)),
                             "the tolerance widens as time passes: people move")
    }

    func testTemporalContinuity() {
        XCTAssertGreaterThan(ReID.score(evidence(delta: 0, seconds: 1)), ReID.score(evidence(delta: 0, seconds: 9)))
        XCTAssertEqual(ReID.score(evidence(delta: 0, seconds: 30)), 0, "time alone, with no appearance and a stale bearing, is not evidence")
    }

    func testIdentityScoreCombinesMultipleEvidenceTypes() {
        let look = ReID.score(evidence(visual: 0.6, delta: 150, seconds: 60))        // appearance only
        let place = ReID.score(evidence(delta: 5, seconds: 2))                       // place + time only
        let both = ReID.score(evidence(visual: 0.6, delta: 5, seconds: 2))
        XCTAssertGreaterThan(both, look)
        XCTAssertGreaterThan(both, place)
        // A poor resemblance makes "they were just here" count for less than knowing nothing about looks.
        XCTAssertLessThan(ReID.score(evidence(visual: 0.25, delta: 5, seconds: 2)), place)
        let base = ReID.score(evidence(visual: 0.5, delta: 20, seconds: 4))
        XCTAssertEqual(ReID.score(evidence(visual: 0.5, delta: 20, seconds: 4, audio: true)), base + Tunables.reidAudioBonus, accuracy: 1e-9)
        XCTAssertEqual(ReID.score(evidence(visual: 0.5, delta: 20, seconds: 4, body: true)), base + Tunables.reidBodyBonus, accuracy: 1e-9)
        XCTAssertEqual(ReID.score(evidence(visual: 0.5, delta: 20, seconds: 4, pinned: true)), base + Tunables.reidPinBonus, accuracy: 1e-9)
        var edge = evidence(visual: 0.5, delta: 20, seconds: 4)
        edge.edgeContinuity = true
        XCTAssertEqual(ReID.score(edge), base + Tunables.reidEdgeBonus, accuracy: 1e-9)
        XCTAssertEqual(ReID.score(evidence(delta: 150, seconds: 60, body: true, audio: true, pinned: true)), 0,
                       "continuity bonuses never make a match out of nothing")
    }

    func testAmbiguousReentryCreatesANewPersonInsteadOfGuessing() {
        let d = ReID.decide([evidence(1, visual: 0.9, delta: 20, seconds: 5), evidence(2, visual: 0.9, delta: 22, seconds: 5)])
        XCTAssertNil(d.personId)
        XCTAssertEqual(d.reason, "ambiguous")
        let clear = ReID.decide([evidence(1, visual: 0.92, delta: 5, seconds: 5), evidence(2, visual: 0.3, delta: 40, seconds: 5)])
        XCTAssertEqual(clear.personId, 1)
        XCTAssertEqual(ReID.decide([]).reason, "no candidates")
        XCTAssertEqual(ReID.decide([evidence(1, visual: 0.3, delta: 40, seconds: 5)]).reason, "low score")
    }

    func testColorAndPinSurviveTrackReplacement() {
        let people = SessionPeople()
        let p = people.resolve(trackId: 1, bearing: 20, now: 0) { _ in nil }
        people.togglePin(personId: p.id)
        people.associate(trackId: 1, sourceId: 4, now: 0.5)
        // Vision resets the track five times while they move around.
        for i in 0..<5 {
            let t = Double(i + 1)
            people.detach(trackId: 1 + i, now: t)
            XCTAssertEqual(people.all.count, 1, "temporary face loss does not end the person")
            let again = people.resolve(trackId: 2 + i, bearing: 20 + Double(i) * 15, now: t + 0.2) { _ in 0.85 }
            XCTAssertEqual(again.id, p.id)
            XCTAssertEqual(again.displayName, "Person 1")
            XCTAssertEqual(again.colorIndex, p.colorIndex, "color belongs to the person, not the track")
            XCTAssertTrue(again.isSessionPinned, "so does the pin")
            XCTAssertEqual(again.audioSourceID, 4, "and their audio source")
        }
    }

    func testTwoPeopleCrossingDoNotSwapColors() {
        let people = SessionPeople()
        let one = people.resolve(trackId: 1, bearing: 20, now: 0) { _ in nil }
        let two = people.resolve(trackId: 2, bearing: -20, now: 0) { _ in nil }
        XCTAssertNotEqual(one.colorIndex, two.colorIndex)
        // Both tracks are lost as they pass each other; they reappear having swapped sides.
        people.detach(trackId: 1, now: 2); people.detach(trackId: 2, now: 2)
        let left = people.resolve(trackId: 3, bearing: -20, now: 2.4) { $0.id == one.id ? 0.9 : 0.2 }   // person one, now on the left
        let right = people.resolve(trackId: 4, bearing: 20, now: 2.4) { $0.id == two.id ? 0.9 : 0.2 }
        XCTAssertEqual(left.id, one.id, "appearance, not position, says who this is")
        XCTAssertEqual(left.colorIndex, one.colorIndex)
        XCTAssertEqual(right.id, two.id)
        XCTAssertEqual(right.colorIndex, two.colorIndex)
        // With nothing to tell them apart, position alone must not hand out identities at swapped spots.
        let blind = SessionPeople()
        let a = blind.resolve(trackId: 1, bearing: 4, now: 0) { _ in nil }
        _ = blind.resolve(trackId: 2, bearing: -4, now: 0) { _ in nil }
        blind.detach(trackId: 1, now: 1); blind.detach(trackId: 2, now: 1)
        let unsure = blind.resolve(trackId: 3, bearing: 0, now: 1.3) { _ in nil }
        XCTAssertNotEqual(unsure.id, a.id)
        XCTAssertEqual(blind.recentDecisions.last?.reason, "ambiguous")
    }

    func testCaptionFollowsAPersonOntoTheirNewTrack() {
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u", bearing: 20, faces: [FaceRef(id: 1, label: "Person 1", bearing: 20)], now: 0).label, "Person 1")
        XCTAssertEqual(a.retarget(from: 1, to: 9, label: "Person 1"), ["u"])
        // Still theirs on the new track, with no off-camera gap.
        XCTAssertNil(a.refresh(faces: [FaceRef(id: 9, label: "Person 1", bearing: 40)], bearings: ["u": 20], now: 5)["u"])
        XCTAssertEqual(a.assignments["u"]?.speaker, .person(id: 9, label: "Person 1"))
    }

    func testSoundSourceContinuity() {
        let live = [sound("e1", .dogBarking, bearing: 100, at: 10)]
        XCTAssertEqual(SoundContinuity.key(forEvent: "e1", label: .dogBarking, bearing: 100, among: live, now: 11), "e1")
        XCTAssertEqual(SoundContinuity.key(forEvent: "e2", label: .dogBarking, bearing: 108, among: live, now: 11), "e1",
                       "same kind, same direction, moments later: the same source")
        XCTAssertEqual(SoundContinuity.key(forEvent: "e2", label: .knock, bearing: 108, among: live, now: 11), "e2", "a different kind of sound")
        XCTAssertEqual(SoundContinuity.key(forEvent: "e2", label: .dogBarking, bearing: -60, among: live, now: 11), "e2", "somewhere else")
        XCTAssertEqual(SoundContinuity.key(forEvent: "e2", label: .dogBarking, bearing: 108, among: live, now: 20), "e2", "long after")
    }

    // MARK: aura

    func testAuraStateFollowsSpeakingState() {
        let f = face(7, "Person 1", bearing: 0, color: 2, person: 1)
        let who = [person(1, bearing: 0, track: 7)]
        let engine = PresenceEngine()
        XCTAssertTrue(engine.step(input(now: 0, faces: [f], people: who)).speakers.isEmpty, "silent: no aura")
        let speaking = engine.step(input(now: 1, captions: [caption("u", .person(id: 7, label: "Person 1"), bearing: 0, at: 1, loudness: 0.8)],
                                         faces: [f], people: who)).speakers[0]
        XCTAssertEqual(speaking.activity, 1)
        XCTAssertEqual(speaking.colorIndex, 2, "the aura is the person's identity color")
        XCTAssertGreaterThan(speaking.intensity, 0)
        // Speech stops: a short release, then it fades; the smoothed intensity comes down with it.
        let stopped = caption("u", .person(id: 7, label: "Person 1"), bearing: 0, at: 2, final: true, loudness: 0.8)
        let releasing = engine.step(input(now: 2 + Tunables.auraFadeS / 2, captions: [stopped], faces: [f], people: who)).speakers[0]
        XCTAssertEqual(releasing.activity, 0.5, accuracy: 1e-6)
        XCTAssertLessThan(releasing.intensity, speaking.intensity)
        XCTAssertTrue(engine.step(input(now: 2 + Tunables.captionHoldS + 0.1, captions: [], faces: [f], people: who)).speakers.isEmpty)
    }

    func testAuraIntensityMappingIsStable() {
        var last = -1.0
        for i in 0...10 {
            let o = SoundContinuity.auraOpacity(activity: 1, resting: 1, intensity: Double(i) / 10)
            XCTAssertGreaterThanOrEqual(o, last)
            XCTAssertTrue((0...1).contains(o))
            last = o
        }
        XCTAssertEqual(SoundContinuity.auraOpacity(activity: 1, resting: 1, intensity: 0), Tunables.auraActiveOpacityMin)
        XCTAssertEqual(SoundContinuity.auraOpacity(activity: 1, resting: 1, intensity: 1), Tunables.auraActiveOpacity, accuracy: 1e-9)
        XCTAssertEqual(SoundContinuity.auraOpacity(activity: 0, resting: 1, intensity: 1), Tunables.auraRestOpacity)
        XCTAssertEqual(SoundContinuity.auraOpacity(activity: 0, resting: 0, intensity: 1), 0, "silent: nothing")
        XCTAssertEqual(SoundContinuity.auraOpacity(activity: 1, resting: 1, intensity: 9), Tunables.auraActiveOpacity, accuracy: 1e-9, "clamped")
        // The pulse is small, grows a little with intensity, and is absent with Reduce Motion.
        let quiet = SoundContinuity.auraPulse(activity: 1, intensity: 0, reduceMotion: false)
        let loud = SoundContinuity.auraPulse(activity: 1, intensity: 1, reduceMotion: false)
        XCTAssertGreaterThan(loud, quiet)
        XCTAssertLessThanOrEqual(loud, 0.1, "never a size jump")
        XCTAssertEqual(SoundContinuity.auraPulse(activity: 1, intensity: 1, reduceMotion: true), 0)
        XCTAssertEqual(SoundContinuity.auraPulse(activity: 0, intensity: 1, reduceMotion: false), 0)
        // The clear middle really is clear, and the glow reaches past the face box.
        XCTAssertGreaterThan(Tunables.auraInnerClear * Tunables.auraExpandX, 0.85, "the face box's width stays unpainted")
        XCTAssertGreaterThan(Tunables.auraExpandX, 1.5)
    }

    func testSessionPersonReacquisition() {
        let people = SessionPeople()
        let first = people.resolve(trackId: 10, bearing: 22, now: 0) { _ in nil }
        XCTAssertEqual(first.displayName, "Person 1")
        people.detach(trackId: 10, now: 2)
        XCTAssertEqual(people.all.count, 1, "the track ended; the person did not")
        XCTAssertFalse(people.all[0].isVisible)
        // Back 40 s later somewhere else, recognisably the same person: still Person 1.
        let back = people.resolve(trackId: 11, bearing: -60, now: 42) { _ in 0.93 }
        XCTAssertEqual(back.id, first.id)
        XCTAssertEqual(back.currentTrackID, 11)
        XCTAssertEqual(people.all.count, 1)
        // Someone who doesn't look like them is a new person.
        people.detach(trackId: 11, now: 43)
        XCTAssertEqual(people.resolve(trackId: 12, bearing: -60, now: 44) { _ in 0.2 }.displayName, "Person 2")
    }

    func testIdentityConflictPrevention() {
        let people = SessionPeople()
        let one = people.resolve(trackId: 1, bearing: 20, now: 0) { _ in nil }
        // A second face appears right next to Person 1 while they are still being tracked.
        let two = people.resolve(trackId: 2, bearing: 21, now: 0.1) { _ in 0.99 }
        XCTAssertNotEqual(one.id, two.id, "one person is never two tracks")
        XCTAssertEqual(Set(people.all.compactMap(\.currentTrackID)), [1, 2])
        // Two lookalikes gone, one track returns between them: nobody's identity is handed over.
        people.detach(trackId: 1, now: 1); people.detach(trackId: 2, now: 1)
        let unsure = people.resolve(trackId: 3, bearing: 20.5, now: 3) { _ in 0.9 }
        XCTAssertEqual(unsure.displayName, "Person 3")
        XCTAssertEqual(people.recentDecisions.last?.reason, "ambiguous")
    }

    func testAudioSourceContinuity() {
        let people = SessionPeople()
        let p = people.resolve(trackId: 1, bearing: 20, now: 0) { _ in nil }
        people.associate(trackId: 1, sourceId: 4, now: 1)
        people.detach(trackId: 1, now: 2)
        // With meaningful ids their source keeps their bearing alive while they walk around behind.
        people.follow(sources: [Source(id: 4, bearingDeg: 150, energy: 0.5, speech: true, confidence: 0.9)], now: 20)
        XCTAssertEqual(people.all[0].lastKnownWorldBearing, 150)
        XCTAssertTrue(people.all[0].hasCredibleBearing(now: 21))
        XCTAssertEqual(people.heard(bearing: -90, sourceId: 4, now: 22)?.id, p.id, "the source is theirs wherever it is heard")
        // Without meaningful ids: only time + bearing, and only while credible and unambiguous.
        let plain = SessionPeople()
        let a = plain.resolve(trackId: 1, bearing: 20, now: 0) { _ in nil }
        plain.detach(trackId: 1, now: 1)
        XCTAssertEqual(plain.heard(bearing: 26, sourceId: nil, now: 3)?.id, a.id)
        XCTAssertNil(plain.heard(bearing: 90, sourceId: nil, now: 3), "wrong direction")
        XCTAssertNil(plain.heard(bearing: 26, sourceId: nil, now: 3 + Tunables.spatialCredibleS + 1), "too long ago to trust")
        let two = SessionPeople()
        _ = two.resolve(trackId: 1, bearing: 20, now: 0) { _ in nil }
        _ = two.resolve(trackId: 2, bearing: 28, now: 0) { _ in nil }
        two.detach(trackId: 1, now: 1); two.detach(trackId: 2, now: 1)
        XCTAssertNil(two.heard(bearing: 24, sourceId: nil, now: 2), "two people it could be: nobody is named")
    }

    func testEstablishedPersonGetsAWiderAttributionAngle() {
        let faces = [FaceRef(id: 1, label: "Person 1", bearing: 0)]
        XCTAssertEqual(Attributor().attribute(utt: "u", bearing: 20, faces: faces, now: 0), .offCamera, "unknown pair: conservative")
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u", bearing: 20, faces: faces, now: 0, established: [1]).label, "Person 1")
        XCTAssertEqual(a.lastDecision?.reason, "established audio source")
        XCTAssertEqual(Attributor().attribute(utt: "u", bearing: 40, faces: faces, now: 0, established: [1]), .offCamera, "still bounded")
    }

    // MARK: session pins

    func testSessionPinSurvivesFaceTrackLoss() {
        let people = SessionPeople()
        let p = people.resolve(trackId: 1, bearing: 22, now: 0) { _ in nil }
        XCTAssertTrue(people.togglePin(personId: p.id))
        people.detach(trackId: 1, now: 2)
        XCTAssertTrue(people.all[0].isSessionPinned, "the pin is on the person, not the Vision track")
        people.expire(now: 100_000)
        XCTAssertEqual(people.all.count, 1, "a pinned person is never forgotten")
    }

    func testPinnedPersonOffscreenPresentation() {
        let pin = person(1, bearing: 22, seen: 0, pinned: true)
        let near = PresenceEngine().step(input(now: 3, heading: -95, people: [pin])).presences[0]
        XCTAssertEqual(near.id, "person:1")
        XCTAssertTrue(near.isPinned)
        XCTAssertEqual(near.relativeBearing(heading: -95), 117)
        XCTAssertEqual(near.tone, .person(0))
        XCTAssertEqual(near.label, "Person 1")
        XCTAssertEqual(near.opacity, Tunables.pinnedIdleOpacity)
        // Speaking: their words ride with the notification.
        var c = caption("u", .offCamera, bearing: 24, at: 3, owner: 1)
        c.text = "Over here"
        c.pending = "when you"
        let talking = PresenceEngine().step(input(now: 3, heading: -95, captions: [c], people: [pin])).presences[0]
        XCTAssertEqual(talking.captionText, "Over here")
        XCTAssertEqual(talking.captionPending, "when you")
        XCTAssertTrue(talking.speaking)
        XCTAssertEqual(talking.tier, 8)
        // Their location is no longer credible: the pin stays, the direction is not invented.
        let stale = PresenceEngine().step(input(now: Tunables.spatialCredibleS + 1, heading: -95, people: [pin])).presences[0]
        XCTAssertTrue(stale.stale)
        XCTAssertNil(stale.worldBearing)
        XCTAssertTrue(stale.isPinned)
    }

    func testPinnedPersonReturnsAndReassociates() {
        let people = SessionPeople()
        let engine = PresenceEngine()
        let p = people.resolve(trackId: 1, bearing: 22, now: 0) { _ in nil }
        people.togglePin(personId: p.id)
        people.detach(trackId: 1, now: 1)
        XCTAssertEqual(engine.step(input(now: 3, heading: -95, people: people.all)).presences.map(\.id), ["person:1"])
        // They come back: a new Vision track, the same pinned person.
        let back = people.resolve(trackId: 2, bearing: 25, now: 4) { _ in 0.92 }
        XCTAssertEqual(back.id, p.id)
        XCTAssertTrue(back.isSessionPinned)
        let f = face(2, back.displayName, bearing: 25, person: back.id)
        let handing = engine.step(input(now: 4, faces: [f], people: people.all))
        XCTAssertEqual(handing.presences.first?.handoffProgress, 0, "the notification travels back to their face")
        XCTAssertTrue(handing.speakers.first?.isPinned ?? false)
        let done = engine.step(input(now: 4 + Tunables.handoffS + 0.05, faces: [f], people: people.all))
        XCTAssertTrue(done.presences.isEmpty)
        XCTAssertEqual(done.pinnedPersonIds, [p.id])
    }

    // MARK: known people

    func testKnownPersonMatchLogic() {
        let a = UUID(), b = UUID()
        XCTAssertEqual(KnownPeople.match([(a, 0.9), (b, 0.4)]), a)
        XCTAssertNil(KnownPeople.match([(a, 0.7), (b, 0.4)]), "not sure enough to put a name on someone")
        XCTAssertNil(KnownPeople.match([(a, 0.9), (b, 0.85)]), "two enrolled people it could be")
        XCTAssertNil(KnownPeople.match([]))
        // Enrollment restores name, color and priority onto the session person; a pin is something else.
        let people = SessionPeople()
        let p = people.resolve(trackId: 1, bearing: 0, now: 0) { _ in nil }
        people.apply(KnownPerson(chosenName: "Maya", preferredColor: 3, priority: .always), to: p.id)
        XCTAssertEqual(people.all[0].displayName, "Maya")
        XCTAssertEqual(people.all[0].colorIndex, 3)
        XCTAssertFalse(people.all[0].isSessionPinned, "adding to People is not pinning")
        let store = KnownPeopleStore(inMemory: [KnownPerson(chosenName: "Maya", preferredColor: 3)])
        XCTAssertEqual(try JSONDecoder().decode([KnownPerson].self, from: JSONEncoder().encode(store.people)), store.people)
    }

    func testKnownPersonPriorityLogic() {
        func speechTier(_ priority: KnownPerson.Priority) -> Double {
            var p = person(1, bearing: 150)
            p.priority = priority
            return PresenceEngine().step(input(captions: [caption("u", .offCamera, bearing: 150, owner: 1)], people: [p])).presences[0].tier
        }
        XCTAssertGreaterThan(speechTier(.whenSpeaking), speechTier(.normal))
        XCTAssertEqual(speechTier(.always), speechTier(.whenSpeaking))
        // Always prioritize: keeps a place out of view while their direction is credible...
        var always = person(1, bearing: 150, seen: 0)
        always.priority = .always
        XCTAssertEqual(PresenceEngine().step(input(now: 2, people: [always])).presences.count, 1)
        XCTAssertTrue(PresenceEngine().step(input(now: Tunables.spatialCredibleS + 2, people: [always])).presences.isEmpty)
        // ...and never a permanent badge while they sit silently in view (clean idle).
        always.currentTrackID = 5
        let idle = PresenceEngine().step(input(faces: [face(5, "Person 1", bearing: 0, person: 1)], people: [always]))
        XCTAssertTrue(idle.isIdle)
        var normal = person(2, bearing: 150, seen: 0)
        normal.priority = .normal
        XCTAssertTrue(PresenceEngine().step(input(now: 2, people: [normal])).presences.isEmpty)
    }

    // MARK: layout

    func testCornerStackIsCompactTopRightOnePerSource() {
        let ps = (0..<Tunables.maxPerimeterIcons).map { i -> SpatialPresence in
            var p = SpatialPresence(id: "s\(i)", sourceType: .sound, worldBearing: Double(i) * 60 - 120, category: .other, icon: "x",
                                    tone: .environment, label: nil, prominence: 1, urgency: 0, persistence: 0, score: 1,
                                    level: .relevant, firstSeen: 0, lastSeen: 0)
            p.opacity = 1
            return p
        }
        let placed = SpatialLayout.place(ps, proj: proj(), faceRects: [:], size: size, reduceMotion: false, placement: .corner)
        XCTAssertEqual(placed.count, ps.count)
        for (i, p) in placed.enumerated() {
            XCTAssertEqual(p.point, SpatialLayout.cornerPoint(slot: i, size: size))
            XCTAssertGreaterThan(p.point.x, size.width * 0.85)
            XCTAssertLessThanOrEqual(p.point.y + Tunables.cornerSlot / 2, size.height, "the full stack fits")
        }
        let turned = SpatialLayout.place(ps, proj: proj(heading: 60), faceRects: [:], size: size, reduceMotion: false, placement: .corner)
        XCTAssertEqual(turned[0].point, placed[0].point, "the notification stays put")
        XCTAssertEqual(turned[0].relative, Geo.relative(-120, heading: 60), "only its marker moves")
    }

    func testTwoCaptionsNeverCoverAFaceOrEachOther() {
        let r1 = CGRect(x: 530, y: 118, width: 78, height: 78), r2 = CGRect(x: 240, y: 118, width: 78, height: 78)
        let faces = [face(1, "Person 1", bearing: 22), face(2, "Person 2", bearing: -26, color: 1)]
        let caps = [caption("a", .person(id: 1, label: "Person 1"), bearing: 22, at: 0, final: true),
                    caption("b", .person(id: 2, label: "Person 2"), bearing: -26, at: 1)]
        let out = SpatialLayout.captions(caps, scene: .empty, placed: [], faces: faces, rects: [1: r1, 2: r2], proj: proj(),
                                         size: size, reduceMotion: false)
        let boxes = out.map { CGRect(x: $0.point.x - Layout.boxWidth / 2, y: $0.point.y - Layout.boxHeight / 2,
                                     width: Layout.boxWidth, height: Layout.boxHeight) }
        XCTAssertEqual(boxes.count, 2)
        for box in boxes {
            XCTAssertFalse(box.intersects(r1), "README §14: never cover a mouth")
            XCTAssertFalse(box.intersects(r2), "README §14: never cover a mouth")
        }
        XCTAssertFalse(boxes[0].intersects(boxes[1]))
    }

    func testCaptionPlateContrast() {
        let plate = 1 - Tunables.captionPlateOpacity
        XCTAssertGreaterThanOrEqual(Relevance.contrast(1.0, plate), 4.5)
        XCTAssertGreaterThanOrEqual(Relevance.contrast(Tunables.captionPendingWhite, plate), 4.5)
    }

    func testBaselineAdaptsToASteadySoundAndASuddenOneStandsOut() {
        var b = RoomBaseline()
        b.update(level: 0.3, now: 0)
        for i in 1...400 { b.update(level: 0.3, now: Double(i) * 0.05) }
        XCTAssertLessThan(Relevance.prominence(level: 0.3, baseline: b.value!), 0.05)
        XCTAssertGreaterThan(Relevance.prominence(level: 0.9, baseline: b.value!), 0.95)
    }

    // MARK: the real array: empty sources, fixed beams, perception_enrichment

    func testEmptySourcesKeepTheRoomBaseline() {
        let core = PresentationCore()
        for k in 0...100 { core.ingest(sources: [src(0, 0.4)], now: Double(k) * 0.05) }       // a loud room, 5 s
        let loudRoom = core.baseline.value!
        XCTAssertEqual(loudRoom, 0.4, accuracy: 1e-9)
        // Nothing live: `"sources": []` once, then as a 1 s keepalive. That is no reading, not a reading of 0.
        for k in 1...10 { core.ingest(sources: [], now: 5 + Double(k)) }
        XCTAssertEqual(core.baseline.value!, loudRoom, accuracy: 1e-12, "the baseline holds through a quiet stretch")
        // The next sound is weighed from the last keepalive, so it doesn't drag the baseline up at once.
        core.ingest(sources: [src(180, 0.7)], now: 15.05)
        XCTAssertLessThan(core.baseline.value!, loudRoom + 0.01)
        // Before anything has been heard there is no baseline (sounds use the classifier fallback).
        let fresh = PresentationCore()
        fresh.ingest(sources: [], now: 0)
        XCTAssertNil(fresh.baseline.value)
    }

    func testSoundMatchesItsSourceIdAndAFailedMatchIsNeverZero() {
        // The real array: fixed beams at 0 (id 0, front) and 180 (id 1, rear); a sound's bearing comes from
        // the raw mics, so a doorbell heard on the rear beam can sit at 100.
        let beams = [Source(id: 0, bearingDeg: 0, energy: 0.05, speech: false, confidence: 0.5),
                     Source(id: 1, bearingDeg: 180, energy: 0.3, speech: false, confidence: 0.8)]
        func prominence(_ tag: SoundTag, _ sources: [Source]) -> Double? {
            PresenceEngine().step(input(sounds: [tag], sources: sources)).presences.first { $0.id == "snd:\(tag.eventId)" }?.prominence
        }
        var bell = sound("b", .doorbell, bearing: 100)
        bell.sourceId = 1
        XCTAssertEqual(prominence(bell, beams)!, Relevance.prominence(level: 0.3, baseline: 0.05), accuracy: 1e-9,
                       "its own beam's level, wherever the raw mics put it")
        let fallback = Relevance.fallbackProminence(confidence: bell.confidence)
        XCTAssertGreaterThan(fallback, 0)
        var unnamed = bell
        unnamed.sourceId = nil
        XCTAssertEqual(prominence(unnamed, beams)!, fallback, accuracy: 1e-9, "no beam near 100: the classifier fallback, not 0")
        XCTAssertEqual(prominence(unnamed, [])!, fallback, accuracy: 1e-9, "exactly as with no sources at all")
        var gone = bell
        gone.sourceId = 7
        XCTAssertEqual(prominence(gone, beams)!, fallback, accuracy: 1e-9, "its track isn't live")
        // Without a source_id, a source near the sound's bearing is still its level.
        XCTAssertEqual(prominence(unnamed, [src(95, 0.3)])!, Relevance.prominence(level: 0.3, baseline: 0.05), accuracy: 1e-9)
    }

    func testEnrichmentSetsSoundProminence() {
        func knock(_ attention: Attention?, about id: String = "k") -> SpatialScene {
            var enrichments: [String: EnrichmentState] = [:]
            if let attention { enrichments[EnrichmentState.key(eventId: id)] = judged(attention) }
            return PresenceEngine().step(input(sounds: [sound("k", .knock, bearing: 100)], sources: [src(100, 0.6)],
                                               enrichments: enrichments))
        }
        let rules = knock(nil).presences[0]
        XCTAssertEqual(rules.level, .relevant)
        XCTAssertEqual(knock(.urgent, about: "someone-else"), knock(nil), "a judgment about something else changes nothing")

        let ambient = knock(.ambient).presences[0]
        XCTAssertEqual(ambient.level, .ambient)
        XCTAssertLessThanOrEqual(ambient.opacity, Tunables.enrichAmbientOpacityMax + 1e-9, "minimal")
        XCTAssertLessThan(ambient.rank, rules.rank)
        XCTAssertEqual(knock(.notice).presences[0].level, .ambient, "a small indicator")
        let important = knock(.important).presences[0]
        XCTAssertGreaterThanOrEqual(important.level, SpatialPresence.Level.relevant, "a full card")
        XCTAssertGreaterThanOrEqual(important.tier, Tunables.enrichImportantTier)
        XCTAssertGreaterThanOrEqual(important.opacity, Tunables.alertOpacityFloor)
        let urgent = knock(.urgent).presences[0]
        XCTAssertEqual(urgent.level, .important, "interrupt: it keeps pulsing while it lasts")
        XCTAssertEqual(urgent.tier, Tunables.enrichUrgentTier)

        // An unknown sound too faint for the rules is shown once it's judged worth a notice.
        let faint = SoundTag(eventId: "u", label: .unknown, category: .other, emoji: "", text: "Unknown sound", bearing: -120,
                             count: 1, updated: 0, confidence: 0.32)
        let unjudged = PresenceEngine().step(input(sounds: [faint], sources: quiet))
        XCTAssertTrue(unjudged.presences.isEmpty)
        XCTAssertEqual(unjudged.suppressed.map(\.id), ["snd:u"])
        let noticed = PresenceEngine().step(input(sounds: [faint], sources: quiet,
                                                  enrichments: [EnrichmentState.key(eventId: "u"): judged(.notice)]))
        XCTAssertEqual(noticed.presences.map(\.id), ["snd:u"])
        // Danger is never made less than the rules make it.
        let alarm = sound("a", .alarm, bearing: 170)
        let danger = PresenceEngine().step(input(sounds: [alarm], sources: [src(170, 0.6)]))
        let calmed = PresenceEngine().step(input(sounds: [alarm], sources: [src(170, 0.6)],
                                                 enrichments: [EnrichmentState.key(eventId: "a"): judged(.ambient)]))
        XCTAssertEqual(calmed, danger)
    }

    func testEnrichmentNeverDemotesAnAlertOrTheWearersName() {
        var name = alert("n", .nameCalled, bearing: 160)
        name.refUttId = "u"
        var bell = alert("d", .doorbell, bearing: 165)
        bell.refEventId = "s"
        let rules = PresenceEngine().step(input(alerts: [name, bell], sources: quiet))
        for attention in [Attention.ambient, .notice, .important] {
            let enrichments = [EnrichmentState.key(uttId: "u"): judged(attention), EnrichmentState.key(eventId: "s"): judged(attention)]
            XCTAssertEqual(PresenceEngine().step(input(alerts: [name, bell], sources: quiet, enrichments: enrichments)), rules,
                           "\(attention): alerts stay as the rules give them")
        }
        // urgent = interrupt: the doorbell goes to the top, above the name, never fainter than a warning.
        let urgent = PresenceEngine().step(input(alerts: [name, bell], sources: quiet,
                                                 enrichments: [EnrichmentState.key(eventId: "s"): judged(.urgent)]))
        let raised = urgent.presences.first { $0.id == "alr:d" }!
        XCTAssertEqual(raised.tier, Tunables.enrichUrgentTier)
        XCTAssertGreaterThanOrEqual(raised.opacity, Tunables.dangerOpacityFloor)
        XCTAssertEqual(urgent.presences.first?.id, "alr:d")
    }

    func testEnrichmentOnlyRaisesSpeech() {
        let c = caption("u1", .offCamera, bearing: 160)
        func speech(_ e: EnrichmentState?) -> SpatialPresence {
            var enrichments: [String: EnrichmentState] = [:]
            if let e { enrichments[EnrichmentState.key(uttId: "u1")] = e }
            return PresenceEngine().step(input(captions: [c], enrichments: enrichments)).presences[0]
        }
        let rules = speech(nil)
        XCTAssertEqual(speech(judged(.ambient)), rules, "a caption is never made less than the rules make it")
        XCTAssertEqual(speech(judged(.notice)), rules)
        XCTAssertEqual(speech(judged(.notice, directed: 0.5)), rules, "conservative: a maybe is not 'to you'")
        XCTAssertGreaterThan(speech(judged(.notice, directed: 0.95)).tier, rules.tier, "clearly addressed to the wearer")
        XCTAssertEqual(speech(judged(.important)).tier, Tunables.enrichSpeechTier)
        XCTAssertEqual(speech(judged(.urgent)).level, .important)
    }

    // MARK: scenarios (what Settings > Developer plays and the previews render)

    private func at(_ number: Int, _ t: Double) -> ScenarioHarness.Snapshot {
        ScenarioHarness(.numbered(number)).advance(to: t)
    }

    private func run(_ number: Int, _ check: (ScenarioHarness.Snapshot) -> Void) {
        let scenario = SpatialScenario.numbered(number)
        let harness = ScenarioHarness(scenario)
        var t = ScenarioHarness.step
        while t <= scenario.duration + 1e-9 {
            check(harness.advance(to: t))
            t += ScenarioHarness.step
        }
    }

    private func sound(_ s: ScenarioHarness.Snapshot, _ id: String) -> SpatialPresence? {
        s.scene.presences.first { $0.id == "snd:\(id)" }
    }

    func testAllScenariosAreDeterministicAndWithinLimits() {
        XCTAssertEqual(SpatialScenario.all.map(\.number), Array(1...72))
        for scenario in SpatialScenario.all {
            XCTAssertEqual(ScenarioHarness(scenario).advance(to: scenario.previewTime).scene,
                           ScenarioHarness(scenario).advance(to: scenario.previewTime).scene, "scenario \(scenario.number)")
            run(scenario.number) { snap in
                let places = snap.scene.presences.filter { $0.associatedTrackedFaceID == nil }.count
                XCTAssertLessThanOrEqual(places, Tunables.maxPerimeterIcons, "scenario \(scenario.number) at \(snap.t)")
                for p in snap.scene.presences { XCTAssertTrue((0...1).contains(p.opacity), "scenario \(scenario.number)") }
                // No floating captions for anyone who isn't on camera.
                for c in snap.visibleCaptions {
                    if case let .person(id, _) = c.speaker, snap.faces.contains(where: { $0.id == id }) { continue }
                    let rects = Dictionary(uniqueKeysWithValues: snap.faces.map { ($0.id, CGRect(x: 0, y: 0, width: 10, height: 10)) })
                    XCTAssertFalse(SpatialLayout.captions([c], scene: snap.scene, placed: [], faces: snap.faces, rects: rects,
                                                          proj: proj(heading: snap.heading), size: size, reduceMotion: false)
                        .contains { $0.id == c.uttId }, "scenario \(scenario.number)")
                }
            }
        }
    }

    func testScenario01QuietRoom() {
        run(1) { XCTAssertTrue($0.scene.isIdle, "t=\($0.t)"); XCTAssertEqual($0.faces.count, 2) }
    }

    func testScenarios02To04WeakAndLoud() {
        let weak = sound(at(2, 2.0), "dog")!, loud = sound(at(3, 2.0), "dog")!
        XCTAssertLessThan(weak.opacity, loud.opacity - 0.3)
        XCTAssertEqual(weak.tone, .environment)
        XCTAssertEqual(weak.icon, loud.icon)
        let both = at(4, 2.0)
        XCTAssertNotNil(sound(both, "weak")); XCTAssertNotNil(sound(both, "loud"))
        XCTAssertLessThan(sound(both, "weak")!.opacity, sound(both, "loud")!.opacity)
        // The loud one never pushes the weak one out.
        run(4) { s in if s.t > 0.7 && s.t < 4.0 { XCTAssertNotNil(sound(s, "weak"), "t=\(s.t)") } }
    }

    func testScenarios05And06SeveralAtOnce() {
        let three = at(5, 2.5)
        XCTAssertEqual(Set(three.scene.presences.map(\.id)), ["snd:steps", "snd:door", "snd:phone"])
        XCTAssertEqual(sound(three, "steps")?.icon, "shoeprints.fill")
        XCTAssertEqual(Set(three.scene.presences.compactMap(\.worldBearing)), [-90, 90, 170])
        let five = at(6, 2.8)
        XCTAssertEqual(five.scene.presences.count, 5)
        XCTAssertTrue(five.scene.queued.isEmpty)
    }

    func testScenarios07And08OpacityFollowsLevelWhileActive() {
        XCTAssertLessThan(sound(at(7, 1.5), "dog")!.opacity, sound(at(7, 4.8), "dog")!.opacity - 0.2)
        XCTAssertGreaterThan(sound(at(8, 1.5), "dog")!.opacity, sound(at(8, 5.5), "dog")!.opacity + 0.2)
        run(8) { s in if s.t > 0.7 && s.t < 6.0 { XCTAssertNotNil(sound(s, "dog"), "getting quieter is not leaving (t=\(s.t))") } }
    }

    func testScenarios09And10LoudAndQuietStopOnTheSameSchedule() {
        func lastSeen(_ n: Int) -> Double {
            var last = 0.0
            run(n) { s in if sound(s, "s") != nil { last = s.t } }
            return last
        }
        XCTAssertEqual(lastSeen(9), lastSeen(10), accuracy: 1e-9)
        XCTAssertEqual(lastSeen(9), 3.0 + Tunables.soundReleaseHoldS + Tunables.notificationFadeS, accuracy: 0.15)
        XCTAssertGreaterThan(sound(at(9, 2.0), "s")!.opacity, sound(at(10, 2.0), "s")!.opacity)
    }

    func testScenario11ShortDipDoesNotRemoveIt() {
        run(11) { s in if s.t > 0.7 && s.t < 6.0 { XCTAssertNotNil(sound(s, "phone"), "t=\(s.t)") } }
        XCTAssertLessThan(sound(at(11, 3.2), "phone")!.opacity, sound(at(11, 2.2), "phone")!.opacity, "it dims during the dip")
    }

    func testScenario12Unknown() {
        let p = sound(at(12, 2.0), "x")!
        XCTAssertEqual(p.icon, "questionmark")
        XCTAssertEqual(p.tone, .neutral)
        XCTAssertEqual(p.worldBearing, 120)
    }

    func testScenario13And28Danger() {
        let alarm = at(13, 3.0).scene.presences[0]
        XCTAssertEqual(alarm.tone, .danger)
        XCTAssertEqual(alarm.worldBearing, 170)
        XCTAssertGreaterThanOrEqual(alarm.opacity, Tunables.dangerOpacityFloor)
        let mixed = at(28, 3.5).scene.presences
        XCTAssertEqual(mixed.map(\.tone), [.danger, .human, .environment], "all three at once, danger first")
        XCTAssertEqual(mixed.compactMap(\.worldBearing), [170, -150, -90])
    }

    func testScenario14And15SpeechColor() {
        let unknown = at(14, 3.0)
        XCTAssertEqual(unknown.scene.presences.map(\.tone), [.human])
        let known = at(15, 5.5)
        XCTAssertEqual(known.scene.presences.map(\.id), ["person:1"])
        XCTAssertEqual(known.scene.presences[0].tone, .person(0))
        XCTAssertEqual(known.scene.presences[0].label, "Person 1")
    }

    func testScenarios16To18SpeakerLeavesAndReturns() {
        let visible = at(16, 3.0)
        XCTAssertTrue(visible.scene.presences.isEmpty)
        XCTAssertEqual(visible.scene.speakers.first?.activity, 1)

        let away = at(17, 4.0)
        XCTAssertTrue(away.faces.isEmpty)
        XCTAssertEqual(away.scene.presences.map(\.id), ["person:1"])
        XCTAssertTrue(away.scene.presences[0].speaking)
        XCTAssertNil(away.scene.presences[0].captionText, "unpinned: no caption out of view")
        run(17) { s in XCTAssertFalse(s.scene.presences.contains { $0.tone == .human }, "never falls back to anonymous blue (t=\(s.t))") }

        var handedOff = false
        run(18) { s in if s.scene.presences.contains(where: { $0.handoffProgress != nil }) { handedOff = true } }
        XCTAssertTrue(handedOff, "the notification resolves back into the face")
        let back = at(18, 9.2)
        XCTAssertEqual(back.people.count, 1, "the same Person 1, not a new one")
        XCTAssertEqual(back.faces.first?.label, "Person 1")
        XCTAssertEqual(back.scene.speakers.first?.activity, 1)
        XCTAssertTrue(back.scene.presences.isEmpty)
        XCTAssertEqual(back.captions["s"]?.speaker.label, "Person 1")
    }

    func testScenarios19To22Pinned() {
        let visible = at(19, 2.0)
        XCTAssertEqual(visible.scene.speakers.map(\.label), ["Person 2"])
        XCTAssertTrue(visible.scene.speakers[0].isPinned)
        XCTAssertTrue(visible.scene.presences.isEmpty)
        XCTAssertEqual(at(19, 5.0).scene.speakers.first { $0.label == "Person 2" }?.activity, 1)

        let off = at(20, 5.0).scene.presences[0]
        XCTAssertEqual(off.id, "person:1")
        XCTAssertFalse(off.stale)
        XCTAssertEqual(off.relativeBearing(heading: -95)!, 117, accuracy: 0.5)
        let stale = at(20, 12.0)
        XCTAssertTrue(stale.scene.presences[0].stale, "direction no longer credible: say so")
        XCTAssertNil(stale.scene.presences[0].worldBearing)
        XCTAssertTrue(stale.people[0].isSessionPinned, "but the pin is kept")

        let talking = at(21, 6.0).scene.presences[0]
        XCTAssertTrue(talking.speaking)
        XCTAssertTrue(talking.captionText?.hasPrefix("Over here") ?? false)
        XCTAssertFalse(talking.stale, "hearing them keeps their direction credible")

        let returned = at(22, 9.5)
        XCTAssertEqual(returned.people.count, 1)
        XCTAssertTrue(returned.scene.speakers.first?.isPinned ?? false)
        XCTAssertEqual(returned.scene.speakers.first?.activity, 1)
        XCTAssertTrue(returned.scene.presences.isEmpty)
    }

    func testScenario23LeavesAndReentersAsTheSamePerson() {
        let gone = at(23, 5.0)
        XCTAssertEqual(gone.faces.map(\.label), ["Person 2"])
        XCTAssertEqual(gone.people.count, 2, "Person 1 is detached, not destroyed")
        let back = at(23, 9.0)
        XCTAssertEqual(Set(back.faces.map(\.label)), ["Person 1", "Person 2"])
        XCTAssertEqual(back.people.count, 2)
    }

    func testScenario24AmbiguousCrossingKeepsBothIdentities() {
        let h = ScenarioHarness(.numbered(24))
        // Two lookalikes come back at each other's spots. While it's unclear who is who, neither is labelled...
        let unsure = h.advance(to: 9.0)
        XCTAssertTrue(unsure.faces.isEmpty, "a brief unlabelled moment, not a confident guess")
        XCTAssertEqual(unsure.people.count, 2, "and nobody new is invented")
        // ...then each takes the more likely of the two existing identities.
        let settled = h.advance(to: 11.5)
        XCTAssertEqual(Set(settled.faces.map(\.label)), ["Person 1", "Person 2"])
        XCTAssertEqual(settled.people.count, 2)
        for face in settled.faces { XCTAssertEqual(h.truthName(ofPerson: face.personId!), h.faceTruth[face.id], "no swap") }
    }

    func testTwoStageMatchingContinuityFirst() {
        // Stage 1: a track reappearing where exactly one person just was is that person, even if
        // someone who left long ago looks more like the new track than anyone should.
        let d = ReID.decide([evidence(1, visual: 0.6, delta: 3, seconds: 0.4), evidence(2, visual: 0.95, delta: 120, seconds: 300)])
        XCTAssertEqual(d.personId, 1)
        XCTAssertEqual(d.reason, "continuity")
        // Not continuity if they clearly look like someone else, or if two people were just there.
        XCTAssertNotEqual(ReID.decide([evidence(1, visual: 0.1, delta: 3, seconds: 0.4)]).reason, "continuity")
        XCTAssertEqual(ReID.decide([evidence(1, delta: 3, seconds: 0.4), evidence(2, delta: -3, seconds: 0.4)]).reason, "ambiguous")
        // Stage 2: nobody was just there; appearance against everyone dormant.
        let later = ReID.decide([evidence(1, visual: 0.3, delta: 80, seconds: 40), evidence(2, visual: 0.9, delta: 120, seconds: 300)])
        XCTAssertEqual(later.personId, 2)
        XCTAssertEqual(later.reason, "accepted")
    }

    func testIdentityHysteresisAndWaitingOutAmbiguity() {
        XCTAssertFalse(ReID.shouldSwitch(currentScore: 0.78, challengerScore: 0.81), "a slightly better score for someone else changes nothing")
        XCTAssertTrue(ReID.shouldSwitch(currentScore: 0.5, challengerScore: 0.5 + Tunables.reidSwitchMargin))
        // An ambiguous new track: wait rather than decide; then keep the better existing identity.
        let people = SessionPeople()
        let a = people.resolve(trackId: 1, bearing: 4, now: 0) { _ in nil }
        let b = people.resolve(trackId: 2, bearing: -4, now: 0) { _ in nil }
        people.togglePin(personId: a.id)
        people.detach(trackId: 1, now: 1); people.detach(trackId: 2, now: 1)
        let looks: (SessionPerson) -> Double? = { $0.id == a.id ? 0.86 : 0.84 }
        XCTAssertNil(people.attach(trackId: 3, bearing: 0, now: 8, mode: .wait, similarity: looks), "undecided")
        XCTAssertEqual(people.all.count, 2, "nobody invented while undecided")
        let settled = people.attach(trackId: 3, bearing: 0, now: 10, mode: .takeBest, similarity: looks)
        XCTAssertEqual(settled?.id, a.id)
        XCTAssertEqual(settled?.colorIndex, a.colorIndex)
        XCTAssertTrue(settled?.isSessionPinned ?? false)
        XCTAssertEqual(people.all.count, 2)
        XCTAssertFalse(people.person(id: b.id)!.isVisible)
        // Taking the best never means taking a bad match: a stranger is still a new person.
        let stranger = people.attach(trackId: 4, bearing: 60, now: 11, mode: .takeBest) { _ in 0.2 }
        XCTAssertEqual(stranger?.displayName, "Person 3")
    }

    func testDescriptorBankOnlyTakesGoodViews() {
        XCTAssertTrue(ReID.isGoodSample(faceConfidence: 0.95, facePixels: 120, viaBody: false))
        XCTAssertFalse(ReID.isGoodSample(faceConfidence: 0.5, facePixels: 120, viaBody: false), "an uncertain face")
        XCTAssertFalse(ReID.isGoodSample(faceConfidence: 0.95, facePixels: 20, viaBody: false), "too small to describe anyone")
        XCTAssertFalse(ReID.isGoodSample(faceConfidence: 0.95, facePixels: 120, viaBody: true), "no face in a body-held frame")
    }

    func testScenarios25To27DistantBodyAndKnown() {
        let far = at(25, 3.0)
        XCTAssertLessThan(far.faces[0].pixelHeight, 80)
        XCTAssertEqual(far.scene.speakers.first?.activity, 1)

        var labels = Set<String>(), bodyHeld = false
        run(26) { s in
            labels.formUnion(s.faces.map(\.label))
            if s.faces.first?.viaBody == true {
                bodyHeld = true
                if s.t > 3.6 && s.t < 6 { XCTAssertEqual(s.scene.speakers.first?.activity, 1, "still captioned while only their body is seen") }
            }
        }
        XCTAssertTrue(bodyHeld)
        XCTAssertEqual(labels, ["Person 1"], "the same person through face -> body -> face")

        let known = at(27, 4.0)
        XCTAssertTrue(known.faces.contains { $0.label == "Maya" && $0.colorIndex == 3 })
        XCTAssertEqual(known.scene.speakers.first { $0.label == "Maya" }?.activity, 1)
        XCTAssertNotNil(known.people.first { $0.displayName == "Maya" }?.knownPersonID)
        XCTAssertFalse(known.people.contains(where: \.isSessionPinned), "known is not pinned")
    }

    func testScenario29OverCapacityQueuesThenPromotes() {
        let full = at(29, 2.8)
        XCTAssertEqual(full.scene.presences.count, Tunables.maxPerimeterIcons)
        XCTAssertEqual(full.scene.queued.count, 3)
        XCTAssertEqual(full.sounds.count, 8, "all eight are held")
        let waiting = Set(full.scene.queued.map(\.id))
        let later = at(29, 6.5)
        XCTAssertTrue(later.scene.queued.isEmpty)
        // (Steady music/TV may have faded below relevance by now as the room baseline adapted to them.)
        XCTAssertFalse(waiting.isDisjoint(with: Set(later.scene.presences.map(\.id))), "a sound that waited now has a place")
    }

    func testScenario30ReduceMotionKeepsDirectionAndState() {
        let scenario = SpatialScenario.numbered(30)
        XCTAssertTrue(scenario.reduceMotion)
        let harness = ScenarioHarness(scenario)
        var checked = false
        var t = ScenarioHarness.step
        while t <= scenario.duration {
            let snap = harness.advance(to: t)
            t += ScenarioHarness.step
            let p = Projector(layer: nil, captureAngle: 0, hfovDeg: harness.hfovDeg, heading: snap.heading, size: size)
            let rects = Dictionary(uniqueKeysWithValues: snap.faces.map { ($0.id, p.rect(forVisionBox: $0.box)) })
            let placed = SpatialLayout.place(snap.scene.presences, proj: p, faceRects: rects, size: size, reduceMotion: true, placement: .corner)
            for (i, item) in placed.enumerated() where item.presence.sourceType == .person {
                XCTAssertEqual(item.point, SpatialLayout.cornerPoint(slot: i, size: size), "no travel: it fades in place")
                if item.presence.handoffProgress != nil { checked = true } else { XCTAssertNotNil(item.relative, "direction is still shown") }
            }
        }
        XCTAssertTrue(checked)
    }

    // MARK: aura + identity scenarios

    func testAuraScenarios() {
        XCTAssertGreaterThan(at(35, 3.0).scene.speakers[0].intensity, at(34, 3.0).scene.speakers[0].intensity + 0.5,
                             "loud speech drives the aura harder than a whisper")
        let two = at(32, 3.0).scene.speakers
        XCTAssertEqual(two.count, 2)
        XCTAssertNotEqual(two[0].colorIndex, two[1].colorIndex)
        XCTAssertTrue(two.allSatisfy { $0.activity == 1 })
        // Moving speaker: the aura and caption stay on them the whole way across.
        run(33) { s in
            if s.t > 1.5 && s.t < 5.9 {
                XCTAssertEqual(s.scene.speakers.first?.activity, 1, "t=\(s.t)")
                XCTAssertTrue(s.scene.presences.isEmpty, "t=\(s.t)")
            }
        }
        XCTAssertTrue(SpatialScenario.numbered(36).reduceMotion)
        XCTAssertEqual(at(31, 3.0).scene.speakers.first?.activity, 1)
    }

    func testIdentityScenariosKeepTheSamePersonAndColor() {
        for number in 37...51 {
            let scenario = SpatialScenario.numbered(number)
            let harness = ScenarioHarness(scenario)
            var identity: [String: (id: Int, label: String, color: Int)] = [:]
            var t = ScenarioHarness.step
            while t <= scenario.duration + 1e-9 {
                let snap = harness.advance(to: t)
                t += ScenarioHarness.step
                for face in snap.faces {
                    guard let who = harness.faceTruth[face.id], let pid = face.personId else { XCTFail("untracked face"); continue }
                    if let known = identity[who] {
                        XCTAssertEqual(pid, known.id, "scenario \(number): \(who) became a different SessionPerson at \(snap.t)")
                        XCTAssertEqual(face.label, known.label, "scenario \(number): Person N changed at \(snap.t)")
                        XCTAssertEqual(face.colorIndex, known.color, "scenario \(number): color changed at \(snap.t)")
                    } else {
                        identity[who] = (pid, face.label, face.colorIndex)
                    }
                }
            }
            XCTAssertEqual(harness.people.all.count, Set(scenario.people.map(\.name)).count, "scenario \(number): nobody was duplicated")
        }
    }

    func testIdentityScenarioDetails() {
        // Leaves while speaking: on the face, then their own notification, same color, never anonymous.
        run(45) { s in XCTAssertFalse(s.scene.presences.contains { $0.tone == .human }, "t=\(s.t)") }
        XCTAssertEqual(at(45, 5.5).scene.presences.first?.tone, .person(0))
        // Pinned leaves, pinned returns.
        XCTAssertTrue(at(46, 5.0).scene.presences.first?.isPinned ?? false)
        let back = at(47, 11.5)
        XCTAssertTrue(back.scene.speakers.contains { $0.label == "Person 1" && $0.isPinned })
        XCTAssertTrue(back.scene.presences.isEmpty)
        // Body-held tracks are still the person.
        XCTAssertTrue(at(49, 5.0).faces.contains { $0.viaBody && $0.label == "Person 1" })
        XCTAssertFalse(at(50, 7.0).faces.contains { $0.viaBody })
    }
}
