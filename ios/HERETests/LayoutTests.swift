import XCTest
@testable import HERE

/// `Layout.captions` (README §14 "What the screen shows" + the accessibility rule "never cover a mouth").
/// `proj` always uses `layer: nil`, which routes through `Projector`'s pure-geometry fallback math
/// (no `AVCaptureVideoPreviewLayer` needed), so these are deterministic pure-function tests.
final class LayoutTests: XCTestCase {
    let size = CGSize(width: 800, height: 600)

    private func cap(_ id: String, _ speaker: Speaker, bearing: Double) -> CaptionState {
        CaptionState(uttId: id, speaker: speaker, bearing: bearing, started: 0, updated: 0)
    }

    private func proj(heading: Double = 0, hfovDeg: Double = 106) -> Projector {
        Projector(layer: nil, captureAngle: 0, hfovDeg: hfovDeg, heading: heading, size: size)
    }

    // MARK: above the head / below the chin ("never cover a mouth")

    func testAboveHeadWhenThereIsRoom() {
        let faceRect = CGRect(x: 300, y: 300, width: 100, height: 150)   // plenty of room above
        let c = cap("u1", .person(id: 1, label: "Person 1"), bearing: 0)
        let out = Layout.captions([c], faces: [], rects: [1: faceRect], proj: proj(), size: size)

        XCTAssertEqual(out.count, 1)
        let p = out[0]
        XCTAssertEqual(p.tail, .bottom)
        XCTAssertEqual(p.chevron, .none)
        // The box (centered at point.y, height 96) must sit fully above the face, 14pt gap.
        XCTAssertEqual(p.point.y, faceRect.minY - 14 - Layout.boxHeight / 2, accuracy: 1e-9)
        XCTAssertLessThanOrEqual(p.point.y + Layout.boxHeight / 2, faceRect.minY,
                                  "caption box must not reach the face's top edge")
    }

    func testBelowChinWhenNoRoomAbove() {
        let faceRect = CGRect(x: 300, y: 20, width: 100, height: 150)    // near the top: no room above
        let c = cap("u1", .person(id: 1, label: "Person 1"), bearing: 0)
        let out = Layout.captions([c], faces: [], rects: [1: faceRect], proj: proj(), size: size)

        let p = out[0]
        XCTAssertEqual(p.tail, .top)
        XCTAssertEqual(p.point.y, faceRect.maxY + 14 + Layout.boxHeight / 2, accuracy: 1e-9)
        XCTAssertGreaterThanOrEqual(p.point.y - Layout.boxHeight / 2, faceRect.maxY,
                                     "caption box must not reach the face's bottom edge")
    }

    func testForcedAboveWhenBelowWouldOverflowTheBottom() {
        // A short screen where neither "has room above" nor "below fits" is true on its own merits:
        // the function must still pick *above* rather than let the caption run off-screen below.
        let shortScreen = CGSize(width: 800, height: 150)
        let faceRect = CGRect(x: 300, y: 40, width: 100, height: 20)
        let c = cap("u1", .person(id: 1, label: "Person 1"), bearing: 0)
        let out = Layout.captions([c], faces: [], rects: [1: faceRect], proj: proj(), size: shortScreen)

        XCTAssertEqual(out[0].tail, .bottom)   // "above" placement, even without the usual headroom margin
    }

    // MARK: off-camera: edge placement with a chevron toward the bearing

    func testOffCameraTrailingEdgeBehind() {
        let c = cap("u1", .offCamera, bearing: 180)   // directly behind, heading 0
        let out = Layout.captions([c], faces: [], rects: [:], proj: proj(heading: 0), size: size)

        let p = out[0]
        XCTAssertEqual(p.chevron, .trailing)
        XCTAssertEqual(p.tail, .none)
        XCTAssertEqual(p.hint, Geo.describe(relative: 180))
        XCTAssertEqual(p.point.x, size.width - Layout.margin - 34 - Layout.boxWidth / 2, accuracy: 1e-9)
    }

    func testOffCameraLeadingEdgeToTheLeft() {
        let c = cap("u1", .offCamera, bearing: -100)   // behind-left, heading 0
        let out = Layout.captions([c], faces: [], rects: [:], proj: proj(heading: 0), size: size)

        let p = out[0]
        XCTAssertEqual(p.chevron, .leading)
        XCTAssertEqual(p.hint, "to your left")
        XCTAssertEqual(p.point.x, Layout.margin + 34 + Layout.boxWidth / 2, accuracy: 1e-9)
    }

    func testInViewButNoFaceFound() {
        // Within the camera's HFOV, but no tracked face at that id: the middle branch (no chevron,
        // placed at the bearing's screen x, no hint).
        let c = cap("u1", .person(id: 99, label: "Person 2"), bearing: 10)
        let out = Layout.captions([c], faces: [], rects: [:], proj: proj(heading: 0, hfovDeg: 106), size: size)

        let p = out[0]
        XCTAssertEqual(p.chevron, .none)
        XCTAssertEqual(p.tail, .bottom)
        XCTAssertNil(p.hint)
    }

    // MARK: no overlap between two close on-camera speakers

    func testTwoCloseFacesDoNotOverlap() {
        let r1 = CGRect(x: 300, y: 300, width: 100, height: 150)
        let r2 = CGRect(x: 340, y: 300, width: 100, height: 150)   // close enough in x to collide
        let c1 = cap("u1", .person(id: 1, label: "Person 1"), bearing: 0)
        let c2 = cap("u2", .person(id: 2, label: "Person 2"), bearing: 0)
        let out = Layout.captions([c1, c2], faces: [], rects: [1: r1, 2: r2], proj: proj(), size: size)

        let p1 = out.first { $0.id == "u1" }!
        let p2 = out.first { $0.id == "u2" }!
        // The first stays at its natural "above the head" position...
        XCTAssertEqual(p1.point.y, r1.minY - 14 - Layout.boxHeight / 2, accuracy: 1e-9)
        // ...the second is lifted clear of it, not stacked on top.
        XCTAssertEqual(p2.point.y, p1.point.y - Layout.boxHeight - 8, accuracy: 1e-9)
        XCTAssertGreaterThanOrEqual(abs(p2.point.y - p1.point.y), Layout.boxHeight + 6)
    }

    func testTwoOffCameraCaptionsOnTheSameEdgeStack() {
        // Built-in edge stacking (separate from the generic separate() pass): same side, offset by row.
        let c1 = cap("u1", .offCamera, bearing: 175)
        let c2 = cap("u2", .offCamera, bearing: 178)
        let out = Layout.captions([c1, c2], faces: [], rects: [:], proj: proj(heading: 0), size: size)

        let p1 = out.first { $0.id == "u1" }!
        let p2 = out.first { $0.id == "u2" }!
        XCTAssertEqual(p1.chevron, .trailing)
        XCTAssertEqual(p2.chevron, .trailing)
        XCTAssertEqual(p2.point.y, p1.point.y + Layout.boxHeight + 12, accuracy: 1e-9)
    }
}
