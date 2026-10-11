import CoreML
import UIKit
import Vision
import XCTest
@testable import HERE

/// Quiet objects: detection-independent logic for SceneObject identity, memory and reacquisition,
/// and for attaching a sound to a known object only when the evidence supports it.
final class SceneObjectTests: XCTestCase {
    private func obs(_ cls: ObjectClass, _ bearing: Double, width: Double = 14, confidence: Double = 0.8) -> ObjectObservation {
        ObjectObservation(cls: cls, confidence: confidence, box: CGRect(x: 0.4, y: 0.3, width: 0.1, height: 0.3),
                          bearing: bearing, angularWidth: width)
    }

    /// A camera looking at `heading` with a 106 degree view.
    private func view(_ heading: Double) -> (Double) -> Bool { { abs(Geo.relative($0, heading: heading)) < 53 } }

    private func known(_ scene: SceneObjects, _ things: [ObjectObservation], heading: Double = 0, from t: Double = 0, scans: Int = 3) {
        for i in 0..<scans { scene.observe(things, now: t + Double(i), inView: view(heading)) }
    }

    func testVocabularyIsBroadSemanticAndReal() {
        XCTAssertGreaterThanOrEqual(ObjectClass.visionVocabulary.count, 150)
        XCTAssertGreaterThanOrEqual(ObjectClass.all.count, 120, "distinct classes after folding variants together")
        for name in ["door", "phone", "car", "chair", "table", "laptop", "monitor", "television", "backpack", "cup", "bottle", "book",
                     "sofa", "bed", "bicycle", "dog", "cat", "refrigerator", "microwave", "oven", "sink", "cabinet", "window",
                     "keyboard", "mouse", "clock", "trash_can"] {
            XCTAssertTrue(ObjectClass.all.contains(ObjectClass(name: name)), "\(name) should be recognisable")
        }
        XCTAssertFalse(ObjectClass.all.contains(ObjectClass(name: "object")), "never a generic 'object'")
        XCTAssertEqual(ObjectClass(visionIdentifier: "armchair"), .chair)
        XCTAssertEqual(ObjectClass(visionIdentifier: "mug"), .cup)
        XCTAssertNil(ObjectClass(visionIdentifier: "outdoor"), "a scene label is not an object")
        XCTAssertNil(ObjectClass(visionIdentifier: "people"))
        for cls in ObjectClass.all { XCTAssertNotNil(UIImage(systemName: cls.symbol), "\(cls.symbol) is not an SF Symbol") }
        // Every name we accept must be one Apple's classifier actually produces.
        guard let supported = try? VNClassifyImageRequest().supportedIdentifiers(), !supported.isEmpty else { return }
        for id in ObjectClass.visionVocabulary { XCTAssertTrue(supported.contains(id), "\(id) is not a Vision label") }
    }

    func testDetectorLabelsMapToClassesAndBaselineObjectsAreCovered() {
        XCTAssertEqual(ObjectClass(detectorLabel: "cell phone"), .phone)
        XCTAssertEqual(ObjectClass(detectorLabel: "tvmonitor"), .television)
        XCTAssertEqual(ObjectClass(detectorLabel: "diningtable"), .table)
        XCTAssertEqual(ObjectClass(detectorLabel: "Dining Table"), .table)
        XCTAssertEqual(ObjectClass(detectorLabel: "car"), .car)
        XCTAssertEqual(ObjectClass(detectorLabel: "chair"), .chair)
        XCTAssertNil(ObjectClass(detectorLabel: "person"), "people are SessionPersons, not objects")
        // The baseline: phone, car, chair, TV, table from the detector; door from the classifier.
        for cls in [ObjectClass.phone, .car, .chair, .television, .table] {
            XCTAssertTrue(ObjectClass.detectorClasses.contains(cls.name), "\(cls.name) gets a tight box")
        }
        XCTAssertFalse(ObjectClass.detectorClasses.contains("door"), "no door in the detector's classes...")
        XCTAssertEqual(ObjectClass(visionIdentifier: "door"), .door, "...so the classifier keeps finding doors")
    }

    func testTightBoxesSeparateNeighboursThatRegionsCannot() {
        func tight(_ bearing: Double) -> ObjectObservation {
            ObjectObservation(cls: .chair, confidence: 0.9, box: CGRect(x: 0.4, y: 0.3, width: 0.05, height: 0.2),
                              bearing: bearing, angularWidth: 6, precise: true)
        }
        let precise = SceneObjects()
        for t in 0..<3 { precise.observe([tight(-8), tight(8)], now: Double(t), inView: view(0)) }
        XCTAssertEqual(precise.all.count, 2, "two chairs 16 degrees apart are two chairs when each has its own box")
        XCTAssertTrue(precise.all.allSatisfy(\.precise))
        // A coarse region sighting refreshes a boxed object without replacing its tight box.
        let box = precise.all[0].box
        precise.observe([ObjectObservation(cls: .chair, confidence: 0.5, box: CGRect(x: 0, y: 0, width: 0.34, height: 1),
                                           bearing: precise.all[0].worldBearing, angularWidth: 6)], now: 4, inView: view(0))
        XCTAssertEqual(precise.all[0].box, box)
        XCTAssertTrue(precise.all[0].precise)
    }

    func testBundledDetectorLoadsRunsAndItsLabelsAreMapped() throws {
        let scanner = ObjectScanner()
        guard let detector = scanner.detector else {
            throw XCTSkip("no detection model in HERE/Models (see its README): the classifier-only path is in use")
        }
        XCTAssertEqual(scanner.detectorName?.uppercased().hasPrefix("YOLO"), true)
        // It runs through Vision and returns object observations (an empty gray frame: none expected).
        var buffer: CVPixelBuffer?
        CVPixelBufferCreate(nil, 416, 416, kCVPixelFormatType_32BGRA, [kCVPixelBufferIOSurfacePropertiesKey: [:]] as CFDictionary, &buffer)
        let pb = try XCTUnwrap(buffer)
        CVPixelBufferLockBaseAddress(pb, [])
        memset(CVPixelBufferGetBaseAddress(pb), 128, CVPixelBufferGetDataSize(pb))
        CVPixelBufferUnlockBaseAddress(pb, [])
        let request = VNCoreMLRequest(model: detector)
        XCTAssertNoThrow(try VNImageRequestHandler(cvPixelBuffer: pb, orientation: .up, options: [:]).perform([request]))
        XCTAssertNotNil(request.results as? [VNRecognizedObjectObservation], "a detector with boxes, not a classifier")
        // Every label the model can emit, other than "person", maps to one of our classes.
        let labels = (detector.value(forKey: "model") as? MLModel)?.modelDescription.classLabels as? [String] ?? []
        for label in labels where label != "person" {
            XCTAssertNotNil(ObjectClass(detectorLabel: label), "unmapped detector label: \(label)")
        }
    }

    func testAQuietObjectExistsWithoutAnySound() {
        let scene = SceneObjects()
        scene.observe([obs(.door, 38)], now: 0, inView: view(0))
        XCTAssertEqual(scene.all.count, 1)
        XCTAssertFalse(scene.all[0].isConfirmed, "one sighting is not yet believed")
        scene.observe([obs(.door, 39)], now: 1, inView: view(0))
        XCTAssertEqual(scene.all.count, 1, "the same door, not a second one")
        XCTAssertTrue(scene.all[0].isConfirmed)
        XCTAssertEqual(scene.all[0].cls, .door)
        XCTAssertEqual(scene.all[0].state(now: 1, inView: true), .visible)
        XCTAssertNil(scene.all[0].associatedSoundID)
        XCTAssertNil(scene.all[0].approximateDistance, "distance is not measured, and not made up")
    }

    func testIdentityIsStableAcrossNoisyScans() {
        let scene = SceneObjects()
        // Strip-level localization wobbles by many degrees from scan to scan.
        for (i, b) in [38.0, 46, 33, 41, 30, 44].enumerated() { scene.observe([obs(.door, b)], now: Double(i), inView: view(0)) }
        XCTAssertEqual(scene.all.map(\.id), [1])
        XCTAssertEqual(scene.all[0].worldBearing, 38, accuracy: 6, "smoothed toward where it really is")
        XCTAssertEqual(scene.all[0].sightings, 6)
    }

    func testDifferentClassOrPlaceIsADifferentObject() {
        XCTAssertEqual(ObjectReID.score(observation: obs(.chair, 38), object: SceneObject(id: 1, cls: .door, confidence: 0.8, box: nil, worldBearing: 38, angularWidth: 14, firstSeenAt: 0, lastSeenAt: 0, lastExpectedAt: 0)), 0)
        let scene = SceneObjects()
        known(scene, [obs(.chair, -40), obs(.chair, 45)])
        XCTAssertEqual(scene.all.count, 2, "two chairs far apart are two chairs")
        known(scene, [obs(.chair, -38), obs(.chair, 47)], from: 5)
        XCTAssertEqual(Set(scene.all.map(\.id)), [1, 2])
        // Size and appearance refine the match; bearing still dominates.
        let door = scene.all[0]
        let same = ObjectReID.score(observation: obs(door.cls, door.worldBearing, width: door.angularWidth), object: door)
        XCTAssertGreaterThan(same, ObjectReID.score(observation: obs(door.cls, door.worldBearing, width: 60), object: door))
        XCTAssertGreaterThan(ObjectReID.score(observation: obs(door.cls, door.worldBearing + 12), object: door, visualSimilarity: 0.95),
                             ObjectReID.score(observation: obs(door.cls, door.worldBearing + 12), object: door, visualSimilarity: 0.1))
    }

    func testLookingAwayKeepsTheObjectAndLookingBackReacquiresIt() {
        let scene = SceneObjects()
        known(scene, [obs(.door, 38), obs(.chair, -5)])
        let doorId = scene.all.first { $0.cls == .door }!.id
        // The wearer turns away for a long time. Nothing is seen; nothing is forgotten.
        for t in stride(from: 3.0, through: 120, by: 1) { scene.observe([], now: t, inView: view(-150)) }
        XCTAssertEqual(scene.all.count, 2)
        XCTAssertEqual(scene.all.first { $0.id == doorId }?.state(now: 120, inView: false), .dormant)
        // They turn back.
        scene.observe([obs(.door, 41), obs(.chair, -3)], now: 121, inView: view(0))
        let door = scene.all.first { $0.cls == .door }!
        XCTAssertEqual(door.id, doorId, "the same SceneObject, not a new one")
        XCTAssertEqual(scene.all.count, 2)
        XCTAssertEqual(door.state(now: 121.5, inView: true), .reacquired)
        XCTAssertEqual(door.state(now: 121 + Tunables.objectReacquiredFlagS + 0.1, inView: true), .visible)
    }

    func testObjectThatShouldBeVisibleButIsGoneIsEventuallyDropped() {
        let scene = SceneObjects()
        known(scene, [obs(.backpack, 10)])
        scene.observe([], now: 5, inView: view(0))
        XCTAssertEqual(scene.all[0].state(now: 5, inView: true), .temporarilyLost)
        scene.observe([], now: 2 + Tunables.objectGoneS + 1, inView: view(0))
        XCTAssertTrue(scene.all.isEmpty, "someone picked it up and left")
        // A single unconfirmed sighting (a misclassification) goes quickly.
        scene.observe([obs(.cup, 0)], now: 100, inView: view(0))
        scene.observe([], now: 100 + Tunables.objectUnconfirmedGoneS + 1, inView: view(0))
        XCTAssertTrue(scene.all.isEmpty)
    }

    // MARK: AudioSource -> visual entity (spatial correspondence first)

    private func object(_ id: Int, _ cls: ObjectClass, _ bearing: Double, sightings: Int = 5, seenAt: Double = 0) -> SceneObject {
        var o = SceneObject(id: id, cls: cls, confidence: 0.8, box: CGRect(x: 0.6, y: 0.3, width: 0.1, height: 0.3),
                            worldBearing: bearing, angularWidth: 14, firstSeenAt: 0, lastSeenAt: seenAt, lastExpectedAt: seenAt)
        o.sightings = sightings
        return o
    }

    /// Fuse a sound at `bearing` with the scene, the wearer facing `heading`.
    private func fuse(at bearing: Double?, _ objects: [SceneObject], faces: [TrackedFace] = [], heading: Double = 0,
                      label: SoundLabel = .other, text: String = "Sound", prior: EntityRef? = nil) -> EntityRef? {
        let c = Fusion.candidates(objects: objects, faces: faces, heading: heading, hfovDeg: 106, now: 0, prior: prior) {
            Fusion.compatibility(label: label, text: text, object: $0)
        }
        return Fusion.associate(bearing: bearing, candidates: c)?.ref
    }

    func testSoundAttachesByDirectionWithoutKnowingWhatItIs() {
        let room = [object(12, .door, 40), object(3, .chair, -21), object(9, .phone, 61)]
        XCTAssertEqual(fuse(at: 39, room), .object(12), "an unknown sound from where the door is: the door")
        XCTAssertEqual(fuse(at: 63, room), .object(9))
        XCTAssertEqual(fuse(at: 39, room, label: .loudBang, text: "Loud bang"), .object(12), "no 'doors make knocks' rule needed")
        XCTAssertNil(fuse(at: -120, room), "nothing known in that direction")
        XCTAssertNil(fuse(at: nil, room), "no direction, no entity")
        XCTAssertNil(fuse(at: 39, [object(12, .door, 40, sightings: 1)]), "an unconfirmed object is not evidence")
        // Remembered objects count; ones in view right now count for more.
        let dormant = Fusion.candidates(objects: room, faces: [], heading: -150, hfovDeg: 106, now: 0, prior: nil) { _ in 0 }
        let visible = Fusion.candidates(objects: room, faces: [], heading: 30, hfovDeg: 106, now: 0, prior: nil) { _ in 0 }
        XCTAssertGreaterThan(Fusion.score(visible.first { $0.ref == .object(12) }!, bearing: 39),
                             Fusion.score(dormant.first { $0.ref == .object(12) }!, bearing: 39))
    }

    func testSeveralThingsInTheSameDirectionStayUncertain() {
        let crowded = [object(1, .door, 95), object(2, .chair, 105), object(3, .backpack, 100)]
        XCTAssertNil(fuse(at: 100, crowded), "it could be any of them")
        // A fitting kind of sound is allowed to break the tie; it is never required.
        XCTAssertEqual(fuse(at: 100, crowded, label: .knock, text: "Knock"), .object(1))
        // So is having owned this source a moment ago: an attribution doesn't flip.
        XCTAssertEqual(fuse(at: 100, crowded, prior: .object(2)), .object(2))
        // A person standing in that direction is also a candidate: the object is not assumed.
        let someone = TrackedFace(id: 7, label: "Person 1", colorIndex: 0, box: .zero, bearing: 41, personId: 1)
        XCTAssertNil(fuse(at: 40, [object(12, .door, 40)], faces: [someone], heading: 30))
        XCTAssertEqual(fuse(at: 40, [object(12, .door, 40)], faces: [someone], heading: 30, label: .knock, text: "Knock"), .object(12))
    }

    func testSemanticHintIsOptional() {
        XCTAssertEqual(Fusion.compatibility(label: .knock, text: "", object: .door), 1)
        XCTAssertEqual(Fusion.compatibility(label: .phoneRinging, text: "", object: .phone), 1)
        XCTAssertEqual(Fusion.compatibility(label: .other, text: "Car horn", object: .car), 1)
        XCTAssertEqual(Fusion.compatibility(label: .other, text: "Sound", object: .door), 0)
        // Zero compatibility does not prevent a spatial match...
        XCTAssertEqual(fuse(at: 40, [object(12, .door, 40)], label: .phoneRinging, text: "Phone ringing"), .object(12))
        // ...it just means the kind of sound adds nothing to the score.
        let plain = Fusion.candidates(objects: [object(12, .door, 40)], faces: [], heading: 0, hfovDeg: 106, now: 0, prior: nil) { _ in 0 }[0]
        var fitting = plain
        fitting.semantic = 1
        XCTAssertGreaterThan(Fusion.score(fitting, bearing: 40), Fusion.score(plain, bearing: 40))
    }

    func testEngineLabelsAnAttributedSoundAndOnlyPlacesItOnAVisibleObject() {
        let door = object(12, .door, 48)
        let sound = SoundTag(eventId: "x", label: .other, category: .other, emoji: "", text: "Sound", bearing: 51, count: 1,
                             updated: 0, confidence: 0.9)
        func step(heading: Double, _ engine: PresenceEngine = PresenceEngine()) -> SpatialScene {
            engine.step(PresenceInput(now: 0, heading: heading, hfovDeg: 106, sounds: [sound], objects: [door],
                                      sources: [Source(id: 0, bearingDeg: 51, energy: 0.7, speech: false, confidence: 0.8)],
                                      baseline: 0.05))
        }
        let away = step(heading: -100).presences[0]
        XCTAssertEqual(away.objectId, 12)
        XCTAssertEqual(away.icon, "questionmark", "the sound is still unknown; what's there is not")
        XCTAssertEqual(away.label, "Sound · Door", "said in words")
        XCTAssertNil(away.objectBox, "the door is not in view: it stays in the top-right")
        let facing = step(heading: 30)
        XCTAssertNotNil(facing.presences[0].objectBox, "in view and recently seen: it sits on the door")
        XCTAssertEqual(facing.objectLinks, [12: "snd:x"])
        // On the object it is no longer a top-right notification.
        let placed = SpatialLayout.place(facing.presences, proj: Projector(layer: nil, captureAngle: 0, hfovDeg: 106, heading: 30,
                                                                           size: CGSize(width: 874, height: 402)),
                                         faceRects: [:], size: CGSize(width: 874, height: 402), reduceMotion: false, placement: .corner)
        XCTAssertEqual(placed[0].anchor, .object)
        // Without a known object there, it is just a sound with a direction.
        let bare = PresenceEngine().step(PresenceInput(now: 0, heading: 0, hfovDeg: 106, sounds: [sound], baseline: nil)).presences[0]
        XCTAssertNil(bare.objectId)
        XCTAssertEqual(bare.label, "Sound")
    }

    // MARK: scenarios 52-57

    private func at(_ number: Int, _ t: Double) -> ScenarioHarness.Snapshot { ScenarioHarness(.numbered(number)).advance(to: t) }

    func testScenario52QuietObjectsAreKnownAndNothingIsDrawn() {
        let snap = at(52, 4.0)
        XCTAssertEqual(Set(snap.objects.filter(\.isConfirmed).map(\.cls.name)), ["door", "chair", "phone"])
        XCTAssertTrue(snap.scene.isIdle, "scene awareness without a single overlay")
        XCTAssertTrue(snap.sounds.isEmpty)
    }

    func testScenario53LookAwayAndBackKeepsTheSameObject() {
        let before = at(53, 2.2).objects.first { $0.cls == .door }!
        let away = at(53, 6.0)
        XCTAssertEqual(away.objects.count, 2, "still remembered while out of view")
        let back = at(53, 10.5).objects.first { $0.cls == .door }!
        XCTAssertEqual(back.id, before.id)
        XCTAssertEqual(at(53, 10.5).objects.count, 2)
        XCTAssertEqual(back.state(now: 10.5, inView: true), .reacquired)
    }

    func testScenario54KnockAtTheKnownDoor() {
        let offscreen = at(54, 6.0)
        let knock = offscreen.scene.presences.first!
        XCTAssertEqual(knock.label, "Knock · Door")
        XCTAssertNil(knock.objectBox)
        let facing = at(54, 10.0)
        XCTAssertNotNil(facing.scene.presences.first?.objectBox, "turned back: indicated on the door")
        XCTAssertEqual(facing.objects.first { $0.cls == .door }?.associatedSoundID, "snd:knock")
        XCTAssertNil(facing.objects.first { $0.cls == .chair }?.associatedSoundID)
    }

    func testScenario55PhoneRinging() {
        let snap = at(55, 5.0)
        let ring = snap.scene.presences.first!
        XCTAssertEqual(snap.objects.first { $0.id == ring.objectId }?.cls, .phone, "the phone, not the cup beside it")
        XCTAssertNotNil(ring.objectBox)
    }

    func testScenarios56And57NoForcedAssociation() {
        let unknown = at(56, 5.0)
        XCTAssertEqual(unknown.objects.filter(\.isConfirmed).count, 3)
        XCTAssertEqual(unknown.scene.presences.first?.icon, "questionmark")
        XCTAssertNil(unknown.scene.presences.first?.objectId, "several things it could be: it stays unattached")
        let voice = at(57, 5.5)
        XCTAssertEqual(voice.scene.presences.first?.tone, .human)
        XCTAssertNil(voice.scene.presences.first?.objectId, "speech belongs to people, never to a chair")
        XCTAssertTrue(voice.objects.allSatisfy { $0.associatedSoundID == nil })
    }

    func testScenarios58To62() {
        let chairs = at(58, 4.0).objects
        XCTAssertEqual(chairs.map(\.cls), [.chair, .chair])
        XCTAssertEqual(Set(chairs.map(\.id)).count, 2, "two chairs, two identities")

        // Known before the sound begins; an unknown sound attaches by direction once it happens.
        XCTAssertTrue(at(59, 4.5).objects.contains { $0.cls == .door && $0.isConfirmed })
        let away = at(59, 6.0).scene.presences[0]
        XCTAssertEqual(away.label, "Sound · Door")
        XCTAssertEqual(away.icon, "questionmark")
        XCTAssertNil(away.objectBox)
        XCTAssertNotNil(at(59, 10.0).scene.presences.first?.objectBox, "attached to the door once the wearer turns toward it")

        let nothing = at(60, 4.5).scene.presences[0]
        XCTAssertNil(nothing.objectId, "a knock from where no known object is: a direction only")
        XCTAssertEqual(nothing.label, "Knock")

        let full = at(61, 6.0)
        XCTAssertEqual(full.objects.filter(\.isConfirmed).count, 4)
        func entity(_ id: String) -> String? {
            full.scene.presences.first { $0.id == id }?.objectId.flatMap { o in full.objects.first { $0.id == o }?.cls.name }
        }
        XCTAssertEqual(entity("snd:knock"), "door")
        XCTAssertEqual(entity("snd:ring"), "phone", "next to the table, but ringing fits the phone")
        XCTAssertEqual(full.scene.speakers.first?.activity, 1, "and the person speaking is a person")

        // Half a minute of nothing happening: everything is still known, and nothing is on screen.
        var idle = true
        let harness = ScenarioHarness(.numbered(62))
        var t = 1.0
        while t <= 30 { if !harness.advance(to: t).scene.isIdle { idle = false }; t += 1 }
        XCTAssertTrue(idle, "a rich scene model with no visible clutter")
        XCTAssertEqual(Set(harness.snapshot.objects.filter(\.isConfirmed).map(\.cls.name)), ["chair", "table", "phone", "door", "laptop", "car"])
        XCTAssertEqual(harness.snapshot.objects.count, 6, "stationary for 30 s: the same six objects, no duplicates")
    }

    // MARK: sounding things glow

    private func scene(_ sounds: [SoundTag], objects: [SceneObject], energy: Double = 0.7, heading: Double = 30, now: Double = 0,
                       alerts: [AlertState] = [], engine: PresenceEngine = PresenceEngine()) -> SpatialScene {
        engine.step(PresenceInput(now: now, heading: heading, hfovDeg: 106, alerts: alerts, sounds: sounds, objects: objects,
                                  sources: sounds.compactMap { s in s.bearing.map { Source(id: 0, bearingDeg: $0, energy: energy, speech: false, confidence: 0.8) } }
                                    + [Source(id: 9, bearingDeg: 0, energy: 0.05, speech: false, confidence: 0.5)],
                                  baseline: 0.05))
    }

    private func tag(_ id: String, _ label: SoundLabel, _ text: String, at bearing: Double, updated: Double = 0) -> SoundTag {
        SoundTag(eventId: id, label: label, category: .other, emoji: "", text: text, bearing: bearing, count: 1, updated: updated, confidence: 0.9)
    }

    func testSilentObjectsDoNotGlowAndASoundingOneDoes() {
        let phone = object(9, .phone, 40), door = object(12, .door, 0)
        XCTAssertTrue(scene([], objects: [phone, door]).objectAuras.isEmpty, "known and silent: no glow")
        XCTAssertTrue(scene([], objects: [phone, door]).isIdle)
        let ringing = scene([tag("r", .phoneRinging, "Phone ringing", at: 41)], objects: [phone, door])
        XCTAssertEqual(ringing.objectAuras.map(\.objectId), [9], "the phone glows; the door does not")
        let aura = ringing.objectAuras[0]
        XCTAssertEqual(aura.tone, .environment, "the same green as its notification")
        XCTAssertEqual(aura.tone, ringing.presences[0].tone)
        XCTAssertEqual(aura.soundId, "snd:r")
        XCTAssertEqual(ringing.presences[0].label, "Phone ringing", "the words already name the source: not said twice")
        XCTAssertGreaterThanOrEqual(aura.confidence, Tunables.auraAttachConfidence)
        XCTAssertFalse(aura.releasing)
    }

    func testAuraColorFollowsTheSound() {
        XCTAssertEqual(SoundContinuity.objectAuraTone(.environment), .environment)
        XCTAssertEqual(SoundContinuity.objectAuraTone(.danger), .danger)
        XCTAssertEqual(SoundContinuity.objectAuraTone(.human), .human)
        XCTAssertEqual(SoundContinuity.objectAuraTone(.neutral), .environment, "an unnamed sound with a known source glows as environmental")
        // An unknown sound attributed to the door: question-mark icon, green glow on the door.
        let s = scene([tag("x", .other, "Sound", at: 1)], objects: [object(12, .door, 0)], heading: 0)
        XCTAssertEqual(s.presences[0].icon, "questionmark")
        XCTAssertEqual(s.objectAuras.first?.tone, .environment)
        // A warning glows red and harder than an ordinary sound of the same intensity.
        XCTAssertGreaterThan(SoundContinuity.objectAuraOpacity(intensity: 0.5, life: 1, danger: true, crowded: false),
                             SoundContinuity.objectAuraOpacity(intensity: 0.5, life: 1, danger: false, crowded: false))
    }

    func testAuraStrengthTracksIntensityNotLifetime() {
        var last = 0.0
        for i in 0...10 {
            let o = SoundContinuity.objectAuraOpacity(intensity: Double(i) / 10, life: 1, danger: false, crowded: false)
            XCTAssertGreaterThanOrEqual(o, last)
            XCTAssertLessThanOrEqual(o, Tunables.auraMaxOpacity)
            last = o
        }
        XCTAssertEqual(SoundContinuity.objectAuraOpacity(intensity: 1, life: 0, danger: true, crowded: false), 0, "the sound is over: no glow")
        XCTAssertLessThan(SoundContinuity.objectAuraPulse(intensity: 1, reduceMotion: false), 0.1, "never a size jump")
        XCTAssertEqual(SoundContinuity.objectAuraPulse(intensity: 1, reduceMotion: true), 0)
        // A loud ring and a quiet ring that stop together lose their glow together.
        func goneAt(_ energy: Double) -> Double {
            let engine = PresenceEngine()
            let phone = object(9, .phone, 40, seenAt: 100)          // stays "visible" throughout
            var t = 0.0
            _ = scene([tag("r", .phoneRinging, "Phone ringing", at: 41)], objects: [phone], energy: energy, engine: engine)
            while t < 10 {
                t += 0.05
                if scene([tag("r", .phoneRinging, "Phone ringing", at: 41)], objects: [phone], energy: 0.05, now: t, engine: engine)
                    .objectAuras.isEmpty { return t }
            }
            return .infinity
        }
        XCTAssertEqual(goneAt(0.9), goneAt(0.2), accuracy: 1e-9)
        XCTAssertGreaterThan(scene([tag("r", .phoneRinging, "Phone ringing", at: 41)], objects: [object(9, .phone, 40)], energy: 0.9).objectAuras[0].opacity,
                             scene([tag("r", .phoneRinging, "Phone ringing", at: 41)], objects: [object(9, .phone, 40)], energy: 0.12).objectAuras[0].opacity)
    }

    func testNoFakeAura() {
        let room = [object(12, .door, 0), object(3, .chair, 10), object(4, .backpack, 5)]
        XCTAssertTrue(scene([tag("x", .other, "Sound", at: 5)], objects: room, heading: 0).objectAuras.isEmpty, "several candidates: nothing glows")
        XCTAssertTrue(scene([tag("x", .other, "Sound", at: -40)], objects: room, heading: 0).objectAuras.isEmpty, "nothing known there: nothing glows")
        // Attributed to a remembered object that is out of view: named in the notification, no glow.
        let away = scene([tag("k", .knock, "Knock", at: 1)], objects: [object(12, .door, 0)], heading: -120)
        XCTAssertEqual(away.presences[0].objectId, 12)
        XCTAssertTrue(away.objectAuras.isEmpty)
        // A weak, low-confidence attribution: still only a notification.
        var faint = object(12, .door, 0)
        faint.confidence = 0.1
        faint.sightings = 2
        let unsure = scene([tag("x", .other, "Sound", at: 16)], objects: [faint], heading: 0)
        XCTAssertTrue(unsure.objectAuras.isEmpty)
    }

    func testSeveralThingsGlowAtOnceAndWarningsStayStrongest() {
        let room = [object(1, .phone, -30), object(2, .dog, 0), object(3, .television, 30), object(4, ObjectClass(name: "alarm"), 45)]
        let alarm = AlertState(alertId: "a", kind: .alarm, label: "Alarm", emoji: "", priority: 3, bearing: 45, active: true, count: 1,
                               updated: 0, lastBuzz: 0)
        let s = scene([tag("r", .phoneRinging, "Phone ringing", at: -30), tag("b", .dogBarking, "Dog barking", at: 0), tag("t", .tv, "TV", at: 30)],
                      objects: room, heading: 5, alerts: [alarm])
        XCTAssertEqual(Set(s.objectAuras.map(\.objectId)), [1, 2, 3, 4], "nothing is serialized: all four glow")
        let red = s.objectAuras.first { $0.objectId == 4 }!
        XCTAssertEqual(red.tone, .danger)
        for other in s.objectAuras where other.objectId != 4 {
            XCTAssertEqual(other.tone, .environment)
            XCTAssertGreaterThan(red.opacity, other.opacity, "crowded view: ordinary glows are dimmed, the warning is not")
        }
    }

    func testGlowScenarios() {
        func glow(_ n: Int, _ t: Double) -> [ObjectAura] { at(n, t).scene.objectAuras }
        XCTAssertEqual(glow(63, 5.0).map(\.cls.name), ["dog"])
        XCTAssertEqual(glow(64, 5.0).map(\.cls.name), ["television"])
        XCTAssertEqual(glow(65, 5.0).first?.tone, .danger)
        // Three sources at once: a person in their color, the phone green, the alarm red.
        let three = at(66, 6.0)
        XCTAssertEqual(Set(three.scene.objectAuras.map(\.cls.name)), ["phone", "alarm"])
        XCTAssertEqual(three.scene.speakers.first?.activity, 1)
        // Hidden while ringing: no glow, back to the top-right; then the same phone glows again.
        let before = at(67, 4.0), hidden = at(67, 8.0), after = at(67, 11.0)
        XCTAssertEqual(before.scene.objectAuras.count, 1)
        XCTAssertTrue(hidden.scene.objectAuras.isEmpty)
        XCTAssertEqual(hidden.scene.presences.count, 1, "still ringing: a top-right notification again")
        XCTAssertNil(hidden.scene.presences[0].objectBox)
        XCTAssertEqual(after.scene.objectAuras.first?.objectId, before.scene.objectAuras.first?.objectId, "the same SceneObject")
        // Ringing stops: the glow is gone, the phone is still known, the screen is clean.
        XCTAssertEqual(at(68, 4.0).scene.objectAuras.count, 1)
        let quiet = at(68, 10.0)
        XCTAssertTrue(quiet.scene.isIdle)
        XCTAssertTrue(quiet.objects.contains { $0.cls == .phone })
        // Silent the whole time (scenario 62): never a glow.
        XCTAssertTrue(at(62, 20.0).scene.objectAuras.isEmpty)
    }
}
