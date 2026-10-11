import Foundation

/// Tracks the phone→laptop clock offset (SPEC §9: latency measurement only; display never needs it).
///
/// `RelayClient` sends a burst of `clock_ping` on connect and every 10 s;
/// for each `clock_pong` reply, call `recordPong`. We keep the sample with
/// the smallest round trip, per the round-trip-midpoint method the plan
/// specifies, and only ever move the offset when a new sample beats the
/// best RTT seen in the current burst.
final class ClockSync {
    /// laptop_time = phone_time + offset
    private(set) var offset: TimeInterval = 0
    private(set) var bestRtt: TimeInterval = .infinity
    private(set) var sampleCount: Int = 0

    /// Call at the start of each ping burst so a fresh burst can only
    /// improve on itself, not get stuck comparing against a stale RTT from
    /// a burst several seconds ago (network conditions change).
    func beginBurst() {
        bestRtt = .infinity
    }

    /// - Parameters:
    ///   - tPhoneSent: the `t_phone` this ping was sent with (phone epoch seconds).
    ///   - tLaptop: the laptop's `t_laptop` from the matching `clock_pong` (SPEC §12.4).
    func recordPong(tPhoneSent: TimeInterval, tLaptop: TimeInterval) {
        let now = PhoneClock.nowEpoch
        let rtt = now - tPhoneSent
        guard rtt >= 0 else { return } // clock went backwards mid-flight; ignore
        sampleCount += 1
        if rtt < bestRtt {
            bestRtt = rtt
            offset = tLaptop - (tPhoneSent + rtt / 2)
        }
    }

    func laptopTime(forPhoneEpoch phoneEpoch: TimeInterval) -> TimeInterval {
        phoneEpoch + offset
    }

    /// Convenience: convert a CoreMotion/AVFoundation uptime timestamp
    /// straight to laptop time.
    func laptopTime(fromUptime uptime: TimeInterval) -> TimeInterval {
        laptopTime(forPhoneEpoch: PhoneClock.epochSeconds(fromUptime: uptime))
    }
}
