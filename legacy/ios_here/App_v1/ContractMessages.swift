import Foundation

/// Builds the common envelope every message carries (HERE_PLAN.md §8.2).
///
/// We use `[String: Any]` + `JSONSerialization` instead of a `Codable` type
/// per message. The contract (HERE_PLAN.md §8) is still young — "change it
/// only through the integrator" — and loose dictionaries make it trivial to
/// match field names exactly without keeping a parallel Swift type in sync
/// for every message this app sends or receives.
enum Contract {
    static let version = 1
    static let src = "ios_here"

    private static var seqCounter = 0
    private static let seqLock = NSLock()

    private static func nextSeq() -> Int {
        seqLock.lock()
        defer { seqLock.unlock() }
        seqCounter += 1
        return seqCounter
    }

    /// `t` is the observation time (converted to laptop clock where
    /// applicable), never the send time (HERE_PLAN.md §7).
    static func envelope(type: String, t: TimeInterval, extra: [String: Any] = [:]) -> [String: Any] {
        var msg: [String: Any] = [
            "type": type,
            "v": version,
            "src": src,
            "t": t,
            "seq": nextSeq(),
        ]
        for (key, value) in extra { msg[key] = value }
        return msg
    }

    static func jsonData(_ dict: [String: Any]) -> Data? {
        try? JSONSerialization.data(withJSONObject: dict, options: [])
    }

    static func jsonText(_ dict: [String: Any]) -> String? {
        jsonData(dict).flatMap { String(data: $0, encoding: .utf8) }
    }

    static func parse(_ text: String) -> [String: Any]? {
        guard let data = text.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }
}
