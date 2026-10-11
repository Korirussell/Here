import Foundation

/// A person HERE knows about for this session. This is the identity: it outlives any one Vision
/// track, so "Person 1" is still Person 1 after they turn away, walk behind the wearer or leave and
/// come back. A Vision face/body track and an audio source are things a SessionPerson *currently has*.
/// On-device only; nothing here is sent to the relay.
struct SessionPerson: Identifiable, Equatable {
    let id: Int
    var displayName: String              // "Person N", or the chosen name once matched to a KnownPerson
    var colorIndex: Int

    var currentTrackID: Int?             // the Vision track showing them right now, if any
    var viaBody = false                  // that track is being held by a body detection, not a face
    var audioSourceID: Int?              // only ever set when the laptop's source ids mean something

    var isSessionPinned = false
    var knownPersonID: UUID?
    var priority = KnownPerson.Priority.normal

    var lastKnownWorldBearing: Double
    var lastKnownScreenX: Double?        // 0 (left) ... 1 (right) where they were last seen in frame
    var bearingVelocity = 0.0            // deg/s, smoothed: which way they were moving when last seen
    var recentBearings: [Double] = []    // world bearings, about one a second, oldest first
    var recentAudioSources: [Int] = []   // audio sources they have held (most recent last)
    var bearingsSampledAt = -Double.infinity
    var lastSeenAt: Double
    var lastHeardAt: Double?
    var descriptors = 0                  // appearance descriptors held for them (the descriptors stay in the tracker)
    var identityConfidence = 1.0         // the score of the last re-identification that attached a track

    var isVisible: Bool { currentTrackID != nil }

    /// 1 while seen; out of view it decays from the last time they were seen or heard. At 0 their
    /// bearing is no longer something HERE should show.
    func spatialConfidence(now: Double) -> Double {
        if isVisible { return 1 }
        let last = max(lastSeenAt, lastHeardAt ?? -.infinity)
        return min(1, max(0, 1 - (now - last) / Tunables.spatialCredibleS))
    }

    func hasCredibleBearing(now: Double) -> Bool { spatialConfidence(now: now) > 0 }

    enum State: String { case visible, temporarilyOccluded, offscreen, dormant }

    /// Where they are in their life cycle. Never "gone": a person only ends by expiry.
    func state(now: Double) -> State {
        if isVisible { return viaBody ? .temporarilyOccluded : .visible }
        return hasCredibleBearing(now: now) ? .offscreen : .dormant
    }

    /// Where they'd be now if they kept moving the way they were (for a short while only).
    func predictedBearing(now: Double) -> Double {
        Geo.wrap(lastKnownWorldBearing + bearingVelocity * min(max(0, now - lastSeenAt), Tunables.reidPredictS))
    }
}

/// Someone the wearer chose to keep across sessions ("Add to People"). Separate from a session pin:
/// a pin says "keep this person important right now"; this says "I know who this is".
struct KnownPerson: Identifiable, Codable, Equatable {
    enum Priority: String, Codable, CaseIterable {
        case normal                 // restore name and color; otherwise ordinary relevance
        case whenSpeaking           // their speech ranks above ordinary speech
        case always                 // stays presentation-relevant while present (never a permanent badge)

        var title: String {
            switch self {
            case .normal: "Normal"
            case .whenSpeaking: "Prioritize when speaking"
            case .always: "Always prioritize"
            }
        }
    }

    var id = UUID()
    var chosenName: String
    var preferredColor: Int
    var priority = Priority.normal
    /// Appearance descriptors captured at enrollment (archived Vision feature prints). Stored on this
    /// phone only, only for people explicitly added, deleted with the person.
    var recognitionProfile: [Data] = []
}

enum KnownPeople {
    /// Which enrolled person a new SessionPerson is, if any. Needs a strong match *and* a clear lead
    /// over the next-best: a wrong name on a stranger is worse than no name.
    static func match(_ similarities: [(id: UUID, similarity: Double)]) -> UUID? {
        let ranked = similarities.sorted { $0.similarity > $1.similarity }
        guard let best = ranked.first, best.similarity >= Tunables.knownMatchSimilarity else { return nil }
        if ranked.count > 1, best.similarity - ranked[1].similarity < Tunables.knownMatchMargin { return nil }
        return best.id
    }
}

/// The enrolled people, persisted on the phone.
final class KnownPeopleStore: ObservableObject {
    private static let key = "here.people.v1"

    @Published var people: [KnownPerson] {
        didSet {
            if let data = try? JSONEncoder().encode(people) { UserDefaults.standard.set(data, forKey: Self.key) }
        }
    }

    init(inMemory: [KnownPerson]? = nil) {
        if let inMemory {
            people = inMemory
        } else if let data = UserDefaults.standard.data(forKey: Self.key),
                  let saved = try? JSONDecoder().decode([KnownPerson].self, from: data) {
            people = saved
        } else {
            people = []
        }
    }
}

// MARK: - Re-identification

/// What we know about whether a new Vision track is a particular SessionPerson coming back.
struct ReIDEvidence: Equatable {
    var personId: Int
    var visualSimilarity: Double?        // 0...1 from appearance (face + clothing), nil when there's nothing to compare
    var bearingDeltaDeg: Double          // new track vs where they were last known
    var secondsSinceSeen: Double
    var bodyContinuity = false           // a body detection carried them through the gap
    var audioContinuity = false          // they were just heard from this direction
    var pinned = false
    var edgeContinuity = false           // re-entered at the side of the frame they left by
}

struct ReIDDecision: Equatable {
    var personId: Int?                   // nil: a new SessionPerson
    var score: Double
    var runnerUp: Double
    var reason: String                   // "accepted", "ambiguous", "low score", "no candidates"
    var evidence: ReIDEvidence?
    var spatial = 0.0                    // the place+time channel of the best candidate (diagnostics)
}

/// Pure scoring. Two channels, appearance and place+time, either of which can carry a match: someone
/// who looks the same is the same person wherever they come back, and someone who reappears where
/// they just were is the same person even with no usable appearance. Continuity (body, audio, pin,
/// frame edge) adds to that. Two plausible candidates means nobody is matched: a new person is safer
/// than a swap.
enum ReID {
    /// Vision feature-print distance -> similarity 0...1.
    static func similarity(printDistance d: Double) -> Double {
        let span = Tunables.printDistanceDifferent - Tunables.printDistanceSame
        return min(1, max(0, 1 - (d - Tunables.printDistanceSame) / span))
    }

    /// Where and when: 1 for "right there, just now", falling with angular distance and with time.
    /// People move, so the angular tolerance widens as time passes, and past a point the old bearing
    /// says nothing.
    static func spatial(_ e: ReIDEvidence) -> Double {
        guard e.secondsSinceSeen <= Tunables.reidBearingRelevantS else { return 0 }
        let tolerance = min(Tunables.reidBearingMaxDeg,
                            Tunables.reidBearingBaseDeg + Tunables.reidBearingDegPerS * e.secondsSinceSeen)
        return max(0, 1 - abs(e.bearingDeltaDeg) / tolerance) * exp(-e.secondsSinceSeen / Tunables.reidTemporalTauS)
    }

    static func score(_ e: ReIDEvidence) -> Double {
        var place = spatial(e)
        if let v = e.visualSimilarity {
            // Appearance that clearly says "someone else" overrides a coincidence of place and time,
            // and a poor resemblance discounts it: two people swapping places must not each inherit
            // the identity of whoever stood there a moment ago.
            if v < Tunables.reidVisualVeto { return 0 }
            place *= min(1, v / Tunables.reidVisualSupport)
        }
        let look = min(1, max(0, e.visualSimilarity ?? 0))
        var s = 1 - (1 - Tunables.reidVisualWeight * look) * (1 - Tunables.reidBearingWeight * place)
        guard s > 0 else { return 0 }                      // continuity alone is not evidence
        if e.bodyContinuity { s += Tunables.reidBodyBonus }
        if e.audioContinuity { s += Tunables.reidAudioBonus }
        if e.edgeContinuity { s += Tunables.reidEdgeBonus }
        if e.pinned { s += Tunables.reidPinBonus }
        return min(1, s)
    }

    /// Stage 1 score: continuity only. Did someone just stand here? (Poor appearance still discounts it.)
    static func continuity(_ e: ReIDEvidence) -> Double {
        guard e.secondsSinceSeen <= Tunables.reidContinuityS else { return 0 }
        var place = spatial(e)
        if let v = e.visualSimilarity {
            if v < Tunables.reidVisualVeto { return 0 }
            place *= min(1, v / Tunables.reidVisualSupport)
        }
        return min(1, place + (e.bodyContinuity ? Tunables.reidBodyBonus : 0) + (e.audioContinuity ? Tunables.reidAudioBonus : 0))
    }

    /// Hysteresis: should a track already identified as one person be re-identified as another?
    /// Only on a clear advantage; a slightly better score for someone else changes nothing.
    static func shouldSwitch(currentScore: Double, challengerScore: Double) -> Bool {
        challengerScore - currentScore >= Tunables.reidSwitchMargin - 1e-9
    }

    enum DistanceTier: String { case close, medium, far }

    /// How far away someone is, judged by how tall their face is in the frame.
    static func tier(facePixels: Double) -> DistanceTier {
        facePixels >= Tunables.faceClosePixels ? .close : (facePixels < Tunables.faceFarPixels ? .far : .medium)
    }

    /// How much of "do they look the same" comes from the face. Close up the face is the best
    /// evidence; far away it is a few dozen pixels and clothing says more.
    static func faceWeight(facePixels: Double) -> Double {
        let span = Tunables.faceClosePixels - Tunables.faceFarPixels
        let k = min(1, max(0, (facePixels - Tunables.faceFarPixels) / span))
        return Tunables.faceWeightFar + (Tunables.faceWeightClose - Tunables.faceWeightFar) * k
    }

    /// Face and torso similarity combined for the distance. One missing: the other alone.
    static func appearance(face: Double?, torso: Double?, facePixels: Double) -> Double? {
        switch (face, torso) {
        case let (f?, t?):
            let w = faceWeight(facePixels: facePixels)
            return w * f + (1 - w) * t
        case let (f?, nil): return f
        case let (nil, t?): return t
        default: return nil
        }
    }

    /// Only a clear, large, face-forward view is worth remembering as what someone looks like.
    static func isGoodSample(faceConfidence: Double, facePixels: Double, viaBody: Bool) -> Bool {
        !viaBody && faceConfidence >= Tunables.descriptorMinFaceConfidence && facePixels >= Tunables.descriptorMinFacePixels
    }

    /// Two stages. Continuity first: a track reappearing where exactly one person just was is that
    /// person, without needing to weigh everyone who has ever been seen. Otherwise full
    /// re-identification against everyone not in view.
    static func decide(_ candidates: [ReIDEvidence]) -> ReIDDecision {
        let recent = candidates.map { (e: $0, s: continuity($0)) }.sorted { ($0.s, -$0.e.personId) > ($1.s, -$1.e.personId) }
        if let best = recent.first, best.s >= Tunables.reidContinuityAccept,
           best.s - (recent.count > 1 ? recent[1].s : 0) >= Tunables.reidAmbiguityMargin {
            return ReIDDecision(personId: best.e.personId, score: max(best.s, score(best.e)), runnerUp: recent.count > 1 ? recent[1].s : 0,
                                reason: "continuity", evidence: best.e, spatial: spatial(best.e))
        }
        let ranked = candidates.map { (e: $0, s: score($0)) }.sorted { ($0.s, -$0.e.personId) > ($1.s, -$1.e.personId) }
        guard let best = ranked.first else {
            return ReIDDecision(personId: nil, score: 0, runnerUp: 0, reason: "no candidates", evidence: nil)
        }
        let second = ranked.count > 1 ? ranked[1].s : 0
        let needed = best.e.pinned ? Tunables.reidAcceptPinned : Tunables.reidAccept
        var d = ReIDDecision(personId: nil, score: best.s, runnerUp: second, reason: "low score", evidence: best.e,
                             spatial: spatial(best.e))
        if best.s < needed { return d }
        if best.s - second < Tunables.reidAmbiguityMargin {
            d.reason = "ambiguous"
            return d
        }
        d.personId = best.e.personId
        d.reason = "accepted"
        return d
    }

    /// The best candidate regardless of how close the runner-up is: used once an ambiguous track has
    /// waited long enough. Keeping one of the existing identities beats minting Person N+1.
    static func best(_ candidates: [ReIDEvidence]) -> ReIDDecision {
        var d = decide(candidates)
        if d.reason == "ambiguous", let e = d.evidence {
            d.personId = e.personId
            d.reason = "best of ambiguous"
        }
        return d
    }
}

/// The session's people. Vision tracks attach to and detach from them; audio keeps their direction
/// alive while they're out of view; pins and known-person matches live on them.
/// Thread-safe: the Vision queue resolves identities while the main thread reads and pins.
final class SessionPeople {
    private let lock = NSLock()
    private var people: [Int: SessionPerson] = [:]
    private var nextId = 1
    private var decisions: [ReIDDecision] = []
    private var decisionByPerson: [Int: ReIDDecision] = [:]

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }

    var all: [SessionPerson] { locked { people.values.sorted { $0.id < $1.id } } }
    var recentDecisions: [ReIDDecision] { locked { decisions } }
    /// The decision that last attached a track to this person (or created them): for the debug overlay.
    func lastDecision(personId: Int) -> ReIDDecision? { locked { decisionByPerson[personId] } }
    func noteDescriptors(_ count: Int, personId: Int) { locked { people[personId]?.descriptors = count } }

    func person(trackId: Int) -> SessionPerson? { locked { people.values.first { $0.currentTrackID == trackId } } }
    func person(id: Int) -> SessionPerson? { locked { people[id] } }

    /// A new Vision track appeared at `bearing`. Decide whether it is someone we already know coming
    /// back (and attach it) or a new person. `similarity` compares the track's appearance with a
    /// candidate's, nil when it can't.
    @discardableResult
    func resolve(trackId: Int, bearing: Double, now: Double, screenX: Double? = nil, bodyHeldPersonId: Int? = nil,
                 similarity: (SessionPerson) -> Double?) -> SessionPerson {
        attach(trackId: trackId, bearing: bearing, now: now, screenX: screenX, bodyHeldPersonId: bodyHeldPersonId,
               mode: .newIfAmbiguous, similarity: similarity)!
    }

    enum AmbiguityMode {
        case newIfAmbiguous      // two plausible people: create a new one
        case wait                // two plausible people: decide nothing yet (returns nil)
        case takeBest            // waited long enough: keep the more likely existing identity
    }

    /// `resolve` with a say in what happens when two people are both plausible. The live tracker
    /// waits (`.wait`) while the picture clears up (the other person is usually re-identified first,
    /// which removes the ambiguity) and after `reidPendingS` takes the best (`.takeBest`): a brief
    /// unlabelled moment instead of a confident swap or a brand-new Person N.
    func attach(trackId: Int, bearing: Double, now: Double, screenX: Double? = nil, bodyHeldPersonId: Int? = nil,
                mode: AmbiguityMode, similarity: (SessionPerson) -> Double?) -> SessionPerson? {
        locked {
            let candidates = people.values.filter { !$0.isVisible }.map { p -> ReIDEvidence in
                let heardHere = p.lastHeardAt.map { now - $0 <= Tunables.reidAudioRecentS } ?? false
                return ReIDEvidence(personId: p.id, visualSimilarity: similarity(p),
                                    bearingDeltaDeg: Geo.angDiff(bearing, p.predictedBearing(now: now)),   // along their path
                                    secondsSinceSeen: now - max(p.lastSeenAt, p.lastHeardAt ?? -.infinity),
                                    bodyContinuity: bodyHeldPersonId == p.id,
                                    audioContinuity: heardHere && abs(Geo.angDiff(bearing, p.lastKnownWorldBearing)) <= Tunables.matchDeg,
                                    pinned: p.isSessionPinned,
                                    edgeContinuity: Self.sameEdge(p.lastKnownScreenX, screenX))
            }
            let decision = mode == .takeBest ? ReID.best(candidates) : ReID.decide(candidates)
            if mode == .wait, decision.reason == "ambiguous" {
                if decisions.last != decision { decisions = Array((decisions + [decision]).suffix(Tunables.reidDecisionLog)) }
                return nil
            }
            decisions = Array((decisions + [decision]).suffix(Tunables.reidDecisionLog))

            var p: SessionPerson
            if let id = decision.personId, let known = people[id] {
                p = known
                p.identityConfidence = decision.score
            } else {
                let n = nextId
                nextId += 1
                p = SessionPerson(id: n, displayName: "Person \(n)", colorIndex: (n - 1) % Palette.people.count,
                                  lastKnownWorldBearing: bearing, lastSeenAt: now)
            }
            p.currentTrackID = trackId
            p.viaBody = false
            p.lastKnownWorldBearing = bearing
            p.lastKnownScreenX = screenX ?? p.lastKnownScreenX
            p.lastSeenAt = now
            people[p.id] = p
            decisionByPerson[p.id] = decision
            return p
        }
    }

    private static func sameEdge(_ left: Double?, _ entered: Double?) -> Bool {
        guard let left, let entered else { return false }
        let f = Tunables.reidEdgeFraction
        return (left < f && entered < f) || (left > 1 - f && entered > 1 - f)
    }

    /// The track is still on them this frame.
    func seen(trackId: Int, bearing: Double, viaBody: Bool, now: Double, screenX: Double? = nil) {
        locked {
            guard var p = people.values.first(where: { $0.currentTrackID == trackId }) else { return }
            p.lastKnownScreenX = screenX ?? p.lastKnownScreenX
            let dt = now - p.lastSeenAt
            if dt > 0.01, dt < 1 {
                let v = Geo.angDiff(bearing, p.lastKnownWorldBearing) / dt
                p.bearingVelocity += (v - p.bearingVelocity) * Tunables.bearingVelocitySmoothing
            }
            p.lastKnownWorldBearing = bearing
            if now - p.bearingsSampledAt >= Tunables.personHistoryEveryS {
                p.recentBearings = Array((p.recentBearings + [bearing]).suffix(Tunables.personHistorySize))
                p.bearingsSampledAt = now
            }
            p.lastSeenAt = now
            p.viaBody = viaBody
            people[p.id] = p
        }
    }

    /// The Vision track ended. The person does not.
    func detach(trackId: Int, now: Double) {
        locked {
            guard var p = people.values.first(where: { $0.currentTrackID == trackId }) else { return }
            p.currentTrackID = nil
            p.viaBody = false
            people[p.id] = p
        }
    }

    /// Faces that already carry a label (simulated faces, tests): keep one person per label.
    func adopt(_ faces: [TrackedFace], now: Double) {
        locked {
            var present = Set<Int>()
            for f in faces {
                var p = people.values.first { $0.displayName == f.label } ?? {
                    let n = nextId
                    nextId += 1
                    return SessionPerson(id: n, displayName: f.label, colorIndex: f.colorIndex,
                                         lastKnownWorldBearing: f.bearing, lastSeenAt: now)
                }()
                p.currentTrackID = f.id
                p.viaBody = f.viaBody
                p.lastKnownWorldBearing = f.bearing
                p.lastSeenAt = now
                people[p.id] = p
                present.insert(p.id)
            }
            for (id, p) in people where p.isVisible && !present.contains(id) {
                people[id]?.currentTrackID = nil
                people[id]?.viaBody = false
            }
        }
    }

    // MARK: audio continuity

    /// Speech was attributed to the person on this track while the laptop's source ids are meaningful:
    /// remember which audio source is theirs.
    func associate(trackId: Int, sourceId: Int, now: Double) {
        locked {
            guard var p = people.values.first(where: { $0.currentTrackID == trackId }) else { return }
            for (id, other) in people where other.audioSourceID == sourceId && id != p.id { people[id]?.audioSourceID = nil }
            p.audioSourceID = sourceId
            if p.recentAudioSources.last != sourceId {
                p.recentAudioSources = Array((p.recentAudioSources + [sourceId]).suffix(Tunables.personHistorySize))
            }
            p.lastHeardAt = now
            people[p.id] = p
        }
    }

    /// Meaningful source ids only: someone out of view whose audio source is still being tracked
    /// keeps a live bearing from it.
    func follow(sources: [Source], now: Double) {
        locked {
            for (id, p) in people where !p.isVisible {
                guard let sid = p.audioSourceID, let s = sources.first(where: { $0.id == sid }), s.speech else { continue }
                people[id]?.lastKnownWorldBearing = s.bearingDeg
                people[id]?.lastHeardAt = now
            }
        }
    }

    /// Speech from out of view. Whose is it? With meaningful ids, the person who owns that source.
    /// Otherwise the one person last known within `matchDeg` of it whose bearing is still credible;
    /// two such people is not an answer.
    func heard(bearing: Double, sourceId: Int?, now: Double) -> SessionPerson? {
        locked {
            let hidden = people.values.filter { !$0.isVisible }
            var match = sourceId.flatMap { sid in hidden.first { $0.audioSourceID == sid } }
            if match == nil {
                let near = hidden.filter {
                    $0.hasCredibleBearing(now: now) && abs(Geo.angDiff($0.lastKnownWorldBearing, bearing)) <= Tunables.matchDeg
                }
                if near.count == 1 { match = near[0] }
            }
            guard var p = match else { return nil }
            p.lastKnownWorldBearing = bearing
            p.lastHeardAt = now
            people[p.id] = p
            return p
        }
    }

    // MARK: pins, known people, housekeeping

    @discardableResult
    func togglePin(personId: Int) -> Bool {
        locked {
            guard var p = people[personId] else { return false }
            p.isSessionPinned.toggle()
            people[personId] = p
            return p.isSessionPinned
        }
    }

    func setPinned(_ pinned: Bool, personId: Int) { locked { people[personId]?.isSessionPinned = pinned } }

    func apply(_ known: KnownPerson, to personId: Int) {
        locked {
            people[personId]?.knownPersonID = known.id
            people[personId]?.displayName = known.chosenName
            people[personId]?.colorIndex = known.preferredColor
            people[personId]?.priority = known.priority
        }
    }

    /// Forget people who have been gone a long time. Pinned and enrolled people are never forgotten.
    func expire(now: Double) {
        locked {
            people = people.filter {
                $0.value.isVisible || $0.value.isSessionPinned || $0.value.knownPersonID != nil
                    || now - max($0.value.lastSeenAt, $0.value.lastHeardAt ?? -.infinity) <= Tunables.sessionPersonExpiryS
            }
        }
    }

    func reset() {
        locked {
            people = [:]
            nextId = 1
            decisions = []
            decisionByPerson = [:]
        }
    }
}
