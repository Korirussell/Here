import XCTest
@testable import HERE

/// Projector and Frames (README §14 "Coordinates (where bugs live)"): captureAngle rotation for all four
/// orientations, and the Simulator's no-camera fallback mapping. `Projector`'s *real* (layer-backed) path
/// goes through `AVCaptureVideoPreviewLayer.layerRectConverted`, a live-session-dependent system call that
/// isn't meaningfully unit-testable off a device -- that's exactly why the human checklist (top of this
/// file) leads with "do the face boxes sit on faces?" on real hardware. What's covered here is everything
/// that *is* pure, deterministic geometry: `Frames`' rotation math for all four `captureAngle`s, and
/// `Projector`'s `layer == nil` fallback (the Simulator / simulated-faces path), which is real code that
/// runs in the Simulator and was previously untested on its own (only exercised indirectly).
final class ProjectorTests: XCTestCase {
    private let upright = CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4)

    // MARK: Frames.sensorRect, all four captureAngles

    private func assertRect(_ r: CGRect, _ x: Double, _ y: Double, _ w: Double, _ h: Double,
                            file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(r.minX, x, accuracy: 1e-9, file: file, line: line)
        XCTAssertEqual(r.minY, y, accuracy: 1e-9, file: file, line: line)
        XCTAssertEqual(r.width, w, accuracy: 1e-9, file: file, line: line)
        XCTAssertEqual(r.height, h, accuracy: 1e-9, file: file, line: line)
    }

    func testSensorRectAllFourAngles() {
        XCTAssertEqual(Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 0), upright)
        assertRect(Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 90), 0.2, 0.6, 0.4, 0.3)
        assertRect(Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 180), 0.6, 0.4, 0.3, 0.4)
        assertRect(Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 270), 0.4, 0.1, 0.4, 0.3)
    }

    func test90And270AreInverseRotations() {
        // Landscape-left vs. landscape-right mounts use opposite captureAngles (README §14 "both
        // landscape orientations"): rotating one way then the other must be the identity.
        let rotated = Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 90)
        let back = Frames.sensorRect(fromUprightTopLeft: rotated, captureAngle: 270)
        XCTAssertEqual(back.minX, upright.minX, accuracy: 1e-9)
        XCTAssertEqual(back.minY, upright.minY, accuracy: 1e-9)
        XCTAssertEqual(back.width, upright.width, accuracy: 1e-9)
        XCTAssertEqual(back.height, upright.height, accuracy: 1e-9)
    }

    func testTwo180sAreTheIdentity() {
        let twice = Frames.sensorRect(fromUprightTopLeft: Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 180),
                                      captureAngle: 180)
        XCTAssertEqual(twice.minX, upright.minX, accuracy: 1e-9)
        XCTAssertEqual(twice.minY, upright.minY, accuracy: 1e-9)
    }

    func testSensorPoint() {
        let p = Frames.sensorPoint(fromUprightTopLeft: CGPoint(x: 0.3, y: 0.4), captureAngle: 90)
        XCTAssertEqual(p.x, 0.4, accuracy: 1e-9)
        XCTAssertEqual(p.y, 0.7, accuracy: 1e-9)
    }

    // MARK: Projector, no layer (Simulator / simulated-faces fallback, README §14 dev tips)

    func testFallbackRectAspectFillsWideScreen() {
        // size wider than a 16:9 frame at its own height: width-constrained (w = size.width).
        let size = CGSize(width: 1000, height: 400)
        let proj = Projector(layer: nil, captureAngle: 0, hfovDeg: 106, heading: 0, size: size)
        let box = CGRect(x: 0.2, y: 0.3, width: 0.1, height: 0.15)   // Vision bottom-left
        let r = proj.rect(forVisionBox: box)

        XCTAssertEqual(r.minX, 200, accuracy: 1e-6)
        XCTAssertEqual(r.minY, 228.125, accuracy: 1e-6)
        XCTAssertEqual(r.width, 100, accuracy: 1e-6)
        XCTAssertEqual(r.height, 84.375, accuracy: 1e-6)
    }

    func testFallbackRectAspectFillsTallScreen() {
        // size taller than a 16:9 frame at its own width: height-constrained (h = size.height).
        let size = CGSize(width: 400, height: 1000)
        let proj = Projector(layer: nil, captureAngle: 0, hfovDeg: 106, heading: 0, size: size)
        let box = CGRect(x: 0.2, y: 0.3, width: 0.1, height: 0.15)
        let r = proj.rect(forVisionBox: box)

        let w = size.height * 16.0 / 9.0
        let ox = (size.width - w) / 2
        XCTAssertEqual(r.minX, ox + 0.2 * w, accuracy: 1e-6)
        XCTAssertEqual(r.minY, 0.55 * size.height, accuracy: 1e-6)   // oy is 0 in this branch
        XCTAssertEqual(r.height, 0.15 * size.height, accuracy: 1e-6)
    }

    func testFallbackXForBearingMatchesNormalizedX() {
        let size = CGSize(width: 1000, height: 400)
        let proj = Projector(layer: nil, captureAngle: 0, hfovDeg: 106, heading: 0, size: size)

        // Dead center: independent of the aspect-fill vertical offset, x is exactly size.width/2.
        XCTAssertEqual(proj.x(forBearing: 0)!, 500, accuracy: 1e-6)

        // Off-center: wired straight through Geo.normalizedX (already covered by GeometryTests) --
        // this test is about Projector's own composition (offset + scale), not re-deriving the trig.
        let nx = Geo.normalizedX(bearing: 30, hfovDeg: 106, heading: 0)!
        XCTAssertEqual(proj.x(forBearing: 30)!, nx * size.width, accuracy: 1e-6)

        // Outside the HFOV: no screen position.
        XCTAssertNil(proj.x(forBearing: 170))
    }

    func testBearingAndRelativeWiring() {
        let proj = Projector(layer: nil, captureAngle: 0, hfovDeg: 106, heading: 15, size: .zero)
        let box = CGRect(x: 0.4, y: 0.3, width: 0.2, height: 0.2)   // midX = 0.5
        XCTAssertEqual(proj.bearing(forVisionBox: box), Geo.bearing(normalizedX: 0.5, hfovDeg: 106, heading: 15))
        XCTAssertEqual(proj.relative(40), Geo.relative(40, heading: 15))
    }
}
