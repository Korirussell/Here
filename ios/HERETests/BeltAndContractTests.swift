import XCTest
@testable import HERE

/// README §14 belt rules and §16 packet.
final class BeltPolicyTests: XCTestCase {
    func testPacketLayout() {
        let cmd = BeltCommand(pattern: .speech, motor: 2, strength: 160, priority: 1)
        XCTAssertEqual([UInt8](cmd.packet), [0x01, 1, 2, 0, 160, 1])
        XCTAssertEqual([UInt8](BeltCommand(pattern: .alert, motor: 9, strength: 300, priority: -1).packet),
                       [0x01, 3, 9, 0, 255, 0])
    }

    func testSpeechInFrontIsSilent() {
        let p = BeltPolicy()
        XCTAssertNil(p.speechStarted(personKey: "p1", relative: 20, strength: 200, now: 0))
        XCTAssertNotNil(p.speechStarted(personKey: "p1", relative: 45, strength: 200, now: 0))
    }

    func testSamePersonNotWithin5Seconds() {
        let p = BeltPolicy()
        XCTAssertEqual(p.speechStarted(personKey: "p1", relative: 90, strength: 200, now: 0)?.motor, 3)
        XCTAssertNil(p.speechStarted(personKey: "p1", relative: 90, strength: 200, now: 3))
        XCTAssertNotNil(p.speechStarted(personKey: "p1", relative: 90, strength: 200, now: 5.1))
    }

    func testOneNonAlertPer1_5Seconds() {
        let p = BeltPolicy()
        XCTAssertNotNil(p.speechStarted(personKey: "p1", relative: 90, strength: 200, now: 0))
        XCTAssertNil(p.speechStarted(personKey: "p2", relative: -90, strength: 200, now: 1.0))
        XCTAssertEqual(p.speechStarted(personKey: "p2", relative: -90, strength: 200, now: 1.6)?.motor, 7)
    }

    func testAlertsAlwaysBuzzWithTheRightPattern() {
        let p = BeltPolicy()
        XCTAssertEqual(p.alert(kind: .doorbell, priority: 2, relative: 180, strength: 200),
                       BeltCommand(pattern: .alert, motor: 5, strength: 200, priority: 3, relativeDeg: 180))
        XCTAssertEqual(p.alert(kind: .nameCalled, priority: 2, relative: -90, strength: 200).pattern, .nameCalled)
        XCTAssertEqual(p.alert(kind: .babyCrying, priority: 3, relative: 0, strength: 200).pattern, .emergency)
        XCTAssertEqual(p.alert(kind: .doorbell, priority: 2, relative: nil, strength: 200).motor, 1)
    }
}

/// The 6-motor belt the team built (belt/firmware/here_belt_6motor): motor layout, wire bytes, pattern timing.
final class BeltSixMotorTests: XCTestCase {
    private func motors(_ mask: UInt8) -> Set<Int> { Set((0..<6).filter { mask & (1 << UInt8($0)) != 0 }) }

    func testNearestMotorsMatchTheRealBelt() {
        // 0 back-right 150, 1 left 270, 2 front-right 30, 3 back-left 210, 4 front-left 330, 5 right 90.
        XCTAssertEqual(motors(BeltSixMotor.mask(forRelative: 90)), [5])
        XCTAssertEqual(motors(BeltSixMotor.mask(forRelative: -90)), [1])
        XCTAssertEqual(motors(BeltSixMotor.mask(forRelative: 35)), [2])
        XCTAssertEqual(motors(BeltSixMotor.mask(forRelative: -150)), [3])
        // Straight ahead / behind fall between two motors: both buzz.
        XCTAssertEqual(motors(BeltSixMotor.mask(forRelative: 0)), [2, 4])
        XCTAssertEqual(motors(BeltSixMotor.mask(forRelative: 180)), [0, 3])
    }

    func testAlertIsThreeGroupPulsesTowardTheSound() {
        let cmd = BeltCommand(pattern: .alert, motor: 3, strength: 200, priority: 3, relativeDeg: 90)
        let steps = BeltSixMotor.steps(for: cmd)
        XCTAssertEqual(steps.count, 3)
        XCTAssertEqual(steps[0], BeltSixMotor.Step(at: 0, bytes: [0xFE, 0b10_0000, 200, 25]))
        XCTAssertEqual(steps.map(\.at), [0, 0.4, 0.8])
        XCTAssertEqual(BeltSixMotor.length(of: cmd), 1.05, accuracy: 1e-9)
    }

    func testWithoutAnExactDirectionTheEightMotorIndexIsUsed() {
        // Motor 3 of 8 = +90 deg (right).
        let cmd = BeltCommand(pattern: .speech, motor: 3, strength: 300, priority: 1)
        XCTAssertEqual(BeltSixMotor.steps(for: cmd), [BeltSixMotor.Step(at: 0, bytes: [0xFE, 0b10_0000, 255, 15])])
    }

    func testStopAndEmergency() {
        XCTAssertEqual(BeltSixMotor.steps(for: BeltCommand(pattern: .stop, motor: 0, strength: 0, priority: 9)),
                       [BeltSixMotor.Step(at: 0, bytes: [0xFF, 0, 0])])
        let e = BeltSixMotor.steps(for: BeltCommand(pattern: .emergency, motor: 5, strength: 220, priority: 4, relativeDeg: 180))
        XCTAssertEqual(e.count, 10)
        XCTAssertEqual(e[0].bytes[1], BeltSixMotor.allMask)
        XCTAssertEqual(motors(e[1].bytes[1]), [0, 3])
    }

    func testTestPatternSweepsClockwiseFromFrontRight() {
        let order = BeltSixMotor.steps(for: BeltCommand(pattern: .test, motor: 0, strength: 200, priority: 9))
            .map { motors($0.bytes[1]).first! }
        XCTAssertEqual(order, [2, 5, 0, 3, 1, 4])
    }

    func testOldSavedUUIDsResolveToTheNewBelt() {
        XCTAssertEqual(BeltUUID.resolve(BeltUUID.legacyService.lowercased(), current: BeltUUID.service, legacy: BeltUUID.legacyService),
                       BeltUUID.service)
        XCTAssertEqual(BeltUUID.resolve("", current: BeltUUID.command, legacy: BeltUUID.legacyCommand), BeltUUID.command)
        XCTAssertEqual(BeltUUID.resolve("1234", current: BeltUUID.command, legacy: BeltUUID.legacyCommand), "1234")
    }
}

/// Every contract example decodes into the right case (README §12; examples bundled from contracts/examples).
final class ContractTests: XCTestCase {
    func testAllExamplesDecode() throws {
        let urls = Bundle(for: Self.self).urls(forResourcesWithExtension: "json", subdirectory: nil) ?? []
        XCTAssertEqual(urls.count, 15, "contracts/examples should be bundled into the test target")
        for url in urls {
            let msg = try HereMessage.decode(try Data(contentsOf: url))
            if case .unknown(let t) = msg { XCTFail("\(url.lastPathComponent) decoded as unknown type \(t)") }
        }
    }

    func testUtteranceFields() throws {
        let json = #"{"type":"utterance","v":3,"src":"laptop","t":1760000000.45,"utt_id":"u-0192","t_start":1760000000.08,"t_end":1760000000.45,"text":"did you see","pending":"the email","final":false,"bearing_deg":38.9,"bearing_spread_deg":4.1,"source_id":0,"overlap":false,"tone":"excited","tone_confidence":0.7,"loudness":0.55,"engine":"faster_whisper"}"#
        guard case let .utterance(u) = try HereMessage.decode(json) else { return XCTFail() }
        XCTAssertEqual(u.uttId, "u-0192")
        XCTAssertEqual(u.bearingDeg, 38.9)
        XCTAssertEqual(u.tone, .excited)
        XCTAssertEqual(u.engine, .fasterWhisper)
        XCTAssertNil(u.stageMs)
    }

    func testNullTone() throws {
        let json = #"{"type":"utterance","v":3,"src":"laptop","t":1,"utt_id":"u","t_start":0,"t_end":1,"text":"","pending":"","final":true,"bearing_deg":0,"bearing_spread_deg":0,"source_id":0,"overlap":false,"tone":null,"tone_confidence":0,"loudness":0,"engine":"faster_whisper"}"#
        guard case let .utterance(u) = try HereMessage.decode(json) else { return XCTFail() }
        XCTAssertNil(u.tone)
    }

    func testAttributionEncodesNullPersonLabel() throws {
        let a = Attribution(t: 1, ref: Ref(uttId: "u1"), personLabel: nil, method: .offCamera, score: 0)
        let obj = try JSONSerialization.jsonObject(with: JSONEncoder().encode(a)) as! [String: Any]
        XCTAssertTrue(obj.keys.contains("person_label"))
        XCTAssertTrue(obj["person_label"] is NSNull)
        XCTAssertEqual(obj["method"] as? String, "off_camera")
    }

    func testConfigRoundTripsSnakeCaseKeys() throws {
        var features = Features()
        features.rings = false
        let c = ConfigMessage(t: 1, userName: "Kori", nameAliases: ["Cory"], focusMode: true, beltOnlyMode: false,
                              features: features, alertsEnabled: ["name_called": true], beltStrength: 180)
        let obj = try JSONSerialization.jsonObject(with: JSONEncoder().encode(c)) as! [String: Any]
        XCTAssertEqual(obj["user_name"] as? String, "Kori")
        XCTAssertEqual((obj["alerts_enabled"] as? [String: Bool])?["name_called"], true)
        guard case let .config(back) = try HereMessage.decode(try JSONEncoder().encode(c)) else { return XCTFail() }
        XCTAssertEqual(back, c)
    }
}
