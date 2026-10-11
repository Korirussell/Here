import CoreMotion
import Foundation

/// The world bearing the back camera points at, in the phone's own arbitrary
/// zero. The app applies the Calibrate offset: heading = wrap(raw - offset) (README §8 step 4).
struct HeadingSample {
    let headingRawDeg: Double
    let quality: String // "good" | "settling" | "bad"
    let captureUptime: TimeInterval // same clock domain as PhoneClock expects
}

/// Reads device orientation at ~60 Hz and turns it into a heading, following
/// README §8 exactly: **not** `attitude.yaw`, because Euler angles
/// gimbal-lock with the phone upright in the chest harness. Instead we take
/// the attitude as a rotation matrix, rotate the back camera's look vector
/// `(0, 0, -1)` (device −Z) into the `.xArbitraryZVertical` reference frame,
/// drop the vertical component, and take `atan2`.
final class HeadingProvider {
    private let motionManager = CMMotionManager()
    private let startTime = Date()
    private let settlingWindow: TimeInterval = 2.0

    /// Fires on the queue passed to `start`, ~60 Hz.
    var onHeading: ((HeadingSample) -> Void)?

    /// See `Settings.headingSignFlip`: the sign below is our best guess and
    /// must be verified empirically on the actual harness (turning clockwise,
    /// viewed from above, must increase the reported heading). Flip from
    /// Settings if it's backwards rather than editing this file mid-demo.
    var signFlip: Bool = false

    var isAvailable: Bool { motionManager.isDeviceMotionAvailable }

    func start(queue: OperationQueue) {
        guard motionManager.isDeviceMotionAvailable else { return }
        motionManager.deviceMotionUpdateInterval = 1.0 / 60.0
        motionManager.startDeviceMotionUpdates(using: .xArbitraryZVertical, to: queue) { [weak self] motion, _ in
            guard let self, let motion else { return }
            self.handle(motion)
        }
    }

    func stop() {
        motionManager.stopDeviceMotionUpdates()
    }

    private func handle(_ motion: CMDeviceMotion) {
        let r = motion.attitude.rotationMatrix

        // Back camera looks along device -Z. Rotate (0, 0, -1) into the
        // reference frame: v = (-R.m31, -R.m32, -R.m33) (README §8). Works for any mount, landscape included.
        let vx = -r.m31
        let vy = -r.m32
        // (vertical component -r.m33 is dropped; horizontalMagnitude below
        // captures whether the camera is pointing near straight up/down.)

        let horizontalMagnitude = (vx * vx + vy * vy).squareRoot()

        var raw = atan2(vy, vx) * 180.0 / .pi
        if signFlip { raw = -raw }
        raw = Self.wrap(raw)

        let quality: String
        if horizontalMagnitude < 0.2 {
            // Camera pointing close to straight up/down; azimuth is unstable.
            quality = "bad"
        } else if Date().timeIntervalSince(startTime) < settlingWindow {
            quality = "settling"
        } else {
            quality = "good"
        }

        let sample = HeadingSample(
            headingRawDeg: raw,
            quality: quality,
            captureUptime: motion.timestamp
        )
        onHeading?(sample)
    }

    /// Normalize degrees to (-180, 180].
    static func wrap(_ deg: Double) -> Double {
        var d = fmod(deg + 180.0, 360.0)
        if d <= 0 { d += 360.0 }
        return d - 180.0
    }
}
