import CoreGraphics
import Foundation

/// The room's normal sound level, adapting over time. A steady sound (HVAC, a fan) pulls the baseline
/// up to meet it and stops standing out; a sudden one is far above it and does.
struct RoomBaseline: Equatable {
    private(set) var value: Double?
    private var last: Double?

    var riseS = Tunables.baselineRiseS
    var fallS = Tunables.baselineFallS

    mutating func update(level: Double, now: Double) {
        guard let v = value, let l = last else {
            value = level
            last = now
            return
        }
        let dt = max(0, now - l)
        last = now
        let tau = level > v ? riseS : fallS
        value = v + (level - v) * (1 - exp(-dt / tau))
    }

    /// A moment with no level at all (`"sources": []`: nothing live, not silence). The value holds;
    /// only the clock moves on, so the next real level is weighed against the time since this one.
    mutating func hold(now: Double) {
        guard value != nil else { return }
        last = now
    }

    mutating func reset() {
        value = nil
        last = nil
    }
}

/// The phone's copy of a `perception_enrichment`: the laptop's attention judgment for an earlier
/// caption (`ref.utt_id`) or sound (`ref.event_id`). Kept by key until it expires; never sent anywhere.
struct EnrichmentState: Equatable {
    var attention: Attention
    var confidence: Double
    var directedAtUser: Double?
    var updated: Double

    static func key(uttId: String) -> String { "utt:\(uttId)" }
    static func key(eventId: String) -> String { "evt:\(eventId)" }
}

/// How much a sound matters right now, and how strongly to show it. Pure functions; thresholds in Tunables.
enum Relevance {
    static func clamp01(_ x: Double) -> Double { min(1, max(0, x)) }

    /// What the sound *is*, 0...1, independent of how loud it is.
    static func semantic(_ label: SoundLabel) -> Double {
        Tunables.semanticImportance[label] ?? Tunables.semanticImportanceDefault
    }

    /// sound prominence = current sound level - ambient room baseline, scaled to 0...1.
    static func prominence(level: Double, baseline: Double) -> Double {
        clamp01((level - baseline) / Tunables.prominenceFullScale)
    }

    /// No level to compare with the baseline (no `sources` near the sound): lean on the classifier.
    static func fallbackProminence(confidence: Double) -> Double {
        clamp01(confidence) * Tunables.unknownLevelProminence
    }

    /// A sound that just keeps going matters less and less. `age` runs from its last new occurrence.
    static func habituation(age: Double) -> Double {
        guard age > Tunables.habituateAfterS else { return 1 }
        let k = clamp01((age - Tunables.habituateAfterS) / Tunables.habituateOverS)
        return 1 - k * (1 - Tunables.habituationFloor)
    }

    static func soundScore(semantic: Double, prominence: Double, habituation: Double = 1) -> Double {
        (Tunables.semanticWeight * semantic + Tunables.prominenceWeight * prominence) * habituation
    }

    /// nil = below the relevance threshold: still received and tracked, not drawn.
    static func soundLevel(score: Double) -> SpatialPresence.Level? {
        if score < Tunables.suppressBelowScore { return nil }
        return score >= Tunables.relevantAtScore ? .relevant : .ambient
    }

    static func speechScore(loudness: Double) -> Double {
        Tunables.speechBaseScore + (1 - Tunables.speechBaseScore) * clamp01(loudness)
    }

    static func alertScore(priority: Int, nameCalled: Bool) -> Double {
        Tunables.alertBaseScore + Double(priority) / 10 + (nameCalled ? 0.05 : 0)
    }

    /// Seconds a sound stays up after its last update.
    /// The same for every sound: a strong one is more opaque, not longer-lived.
    static func lifetime(_ level: SpatialPresence.Level) -> Double { Tunables.soundLifetimeS }

    /// 1 for most of the lifetime, easing to 0 over its tail.
    static func fadeOut(age: Double, lifetime: Double) -> Double {
        clamp01((lifetime - age) / Tunables.presenceFadeOutS)
    }

    /// Opacity is the distance/prominence cue: closer or stronger = more definite.
    static func opacity(level: SpatialPresence.Level, prominence: Double) -> Double {
        let p = clamp01(prominence)
        switch level {
        case .ambient, .relevant:               // a sound's opacity is its intensity, whatever its level
            return Tunables.soundOpacityMin + (Tunables.soundOpacityMax - Tunables.soundOpacityMin) * p
        case .speech: return Tunables.speechOpacityMin + (Tunables.speechOpacityMax - Tunables.speechOpacityMin) * p
        case .important: return 1
        }
    }

    static func pulseStrength(level: SpatialPresence.Level, prominence: Double) -> Double {
        if level == .important { return Tunables.pulseStrengthMax }
        return Tunables.pulseStrengthMin + (Tunables.pulseStrengthMax - Tunables.pulseStrengthMin) * clamp01(prominence)
    }

    static func iconScale(_ level: SpatialPresence.Level) -> CGFloat {
        switch level {
        case .ambient: Tunables.iconScaleAmbient
        case .relevant: Tunables.iconScaleRelevant
        case .speech: Tunables.iconScaleSpeech
        case .important: Tunables.iconScaleImportant
        }
    }

    /// The color grammar for sounds: red danger, blue human-like, green environmental, neutral unknown.
    /// Never an identity color; those belong to people.
    static func tone(label: SoundLabel, text: String = "") -> PresenceTone {
        switch label {
        case .alarm, .loudBang, .siren: return .danger
        case .speech, .babyCrying, .laughter: return .human
        case .other: return SoundIcon.keyword(text) == nil ? .neutral : .environment
        case .unknown: return .neutral
        case .doorbell, .knock, .music, .tv, .dogBarking, .phoneRinging, .vehicle, .door, .carHorn: return .environment
        }
    }

    static func tone(alert kind: AlertKind, priority: Int) -> PresenceTone {
        switch kind {
        case .alarm, .loudBang, .siren: return .danger
        case .nameCalled, .babyCrying: return .human
        case .doorbell, .knock, .dogBarking, .door, .carHorn: return priority >= 3 ? .danger : .environment
        }
    }

    /// Opacity from current intensity, for every ordinary notification alike.
    static func opacity(intensity: Double, floor: Double = 0) -> Double {
        max(floor, Tunables.soundOpacityMin + (Tunables.soundOpacityMax - Tunables.soundOpacityMin) * clamp01(intensity))
    }

    /// One smoothing step toward the current level: quick to rise, a little slower to fall.
    static func smooth(_ current: Double, toward target: Double, dt: Double) -> Double {
        let tau = target > current ? Tunables.intensityAttackS : Tunables.intensityReleaseS
        return current + (target - current) * (1 - exp(-max(0, dt) / tau))
    }

    /// How much of a notification is left after its source went quiet `sinceActive` seconds ago:
    /// held through short pauses, then faded. The same curve whatever the intensity was.
    static func lifecycleFade(sinceActive: Double, hold: Double) -> Double {
        clamp01(1 - (sinceActive - hold) / Tunables.notificationFadeS)
    }

    /// WCAG relative luminance of an sRGB gray (0...1), and the contrast ratio between two grays.
    static func luminance(gray: Double) -> Double {
        gray <= 0.03928 ? gray / 12.92 : pow((gray + 0.055) / 1.055, 2.4)
    }

    static func contrast(_ a: Double, _ b: Double) -> Double {
        let (la, lb) = (luminance(gray: a), luminance(gray: b))
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    }
}

/// Sound-source continuity on the phone. `sound_event.event_id` is the server's identity for a sound,
/// and is used as is; but when the same source keeps going and arrives under a fresh id (same kind,
/// about the same direction, moments later), it is the same notification, not a new one.
enum SoundContinuity {
    static func key(forEvent eventId: String, label: SoundLabel, bearing: Double?, among live: [SoundTag], now: Double) -> String {
        if live.contains(where: { $0.eventId == eventId }) { return eventId }
        guard let bearing else { return eventId }
        let same = live.filter { tag in
            guard tag.label == label, now - tag.updated <= Tunables.soundMergeS, let b = tag.bearing else { return false }
            return abs(Geo.angDiff(b, bearing)) <= Tunables.soundMergeDeg
        }
        return same.min { abs(Geo.angDiff($0.bearing ?? 0, bearing)) < abs(Geo.angDiff($1.bearing ?? 0, bearing)) }?.eventId ?? eventId
    }

    /// Don't force a name out of a weak guess. The laptop sends its top label with the raw classifier
    /// score; under that label's bar the sound is shown as unknown, keeping its direction and intensity.
    static func confidentLabel(_ label: SoundLabel, confidence: Double) -> SoundLabel {
        if label == .unknown || label == .other { return label }
        let bar = Tunables.soundLabelMinConfidence[label] ?? Tunables.soundLabelDefaultMinConfidence
        return confidence >= bar ? label : .unknown
    }

    /// The same continuing source, whatever it was called this time: an event from about the same
    /// direction moments after another is that source again, not a new one. Warnings are never
    /// folded into (or out of) an ordinary sound.
    static func sourceKey(forEvent eventId: String, label: SoundLabel, bearing: Double?, among live: [SoundTag], now: Double) -> String {
        if live.contains(where: { $0.eventId == eventId }) { return eventId }
        guard let bearing, Relevance.tone(label: label) != .danger else { return eventId }
        let same = live.filter { tag in
            guard Relevance.tone(label: tag.label) != .danger, now - tag.updated <= Tunables.soundMergeS, let b = tag.bearing else { return false }
            return abs(Geo.angDiff(b, bearing)) <= Tunables.soundMergeDeg
        }
        return same.min { abs(Geo.angDiff($0.bearing ?? 0, bearing)) < abs(Geo.angDiff($1.bearing ?? 0, bearing)) }?.eventId ?? eventId
    }

    /// Temporal voting. Each classifier window adds its score to its label; older windows fade; the
    /// label only changes when a challenger has clearly more accumulated evidence than the current
    /// one. One stray "dog" window in a run of typing does not turn the keyboard into a dog.
    static func vote(_ votes: inout [SoundLabel: Double], label: SoundLabel, confidence: Double, current: SoundLabel?) -> SoundLabel {
        for k in votes.keys { votes[k]! *= Tunables.soundVoteDecay }
        votes[label, default: 0] += max(0.05, confidence)
        guard let current, let held = votes[current] else { return label }
        let best = votes.max { ($0.value, $0.key.rawValue) < ($1.value, $1.key.rawValue) }!
        return best.key != current && best.value >= held * Tunables.soundLabelSwitchRatio ? best.key : current
    }

    // MARK: aura (visible speakers)

    /// Aura opacity: a quiet tie while the caption is up, rising with the smoothed speech intensity
    /// while they speak.
    static func auraOpacity(activity: Double, resting: Double, intensity: Double) -> Double {
        let k = Relevance.clamp01(intensity)
        let speaking = Tunables.auraActiveOpacityMin + (Tunables.auraActiveOpacity - Tunables.auraActiveOpacityMin) * k
        let rest = Tunables.auraRestOpacity * Relevance.clamp01(resting)
        return rest + (speaking - rest) * Relevance.clamp01(activity)
    }

    /// The glow's color for a sound attributed to an object: the notification's own color, so icon
    /// and aura read as one thing. A sound HERE couldn't name still has a known *source* once it is
    /// attributed, so it glows as an environmental sound rather than in "unknown" gray.
    static func objectAuraTone(_ tone: PresenceTone) -> PresenceTone { tone == .neutral ? .environment : tone }

    /// Object glow strength from the sound's smoothed intensity. `life` is the notification's own
    /// fade (1 while active and through the short hold, then down to 0): lifetime never depends on
    /// how loud it was. Warnings are stronger; a crowded view dims the ordinary ones.
    static func objectAuraOpacity(intensity: Double, life: Double, danger: Bool, crowded: Bool) -> Double {
        var o = Tunables.objectAuraOpacityMin + (Tunables.objectAuraOpacityMax - Tunables.objectAuraOpacityMin) * Relevance.clamp01(intensity)
        if danger { o *= Tunables.objectAuraDangerBoost } else if crowded { o *= Tunables.auraCrowdDim }
        return min(Tunables.auraMaxOpacity, o) * Relevance.clamp01(life)
    }

    static func objectAuraPulse(intensity: Double, reduceMotion: Bool) -> Double {
        reduceMotion ? 0 : Double(Tunables.objectAuraPulse) + Double(Tunables.objectAuraPulseIntensity) * Relevance.clamp01(intensity)
    }

    /// Aura pulse (fraction of its size): small, a little larger with intensity, none with Reduce Motion.
    static func auraPulse(activity: Double, intensity: Double, reduceMotion: Bool) -> Double {
        if reduceMotion { return 0 }
        return (Double(Tunables.auraBreathScale) + Double(Tunables.auraBreathLoudnessScale) * Relevance.clamp01(intensity))
            * Relevance.clamp01(activity)
    }
}
