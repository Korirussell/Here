import Foundation

/// Converts "uptime" timestamps — the clock domain shared by
/// `CMDeviceMotion.timestamp` and `CMSampleBuffer` presentation times (both
/// are mach-absolute-time based, i.e. the same domain as
/// `ProcessInfo.processInfo.systemUptime`) — into phone wall-clock Unix
/// epoch seconds, so they can be compared with `Date()` and shifted into
/// laptop time by `ClockSync`'s offset (README §9).
///
/// This is a best-effort conversion recomputed per call rather than cached
/// once at launch, since `systemUptime` and `Date()` can drift slightly
/// relative to each other (e.g. NTP adjustments); recomputing keeps the
/// error bounded to roughly the cost of the two back-to-back reads below.
enum PhoneClock {
    static func epochSeconds(fromUptime uptime: TimeInterval) -> TimeInterval {
        let nowEpoch = Date().timeIntervalSince1970
        let nowUptime = ProcessInfo.processInfo.systemUptime
        return uptime + (nowEpoch - nowUptime)
    }

    static var nowEpoch: TimeInterval { Date().timeIntervalSince1970 }
}
