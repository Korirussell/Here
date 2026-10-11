import CoreGraphics
import Foundation

/// Which AR layer draws over the camera. `legacy` is the SPEC §14 look (rings, top-right panel, edge
/// glows); `spatialV2` is the calm-until-something-happens direction built on `SpatialPresence`.
/// Kept side by side until the replacement is proven on the rig (Settings > Developer).
enum SpatialVisualStyle: String, Codable, CaseIterable {
    case legacy
    case spatialV2
}

/// What a color is allowed to mean. People get identity colors (shared by their aura and caption);
/// sounds get a color for their category or urgency and never a color of their own.
enum PresenceTone: Equatable {
    case danger          // red: alarm, emergency, dangerous impact
    case environment     // green: knock, footsteps, dog, phone, music, vehicle...
    case human           // blue: a voice or human sound not (yet) attributed to a SessionPerson
    case neutral         // unknown / unclassified: no invented meaning
    case person(Int)     // an attributed person's identity color
}

/// One thing worth showing somewhere in the space around the wearer: derived, iOS-only presentation
/// state. Built by `PresenceEngine` from what the app already holds (captions, alerts, sound tags,
/// tracked faces, pins); nothing here goes on the wire.
struct SpatialPresence: Identifiable, Equatable {
    enum SourceType: String { case speech, sound, alert, person }

    /// Visual priority. Higher levels outrank lower ones when indicators compete.
    enum Level: Int, Comparable {
        case ambient = 1     // subtle, directional, gone quickly
        case relevant        // clear icon, stays long enough to orient toward
        case speech          // a person talking
        case important       // the wearer's name, an alarm, a doorbell
        static func < (a: Level, b: Level) -> Bool { a.rawValue < b.rawValue }
    }

    let id: String
    var sourceType: SourceType
    var worldBearing: Double?            // nil: we know it happened, not where
    var category: SoundCategory
    var icon: String                     // SF Symbol
    var tone: PresenceTone
    var label: String?                   // words under the icon, where they help
    var prominence: Double               // 0...1, above the room's baseline
    var urgency: Double                  // 0...1
    var persistence: Double              // seconds this has been going on
    var score: Double                    // relevance; orders presences within a level
    var level: Level
    var firstSeen: Double
    var lastSeen: Double
    var isPinned = false
    var associatedTrackedFaceID: Int? = nil
    var opacity = 1.0                    // prominence x fade x dissolve, ready to draw
    var speaking = false
    var active = true                    // still happening (an alert that hasn't ended)
    var count = 1
    var occurrence: String? = nil        // changes each time the sound happens again -> one pulse
    var pulseCount = 0
    var captionId: String? = nil         // the utterance whose caption belongs to this presence
    var resolvedAt: Double? = nil        // when it reached the middle of the view
    var handoffProgress: Double? = nil   // 0...1 while a notification resolves into a tracked face

    var personId: Int? = nil             // the SessionPerson this is, when it is one
    var objectId: Int? = nil             // the SceneObject this sound is attributed to, when evidence supports one
    var objectBox: CGRect? = nil         // that object's region, only while it is actually in view
    var objectPrecise = false            // that region is a tight detector box
    var associationConfidence = 0.0      // fusion score of the attribution
    var intensity = 0.0                  // current, smoothed 0...1: the only thing opacity encodes
    var tier = 1.0                       // priority when there are more notifications than places
    var captionText: String? = nil       // a pinned person out of view: their words ride with the notification
    var captionPending = ""
    var stale = false                    // a person whose direction is no longer known; none is shown
    // Inputs to the final opacity, set by whoever builds the presence.
    var fade = 1.0                       // end-of-life fade, the same for every sound
    var opacityFloor = 0.0               // urgency may hold a minimum; it never replaces intensity
    var fixedOpacity: Double? = nil      // a quiet pinned person has no intensity to show
    var opacityCap: Double? = nil        // judged ambient by the laptop: minimal, however intense

    var visualPriority: Level { level }

    /// Sort key, used only when capacity is exceeded: tier first, then score.
    var rank: Double { tier * 10 + min(score, 9.99) }

    func relativeBearing(heading: Double) -> Double? {
        worldBearing.map { Geo.relative($0, heading: heading) }
    }
}

/// Someone on camera who is talking, just talked, or is pinned: drives the aura, the speaking badge
/// and the name tag.
struct SpeakerPresence: Identifiable, Equatable {
    let faceId: Int
    var personId: Int? = nil
    var label: String
    var colorIndex: Int
    var activity: Double     // 1 while speaking, easing to 0 after the last word
    var resting: Double      // 1 while their caption is up, easing to 0 as it leaves
    var loudness: Double
    var isPinned: Bool
    var intensity = 0.0      // smoothed speech intensity: drives the aura's strength and its small pulse
    var id: Int { faceId }
}

/// A visible object that is making sound right now: it glows. The glow belongs to the persistent
/// SceneObject (so it doesn't flicker when the detector refreshes its observation), takes the color
/// of the sound's notification, and exists only while that sound is active and attributed to it.
struct ObjectAura: Identifiable, Equatable {
    let objectId: Int
    var cls: ObjectClass
    var box: CGRect                // Vision normalized: geometry input only, never drawn
    var precise: Bool
    var tone: PresenceTone
    var soundId: String            // the notification it belongs to
    var confidence: Double         // association confidence
    var intensity: Double          // the sound's smoothed intensity
    var opacity: Double
    var releasing: Bool            // the sound has stopped; the glow is fading with it
    var id: Int { objectId }
}

/// Everything the Spatial V2 layer draws, for one moment.
struct SpatialScene: Equatable {
    var presences: [SpatialPresence] = []      // drawn, highest priority first
    var speakers: [SpeakerPresence] = []
    var queued: [SpatialPresence] = []         // relevant but over capacity: tracked, drawn when a place frees up
    var suppressed: [SpatialPresence] = []     // below the relevance threshold: tracked, not drawn
    var pinnedPersonIds: Set<Int> = []
    var objectLinks: [Int: String] = [:]       // SceneObject id -> the sound currently attributed to it
    var objectAuras: [ObjectAura] = []         // visible objects glowing because a sound is theirs
    var baseline: Double? = nil

    static let empty = SpatialScene()

    /// Nothing to show: the camera stays clean.
    var isIdle: Bool {
        presences.isEmpty && objectAuras.isEmpty && !speakers.contains { $0.activity > 0 || $0.resting > 0 || $0.isPinned }
    }
}

/// SF Symbols for what a sound is. Meaning comes from the icon; color only says category or urgency.
enum SoundIcon {
    static let speech = "person.wave.2.fill"
    static let person = "person.fill"
    static let unknown = "questionmark"      // heard something, can't say what

    static func symbol(label: SoundLabel, text: String = "") -> String {
        switch label {
        case .speech: return speech
        case .doorbell: return "bell.fill"
        case .knock: return "door.left.hand.closed"
        case .alarm: return "light.beacon.max.fill"
        case .music: return "music.note"
        case .tv: return "tv.fill"
        case .laughter: return "face.smiling"
        case .dogBarking: return "dog.fill"
        case .babyCrying: return "stroller.fill"
        case .phoneRinging: return "phone.fill"
        case .loudBang: return "burst.fill"
        case .vehicle, .carHorn: return "car.fill"
        case .siren: return "light.beacon.max.fill"
        case .door: return "door.left.hand.closed"
        case .unknown: return unknown
        case .other: return keyword(text) ?? unknown
        }
    }

    static func symbol(alert kind: AlertKind) -> String {
        switch kind {
        case .doorbell: "bell.fill"
        case .knock: "door.left.hand.closed"
        case .alarm: "light.beacon.max.fill"
        case .nameCalled: "exclamationmark.bubble.fill"
        case .dogBarking: "dog.fill"
        case .babyCrying: "stroller.fill"
        case .loudBang: "burst.fill"
        case .siren: "light.beacon.max.fill"
        case .door: "door.left.hand.closed"
        case .carHorn: "car.fill"
        }
    }

    /// The contract's `other` label still carries a short description ("Footsteps", "Car horn"); use
    /// it to pick a better icon than the generic waveform when it names something we have one for.
    static let keywords: [(match: [String], symbol: String)] = [
        (["footstep", "walking", "steps"], "shoeprints.fill"),
        (["car", "vehicle", "engine", "horn", "traffic", "truck"], "car.fill"),
        (["siren"], "light.beacon.max.fill"),
        (["glass", "shatter", "crash"], "burst.fill"),
        (["door"], "door.left.hand.closed"),
        (["phone", "ringtone"], "phone.fill"),
        (["dog", "bark"], "dog.fill"),
        (["water", "tap", "faucet"], "drop.fill"),
    ]

    static func keyword(_ text: String) -> String? {
        let t = text.lowercased()
        return keywords.first { $0.match.contains { t.contains($0) } }?.symbol
    }

    static var all: [String] {
        Array(Set(SoundLabel.allCases.map { symbol(label: $0) } + AlertKind.allCases.map { symbol(alert: $0) }
                  + keywords.map(\.symbol) + [speech, person, unknown, "pin", "pin.fill", "pin.slash"])).sorted()
    }
}
