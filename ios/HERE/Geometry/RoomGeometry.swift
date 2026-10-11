import CoreGraphics
import Foundation

/// The room, seen from above, with the microphone array at the origin.
///
/// The array reports a *direction of arrival*: the angle at which a sound reaches the array (0 = the
/// array's calibrated front, clockwise positive). It does not report where the source is, or how far.
/// The phone measures faces and objects as directions *from the wearer*. Those two kinds of angle
/// are only the same if the wearer stands at the array; anywhere else they differ by parallax, and
/// by more the closer the source is.
///
/// Position convention: metres, x to the array's right (+90 deg), y along its front (0 deg). A point
/// at bearing b and distance r from the array is (r sin b, r cos b).
struct RoomGeometry: Equatable {
    /// Where the wearer stands, relative to the array. Zero = at the array (the original assumption).
    var wearerDistanceM = 0.0
    var wearerBearingDeg = 0.0           // direction *from the array to the wearer*, array frame
    /// How far from the array sound sources are assumed to be when nothing better is known.
    var sourceRadiusM = Tunables.assumedSourceRadiusM

    var wearer: CGPoint { Self.point(bearing: wearerBearingDeg, distance: wearerDistanceM) }
    var isDisplaced: Bool { wearerDistanceM > 0.01 }

    static func point(bearing: Double, distance: Double) -> CGPoint {
        let a = bearing * .pi / 180
        return CGPoint(x: distance * sin(a), y: distance * cos(a))
    }

    static func bearing(from a: CGPoint, to b: CGPoint) -> Double {
        Geo.wrap(atan2(Double(b.x - a.x), Double(b.y - a.y)) * 180 / .pi)
    }

    /// Array direction-of-arrival -> the direction of that source *from the wearer*, assuming the
    /// source sits `radius` metres from the array along that direction. Exact only if the radius is
    /// right; see `listenerUncertainty`.
    func listenerBearing(micBearing: Double, radius: Double? = nil) -> Double {
        guard isDisplaced else { return Geo.wrap(micBearing) }
        let source = Self.point(bearing: micBearing, distance: max(0.05, radius ?? sourceRadiusM))
        let w = wearer
        if hypot(Double(source.x - w.x), Double(source.y - w.y)) < 1e-6 { return Geo.wrap(micBearing) }
        return Self.bearing(from: w, to: source)
    }

    /// How wrong `listenerBearing` could be, in degrees, because the source's distance is a guess:
    /// the spread between assuming it is half as far and twice as far. Zero at the array.
    func listenerUncertainty(micBearing: Double) -> Double {
        guard isDisplaced else { return 0 }
        let near = listenerBearing(micBearing: micBearing, radius: sourceRadiusM / 2)
        let far = listenerBearing(micBearing: micBearing, radius: sourceRadiusM * 2)
        let mid = listenerBearing(micBearing: micBearing)
        return max(abs(Geo.angDiff(near, mid)), abs(Geo.angDiff(far, mid)))
    }

    /// Something the wearer sees at `bearing` (from the wearer) and `distance` metres away: at what
    /// angle would the *array* hear it? This is the exact comparison for attribution -- no guess
    /// about the source's distance from the array is needed when the entity's own distance is known.
    func micBearing(ofEntityAt bearing: Double, distance: Double) -> Double {
        guard isDisplaced else { return Geo.wrap(bearing) }
        let w = wearer
        let e = Self.point(bearing: bearing, distance: max(0.05, distance))
        return Self.bearing(from: .zero, to: CGPoint(x: w.x + e.x, y: w.y + e.y))
    }

    /// Rough distance to a face from how tall it looks: a face is about `faceHeightM` tall.
    static func faceDistance(boxHeight: Double, vfovDeg: Double) -> Double {
        let angle = max(0.5, boxHeight * vfovDeg) * .pi / 180
        return min(Tunables.faceDistanceMaxM, Tunables.faceHeightM / (2 * tan(angle / 2)))
    }

    /// The error made by ignoring all of this (treating the array's angle as the wearer's), for a
    /// source at `sourceDistance` from the array.
    func parallaxError(micBearing: Double, sourceDistance: Double) -> Double {
        abs(Geo.angDiff(listenerBearing(micBearing: micBearing, radius: sourceDistance), micBearing))
    }
}

/// Measures whether the system is actually calibrated instead of assuming it is.
///
/// A sample is one moment where the truth is known two ways: where a sound *should* be heard from
/// (a speaker the camera can see, or a helper standing on a marked spot) and where the array says it
/// came from. Enough samples give the systematic error, the jitter, and a diagnosis of *what kind* of
/// error it is, which is what tells you whether to re-run Calibrate, flip a sign, or move the wearer mark.
struct CalibrationCheck: Equatable {
    struct Sample: Equatable {
        var expected: Double             // array-frame bearing the sound should have
        var measured: Double             // array-frame bearing the array reported
        var note = ""
        var error: Double { Geo.angDiff(measured, expected) }
    }

    enum Quality: String { case good = "GOOD", fair = "FAIR", bad = "BAD", unknown = "NOT MEASURED" }

    private(set) var samples: [Sample] = []

    mutating func add(expected: Double, measured: Double, note: String = "") {
        samples.append(Sample(expected: Geo.wrap(expected), measured: Geo.wrap(measured), note: note))
        if samples.count > Tunables.calibrationMaxSamples { samples.removeFirst() }
    }

    mutating func reset() { samples = [] }

    /// Circular mean of the errors: the constant offset.
    var meanError: Double { Geo.circularMean(samples.map(\.error))?.mean ?? 0 }
    /// Circular standard deviation of the errors: how much it wobbles.
    var jitter: Double { Geo.circularMean(samples.map(\.error))?.spread ?? 0 }
    var maxError: Double { samples.map { abs($0.error) }.max() ?? 0 }
    /// Typical error after removing the constant offset would not help: mean absolute error as is.
    var meanAbsError: Double { samples.isEmpty ? 0 : samples.map { abs($0.error) }.reduce(0, +) / Double(samples.count) }

    var quality: Quality {
        guard samples.count >= Tunables.calibrationMinSamples else { return .unknown }
        if meanAbsError <= Tunables.calibrationGoodDeg { return .good }
        return meanAbsError <= Tunables.calibrationFairDeg ? .fair : .bad
    }

    /// Degrees to add to the heading (i.e. subtract from the stored heading offset) to cancel a
    /// constant offset. Only meaningful when the diagnosis is a constant offset.
    var suggestedHeadingCorrection: Double { meanError }

    /// What kind of error this is. Needs samples from more than one direction to tell them apart.
    var diagnosis: String {
        guard samples.count >= Tunables.calibrationMinSamples else {
            return "Need \(Tunables.calibrationMinSamples - samples.count) more sample(s)."
        }
        if meanAbsError <= Tunables.calibrationGoodDeg { return "Aligned." }
        let spreadOfDirections = Geo.circularMean(samples.map(\.expected))?.spread ?? 0
        // Mirrored: the array (or the heading) counts the other way round. measured ~ -expected.
        let mirrored = samples.map { abs(Geo.angDiff($0.measured, -$0.expected)) }.reduce(0, +) / Double(samples.count)
        if spreadOfDirections > Tunables.calibrationDirectionSpreadDeg, mirrored <= Tunables.calibrationFairDeg, mirrored < meanAbsError / 2 {
            return "Mirrored: left and right are swapped. Flip the heading sign (or the array's clockwise setting)."
        }
        if abs(abs(meanError) - 180) <= Tunables.calibrationFairDeg, jitter <= Tunables.calibrationFairDeg {
            return "Reversed by 180 deg: Calibrate was tapped facing the opposite way (toward the array instead of along its front, or vice versa)."
        }
        if jitter <= Tunables.calibrationFairDeg {
            return String(format: "Constant offset of %+.0f deg: re-run Calibrate, or apply the correction.", meanError)
        }
        if spreadOfDirections <= Tunables.calibrationDirectionSpreadDeg {
            return "Inconsistent from one direction: noisy direction finding (echo, a second talker) rather than calibration."
        }
        return "Error changes with direction: geometry. The wearer is not where the room model says (set the wearer's position), or sources are much nearer/farther than assumed."
    }
}
