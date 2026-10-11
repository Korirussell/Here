import CoreGraphics
import Foundation

/// Bearings and camera geometry (README §8). Pure functions, unit-tested in GeometryTests.
///
/// World bearing: degrees from the array's forward direction, clockwise, (-180, 180].
/// Relative (body) bearing: degrees from the wearer's front. heading = world bearing the wearer faces.
enum Geo {
    /// Wrap any angle into (-180, 180].
    static func wrap(_ a: Double) -> Double { wrapDeg(a) }

    /// Signed smallest difference a - b, in (-180, 180].
    static func angDiff(_ a: Double, _ b: Double) -> Double { wrap(a - b) }

    /// relative_bearing = wrap(world_bearing - heading)
    static func relative(_ world: Double, heading: Double) -> Double { wrap(world - heading) }

    /// World bearing of a horizontal position in the upright camera frame, normalized 0 (left) ... 1 (right).
    /// bearing(x) = heading + atan((x - W/2) / f), f = (W/2) / tan(HFOV/2)  ->  normalized form below.
    static func bearing(normalizedX x: Double, hfovDeg: Double, heading: Double) -> Double {
        let half = tan(hfovDeg / 2 * .pi / 180)
        return wrap(heading + atan((x - 0.5) * 2 * half) * 180 / .pi)
    }

    /// Normalized horizontal position (0...1) of a world bearing, or nil when it's outside the field of view.
    static func normalizedX(bearing: Double, hfovDeg: Double, heading: Double) -> Double? {
        let rel = relative(bearing, heading: heading)
        guard abs(rel) < hfovDeg / 2 else { return nil }
        let half = tan(hfovDeg / 2 * .pi / 180)
        return 0.5 + tan(rel * .pi / 180) / (2 * half)
    }

    /// Belt motor 1...8, clockwise from the front: motor = (round(rel / 45) mod 8) + 1 (README §8).
    static func motor(forRelative rel: Double) -> Int {
        var r = rel.truncatingRemainder(dividingBy: 360)
        if r < 0 { r += 360 }
        return Int((r / 45).rounded()) % 8 + 1
    }

    /// Words for the top-right panel: "in front", "to your right", "behind you"...
    static func describe(relative rel: Double) -> String {
        let a = abs(rel)
        if a <= 22.5 { return "in front" }
        if a >= 157.5 { return "behind you" }
        let side = rel > 0 ? "right" : "left"
        if a < 67.5 { return "front \(side)" }
        if a <= 112.5 { return "to your \(side)" }
        return "behind, \(side)"
    }

    /// The field of view across whichever sensor axis ends up horizontal in the *upright* buffer.
    /// `AVCaptureDevice.Format.videoFieldOfView` only ever reports the format's native horizontal
    /// (landscape, unrotated-sensor) angle of view. `HeadingProvider` already works for any physical
    /// mount (it rotates the camera's look vector through the full attitude, not Euler yaw); this is
    /// the matching fix on the bearing-*from-pixel-position* side: mounted in portrait (captureAngle
    /// 90/270, where `Frames.sensorRect` swaps width and height), the upright buffer's x axis is
    /// actually the sensor's *vertical* extent, which has a different -- usually narrower -- angle of
    /// view. Using the landscape value there skews every bearing computed from a pixel position by a
    /// large, orientation-dependent amount, even though the Vision rects themselves are already
    /// correctly rotated (README §14 "Coordinates (where bugs live)").
    static func effectiveHFOVDeg(hfovDeg: Double, vfovDeg: Double, captureAngle: Double) -> Double {
        Frames.swapsDimensions(captureAngle: captureAngle) ? vfovDeg : hfovDeg
    }

    /// Circular mean (unit vectors) and circular standard deviation, both in degrees.
    static func circularMean(_ angles: [Double], weights: [Double]? = nil) -> (mean: Double, spread: Double)? {
        guard !angles.isEmpty else { return nil }
        let w = weights ?? Array(repeating: 1, count: angles.count)
        var sx = 0.0, sy = 0.0, sw = 0.0
        for (a, wi) in zip(angles, w) {
            sx += wi * cos(a * .pi / 180); sy += wi * sin(a * .pi / 180); sw += wi
        }
        guard sw > 0 else { return nil }
        let r = min(hypot(sx, sy) / sw, 1)
        return (wrap(atan2(sy, sx) * 180 / .pi), sqrt(-2 * log(max(r, 1e-9))) * 180 / .pi)
    }
}

/// Vision rects and capture rotation (README §14 "Coordinates (where bugs live)").
///
/// Vision returns normalized rects in the *upright, rotated* buffer with a bottom-left origin.
/// `AVCaptureVideoPreviewLayer.layerRectConverted(fromMetadataOutputRect:)` wants normalized rects in
/// the *unrotated sensor* space with a top-left origin. The data output rotates sensor -> upright
/// clockwise by `captureAngle` (AVCaptureConnection.videoRotationAngle), so we undo that here.
enum Frames {
    /// Vision bottom-left rect -> top-left rect, same (upright) image.
    static func topLeft(_ r: CGRect) -> CGRect {
        CGRect(x: r.minX, y: 1 - r.minY - r.height, width: r.width, height: r.height)
    }

    /// Capture rotation snapped to one of 0/90/180/270 (`AVCaptureConnection.videoRotationAngle`
    /// reports exact multiples in practice; the mod-360 guards float noise and negative input).
    static func quantizedAngle(_ captureAngle: Double) -> Int {
        Int((captureAngle.truncatingRemainder(dividingBy: 360) + 360).truncatingRemainder(dividingBy: 360).rounded()) % 360
    }

    /// True when this rotation swaps width and height -- i.e. the phone is mounted in portrait
    /// (captureAngle 90 or 270), not landscape (0 or 180).
    static func swapsDimensions(captureAngle: Double) -> Bool {
        let a = quantizedAngle(captureAngle)
        return a == 90 || a == 270
    }

    /// Upright top-left normalized rect -> sensor (metadata-output) normalized rect.
    static func sensorRect(fromUprightTopLeft r: CGRect, captureAngle: Double) -> CGRect {
        let (u, v, w, h) = (r.minX, r.minY, r.width, r.height)
        switch quantizedAngle(captureAngle) {
        case 90:  return CGRect(x: v, y: 1 - u - w, width: h, height: w)
        case 180: return CGRect(x: 1 - u - w, y: 1 - v - h, width: w, height: h)
        case 270: return CGRect(x: 1 - v - h, y: u, width: h, height: w)
        default:  return r
        }
    }

    /// Upright top-left normalized point -> sensor normalized point.
    static func sensorPoint(fromUprightTopLeft p: CGPoint, captureAngle: Double) -> CGPoint {
        let r = sensorRect(fromUprightTopLeft: CGRect(x: p.x, y: p.y, width: 0, height: 0), captureAngle: captureAngle)
        return CGPoint(x: r.minX, y: r.minY)
    }
}
