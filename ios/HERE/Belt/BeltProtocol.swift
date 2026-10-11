import Foundation

/// BLE identifiers of the belt the team built: belt/firmware/here_belt_6motor (SERVICE_UUID / CMD_UUID there).
/// Change both together. The 8-motor `here_belt` firmware's UUIDs are kept as `legacy*` so a phone that saved
/// them in Settings still finds the new belt.
enum BeltUUID {
    static let service = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
    static let command = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"     // write (with or without response), 3-4 bytes
    static let deviceName = "HERE-Belt"
    static let legacyService = "BFEDC85A-C49B-4AED-91CF-69B7E34B2772"
    static let legacyCommand = "BFEDC85B-C49B-4AED-91CF-69B7E34B2772"

    /// A saved Settings value, with empty or the old firmware's UUIDs meaning "the current belt".
    static func resolve(_ saved: String, current: String, legacy: String) -> String {
        let s = saved.trimmingCharacters(in: .whitespaces)
        return s.isEmpty || s.caseInsensitiveCompare(legacy) == .orderedSame ? current : s
    }
}

/// Patterns timed on the ESP32 (README §16). Raw values are the packet's pattern_id.
enum BeltPattern: UInt8, CaseIterable {
    case stop = 0, speech = 1, nameCalled = 2, alert = 3, emergency = 4, tick = 5, test = 6

    var label: String {
        switch self {
        case .stop: "stop"
        case .speech: "speech"
        case .nameCalled: "name"
        case .alert: "alert"
        case .emergency: "emergency"
        case .tick: "tick"
        case .test: "test"
        }
    }
}

struct BeltCommand: Equatable {
    var pattern: BeltPattern
    var motor: Int            // 1...8, 0 = none (the on-screen 8-dot indicator; the wire uses `relativeDeg`)
    var motor2: Int = 0
    var strength: Int         // 0...255
    var priority: Int         // higher interrupts lower
    /// Body-relative direction to buzz toward (deg, clockwise from the wearer's front); nil = the direction of
    /// `motor`. The 6-motor belt picks its own nearest motor(s) from this.
    var relativeDeg: Double? = nil

    /// 6-byte packet: [version 0x01, pattern_id, motor 1..8, motor2 (0 = none), strength, priority].
    var packet: Data {
        Data([0x01, pattern.rawValue, UInt8(clamping: motor), UInt8(clamping: motor2),
              UInt8(clamping: strength), UInt8(clamping: priority)])
    }
}

/// The belt the team built (belt/firmware/here_belt_6motor): 6 motors, no patterns on the ESP32, so the phone
/// times each pattern as a list of writes. Firmware commands:
///   single  [motor 0-5, strength, duration in 10 ms]
///   group   [0xFE, bitmask of motors, strength, duration in 10 ms]
///   stop    [0xFF, 0, 0]
/// The ESP32 turns each motor off when its own duration ends and stops everything on a BLE disconnect.
enum BeltSixMotor {
    /// Where each logical motor sits on the wearer, deg clockwise from the front (measured on the real belt,
    /// 2026-10-11): motor 0 back-right ... motor 5 right. Same table as belt/controller/index.html.
    static let degrees: [Double] = [150, 270, 30, 210, 330, 90]
    static let allMask: UInt8 = 0b11_1111
    /// Two motors this close in distance to the target both buzz (straight ahead / behind fall between two).
    static let tieDeg = 10.0

    struct Step: Equatable {
        var at: Double        // seconds after the command starts
        var bytes: [UInt8]
    }

    static func mask(forRelative rel: Double) -> UInt8 {
        let d = { (a: Double) in abs(Geo.wrap(a - rel)) }
        let order = degrees.indices.sorted { d(degrees[$0]) < d(degrees[$1]) }
        var m = UInt8(1) << UInt8(order[0])
        if d(degrees[order[1]]) - d(degrees[order[0]]) < tieDeg { m |= UInt8(1) << UInt8(order[1]) }
        return m
    }

    /// The command's direction: its own relative bearing, else the centre of its 8-motor index.
    static func direction(of cmd: BeltCommand) -> Double {
        cmd.relativeDeg ?? Geo.wrap(Double(max(cmd.motor, 1) - 1) * 45)
    }

    /// Timed writes for one command. Durations are in seconds; the last step's buzz ends at `length(of:)`.
    static func steps(for cmd: BeltCommand) -> [Step] {
        let s = UInt8(clamping: cmd.strength)
        let toward = mask(forRelative: direction(of: cmd))
        func pulse(_ at: Double, _ mask: UInt8, _ seconds: Double) -> Step {
            Step(at: at, bytes: [0xFE, mask, s, UInt8(clamping: Int((seconds * 100).rounded()))])
        }
        func repeated(_ n: Int, on: Double, off: Double, _ mask: UInt8) -> [Step] {
            (0..<n).map { pulse(Double($0) * (on + off), mask, on) }
        }
        switch cmd.pattern {
        case .stop: return [Step(at: 0, bytes: [0xFF, 0, 0])]
        case .tick: return [pulse(0, toward, 0.06)]
        case .speech: return [pulse(0, toward, 0.15)]
        case .alert: return repeated(3, on: 0.25, off: 0.15, toward)
        case .nameCalled: return repeated(2, on: 0.45, off: 0.2, toward)
        case .emergency:
            // The whole belt, then the direction, five times (about 3 s; the old firmware capped it at 10 s).
            return (0..<5).flatMap { i in [pulse(Double(i) * 0.6, allMask, 0.25), pulse(Double(i) * 0.6 + 0.3, toward, 0.2)] }
        case .test:
            // Clockwise sweep from the front-right.
            let order = degrees.indices.sorted { degrees[$0] < degrees[$1] }
            return order.enumerated().map { i, m in pulse(Double(i) * 0.25, UInt8(1) << UInt8(m), 0.2) }
        }
    }

    /// When the command's last buzz ends (s).
    static func length(of cmd: BeltCommand) -> Double {
        steps(for: cmd).map { $0.at + Double($0.bytes.count == 4 ? $0.bytes[3] : 0) / 100 }.max() ?? 0
    }
}

/// When to buzz (README §14 "Belt logic"). Pure; unit-tested in BeltPolicyTests.
///
/// - Speech: `speech` when someone *starts* talking and |relative bearing| > 30 deg;
///   not again for the same person within 5 s.
/// - Alerts: always buzz toward the bearing (name_called -> nameCalled, priority 3 -> emergency).
/// - At most one non-alert pattern per 1.5 s. Higher priority interrupts lower (the ESP32 enforces it).
final class BeltPolicy {
    var frontDeg = Tunables.beltFrontDeg
    var samePersonS = Tunables.beltSamePersonS
    var nonAlertGapS = Tunables.beltNonAlertGapS

    private var lastByPerson: [String: Double] = [:]
    private var lastNonAlert = -Double.infinity

    func speechStarted(personKey: String, relative rel: Double, strength: Int, now: Double) -> BeltCommand? {
        guard abs(rel) > frontDeg else { return nil }                         // silence is a feature
        if let last = lastByPerson[personKey], now - last < samePersonS { return nil }
        guard now - lastNonAlert >= nonAlertGapS else { return nil }
        lastByPerson[personKey] = now
        lastNonAlert = now
        return BeltCommand(pattern: .speech, motor: Geo.motor(forRelative: rel), strength: strength, priority: 1,
                           relativeDeg: rel)
    }

    func alert(kind: AlertKind, priority: Int, relative rel: Double?, strength: Int) -> BeltCommand {
        let pattern: BeltPattern = kind == .nameCalled ? .nameCalled : (priority >= 3 ? .emergency : .alert)
        // No direction known: buzz the front motor so the wearer still feels it.
        return BeltCommand(pattern: pattern, motor: rel.map(Geo.motor(forRelative:)) ?? 1,
                           strength: strength, priority: 1 + priority, relativeDeg: rel)
    }

    /// A caption or sound the laptop's attention model judged important or urgent (`perception_enrichment`):
    /// buzzed like an alert of that priority (3 -> emergency), toward its bearing.
    func attention(priority: Int, relative rel: Double?, strength: Int) -> BeltCommand {
        BeltCommand(pattern: priority >= 3 ? .emergency : .alert, motor: rel.map(Geo.motor(forRelative:)) ?? 1,
                    strength: strength, priority: 1 + priority, relativeDeg: rel)
    }

    func test(strength: Int) -> BeltCommand {
        BeltCommand(pattern: .test, motor: 0, strength: strength, priority: 9)
    }
}
