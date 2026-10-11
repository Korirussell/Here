import XCTest
@testable import HERE

/// Two relays, one stream: primary (local) first, fallback (Vultr) on failure,
/// back to the primary when a background probe connects. Timers and sockets live in RelayClient;
/// the decisions are here.
final class RelayFailoverTests: XCTestCase {
    func testNoFallbackNeverSwitches() {
        var f = RelayFailover(hasFallback: false)
        XCTAssertFalse(f.primaryHasDeadline)
        XCTAssertFalse(f.connectionFailed())
        XCTAssertFalse(f.connectionFailed())
        XCTAssertEqual(f.route, .primary)
        XCTAssertFalse(f.shouldProbePrimary)
        XCTAssertFalse(f.primaryProbeConnected())
        XCTAssertEqual(f.route, .primary)
    }

    func testPrimaryFailureSwitchesToFallbackOnce() {
        var f = RelayFailover(hasFallback: true)
        XCTAssertEqual(f.route, .primary)
        XCTAssertTrue(f.primaryHasDeadline)
        XCTAssertFalse(f.shouldProbePrimary)
        XCTAssertTrue(f.connectionFailed())
        XCTAssertEqual(f.route, .fallback)
        XCTAssertFalse(f.primaryHasDeadline)
        XCTAssertTrue(f.shouldProbePrimary)
        // The fallback failing too retries the fallback with backoff; the probe keeps looking for the primary.
        XCTAssertFalse(f.connectionFailed())
        XCTAssertEqual(f.route, .fallback)
        XCTAssertTrue(f.shouldProbePrimary)
    }

    func testProbeSwitchesBackAndPrimaryCanFailAgain() {
        var f = RelayFailover(hasFallback: true)
        _ = f.connectionFailed()
        XCTAssertTrue(f.primaryProbeConnected())
        XCTAssertEqual(f.route, .primary)
        XCTAssertFalse(f.shouldProbePrimary)
        XCTAssertTrue(f.primaryHasDeadline)
        XCTAssertFalse(f.primaryProbeConnected())    // a stale probe result while already on the primary
        XCTAssertTrue(f.connectionFailed())          // primary drops again -> fallback again
        XCTAssertEqual(f.route, .fallback)
    }

    func testRelayURLBuildsQueryAndDefaultsPath() {
        let u = RelayClient.relayURL(base: " ws://192.168.1.20:8080 ", session: "demo", token: "s3cret")
        XCTAssertEqual(u?.absoluteString, "ws://192.168.1.20:8080/ws?session=demo&role=phone&token=s3cret")
        let v = RelayClient.relayURL(base: "wss://relay.example.com/ws", session: "demo", token: "")
        XCTAssertEqual(v?.absoluteString, "wss://relay.example.com/ws?session=demo&role=phone")
    }

    func testRelayURLRejectsEmptyAndNonWebSocket() {
        XCTAssertNil(RelayClient.relayURL(base: "", session: "demo", token: ""))
        XCTAssertNil(RelayClient.relayURL(base: "https://relay.example.com/ws", session: "demo", token: ""))
    }

    func testFallbackURLPersistsAndOldSettingsDecode() throws {
        var s = Settings()
        s.relayFallbackURL = "wss://relay.example.com/ws"
        let back = try JSONDecoder().decode(Settings.self, from: JSONEncoder().encode(s))
        XCTAssertEqual(back.relayFallbackURL, "wss://relay.example.com/ws")
        // Settings saved before the fallback existed: no key -> no fallback.
        let old = try JSONDecoder().decode(Settings.self, from: Data(#"{"relayURL":"ws://10.0.0.2:8080/ws"}"#.utf8))
        XCTAssertEqual(old.relayURL, "ws://10.0.0.2:8080/ws")
        XCTAssertEqual(old.relayFallbackURL, "")
    }
}
