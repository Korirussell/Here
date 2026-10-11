import XCTest
@testable import HERE

/// SPEC §13 with synthetic faces and bearings (same cases as laptop/legacy fusion tests).
final class AttributionTests: XCTestCase {
    let p1 = FaceRef(id: 1, label: "Person 1", bearing: 32)
    let p2 = FaceRef(id: 2, label: "Person 2", bearing: -30)

    func testNearestFaceWithin15Degrees() {
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 38.5, faces: [p1, p2], now: 0), .person(id: 1, label: "Person 1"))
        XCTAssertEqual(a.attribute(utt: "u2", bearing: -36, faces: [p1, p2], now: 1), .person(id: 2, label: "Person 2"))
    }

    func testBeyond15DegreesIsOffCamera() {
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 90, faces: [p1, p2], now: 0), .offCamera)
        XCTAssertEqual(a.attribute(utt: "u2", bearing: 180, faces: [], now: 0), .offCamera)
    }

    func testCloseFacesPreferTheRecentSpeaker() {
        let a = Attributor()
        let near1 = FaceRef(id: 1, label: "Person 1", bearing: 10)
        let near2 = FaceRef(id: 2, label: "Person 2", bearing: 14)
        // Person 2 spoke recently (clearly theirs).
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 16, faces: [near1, near2], now: 1).label, "Person 2")
        // Now a bearing exactly between them: distances 2 vs 2 -> the recent speaker wins.
        XCTAssertEqual(a.attribute(utt: "u2", bearing: 12, faces: [near1, near2], now: 5).label, "Person 2")
    }

    func testStickyUnlessBearingMovesFor1Second() {
        let a = Attributor()
        let faces = [FaceRef(id: 1, label: "Person 1", bearing: 0), FaceRef(id: 2, label: "Person 2", bearing: 60)]
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 2, faces: faces, now: 0).label, "Person 1")
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 60, faces: faces, now: 0.3).label, "Person 1")   // blip
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 2, faces: faces, now: 0.5).label, "Person 1")    // back
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 60, faces: faces, now: 0.6).label, "Person 1")
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 60, faces: faces, now: 1.7).label, "Person 2")   // > 1 s
    }

    func testTurnAroundAttachesOffCameraCaptionToTheFace() {
        let a = Attributor()
        // Speaker B behind the wearer: nobody on camera at 180.
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 180, faces: [p1, p2], now: 0), .offCamera)
        // The wearer turns around; Speaker B's face appears at ~178 deg.
        let b = FaceRef(id: 7, label: "Person 3", bearing: 178)
        let changed = a.refresh(faces: [b], bearings: ["u1": 180], now: 1)
        XCTAssertEqual(changed["u1"], .person(id: 7, label: "Person 3"))
    }

    func testFlickeringFaceKeepsItsAssignment() {
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 33, faces: [p1], now: 0).label, "Person 1")
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 33, faces: [], now: 0.2).label, "Person 1")
    }

    func testProvisionalOnsetIsRedecidedByTheFirstUtterance() {
        let a = Attributor()
        // Noisy onset bearing lands on Person 2...
        XCTAssertEqual(a.attribute(utt: "u1", bearing: -28, faces: [p1, p2], now: 0, provisional: true).label, "Person 2")
        // ...the utterance's averaged bearing says Person 1: switch immediately, no 1 s hold.
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 34, faces: [p1, p2], now: 0.4).label, "Person 1")
        // From here on it's sticky again.
        XCTAssertEqual(a.attribute(utt: "u1", bearing: -30, faces: [p1, p2], now: 0.6).label, "Person 1")
    }

    func testFaceLossFallsBackToOffCameraAfterHold() {
        // A face that's tracked, then genuinely leaves frame and stays gone: the caption should stop
        // being glued to a track_id nothing renders and fall back to off-camera (so it gets the edge
        // + chevron treatment instead of freezing at a stale position) -- but only once it's been gone
        // for faceLostHoldS, not the instant it's missing from a single frame.
        let a = Attributor()
        _ = a.attribute(utt: "u1", bearing: 32, faces: [p1], now: 0)
        // First frame the face is gone: starts the hold, doesn't flip yet.
        XCTAssertNil(a.refresh(faces: [], bearings: [:], now: 1.0)["u1"])
        // Still within the hold.
        XCTAssertNil(a.refresh(faces: [], bearings: [:], now: 1.0 + Tunables.faceLostHoldS - 0.1)["u1"])
        // Hold elapsed with the face still gone: falls back to off-camera.
        XCTAssertEqual(a.refresh(faces: [], bearings: [:], now: 1.0 + Tunables.faceLostHoldS + 0.1)["u1"], .offCamera)
    }

    func testBriefFaceLossDoesNotFlickerOffCamera() {
        // Vision tracking can drop a face for a single frame (a blink, a fast turn) without them
        // actually leaving -- reappearing within the hold must cancel it, same spirit as moveHoldS.
        let a = Attributor()
        _ = a.attribute(utt: "u1", bearing: 32, faces: [p1], now: 0)
        _ = a.refresh(faces: [], bearings: [:], now: 1.0)                              // gone for a moment...
        XCTAssertNil(a.refresh(faces: [p1], bearings: [:], now: 1.0 + Tunables.faceLostHoldS / 2)["u1"])  // ...back
        // The hold timer actually reset on reappearance: if it hadn't, this moment (> the original
        // deadline) would already have flipped to off-camera.
        XCTAssertNil(a.refresh(faces: [], bearings: [:], now: 1.0 + Tunables.faceLostHoldS + 0.05)["u1"])
    }

    // MARK: spread-aware (fixed-beam array: front speech arrives at ~0 with an honest wide spread)

    func testWideSpreadAttachesALoneTalkerOffCentre() {
        let face = FaceRef(id: 1, label: "Person 1", bearing: 25)
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 0, faces: [face], now: 0, spread: 30), .person(id: 1, label: "Person 1"))
        XCTAssertTrue(a.lastDecision?.reason.hasPrefix("within the match angle (window widened") ?? false)
    }

    func testTightSpreadIsUnchanged() {
        let face = FaceRef(id: 1, label: "Person 1", bearing: 25)
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 0, faces: [face], now: 0, spread: 0), .offCamera)
        XCTAssertEqual(a.lastDecision?.reason, "nearest face beyond the match angle")
        XCTAssertEqual(a.attribute(utt: "u2", bearing: 0, faces: [face], now: 0, spread: Tunables.matchDeg), .offCamera)
    }

    func testWideSpreadWithTwoFacesPrefersTheEstablishedOwner() {
        let left = FaceRef(id: 1, label: "Person 1", bearing: -25)
        let right = FaceRef(id: 2, label: "Person 2", bearing: 25)
        let a = Attributor()
        _ = a.attribute(utt: "u0", bearing: -25, faces: [left, right], now: 0)       // Person 1 spoke recently...
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 0, faces: [left, right], now: 2, established: [2], spread: 30).label,
                       "Person 2", "...but Person 2 owns this audio source")
        XCTAssertTrue(a.lastDecision?.reason.hasPrefix("established audio source") ?? false)
    }

    func testWideSpreadWithTwoFacesPrefersTheLastSpeaker() {
        let left = FaceRef(id: 1, label: "Person 1", bearing: -25)
        let right = FaceRef(id: 2, label: "Person 2", bearing: 25)
        let a = Attributor()
        _ = a.attribute(utt: "u0", bearing: 25, faces: [left, right], now: 0)        // Person 2 spoke (clearly theirs)
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 0, faces: [left, right], now: 2, spread: 30).label, "Person 2")
        XCTAssertTrue(a.lastDecision?.reason.hasPrefix("most recent speaker") ?? false)
    }

    func testWideSpreadWithNothingToGoOnPicksTheNearestTentatively() {
        let left = FaceRef(id: 1, label: "Person 1", bearing: -25)
        let right = FaceRef(id: 2, label: "Person 2", bearing: 20)
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 0, faces: [left, right], now: 0, spread: 30).label, "Person 2")
        XCTAssertTrue(a.lastDecision?.reason.hasPrefix("tentative") ?? false)
        XCTAssertLessThanOrEqual(a.score(for: "u1"), Tunables.tentativeAttributionScore)
    }

    func testHugeSpreadIsCappedAtMatchDegSpreadMax() {
        let a = Attributor()
        let inside = FaceRef(id: 1, label: "Person 1", bearing: Tunables.matchDegSpreadMax - 2)
        let outside = FaceRef(id: 2, label: "Person 2", bearing: Tunables.matchDegSpreadMax + 5)
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 0, faces: [inside], now: 0, spread: 90).label, "Person 1")
        XCTAssertEqual(a.attribute(utt: "u2", bearing: 0, faces: [outside], now: 0, spread: 90), .offCamera)
        XCTAssertTrue(a.lastDecision?.reason.contains("widened") ?? false)
    }

    func testOffCameraTurnAroundKeepsItsSpread() {
        let a = Attributor()
        XCTAssertEqual(a.attribute(utt: "u1", bearing: 180, faces: [], now: 0, spread: 30), .offCamera)
        // The wearer turns; the talker's face appears 25 deg from the (wide) bearing.
        let b = FaceRef(id: 7, label: "Person 3", bearing: 155)
        XCTAssertEqual(a.refresh(faces: [b], bearings: ["u1": 180], now: 1)["u1"], .person(id: 7, label: "Person 3"))
    }

    func testForget() {
        let a = Attributor()
        a.attribute(utt: "u1", bearing: 33, faces: [p1], now: 0)
        a.forget("u1")
        XCTAssertNil(a.assignments["u1"])
    }
}
