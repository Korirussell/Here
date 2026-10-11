import Foundation

/// A face the phone can see right now, with its world bearing.
struct FaceRef: Equatable {
    let id: Int          // tracker id (stable while tracked)
    let label: String    // "Person 1"
    let bearing: Double  // world degrees
}

/// Who an utterance (or sound) is attached to.
enum Speaker: Equatable, Hashable {
    case person(id: Int, label: String)
    case offCamera

    var label: String? {
        if case let .person(_, label) = self { return label }
        return nil
    }
}

/// Speech -> person, on the phone (README §13). Thresholds are (tune) in Tunables.
///
/// 1. On speech_start / utterance, pick the visible face within `matchDeg` of the bearing;
///    if two are close (within `tieDeg` of each other's distance), prefer whoever spoke most recently.
/// 2. No face in range -> off-camera speaker (caption at the screen edge, chevron toward the bearing).
/// 3. Sticky: keep the assignment for the whole utterance unless the bearing moves > `moveDeg`
///    for > `moveHoldS`.
/// 4. The turn-around: an off-camera utterance attaches to a face as soon as one appears at its
///    bearing (the wearer turned toward them), so the caption slides onto the person.
/// 5. Spread-aware: the laptop's `bearing_spread_deg` widens the match window to
///    max(matchDeg, min(spread, matchDegSpreadMax)) -- a fixed-beam array that only knows "front"
///    still lands a lone talker 25 deg off-centre on their face. With several faces in a widened
///    window the angle alone isn't trusted: established audio-source owner, then the most recent
///    speaker, else the nearest with a low (tentative) score. A spread <= matchDeg changes nothing.
final class Attributor {
    struct Assignment {
        var speaker: Speaker
        var bearing: Double            // bearing the assignment was made at
        var movedSince: Double?        // when the bearing first moved > moveDeg away
        var score: Double
        var provisional = false        // made from speech_start's quick bearing; the first utterance re-decides
        var faceLostSince: Double? = nil   // when the attached face's id first stopped appearing in `faces`
        var spread: Double = 0             // bearing spread it was last decided with (refresh re-uses it)
    }

    var matchDeg = Tunables.matchDeg
    var tieDeg = Tunables.tieDeg
    var moveDeg = Tunables.moveDeg
    var moveHoldS = Tunables.moveHoldS
    var faceLostHoldS = Tunables.faceLostHoldS

    /// Why the most recent fresh attribution went the way it did (developer overlay).
    struct Decision: Equatable {
        var candidate: String?         // the nearest face's label, if any face was near at all
        var angularError: Double?
        var accepted: Bool
        var reason: String
    }
    private(set) var lastDecision: Decision?
    var matchDegEstablished = Tunables.matchDegEstablished
    var matchDegSpreadMax = Tunables.matchDegSpreadMax

    private(set) var assignments: [String: Assignment] = [:]
    private(set) var lastSpoke: [Int: Double] = [:]      // face id -> time

    /// Assign (or re-check) an utterance. Returns who it's on now.
    /// `provisional`: speech_start's bearing comes from a few readings at voice onset and is often off in
    /// a loud room (tools/stress_test.py); the first utterance update re-decides it from scratch using the
    /// utterance's averaged bearing, after which the normal sticky rule applies.
    @discardableResult
    func attribute(utt: String, bearing: Double, faces: [FaceRef], now: Double, provisional: Bool = false,
                   established: Set<Int> = [], spread: Double = 0) -> Speaker {
        self.established = established
        if let a = assignments[utt], a.provisional, !provisional {
            assignments[utt] = nil
        }
        if var a = assignments[utt] {
            let moved = abs(Geo.angDiff(bearing, a.bearing)) > moveDeg
            if moved {
                if a.movedSince == nil { a.movedSince = now }
                if let since = a.movedSince, now - since >= moveHoldS {
                    a = fresh(bearing: bearing, faces: faces, spread: spread)
                }
            } else {
                a.movedSince = nil
            }
            if case .offCamera = a.speaker, !moved {
                let candidate = fresh(bearing: bearing, faces: faces, spread: spread)   // the turn-around
                if case .person = candidate.speaker { a = candidate }
            }
            if case let .person(id, _) = a.speaker {
                // Sticky even if the face flickers out of tracking for a moment; the UI places a
                // person who isn't visible right now at their bearing instead.
                lastSpoke[id] = now
            }
            a.spread = spread
            assignments[utt] = a
            return a.speaker
        }
        var a = fresh(bearing: bearing, faces: faces, spread: spread)
        a.provisional = provisional
        if case let .person(id, _) = a.speaker { lastSpoke[id] = now }
        assignments[utt] = a
        return a.speaker
    }

    /// Re-check every utterance against the faces in this frame (called per frame): off-camera
    /// utterances look for a face to attach to (the turn-around), and -- the reverse, previously
    /// missing -- utterances attached to a face whose track disappeared and *stays* gone for
    /// `faceLostHoldS` fall back to off-camera at their last known bearing, instead of staying glued
    /// to a `track_id` nothing renders anymore. Without this, `Layout.captions` can't find a rect for
    /// that id, falls into its "in view, no face found" branch, and places the caption at a bearing
    /// that's now frozen (no face left to re-derive it from) -- it looks like the caption drifting
    /// free of the person, and lingers indefinitely once they fully leave frame.
    ///
    /// The hold matters: this runs every frame, and Vision's per-frame tracking can drop a face for a
    /// single frame on a blink or a fast turn without them actually leaving (same spirit as `moveHoldS`
    /// for bearing drift) -- react immediately and every blink flickers the caption off-camera and back.
    func refresh(faces: [FaceRef], bearings: [String: Double], now: Double) -> [String: Speaker] {
        var changed: [String: Speaker] = [:]
        let visibleIds = Set(faces.map(\.id))
        for (utt, a) in assignments {
            if case let .person(id, _) = a.speaker {
                var updated = a
                if visibleIds.contains(id) {
                    if a.faceLostSince != nil {
                        updated.faceLostSince = nil
                        assignments[utt] = updated
                    }
                } else {
                    let since = a.faceLostSince ?? now
                    if a.faceLostSince == nil { updated.faceLostSince = now }
                    if now - since >= faceLostHoldS {
                        updated.speaker = .offCamera
                        updated.movedSince = nil
                        updated.faceLostSince = nil
                        changed[utt] = .offCamera
                    }
                    assignments[utt] = updated
                }
                continue
            }
            guard case .offCamera = a.speaker, let b = bearings[utt] else { continue }
            let s = attribute(utt: utt, bearing: b, faces: faces, now: now, spread: a.spread)
            if s != a.speaker { changed[utt] = s }
        }
        return changed
    }

    /// The same person is now on a different Vision track (the old one was lost and they were
    /// re-identified): their utterances and "spoke most recently" follow them, with no gap.
    @discardableResult
    func retarget(from old: Int, to new: Int, label: String) -> [String] {
        var moved: [String] = []
        for (utt, a) in assignments {
            guard case let .person(id, _) = a.speaker, id == old else { continue }
            assignments[utt]?.speaker = .person(id: new, label: label)
            assignments[utt]?.faceLostSince = nil
            moved.append(utt)
        }
        if let t = lastSpoke.removeValue(forKey: old) { lastSpoke[new] = max(lastSpoke[new] ?? 0, t) }
        return moved
    }

    func score(for utt: String) -> Double { assignments[utt]?.score ?? 0 }

    func forget(_ utt: String) { assignments[utt] = nil }

    /// Face ids whose SessionPerson already owns the audio source being attributed. A brand-new
    /// face/source pair must be within `matchDeg`; an established pair has continuity on its side and
    /// is allowed `matchDegEstablished`.
    private var established: Set<Int> = []

    private func fresh(bearing: Double, faces: [FaceRef], spread: Double) -> Assignment {
        // The laptop's honest uncertainty: a spread wider than matchDeg widens every face's window (capped).
        // A spread <= matchDeg leaves spreadWin <= every base window, so today's rule runs unchanged.
        let spreadWin = min(max(spread, 0), matchDegSpreadMax)
        let widened = spreadWin > matchDeg
        let all = faces.map { (face: $0, d: abs(Geo.angDiff($0.bearing, bearing))) }.sorted { $0.d < $1.d }
        let near = all.filter { $0.d <= max(established.contains($0.face.id) ? matchDegEstablished : matchDeg, spreadWin) }
        let widenNote = widened ? String(format: " (window widened to %.0f deg by spread)", spreadWin) : ""
        guard let best = near.first else {
            lastDecision = Decision(candidate: all.first?.face.label, angularError: all.first?.d, accepted: false,
                                    reason: all.isEmpty ? "no faces in view" : "nearest face beyond the match angle" + widenNote)
            return Assignment(speaker: .offCamera, bearing: bearing, movedSince: nil, score: 0, spread: spread)
        }
        if widened && near.count > 1 {
            // Several faces inside a window only the spread opened: the angle alone can't choose.
            let owner = near.first(where: { established.contains($0.face.id) })
            let recent = near.filter { lastSpoke[$0.face.id] != nil }
                .max { (lastSpoke[$0.face.id] ?? 0) < (lastSpoke[$1.face.id] ?? 0) }
            let pick = owner ?? recent ?? best
            let tentative = owner == nil && recent == nil
            let why = owner != nil ? "established audio source"
                : recent != nil ? "most recent speaker" : "tentative: nearest of \(near.count) faces"
            lastDecision = Decision(candidate: pick.face.label, angularError: pick.d, accepted: true, reason: why + widenNote)
            let fit = max(0, 1 - pick.d / spreadWin)
            return Assignment(speaker: .person(id: pick.face.id, label: pick.face.label), bearing: bearing, movedSince: nil,
                              score: tentative ? min(fit, Tunables.tentativeAttributionScore) : fit, spread: spread)
        }
        lastDecision = Decision(candidate: best.face.label, angularError: best.d, accepted: true,
                                reason: (established.contains(best.face.id) ? "established audio source" : "within the match angle") + widenNote)
        let close = near.filter { $0.d - best.d <= tieDeg }
        let pick = close.max { (lastSpoke[$0.face.id] ?? 0) < (lastSpoke[$1.face.id] ?? 0) } ?? best
        return Assignment(speaker: .person(id: pick.face.id, label: pick.face.label), bearing: bearing,
                          movedSince: nil, score: max(0, 1 - pick.d / (widened ? spreadWin : matchDeg)), spread: spread)
    }
}
