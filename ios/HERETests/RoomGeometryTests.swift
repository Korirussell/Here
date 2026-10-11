import XCTest
@testable import HERE

/// Array frame vs wearer frame, the measured calibration check, and the phone's sound-label rules.
final class RoomGeometryTests: XCTestCase {
    // MARK: the geometry

    func testAtTheArrayNothingChanges() {
        let room = RoomGeometry()
        for b in stride(from: -180.0, through: 180.0, by: 15) {
            XCTAssertEqual(abs(Geo.angDiff(room.listenerBearing(micBearing: b), b)), 0, accuracy: 1e-9)
            XCTAssertEqual(abs(Geo.angDiff(room.micBearing(ofEntityAt: b, distance: 2), b)), 0, accuracy: 1e-9)
            XCTAssertEqual(room.listenerUncertainty(micBearing: b), 0)
        }
    }

    func testTheArrayAngleIsNotTheWearersAngleOnceTheyMove() {
        // Wearer 1 m to the array's right. A talker 1.5 m straight in front of the array.
        let room = RoomGeometry(wearerDistanceM: 1, wearerBearingDeg: 90, sourceRadiusM: 1.5)
        let fromWearer = room.listenerBearing(micBearing: 0)
        XCTAssertEqual(fromWearer, -33.7, accuracy: 0.1, "the wearer sees them well to the left, not straight ahead")
        XCTAssertEqual(room.parallaxError(micBearing: 0, sourceDistance: 1.5), 33.7, accuracy: 0.1)
        // README §5: 0.5 m off, speaker 1.5 m away -> atan(0.5 / 1.5) = 18.4 deg, more than the 15 deg match angle.
        let near = RoomGeometry(wearerDistanceM: 0.5, wearerBearingDeg: 90)
        XCTAssertEqual(near.parallaxError(micBearing: 0, sourceDistance: 1.5), 18.4, accuracy: 0.1)
        XCTAssertGreaterThan(near.parallaxError(micBearing: 0, sourceDistance: 1.5), Tunables.matchDeg)
        // The farther the source, the less it matters.
        XCTAssertLessThan(near.parallaxError(micBearing: 0, sourceDistance: 6), 5)
    }

    func testRoundTripThroughBothFrames() {
        let room = RoomGeometry(wearerDistanceM: 1.2, wearerBearingDeg: 140, sourceRadiusM: 2)
        // A person the wearer sees at +20 deg, 1.8 m away: where does the array hear them...
        let mic = room.micBearing(ofEntityAt: 20, distance: 1.8)
        // ...and, knowing their true distance from the array, that converts back to where the wearer sees them.
        let w = room.wearer
        let person = CGPoint(x: w.x + 1.8 * sin(20 * .pi / 180), y: w.y + 1.8 * cos(20 * .pi / 180))
        let r = hypot(Double(person.x), Double(person.y))
        XCTAssertEqual(room.listenerBearing(micBearing: mic, radius: r), 20, accuracy: 1e-6)
    }

    func testAttributionInTheArrayFrameSurvivesTheWearerMoving() {
        // Wearer 1 m to the right of the array, facing the same way as the array. Two people in view.
        let room = RoomGeometry(wearerDistanceM: 1, wearerBearingDeg: 90)
        let seenAt: [(id: Int, label: String, bearing: Double, distance: Double)] = [(1, "Person 1", -30, 1.6), (2, "Person 2", 25, 1.6)]
        // Person 1 speaks: the array reports the direction from *its* position.
        let said = room.micBearing(ofEntityAt: -30, distance: 1.6)
        XCTAssertGreaterThan(abs(Geo.angDiff(said, -30)), Tunables.matchDeg, "the raw comparison would miss by more than the match angle")
        // Old way: compare the array's angle with where the wearer sees faces -> nobody, or the wrong person.
        let naive = seenAt.map { FaceRef(id: $0.id, label: $0.label, bearing: $0.bearing) }
        XCTAssertNotEqual(Attributor().attribute(utt: "u", bearing: said, faces: naive, now: 0).label, "Person 1")
        // New way: compare with where the array *would* hear each face.
        let inArrayFrame = seenAt.map { FaceRef(id: $0.id, label: $0.label, bearing: room.micBearing(ofEntityAt: $0.bearing, distance: $0.distance)) }
        XCTAssertEqual(Attributor().attribute(utt: "u", bearing: said, faces: inArrayFrame, now: 0).label, "Person 1")
    }

    func testUncertaintyGrowsWithDisplacementAndIsHonest() {
        let close = RoomGeometry(wearerDistanceM: 0.3, wearerBearingDeg: 90)
        let far = RoomGeometry(wearerDistanceM: 2, wearerBearingDeg: 90)
        XCTAssertLessThan(close.listenerUncertainty(micBearing: 0), far.listenerUncertainty(micBearing: 0))
        // The true bearing for a source anywhere between half and twice the assumed distance is inside the stated band.
        let room = RoomGeometry(wearerDistanceM: 1, wearerBearingDeg: 60, sourceRadiusM: 1.5)
        for mic in stride(from: -180.0, to: 180.0, by: 20) {
            let stated = room.listenerUncertainty(micBearing: mic)
            for d in [0.75, 1.0, 1.5, 2.2, 3.0] {
                let truth = room.listenerBearing(micBearing: mic, radius: d)
                XCTAssertLessThanOrEqual(abs(Geo.angDiff(truth, room.listenerBearing(micBearing: mic))), stated + 1e-6, "mic \(mic) d \(d)")
            }
        }
    }

    func testFaceDistanceFromApparentSize() {
        // A 0.21 m face filling 10% of a 60 deg tall frame subtends 6 deg: about 2 m away.
        XCTAssertEqual(RoomGeometry.faceDistance(boxHeight: 0.1, vfovDeg: 60), 2.0, accuracy: 0.05)
        XCTAssertGreaterThan(RoomGeometry.faceDistance(boxHeight: 0.05, vfovDeg: 60), RoomGeometry.faceDistance(boxHeight: 0.2, vfovDeg: 60))
        XCTAssertLessThanOrEqual(RoomGeometry.faceDistance(boxHeight: 0.0001, vfovDeg: 60), Tunables.faceDistanceMaxM)
    }

    func testUncertainGeometryStopsObjectAttachment() {
        var door = SceneObject(id: 1, cls: .door, confidence: 0.8, box: CGRect(x: 0.5, y: 0.3, width: 0.1, height: 0.3),
                               worldBearing: 0, angularWidth: 14, firstSeenAt: 0, lastSeenAt: 0, lastExpectedAt: 0)
        door.sightings = 5
        let knock = SoundTag(eventId: "k", label: .knock, category: .other, emoji: "", text: "Knock", bearing: 2, count: 1, updated: 0,
                             confidence: 0.9, micBearing: 2)
        func attached(uncertainty: Double) -> Bool {
            PresenceEngine().step(PresenceInput(now: 0, heading: 0, hfovDeg: 106, sounds: [knock], objects: [door], baseline: nil,
                                                associationUncertaintyDeg: uncertainty)).presences.first?.objectId != nil
        }
        XCTAssertTrue(attached(uncertainty: 0))
        XCTAssertTrue(attached(uncertainty: 5), "a few degrees of measured error is fine")
        XCTAssertFalse(attached(uncertainty: 25), "badly calibrated: unattached beats confidently wrong")
    }

    // MARK: calibration check

    private func check(_ pairs: [(Double, Double)]) -> CalibrationCheck {
        var c = CalibrationCheck()
        for (e, m) in pairs { c.add(expected: e, measured: m) }
        return c
    }

    func testCalibrationQualityIsMeasured() {
        XCTAssertEqual(CalibrationCheck().quality, .unknown)
        XCTAssertEqual(check([(0, 2), (45, 41)]).quality, .unknown, "two samples are not a measurement")
        let good = check([(0, 2), (45, 41), (-90, -93), (170, 174)])
        XCTAssertEqual(good.quality, .good)
        XCTAssertEqual(good.maxError, 4, accuracy: 1e-9)
        XCTAssertEqual(good.diagnosis, "Aligned.")
        XCTAssertEqual(check([(0, 9), (45, 36), (-90, -100)]).quality, .fair)
        XCTAssertEqual(check([(0, 30), (45, 80), (-90, -60)]).quality, .bad)
        // Errors are compared across the wrap: +178 measured for -178 expected is 4 deg off, not 356.
        XCTAssertEqual(check([(-178, 178), (178, -179), (180, -178)]).quality, .good)
    }

    func testCalibrationDiagnosisTellsErrorTypesApart() {
        let offset = check([(0, 22), (90, 111), (-90, -69), (180, -158)])
        XCTAssertTrue(offset.diagnosis.hasPrefix("Constant offset of +2"), offset.diagnosis)
        XCTAssertEqual(offset.suggestedHeadingCorrection, 21.5, accuracy: 1.5)
        XCTAssertLessThan(offset.jitter, 3)

        XCTAssertTrue(check([(30, -31), (90, -88), (-60, 62), (150, -149)]).diagnosis.hasPrefix("Mirrored"))
        XCTAssertTrue(check([(0, 178), (90, -91), (-90, 92)]).diagnosis.hasPrefix("Reversed by 180"))
        XCTAssertTrue(check([(10, 40), (12, -15), (9, 30), (11, -20)]).diagnosis.hasPrefix("Inconsistent from one direction"))
        // Error that changes with direction: what a displaced wearer looks like.
        let room = RoomGeometry(wearerDistanceM: 1, wearerBearingDeg: 90)
        let parallax = check([-60.0, 0, 60, 150].map { ($0, room.listenerBearing(micBearing: $0)) })
        XCTAssertTrue(parallax.diagnosis.hasPrefix("Error changes with direction"), parallax.diagnosis)
    }

    // MARK: sound labels on the phone

    func testWeakGuessBecomesUnknownNotAWrongIcon() {
        XCTAssertEqual(SoundContinuity.confidentLabel(.dogBarking, confidence: 0.31), .unknown, "keyboard typing scoring 0.31 as 'dog'")
        XCTAssertEqual(SoundContinuity.confidentLabel(.dogBarking, confidence: 0.7), .dogBarking)
        XCTAssertEqual(SoundContinuity.confidentLabel(.doorbell, confidence: 0.27), .doorbell, "impacts legitimately score low: the laptop's own bar")
        XCTAssertEqual(SoundContinuity.confidentLabel(.phoneRinging, confidence: 0.3), .unknown)
        XCTAssertEqual(SoundContinuity.confidentLabel(.unknown, confidence: 0.1), .unknown)
        // The unknown it becomes is the question-mark notification that already exists.
        XCTAssertEqual(SoundIcon.symbol(label: .unknown), "questionmark")
        XCTAssertEqual(Relevance.tone(label: .unknown), .neutral)
    }

    func testOneStrayWindowDoesNotRenameAContinuingSound() {
        var votes: [SoundLabel: Double] = [:]
        var label = SoundContinuity.vote(&votes, label: .unknown, confidence: 0.3, current: nil)
        for _ in 0..<4 { label = SoundContinuity.vote(&votes, label: .unknown, confidence: 0.3, current: label) }
        label = SoundContinuity.vote(&votes, label: .dogBarking, confidence: 0.55, current: label)
        XCTAssertEqual(label, .unknown, "one window is not enough evidence")
        // Sustained evidence does change it.
        for _ in 0..<5 { label = SoundContinuity.vote(&votes, label: .dogBarking, confidence: 0.8, current: label) }
        XCTAssertEqual(label, .dogBarking)
        // And then it holds through a stray window the other way.
        XCTAssertEqual(SoundContinuity.vote(&votes, label: .unknown, confidence: 0.3, current: label), .dogBarking)
    }

    func testOneSourceStaysOneNotificationAcrossLabels() {
        let typing = SoundTag(eventId: "e1", label: .unknown, category: .other, emoji: "", text: "Unknown sound", bearing: 40, count: 1,
                              updated: 10, confidence: 0.3)
        XCTAssertEqual(SoundContinuity.sourceKey(forEvent: "e2", label: .dogBarking, bearing: 44, among: [typing], now: 11), "e1",
                       "a new label from the same place moments later is the same source")
        XCTAssertEqual(SoundContinuity.sourceKey(forEvent: "e2", label: .dogBarking, bearing: -90, among: [typing], now: 11), "e2")
        XCTAssertEqual(SoundContinuity.sourceKey(forEvent: "e2", label: .dogBarking, bearing: 44, among: [typing], now: 30), "e2")
        XCTAssertEqual(SoundContinuity.sourceKey(forEvent: "e3", label: .alarm, bearing: 44, among: [typing], now: 11), "e3",
                       "a warning is never folded into an ordinary sound")
    }
}
