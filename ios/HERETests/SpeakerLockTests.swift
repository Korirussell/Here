import XCTest
@testable import HERE

/// Per-source speaker locks: who a speech source belongs to, and what it takes to change that.
final class SpeakerLockTests: XCTestCase {
    private func person(_ id: Int, at bearing: Double, mouth: Double = 0) -> SpeakerCandidate {
        SpeakerCandidate(personId: id, micBearing: bearing, mouthActivity: mouth)
    }

    func testNewSourceLocksToAClearOwnerAndStaysUnattributedWhenAmbiguous() {
        let locks = SpeakerLocks()
        let clear = locks.resolve(utt: "u1", sourceId: 0, micBearing: 21, speaking: true, candidates: [person(1, at: 20), person(2, at: -25)], now: 0)
        XCTAssertEqual(clear.personId, 1)
        XCTAssertEqual(clear.state, .locked)
        // Two people equally plausible and nothing to tell them apart: nobody is named.
        let unsure = SpeakerLocks().resolve(utt: "u", sourceId: 0, micBearing: 10, speaking: true, candidates: [person(1, at: 7), person(2, at: 13)], now: 0)
        XCTAssertNil(unsure.personId, "unattributed beats confidently wrong")
        XCTAssertEqual(unsure.state, .unassigned)
        // Lip movement can settle it (supporting evidence, on top of a plausible bearing)...
        let lips = SpeakerLocks().resolve(utt: "u", sourceId: 0, micBearing: 10, speaking: true,
                                          candidates: [person(1, at: 7), person(2, at: 13, mouth: 0.9)], now: 0)
        XCTAssertEqual(lips.personId, 2)
        // ...but never creates a claim for someone nowhere near the sound.
        let far = SpeakerLocks().resolve(utt: "u", sourceId: 0, micBearing: 10, speaking: true, candidates: [person(3, at: 80, mouth: 1)], now: 0)
        XCTAssertNil(far.personId)
    }

    func testBearingJitterDoesNotMoveALockedSource() {
        let locks = SpeakerLocks()
        let people = [person(1, at: 20), person(2, at: 2)]
        XCTAssertEqual(locks.resolve(utt: "u", sourceId: 0, micBearing: 20, speaking: true, candidates: people, now: 0).personId, 1)
        // The array's direction wobbles, sometimes landing nearer Person 2.
        for (i, noisy) in [14.0, 26, 5, 22, 8, 19, 3, 21].enumerated() {
            let r = locks.resolve(utt: "u", sourceId: 0, micBearing: noisy, speaking: true, candidates: people, now: 0.2 * Double(i + 1))
            XCTAssertEqual(r.personId, 1, "reading \(noisy)")
        }
        XCTAssertTrue(locks.log.filter { $0.contains("->") }.isEmpty, "no switch was ever logged")
    }

    func testSomeoneWalkingInFrontDoesNotStealTheSource() {
        let locks = SpeakerLocks()
        XCTAssertEqual(locks.resolve(utt: "u", sourceId: 0, micBearing: 15, speaking: true, candidates: [person(1, at: 15), person(2, at: -40)], now: 0).personId, 1)
        // Person 2 steps directly in front of Person 1: Person 1 is hidden, Person 2 is now exactly where the sound is.
        var t = 1.0
        while t <= 1.0 + Tunables.speakerOcclusionGraceS - 0.2 {
            let r = locks.resolve(utt: "u", sourceId: 0, micBearing: 15, speaking: true, candidates: [person(2, at: 15)], now: t)
            XCTAssertEqual(r.personId, 1, "t=\(t)")
            XCTAssertEqual(r.state, .temporarilyOccluded)
            t += 0.2
        }
        // Person 2 moves on, Person 1 is visible again: still theirs, and locked.
        let back = locks.resolve(utt: "u", sourceId: 0, micBearing: 15, speaking: true, candidates: [person(1, at: 15), person(2, at: 45)], now: t)
        XCTAssertEqual(back.personId, 1)
        XCTAssertEqual(back.state, .locked)
    }

    func testSwitchNeedsAMarginAndAHoldAndIsExplained() {
        let locks = SpeakerLocks()
        let both = [person(1, at: 20), person(2, at: -30)]
        XCTAssertEqual(locks.resolve(utt: "u", sourceId: 0, micBearing: 20, speaking: true, candidates: both, now: 0).personId, 1)
        // The sound really does move to Person 2 and stays there (same stream, no pause: one long utterance).
        var t = 0.2
        var r = locks.resolve(utt: "u", sourceId: 0, micBearing: -30, speaking: true, candidates: both, now: t)
        var switchedAt: Double?
        var challengedAt: Double?
        while t < 6 {
            t += 0.2
            r = locks.resolve(utt: "u", sourceId: 0, micBearing: -30, speaking: true, candidates: both, now: t)
            if challengedAt == nil, locks.associations[0].challengerId == 2 { challengedAt = t }
            if switchedAt == nil, r.personId == 2 { switchedAt = t }
        }
        XCTAssertEqual(r.personId, 2)
        XCTAssertGreaterThanOrEqual(switchedAt! - challengedAt!, Tunables.speakerSwitchHoldS - 1e-9, "not from one frame")
        let why = locks.log.first { $0.contains("person 1 -> person 2") }
        XCTAssertNotNil(why)
        XCTAssertTrue(why!.contains("margin") && why!.contains("held"), why!)
        // A challenger only slightly better never switches.
        let close = SpeakerLocks()
        _ = close.resolve(utt: "u", sourceId: 0, micBearing: 20, speaking: true, candidates: [person(1, at: 20), person(2, at: 12)], now: 0)
        for i in 1...40 {
            XCTAssertEqual(close.resolve(utt: "u", sourceId: 0, micBearing: 13, speaking: true, candidates: [person(1, at: 20), person(2, at: 12)],
                                         now: Double(i) * 0.2).personId, 1)
        }
    }

    func testAPauseKeepsTheSpeakerAndSilenceReleasesCleanly() {
        let locks = SpeakerLocks()
        let people = [person(1, at: 20), person(2, at: 8)]
        XCTAssertEqual(locks.resolve(utt: "s1", sourceId: 0, micBearing: 21, speaking: true, candidates: people, now: 0).personId, 1)
        XCTAssertEqual(locks.resolve(utt: "s1", sourceId: 0, micBearing: 21, speaking: false, candidates: people, now: 1.5).state, .releasePending)
        // "...grab that?" a second and a half later, and the array hears it a little nearer Person 2.
        let resumed = locks.resolve(utt: "s2", sourceId: 0, micBearing: 13, speaking: true, candidates: people, now: 3.0)
        XCTAssertEqual(resumed.personId, 1, "the next sentence from the same source is the same speaker")
        XCTAssertEqual(locks.associations.count, 1)
        // Long silence: released. The next voice there starts fresh.
        locks.expire(now: 3.0 + Tunables.speakerReleaseHoldS + 0.1)
        XCTAssertTrue(locks.associations.isEmpty)
        XCTAssertEqual(locks.resolve(utt: "s3", sourceId: 0, micBearing: 8, speaking: true, candidates: people, now: 10).personId, 2)
    }

    func testTwoSourcesHoldTwoIndependentLocks() {
        let locks = SpeakerLocks()
        let people = [person(1, at: 22), person(2, at: -26)]
        XCTAssertEqual(locks.resolve(utt: "a", sourceId: 0, micBearing: 22, speaking: true, candidates: people, now: 0).personId, 1)
        // Person 2 starts while Person 1 is still talking: a second source, not a takeover.
        XCTAssertEqual(locks.resolve(utt: "b", sourceId: 1, micBearing: -26, speaking: true, candidates: people, now: 1).personId, 2)
        XCTAssertEqual(locks.associations.count, 2)
        XCTAssertEqual(locks.resolve(utt: "a", sourceId: 0, micBearing: 22, speaking: true, candidates: people, now: 1.2).personId, 1)
        // Person 2 stops. Person 1's lock is untouched, through and after Person 2's release.
        _ = locks.resolve(utt: "b", sourceId: 1, micBearing: -26, speaking: false, candidates: people, now: 2)
        for i in 0..<20 {
            let t = 2.2 + Double(i) * 0.2
            XCTAssertEqual(locks.resolve(utt: "a", sourceId: 0, micBearing: 22, speaking: true, candidates: people, now: t).personId, 1)
        }
        XCTAssertEqual(locks.associations.map(\.personId), [1], "one source ended; the other never noticed")
        // Same stream, but far apart in direction, is still two sources.
        let sameStream = SpeakerLocks()
        _ = sameStream.resolve(utt: "a", sourceId: 0, micBearing: 40, speaking: true, candidates: [person(1, at: 40), person(2, at: -40)], now: 0)
        XCTAssertEqual(sameStream.resolve(utt: "b", sourceId: 0, micBearing: -40, speaking: true, candidates: [person(1, at: 40), person(2, at: -40)], now: 0.5).personId, 2)
        XCTAssertEqual(sameStream.associations.count, 2)
    }

    func testSpeakerLeavingViewKeepsTheirSource() {
        let locks = SpeakerLocks()
        XCTAssertEqual(locks.resolve(utt: "u", sourceId: 0, micBearing: 20, speaking: true, candidates: [person(1, at: 20)], now: 0).personId, 1)
        // They walk behind the wearer, still talking; someone else is in view elsewhere.
        var r = locks.resolve(utt: "u", sourceId: 0, micBearing: 90, speaking: true, candidates: [person(2, at: -30)], now: 2)
        r = locks.resolve(utt: "u", sourceId: 0, micBearing: 170, speaking: true, candidates: [person(2, at: -30)], now: 6)
        XCTAssertEqual(r.personId, 1)
        XCTAssertEqual(r.state, .offscreenButTracked)
        // They come back into view at the source's bearing: locked again, same person.
        let back = locks.resolve(utt: "u", sourceId: 0, micBearing: 172, speaking: true, candidates: [person(1, at: 170), person(2, at: -30)], now: 7)
        XCTAssertEqual(back.state, .locked)
        XCTAssertEqual(back.personId, 1)
    }

    func testMouthActivityNeedsMovementNotAnOpenMouth() {
        XCTAssertEqual(MouthActivity.score([0.08, 0.08, 0.08, 0.08, 0.08]), 0, "a mouth held open is not talking")
        XCTAssertEqual(MouthActivity.score([0.01, 0.01]), 0, "too few samples to say")
        XCTAssertGreaterThan(MouthActivity.score([0.01, 0.05, 0.02, 0.06, 0.01, 0.05]), 0.8)
        XCTAssertLessThan(MouthActivity.score([0.020, 0.021, 0.019, 0.020, 0.021]), 0.1, "landmark noise on a still face")
        XCTAssertEqual(MouthActivity.smooth(0, toward: 1), Tunables.mouthActivitySmoothing, "one sample doesn't decide")
    }

    func testMotionPredictionFollowsAPersonAlongTheirPath() {
        let people = SessionPeople()
        let p = people.resolve(trackId: 1, bearing: 0, now: 0) { _ in nil }
        // Walking right at 20 deg/s, then lost.
        for i in 1...10 { people.seen(trackId: 1, bearing: Double(i) * 2, viaBody: false, now: Double(i) * 0.1) }
        people.detach(trackId: 1, now: 1.0)
        let lost = people.person(id: p.id)!
        XCTAssertEqual(lost.bearingVelocity, 20, accuracy: 4)
        XCTAssertEqual(lost.predictedBearing(now: 2.0), 40, accuracy: 5, "expected further along a second later")
        XCTAssertEqual(lost.predictedBearing(now: 60), lost.predictedBearing(now: 1.0 + Tunables.reidPredictS), accuracy: 1e-9, "only for a short while")
        XCTAssertEqual(lost.state(now: 2), .offscreen)
        XCTAssertEqual(lost.state(now: 2 + Tunables.spatialCredibleS + 1), .dormant)
        // A track appearing where they were heading is them; one appearing where they came from is less likely.
        let ahead = ReIDEvidence(personId: p.id, visualSimilarity: nil, bearingDeltaDeg: Geo.angDiff(42, lost.predictedBearing(now: 2.0)), secondsSinceSeen: 1.0)
        let behind = ReIDEvidence(personId: p.id, visualSimilarity: nil, bearingDeltaDeg: Geo.angDiff(0, lost.predictedBearing(now: 2.0)), secondsSinceSeen: 1.0)
        XCTAssertGreaterThan(ReID.score(ahead), ReID.score(behind))
    }

    func testDistanceTiersShiftIdentityFromFaceToClothing() {
        XCTAssertEqual(ReID.tier(facePixels: 200), .close)
        XCTAssertEqual(ReID.tier(facePixels: 70), .medium)
        XCTAssertEqual(ReID.tier(facePixels: 20), .far)
        XCTAssertEqual(ReID.faceWeight(facePixels: 200), Tunables.faceWeightClose, accuracy: 1e-9)
        XCTAssertEqual(ReID.faceWeight(facePixels: 10), Tunables.faceWeightFar, accuracy: 1e-9)
        XCTAssertGreaterThan(ReID.faceWeight(facePixels: 90), ReID.faceWeight(facePixels: 60))
        // Far away the face print is a few dozen blurry pixels and matches badly, but they are wearing
        // the same clothes: still recognisably them. Close up, the same poor face match counts against.
        let far = ReID.appearance(face: 0.2, torso: 0.9, facePixels: 25)!
        let close = ReID.appearance(face: 0.2, torso: 0.9, facePixels: 200)!
        XCTAssertGreaterThan(far, close)
        XCTAssertGreaterThanOrEqual(far, Tunables.reidAccept, "a small face does not make them a new person")
        XCTAssertEqual(ReID.appearance(face: nil, torso: 0.8, facePixels: 25), 0.8, "no face at all: clothing alone")
        XCTAssertEqual(ReID.appearance(face: 0.7, torso: nil, facePixels: 25), 0.7)
        XCTAssertNil(ReID.appearance(face: nil, torso: nil, facePixels: 25))
        // A weak detection may continue a track but not start one.
        XCTAssertLessThan(Tunables.faceWeakConfidence, Tunables.faceMinConfidence)
    }

    func testPersonKeepsAShortHistory() {
        let people = SessionPeople()
        let p = people.resolve(trackId: 1, bearing: 0, now: 0) { _ in nil }
        for i in 1...30 { people.seen(trackId: 1, bearing: Double(i), viaBody: false, now: Double(i) * 0.5) }
        people.associate(trackId: 1, sourceId: 0, now: 5)
        people.associate(trackId: 1, sourceId: 1, now: 9)
        people.associate(trackId: 1, sourceId: 1, now: 10)
        let known = people.person(id: p.id)!
        XCTAssertEqual(known.recentBearings.count, Tunables.personHistorySize, "about one a second, capped")
        XCTAssertEqual(known.recentBearings.last!, 30, accuracy: 2)
        XCTAssertEqual(known.recentAudioSources, [0, 1])
        people.detach(trackId: 1, now: 16)
        XCTAssertEqual(people.person(id: p.id)?.recentBearings.count, Tunables.personHistorySize, "kept while they are away")
    }

    // MARK: scenarios 69-72

    private func run(_ number: Int, _ check: (ScenarioHarness.Snapshot, ScenarioHarness) -> Void) {
        let scenario = SpatialScenario.numbered(number)
        let harness = ScenarioHarness(scenario)
        var t = ScenarioHarness.step
        while t <= scenario.duration + 1e-9 {
            check(harness.advance(to: t), harness)
            t += ScenarioHarness.step
        }
    }

    func testScenario69OccluderNeverGetsTheCaption() {
        var hiddenChecked = false
        run(69) { snap, h in
            for c in snap.captions.values {
                if case let .person(track, _) = c.speaker {
                    XCTAssertEqual(h.faceTruth[track], "A", "t=\(snap.t): the caption is on the wrong person")
                }
                if let owner = c.speakerPersonId { XCTAssertEqual(h.truthName(ofPerson: owner), "A", "t=\(snap.t)") }
            }
            if snap.t > 4.3 && snap.t < 5.9 {
                // Person 1 is hidden behind Person 2 and still talking: their own notification, their color.
                hiddenChecked = true
                XCTAssertTrue(snap.scene.presences.contains { $0.sourceType == .person && h.truthName(ofPerson: $0.personId ?? -1) == "A" }, "t=\(snap.t)")
                XCTAssertFalse(snap.scene.speakers.contains { $0.activity > 0 }, "nobody visible is shown as speaking (t=\(snap.t))")
            }
        }
        XCTAssertTrue(hiddenChecked)
    }

    func testScenario70PauseKeepsTheSpeaker() {
        run(70) { snap, h in
            for c in snap.captions.values {
                if case let .person(track, _) = c.speaker { XCTAssertEqual(h.faceTruth[track], "A", "\(c.uttId) at t=\(snap.t)") }
            }
        }
        let h = ScenarioHarness(.numbered(70))
        h.advance(to: 5.0)
        XCTAssertEqual(h.locks.associations.count, 1, "both sentences are one source")
    }

    func testScenarios71And72SimultaneousSpeakersKeepTheirOwnCaptions() {
        let both = ScenarioHarness(.numbered(71))
        let snap = both.advance(to: 4.0)
        XCTAssertEqual(both.locks.associations.count, 2)
        XCTAssertEqual(snap.scene.speakers.filter { $0.activity == 1 }.count, 2, "two auras at once")
        XCTAssertEqual(Set(snap.captions.values.compactMap { $0.speaker.label }), ["Person 1", "Person 2"])
        // Person 2 stops; Person 1 carries on as Person 1.
        let later = both.advance(to: 6.5)
        XCTAssertEqual(later.captions["s1"]?.speaker.label, "Person 1")
        XCTAssertEqual(both.locks.associations.first { $0.sourceId == 0 }?.personId, both.sessionPerson(named: "A")?.id)

        run(72) { snap, h in
            for c in snap.captions.values {
                let truth = c.uttId == "s1" ? "A" : "B"
                if case let .person(track, _) = c.speaker { XCTAssertEqual(h.faceTruth[track], truth, "\(c.uttId) at t=\(snap.t)") }
                if let owner = c.speakerPersonId { XCTAssertEqual(h.truthName(ofPerson: owner), truth, "\(c.uttId) at t=\(snap.t)") }
            }
        }
    }
}
