import XCTest
@testable import HERE

/// SPEC §8 geometry. Mirrors laptop/tests geometry cases so both sides agree.
final class GeometryTests: XCTestCase {
    func testWrap() {
        XCTAssertEqual(Geo.wrap(180), 180)
        XCTAssertEqual(Geo.wrap(-180), 180)
        XCTAssertEqual(Geo.wrap(190), -170)
        XCTAssertEqual(Geo.wrap(-190), 170)
        XCTAssertEqual(Geo.wrap(720), 0)
        XCTAssertEqual(Geo.wrap(540), 180)
    }

    func testRelative() {
        XCTAssertEqual(Geo.relative(41, heading: 0), 41)
        XCTAssertEqual(Geo.relative(-170, heading: 170), 20)
        XCTAssertEqual(Geo.relative(10, heading: -175), -175)
    }

    func testBearingPixelRoundTrip() {
        for b in [-30.0, 0, 12.5, 30] {
            let x = Geo.normalizedX(bearing: b, hfovDeg: 70, heading: 0)!
            XCTAssertEqual(Geo.bearing(normalizedX: x, hfovDeg: 70, heading: 0), b, accuracy: 1e-9)
        }
        XCTAssertEqual(Geo.normalizedX(bearing: 0, hfovDeg: 106, heading: 0)!, 0.5, accuracy: 1e-12)
        XCTAssertNil(Geo.normalizedX(bearing: 100, hfovDeg: 70, heading: 0))
        XCTAssertNil(Geo.normalizedX(bearing: 180, hfovDeg: 106, heading: 0))
        // Frame edge is exactly half the FOV away from the heading.
        XCTAssertEqual(Geo.bearing(normalizedX: 1.0, hfovDeg: 106, heading: 10), 63, accuracy: 1e-9)
    }

    func testMotorForRelativeBearing() {
        let rels: [Double] = [0, 45, 90, 135, 180, -135, -90, -45, 20, -20, 22, 23, -179]
        XCTAssertEqual(rels.map(Geo.motor(forRelative:)), [1, 2, 3, 4, 5, 6, 7, 8, 1, 1, 1, 2, 5])
        // SPEC §12.11 example: relative ~39 deg -> motor 2
        XCTAssertEqual(Geo.motor(forRelative: 39), 2)
    }

    func testDescribe() {
        XCTAssertEqual(Geo.describe(relative: 0), "in front")
        XCTAssertEqual(Geo.describe(relative: 179), "behind you")
        XCTAssertEqual(Geo.describe(relative: -90), "to your left")
        XCTAssertEqual(Geo.describe(relative: 40), "front right")
    }

    func testCircularMeanAcrossTheSeam() {
        let r = Geo.circularMean([170, -170])!
        XCTAssertEqual(abs(r.mean), 180, accuracy: 1e-6)
        XCTAssertLessThan(r.spread, 15)
    }

    func testVisionRectToSensorRect() {
        let upright = CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.4)
        XCTAssertEqual(Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 0), upright)
        let r180 = Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 180)
        XCTAssertEqual(r180.minX, 0.6, accuracy: 1e-9)
        XCTAssertEqual(r180.minY, 0.4, accuracy: 1e-9)
        let r90 = Frames.sensorRect(fromUprightTopLeft: upright, captureAngle: 90)
        XCTAssertEqual(r90.width, 0.4, accuracy: 1e-9)
        XCTAssertEqual(r90.height, 0.3, accuracy: 1e-9)
        // Round trip through two half turns is the identity.
        let back = Frames.sensorRect(fromUprightTopLeft: r180, captureAngle: 180)
        XCTAssertEqual(back.minX, upright.minX, accuracy: 1e-9)
        XCTAssertEqual(back.minY, upright.minY, accuracy: 1e-9)
        // Vision's bottom-left origin -> top-left.
        XCTAssertEqual(Frames.topLeft(CGRect(x: 0, y: 0.1, width: 0.2, height: 0.3)).minY, 0.6, accuracy: 1e-9)
    }

    /// Any mount orientation (SPEC §8's "works for any mount" promise on the heading side) needs
    /// the matching promise on the bearing-from-pixel side: landscape (0/180) keeps the camera's
    /// native horizontal FOV; portrait (90/270, where `sensorRect` swaps width/height) must switch to
    /// the vertical FOV, not silently keep using the (wrong, usually wider) horizontal one.
    func testSwapsDimensionsForPortraitMounts() {
        XCTAssertFalse(Frames.swapsDimensions(captureAngle: 0))
        XCTAssertTrue(Frames.swapsDimensions(captureAngle: 90))
        XCTAssertFalse(Frames.swapsDimensions(captureAngle: 180))
        XCTAssertTrue(Frames.swapsDimensions(captureAngle: 270))
        // Float noise / negative input / a full turn over all still quantize to the same four cases.
        XCTAssertTrue(Frames.swapsDimensions(captureAngle: 89.6))
        XCTAssertTrue(Frames.swapsDimensions(captureAngle: -90))
        XCTAssertTrue(Frames.swapsDimensions(captureAngle: 450))
        XCTAssertFalse(Frames.swapsDimensions(captureAngle: 360))
    }

    func testEffectiveHFOVPicksTheAxisThatIsActuallyHorizontal() {
        let hfov = 106.0, vfov = 75.0   // landscape ultrawide: wide across the sensor's native width
        XCTAssertEqual(Geo.effectiveHFOVDeg(hfovDeg: hfov, vfovDeg: vfov, captureAngle: 0), hfov)
        XCTAssertEqual(Geo.effectiveHFOVDeg(hfovDeg: hfov, vfovDeg: vfov, captureAngle: 180), hfov)
        XCTAssertEqual(Geo.effectiveHFOVDeg(hfovDeg: hfov, vfovDeg: vfov, captureAngle: 90), vfov)
        XCTAssertEqual(Geo.effectiveHFOVDeg(hfovDeg: hfov, vfovDeg: vfov, captureAngle: 270), vfov)
    }
}
