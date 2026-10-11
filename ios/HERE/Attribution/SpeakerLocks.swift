import Foundation

/// A person who could be the one speaking, as the lock logic sees them.
struct SpeakerCandidate: Equatable {
    var personId: Int                // SessionPerson
    var micBearing: Double           // where the array would hear this person from
    var mouthActivity = 0.0          // 0...1, smoothed lip movement; supporting evidence only
}

/// "This audio source currently belongs to this SessionPerson." One per active speech source, so
/// two people can talk at once and each keeps their own. Distinct from person identity: a person
/// exists without speaking, and a source can exist without a known person.
struct ActiveSpeakerAssociation: Identifiable, Equatable {
    enum State: String {
        case unassigned              // heard, nobody it clearly belongs to
        case locked                  // belongs to a person who is in view
        case temporarilyOccluded     // its person just went out of sight; nobody may take it yet
        case offscreenButTracked     // its person is out of view and still talking
        case releasePending          // speech stopped; kept through a pause
    }

    let id: Int
    var sourceId: Int                // the laptop's stream/source id (a fixed beam, or an ODAS track)
    var personId: Int?
    var state = State.unassigned
    var confidence = 0.0
    var rawBearing: Double
    var smoothedBearing: Double
    var startedAt: Double
    var lastConfirmedAt: Double
    var lastSpeechAt: Double
    var lostSince: Double?           // since when its person has not been visible
    var challengerId: Int?           // someone scoring better than the current person...
    var challengerSince: Double?     // ...and since when
    var lastScores: [Int: Double] = [:]
}

/// Sticky, per-source speaker attribution.
///
/// The old rule decided every sentence from scratch: nearest visible face to the sound. So a
/// bystander crossing in front of the speaker took the next caption. Here a speech source, once it
/// belongs to someone, stays theirs through bearing jitter, pauses, their face being hidden, and
/// them leaving view. It changes hands only when someone else scores clearly better for a sustained
/// time, and never during the grace period after its person disappears. Every change is logged with
/// the scores that caused it.
final class SpeakerLocks {
    struct Resolution: Equatable {
        var associationId: Int
        var personId: Int?
        var state: ActiveSpeakerAssociation.State
    }

    private(set) var associations: [ActiveSpeakerAssociation] = []
    private(set) var log: [String] = []
    private var byUtterance: [String: Int] = [:]
    private var nextId = 1

    func reset() {
        associations = []
        byUtterance = [:]
        log = []
    }

    /// One update of one utterance. `candidates` are the people in view right now.
    @discardableResult
    func resolve(utt: String, sourceId: Int, micBearing: Double, speaking: Bool, candidates: [SpeakerCandidate],
                 attributed: Int? = nil, heardNow: Bool = true, now: Double) -> Resolution {
        // `heardNow`: this call carries news from the laptop about the utterance (words, or its end),
        // as opposed to a per-frame re-check. The source was audible at this moment either way.
        let id = associations[associationIndex(utt: utt, sourceId: sourceId, bearing: micBearing, now: now)].id
        if speaking || heardNow, let i = associations.firstIndex(where: { $0.id == id }) { associations[i].lastSpeechAt = now }
        expire(now: now)                                  // others that have gone quiet; never the one being updated
        let index = associations.firstIndex { $0.id == id }!
        var a = associations[index]
        a.rawBearing = micBearing
        // One noisy direction reading must not move the source: attribution uses the smoothed bearing.
        a.smoothedBearing = Geo.wrap(a.smoothedBearing + Geo.angDiff(micBearing, a.smoothedBearing) * Tunables.audioBearingSmoothing)

        func agreement(_ c: SpeakerCandidate, within degrees: Double) -> Double {
            max(0, 1 - abs(Geo.angDiff(c.micBearing, a.smoothedBearing)) / degrees)
        }
        /// A new claim on a source: must be close to it. Lip movement adds to a plausible candidate, never creates one.
        func claim(_ c: SpeakerCandidate) -> Double {
            let g = agreement(c, within: Tunables.matchDeg)
            return g > 0 ? g + Tunables.mouthActivityWeight * c.mouthActivity : 0
        }
        let claims = candidates.map { (c: $0, s: claim($0)) }.filter { $0.s > 0 }.sorted { ($0.s, -$0.c.personId) > ($1.s, -$1.c.personId) }
        a.lastScores = Dictionary(claims.map { ($0.c.personId, $0.s) }, uniquingKeysWith: { x, _ in x })

        if let holder = a.personId {
            // Existing source: continuity-biased. The holder keeps a large bonus and a wider angle.
            let seen = candidates.first { $0.personId == holder }
            let holding = Tunables.existingSpeakerContinuityBonus
                + (seen.map { agreement($0, within: Tunables.speakerLockMatchDeg) + Tunables.mouthActivityWeight * $0.mouthActivity } ?? 0)
            a.lastScores[holder] = holding
            if seen != nil {
                a.lostSince = nil
                a.state = .locked
                a.lastConfirmedAt = now
            } else {
                let since = a.lostSince ?? now
                a.lostSince = since
                a.state = now - since <= Tunables.speakerOcclusionGraceS ? .temporarilyOccluded : .offscreenButTracked
            }
            let inGrace = a.lostSince.map { now - $0 <= Tunables.speakerOcclusionGraceS } ?? false
            if let rival = claims.first(where: { $0.c.personId != holder }), rival.s > holding + Tunables.speakerSwitchMargin, !inGrace {
                if a.challengerId != rival.c.personId {
                    a.challengerId = rival.c.personId
                    a.challengerSince = now
                }
                let held = now - (a.challengerSince ?? now)
                if held >= Tunables.speakerSwitchHoldS {
                    note(String(format: "source %d: person %d -> person %d | holder %.2f, challenger %.2f, margin %.2f, held %.1f s | %@",
                                a.id, holder, rival.c.personId, holding, rival.s, Tunables.speakerSwitchMargin, held,
                                seen == nil ? "holder not in view, challenger sustained at the source" : "source moved to the challenger and stayed"))
                    a.personId = rival.c.personId
                    a.challengerId = nil
                    a.challengerSince = nil
                    a.lostSince = nil
                    a.state = .locked
                    a.lastConfirmedAt = now
                }
            } else {
                a.challengerId = nil
                a.challengerSince = nil
            }
            a.confidence = min(1, holding)
        } else if let attributed {
            // New source, and the per-utterance attribution (SPEC §13) already named its speaker.
            a.personId = attributed
            a.state = .locked
            a.confidence = min(1, a.lastScores[attributed] ?? Tunables.speakerLockThreshold)
            a.lastConfirmedAt = now
            note(String(format: "source %d: locked to person %d | attributed by bearing, %+.0f°", a.id, attributed, a.smoothedBearing))
        } else if let best = claims.first {
            // New source: conservative. It needs a clear owner, or it stays unattributed.
            let second = claims.count > 1 ? claims[1].s : 0
            if best.s >= Tunables.speakerLockThreshold, best.s - second >= Tunables.speakerAmbiguityMargin {
                a.personId = best.c.personId
                a.state = .locked
                a.confidence = min(1, best.s)
                a.lastConfirmedAt = now
                note(String(format: "source %d: locked to person %d | score %.2f, next %.2f, bearing %+.0f°", a.id, best.c.personId,
                            best.s, second, a.smoothedBearing))
            } else {
                a.state = .unassigned
                a.confidence = best.s
            }
        } else {
            a.state = .unassigned
            a.confidence = 0
        }
        if !speaking, a.state == .locked || a.state == .unassigned { a.state = a.personId == nil ? .unassigned : .releasePending }
        associations[index] = a
        return Resolution(associationId: a.id, personId: a.personId, state: a.state)
    }

    /// Associations whose speech stopped longer ago than the pause allowance are released. One
    /// source ending never touches another.
    func expire(now: Double) {
        for a in associations where now - a.lastSpeechAt > Tunables.speakerReleaseHoldS {
            note(String(format: "source %d: released after %.1f s of silence (was %@)", a.id, now - a.lastSpeechAt,
                        a.personId.map { "person \($0)" } ?? "unattributed"))
            byUtterance = byUtterance.filter { $0.value != a.id }
        }
        associations.removeAll { now - $0.lastSpeechAt > Tunables.speakerReleaseHoldS }
    }

    func association(forUtterance utt: String) -> ActiveSpeakerAssociation? {
        byUtterance[utt].flatMap { id in associations.first { $0.id == id } }
    }

    /// The same source as before (this utterance already has one; or the same stream at about the
    /// same bearing within the pause allowance: the next sentence from the same talker), else a new one.
    private func associationIndex(utt: String, sourceId: Int, bearing: Double, now: Double) -> Int {
        if let id = byUtterance[utt], let i = associations.firstIndex(where: { $0.id == id }) { return i }
        let taken = Set(byUtterance.filter { $0.key != utt }.values)
        let same = associations.indices.filter { i in
            associations[i].sourceId == sourceId && abs(Geo.angDiff(associations[i].smoothedBearing, bearing)) <= Tunables.speakerSourceBearingDeg
                && (!taken.contains(associations[i].id) || now - associations[i].lastSpeechAt > 0.2)
        }.min { abs(Geo.angDiff(associations[$0].smoothedBearing, bearing)) < abs(Geo.angDiff(associations[$1].smoothedBearing, bearing)) }
        if let same {
            byUtterance[utt] = associations[same].id
            return same
        }
        associations.append(ActiveSpeakerAssociation(id: nextId, sourceId: sourceId, rawBearing: bearing, smoothedBearing: bearing,
                                                     startedAt: now, lastConfirmedAt: now, lastSpeechAt: now))
        byUtterance[utt] = nextId
        nextId += 1
        return associations.count - 1
    }

    private func note(_ line: String) {
        log = Array((log + [line]).suffix(Tunables.speakerLogSize))
    }
}

/// Lip movement over the last second or so, 0...1. Not "mouth open": a mouth held open scores zero.
/// What counts is the opening *changing*, repeatedly. Supporting evidence for who is talking when two
/// faces are both plausible; never enough on its own.
enum MouthActivity {
    /// `openness`: recent samples of lip opening relative to face height (oldest first).
    static func score(_ openness: [Double]) -> Double {
        guard openness.count >= Tunables.mouthMinSamples else { return 0 }
        let mean = openness.reduce(0, +) / Double(openness.count)
        let variance = openness.map { ($0 - mean) * ($0 - mean) }.reduce(0, +) / Double(openness.count)
        return min(1, sqrt(variance) / Tunables.mouthActivityFull)
    }

    static func smooth(_ previous: Double, toward current: Double) -> Double {
        previous + (current - previous) * Tunables.mouthActivitySmoothing
    }
}
