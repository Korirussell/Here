import CoreGraphics
import Foundation

/// The invisible path sound icons travel on: an ellipse around the middle of the view, never drawn.
/// The wearer's front is the top of the path, their right is its right side, behind them is the
/// bottom. An icon sits where its sound's bearing relative to the wearer puts it, so as the wearer
/// turns, the icon slides around the path and stays put relative to the world.
enum Perimeter {
    static func center(size: CGSize) -> CGPoint {
        CGPoint(x: size.width / 2, y: size.height / 2 + size.height * Tunables.perimeterVerticalOffset)
    }

    /// Where a relative bearing (degrees, 0 = ahead, + = to the right) lands on the path.
    /// `stack` > 0 moves a lower-priority icon inward along the same bearing instead of off it.
    static func point(relative rel: Double, size: CGSize, stack: Int = 0) -> CGPoint {
        let shrink = max(0.2, 1 - Tunables.perimeterStackInset * CGFloat(stack))
        let rx = size.width * Tunables.perimeterRadiusX * shrink
        let ry = size.height * Tunables.perimeterRadiusY * shrink
        let a = rel * .pi / 180
        let c = center(size: size)
        return CGPoint(x: c.x + rx * CGFloat(sin(a)), y: c.y - ry * CGFloat(cos(a)))
    }

    /// 0 while the sound is off to the side, rising smoothly to 1 as the wearer turns to face it:
    /// how far the treatment has moved inward from the path toward where the sound actually is.
    static func inwardProgress(relative rel: Double, thresholdDeg: Double = Tunables.inwardThresholdDeg,
                               completeDeg: Double = Tunables.resolveCompleteDeg) -> Double {
        let a = abs(rel)
        guard thresholdDeg > completeDeg else { return a <= completeDeg ? 1 : 0 }
        let k = min(1, max(0, (thresholdDeg - a) / (thresholdDeg - completeDeg)))
        return k * k * (3 - 2 * k)
    }

    /// How many already-placed icons sit within `minSeparationDeg` of this bearing. Icons are placed
    /// in priority order, so the result is how far in the newcomer stacks.
    static func stackIndex(relative rel: Double, placed: [Double],
                           minSeparationDeg: Double = Tunables.perimeterMinSeparationDeg) -> Int {
        placed.filter { abs(Geo.angDiff($0, rel)) < minSeparationDeg }.count
    }

    /// Where the direction marker sits on a notification's ring, as an offset from the ring's center:
    /// straight ahead at the top, clockwise to the right. `relative` is the same body-relative bearing
    /// the belt uses (`Geo.relative`); the belt rounds it to a motor, the marker shows it exactly.
    static func ringMarker(relative rel: Double, radius: CGFloat) -> CGSize {
        let a = rel * .pi / 180
        return CGSize(width: radius * CGFloat(sin(a)), height: -radius * CGFloat(cos(a)))
    }

    static func lerp(_ a: CGPoint, _ b: CGPoint, _ t: Double) -> CGPoint {
        CGPoint(x: a.x + (b.x - a.x) * CGFloat(t), y: a.y + (b.y - a.y) * CGFloat(t))
    }

    static func smooth(_ t: Double) -> Double {
        let k = min(1, max(0, t))
        return k * k * (3 - 2 * k)
    }
}
