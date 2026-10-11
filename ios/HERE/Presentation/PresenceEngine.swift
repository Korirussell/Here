import Foundation

/// Everything the presentation layer looks at for one step. All of it is state the app already has.
struct PresenceInput {
    var now: Double
    var heading: Double
    var hfovDeg: Double
    var captions: [CaptionState] = []     // the newest caption per speaker
    var alerts: [AlertState] = []
    var sounds: [SoundTag] = []
    var faces: [TrackedFace] = []
    var people: [SessionPerson] = []
    var objects: [SceneObject] = []       // what the room is known to contain, silent or not
    var sources: [Source] = []
    var baseline: Double? = nil
    var focusMode = false
    var enrichments: [String: EnrichmentState] = [:]   // the laptop's attention judgments, by EnrichmentState.key
    /// How unsure sound directions are in the wearer's frame right now (unknown source distance when
    /// the wearer is away from the array, measured calibration error). Attributions to objects are
    /// discounted by it: an unattached sound is better than a confidently wrong attachment.
    var associationUncertaintyDeg = 0.0
    var room = RoomGeometry()                          // for per-sound parallax uncertainty
}

/// Turns app state into `SpatialScene`: one independent notification per active source (its own icon,
/// color, direction, intensity and life cycle), plus who on camera is speaking.
///
/// Presentation only. It never drops or alters what the phone received: a sound below the relevance
/// threshold lands in `suppressed`, one that doesn't fit lands in `queued`, both still tracked.
///
/// Deterministic: no clocks, no randomness.
final class PresenceEngine {
    struct Memo: Equatable {
        var firstSeen: Double
        var lastStep: Double
        var habituationStart: Double
        var count = 0
        var occurrence: String? = nil
        var pulses = 0
        var peakProminence = 0.0
        var intensity: Double? = nil
        var resolvedAt: Double? = nil
        var wasNotification = false
        var attachedAt: Double? = nil
        var entity: EntityRef? = nil         // what this source was last attributed to
    }

    private(set) var memory: [String: Memo] = [:]
    /// Smoothed speech intensity per visible speaker (keyed by SessionPerson, so it survives a track
    /// reset): the same attack/release smoothing the notifications use, feeding the aura.
    private var aura: [Int: (value: Double, at: Double)] = [:]

    /// An environmental or unknown sound the wearer has turned to face has done its job: it pulses
    /// and is removed. HERE has no visual track for such a sound, so nothing is highlighted.
    var dissolveWhenFaced = true

    func reset() {
        memory = [:]
        aura = [:]
    }

    func step(_ i: PresenceInput) -> SpatialScene {
        let facesById = Dictionary(i.faces.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let peopleById = Dictionary(i.people.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        func visibleFace(of p: SessionPerson) -> TrackedFace? { p.currentTrackID.flatMap { facesById[$0] } }

        var candidates: [SpatialPresence] = []
        var suppressed: [SpatialPresence] = []
        var seen = Set<String>()
        var speechByPerson: [Int: CaptionState] = [:]

        // Speech. On a tracked face it is that person's aura and caption. Out of view it is a
        // notification: blue until it's known whose voice it is, then that person's color.
        for c in i.captions {
            var faceId: Int?
            if case let .person(id, _) = c.speaker, facesById[id] != nil { faceId = id }
            // Out of view. Whose voice? The person it was attributed to, or -- in the moment between
            // their face leaving view and attribution letting go -- the person it was just on.
            var ownerId = c.ownerPersonId
            if ownerId == nil, faceId == nil, case let .person(_, label) = c.speaker {
                ownerId = i.people.first { $0.displayName == label && !$0.isVisible }?.id
            }
            if faceId == nil, let owner = ownerId.flatMap({ peopleById[$0] }), visibleFace(of: owner) == nil {
                if let existing = speechByPerson[owner.id], existing.updated >= c.updated { continue }
                speechByPerson[owner.id] = c
                continue
            }
            let speaking = Self.isSpeaking(c, now: i.now)
            let fade = c.final ? Relevance.lifecycleFade(sinceActive: i.now - c.updated, hold: Tunables.speechReleaseHoldS) : 1
            guard fade > 0 || faceId != nil else { continue }
            let loud = Relevance.clamp01(c.loudness)
            var p = SpatialPresence(id: "utt:\(c.uttId)", sourceType: .speech, worldBearing: c.bearing,
                                    category: .speech, icon: SoundIcon.speech, tone: .human, label: nil,
                                    prominence: loud, urgency: 0, persistence: i.now - c.started,
                                    score: Relevance.speechScore(loudness: loud), level: .speech,
                                    firstSeen: c.started, lastSeen: c.updated)
            p.associatedTrackedFaceID = faceId
            p.speaking = speaking
            p.active = !c.final
            p.occurrence = c.uttId
            p.captionId = c.uttId
            p.intensity = c.final ? 0 : loud
            p.fade = fade
            p.tier = 6
            Self.raise(speech: &p, by: i.enrichments[EnrichmentState.key(uttId: c.uttId)])
            candidates.append(p)
        }

        // People. In view they're a face. Out of view, someone who is speaking, pinned, or always
        // prioritized keeps a notification, with a direction only while that direction is credible.
        for person in i.people {
            let face = visibleFace(of: person)
            let said = face == nil ? speechByPerson[person.id] : nil
            let credible = person.hasCredibleBearing(now: i.now)
            let speechFade = said.map { $0.final ? Relevance.lifecycleFade(sinceActive: i.now - $0.updated, hold: Tunables.speechReleaseHoldS) : 1 } ?? 0
            let important = person.priority != .normal
            let holdsPlace = person.isSessionPinned || (person.priority == .always && credible)
            guard face != nil || holdsPlace || speechFade > 0 else { continue }

            let talking = said != nil && speechFade > 0
            let loud = said.map { Relevance.clamp01($0.loudness) } ?? 0
            var p = SpatialPresence(id: "person:\(person.id)", sourceType: .person,
                                    worldBearing: face?.bearing ?? (credible ? person.lastKnownWorldBearing : nil),
                                    category: .speech, icon: talking ? SoundIcon.speech : SoundIcon.person,
                                    tone: .person(person.colorIndex), label: person.displayName,
                                    prominence: talking ? loud : Tunables.pinnedIdleProminence, urgency: 0,
                                    persistence: 0,
                                    score: talking ? Relevance.speechScore(loudness: loud) : Tunables.pinnedIdleScore,
                                    level: talking ? .speech : .ambient,
                                    firstSeen: person.lastSeenAt, lastSeen: person.lastSeenAt)
            p.personId = person.id
            p.isPinned = person.isSessionPinned
            p.associatedTrackedFaceID = face?.id
            p.stale = face == nil && !credible
            p.speaking = said.map { Self.isSpeaking($0, now: i.now) } ?? false
            p.active = said.map { !$0.final } ?? false
            p.occurrence = said?.uttId
            p.captionId = said?.uttId
            p.intensity = (said.map { !$0.final } ?? false) ? loud : 0
            if holdsPlace {
                // Pinned: the place is theirs whether or not they're talking, and their words ride along.
                if !talking { p.fixedOpacity = Tunables.pinnedIdleOpacity }
                if person.isSessionPinned, let said, !said.typing {
                    p.captionText = said.text
                    p.captionPending = said.pending
                }
            } else {
                p.fade = speechFade
            }
            p.tier = talking ? (person.isSessionPinned ? 8 : (important ? 7 : 6)) : 5.5
            if talking, let said { Self.raise(speech: &p, by: i.enrichments[EnrichmentState.key(uttId: said.uttId)]) }
            candidates.append(p)
        }

        // Sounds: each one its own notification, competing only on whether it is relevant at all.
        for s in i.sounds {
            let id = "snd:\(s.eventId)"
            seen.insert(id)
            var m = memory[id] ?? Memo(firstSeen: i.now, lastStep: i.now, habituationStart: i.now)
            let dt = max(0, i.now - m.lastStep)
            if s.count != m.count {                              // it happened again: fresh, not droning on
                m.count = s.count
                m.habituationStart = i.now
            }

            // Current level -> 0...1: its source's energy, above the room's adapting baseline.
            var current: Double
            if let baseline = i.baseline, let level = Self.sourceLevel(of: s, among: i.sources) {
                current = Relevance.prominence(level: level, baseline: baseline)
                m.peakProminence = max(current, m.peakProminence * exp(-dt / Tunables.prominencePeakHoldS))
            } else {
                // No level for it (no sources, or none that is its own): lean on the classifier's
                // confidence, flat. A failed match is not silence.
                current = Relevance.fallbackProminence(confidence: s.confidence)
                m.peakProminence = current
            }
            memory[id] = m

            let semantic = Relevance.semantic(s.label)
            let score = Relevance.soundScore(semantic: semantic, prominence: m.peakProminence,
                                             habituation: Relevance.habituation(age: i.now - m.habituationStart))
            var level = Relevance.soundLevel(score: score)

            // Active while the laptop keeps reporting it; held through short gaps; then faded.
            // None of this looks at how loud it was.
            let fade = Relevance.lifecycleFade(sinceActive: i.now - s.updated, hold: Tunables.soundReleaseHoldS)
            guard fade > 0 else { continue }

            let tone = Relevance.tone(label: s.label, text: s.text)
            let shown = level ?? .ambient
            var p = SpatialPresence(id: id, sourceType: .sound, worldBearing: s.bearing, category: s.category,
                                    icon: SoundIcon.symbol(label: s.label, text: s.text), tone: tone,
                                    label: s.text.isEmpty ? nil : s.text,
                                    prominence: m.peakProminence, urgency: semantic, persistence: i.now - m.firstSeen,
                                    score: score, level: shown, firstSeen: m.firstSeen, lastSeen: s.updated)
            p.count = s.count
            p.occurrence = "\(s.count)"
            p.intensity = current
            p.fade = fade
            if tone == .danger { p.opacityFloor = Tunables.dangerOpacityFloor }
            p.tier = tone == .danger ? 9 : (tone == .human ? 5 : (shown == .relevant ? 4 : 3))

            // The laptop's attention judgment, once one has arrived, sets how prominent the card is:
            // ambient = minimal, notice = a small indicator, important = a full card, urgent = interrupt.
            // A danger sound is never made less than the rules make it. No judgment: nothing changes.
            var interrupts = false
            if let e = i.enrichments[EnrichmentState.key(eventId: s.eventId)] {
                switch e.attention {
                case .ambient where tone != .danger:
                    p.level = .ambient
                    p.tier = min(p.tier, Tunables.enrichAmbientTier)
                    p.opacityCap = Tunables.enrichAmbientOpacityMax
                case .notice:
                    if tone != .danger { p.level = .ambient }
                    level = level ?? .ambient                    // shown, even below the relevance threshold
                case .important:
                    p.level = max(p.level, SpatialPresence.Level.relevant)
                    p.tier = max(p.tier, Tunables.enrichImportantTier)
                    p.opacityFloor = max(p.opacityFloor, Tunables.alertOpacityFloor)
                    level = level ?? .relevant
                case .urgent:
                    p.level = .important
                    p.tier = max(p.tier, Tunables.enrichUrgentTier)
                    p.opacityFloor = max(p.opacityFloor, Tunables.dangerOpacityFloor)
                    level = level ?? .important
                    interrupts = true                            // like an alert, it comes through focus mode
                default:
                    break
                }
            }
            fuse(&p, input: i, micBearing: s.micBearing) { Fusion.compatibility(label: s.label, text: s.text, object: $0) }

            if (level == nil && tone != .danger) || (i.focusMode && !interrupts) {
                suppressed.append(p)
            } else {
                candidates.append(p)
            }
        }

        // Alerts: the wearer's name, a doorbell, an alarm. Always relevant.
        for a in i.alerts {
            let fading = i.now - a.updated
            guard a.active || fading < Tunables.alertFadeS else { continue }
            let tone = Relevance.tone(alert: a.kind, priority: a.priority)
            var p = SpatialPresence(id: "alr:\(a.alertId)", sourceType: .alert, worldBearing: a.bearing,
                                    category: .alert, icon: SoundIcon.symbol(alert: a.kind), tone: tone, label: a.label,
                                    prominence: 1, urgency: Relevance.clamp01(Double(a.priority) / 3), persistence: 0,
                                    score: Relevance.alertScore(priority: a.priority, nameCalled: a.kind == .nameCalled),
                                    level: .important, firstSeen: a.updated, lastSeen: a.updated)
            p.active = a.active
            p.count = a.count
            p.occurrence = "\(a.count)"
            p.intensity = 1
            p.opacityFloor = tone == .danger ? Tunables.dangerOpacityFloor : Tunables.alertOpacityFloor
            p.fade = a.active ? 1 : Relevance.clamp01(1 - fading / Tunables.alertFadeS)
            p.tier = tone == .danger || a.priority >= 3 ? 9 : (a.kind == .nameCalled ? 8.5 : 4.5)
            // The laptop's judgment of what this alert is about can only raise it: urgent = interrupt.
            // Nothing it says lowers an alert (or the wearer's name) below what the rules give.
            let judged = a.refUttId.flatMap { i.enrichments[EnrichmentState.key(uttId: $0)] }
                ?? a.refEventId.flatMap { i.enrichments[EnrichmentState.key(eventId: $0)] }
            if judged?.attention == .urgent {
                p.tier = max(p.tier, Tunables.enrichUrgentTier)
                p.opacityFloor = max(p.opacityFloor, Tunables.dangerOpacityFloor)
            }
            if a.kind != .nameCalled { fuse(&p, input: i) { Fusion.compatibility(alert: a.kind, object: $0) } }
            if let utt = a.refUttId, let c = i.captions.first(where: { $0.uttId == utt }),
               case let .person(id, _) = c.speaker, facesById[id] != nil {
                p.associatedTrackedFaceID = id
            }
            candidates.append(p)
        }

        // Life cycle shared by everything: intensity smoothing, pulses, resolving, handing off to a face.
        let rearmDeg = min(Tunables.inwardThresholdDeg, i.hfovDeg / 2)
        var shown: [SpatialPresence] = []
        for var p in candidates {
            seen.insert(p.id)
            var m = memory[p.id] ?? Memo(firstSeen: i.now, lastStep: i.now, habituationStart: i.now)
            let dt = max(0, i.now - m.lastStep)
            m.lastStep = i.now
            var happenedAgain = false
            if let occ = p.occurrence, occ != m.occurrence {
                m.occurrence = occ
                m.pulses += 1
                happenedAgain = true
            }
            p.pulseCount = m.pulses
            if p.sourceType != .sound { p.persistence = i.now - m.firstSeen }

            m.intensity = m.intensity.map { Relevance.smooth($0, toward: p.intensity, dt: dt) } ?? p.intensity
            p.intensity = m.intensity ?? p.intensity
            let base = p.fixedOpacity ?? Relevance.opacity(intensity: p.intensity, floor: p.opacityFloor)
            p.opacity = min(base, p.opacityCap ?? 1) * p.fade

            // Now on a tracked face: the aura and face caption take over. If it was a notification a
            // moment ago, let it travel there first (the turn-around).
            if p.associatedTrackedFaceID != nil, p.sourceType != .alert {
                guard m.wasNotification else {
                    memory[p.id] = m
                    continue
                }
                let at = m.attachedAt ?? i.now
                m.attachedAt = at
                let k = (i.now - at) / Tunables.handoffS
                if k >= 1 {
                    m.wasNotification = false
                    m.attachedAt = nil
                    memory[p.id] = m
                    continue
                }
                p.handoffProgress = k
                p.opacity *= 1 - k
                memory[p.id] = m
                shown.append(p)
                continue
            }
            m.attachedAt = nil

            // (A sound resolved onto a visible object stays on it instead: there *is* something to show.)
            if dissolveWhenFaced, p.sourceType == .sound, p.tone != .danger, p.objectBox == nil,
               let rel = p.relativeBearing(heading: i.heading) {
                if happenedAgain { m.resolvedAt = nil }
                if abs(rel) <= Tunables.resolveCompleteDeg {
                    if m.resolvedAt == nil { m.resolvedAt = i.now }
                } else if abs(rel) >= rearmDeg {
                    m.resolvedAt = nil
                }
                if let r = m.resolvedAt {
                    p.resolvedAt = r
                    p.opacity *= Relevance.clamp01(1 - (i.now - r - Tunables.resolveHoldS) / Tunables.resolveDissolveS)
                }
            }
            let drawn = p.opacity > 0.01
            m.wasNotification = drawn && p.associatedTrackedFaceID == nil
            memory[p.id] = m
            if drawn { shown.append(p) }
        }
        memory = memory.filter { seen.contains($0.key) }

        // Capacity. Everything relevant shows at once; priority only decides who waits when there
        // are more notifications than places, and whoever waits gets a place as soon as one frees up.
        shown.sort { ($0.rank, $0.id) > ($1.rank, $1.id) }
        var places = 0
        var presences: [SpatialPresence] = []
        var queued: [SpatialPresence] = []
        for p in shown {
            if p.associatedTrackedFaceID == nil, p.objectBox == nil {
                if places >= Tunables.maxPerimeterIcons {
                    queued.append(p)
                    continue
                }
                places += 1
            }
            presences.append(p)
        }

        var links: [Int: String] = [:]
        for p in presences + queued { if let o = p.objectId { links[o] = p.id } }
        // Each sound resolved onto a visible object makes that object glow, all at once if there are
        // several. (One object, one glow: its strongest sound.)
        let glowing = presences.filter { $0.objectBox != nil }
        let talking = speakers(i)
        let crowded = glowing.count + talking.filter({ $0.activity > 0 }).count > Tunables.auraCrowdCount
        var auras: [Int: ObjectAura] = [:]
        for p in glowing {
            guard let id = p.objectId, let box = p.objectBox, let object = i.objects.first(where: { $0.id == id }) else { continue }
            let tone = SoundContinuity.objectAuraTone(p.tone)
            let aura = ObjectAura(objectId: id, cls: object.cls, box: box, precise: p.objectPrecise, tone: tone, soundId: p.id,
                                  confidence: p.associationConfidence, intensity: p.intensity,
                                  opacity: SoundContinuity.objectAuraOpacity(intensity: p.intensity, life: p.fade, danger: tone == .danger,
                                                                             crowded: crowded),
                                  releasing: p.fade < 1 || !p.active)
            if let existing = auras[id], existing.opacity >= aura.opacity { continue }
            auras[id] = aura
        }
        return SpatialScene(presences: presences, speakers: talking, queued: queued, suppressed: suppressed,
                            pinnedPersonIds: Set(i.people.filter(\.isSessionPinned).map(\.id)), objectLinks: links,
                            objectAuras: auras.values.sorted { $0.objectId < $1.objectId }, baseline: i.baseline)
    }

    /// AudioSource -> visual entity. Which known object occupies the direction this sound comes from?
    /// (People in view are candidates too: a person standing there makes an object attribution
    /// uncertain.) Attributed to an object, the notification says so in words and takes the object's
    /// bearing as the better estimate of where; while that object is actually in view, the indication
    /// leaves the top-right and sits on it. No match: the sound stays what it was, a direction.
    private func fuse(_ p: inout SpatialPresence, input i: PresenceInput, micBearing: Double? = nil,
                      semantic: (ObjectClass) -> Double) {
        var m = memory[p.id] ?? Memo(firstSeen: i.now, lastStep: i.now, habituationStart: i.now)
        let candidates = Fusion.candidates(objects: i.objects, faces: i.faces, heading: i.heading, hfovDeg: i.hfovDeg,
                                           now: i.now, prior: m.entity, semantic: semantic)
        var result = Fusion.associate(bearing: p.worldBearing, candidates: candidates)
        if let r = result {
            let parallax = micBearing.map { i.room.listenerUncertainty(micBearing: $0) } ?? 0
            let trust = max(0, 1 - max(i.associationUncertaintyDeg, parallax) / Tunables.fusionUncertaintyFullDeg)
            result = r.score * trust >= Tunables.fusionAcceptScore ? (r.ref, r.score * trust) : nil
        }
        m.entity = result?.ref
        memory[p.id] = m
        guard case let .object(id)? = result?.ref, let object = i.objects.first(where: { $0.id == id }) else { return }
        p.objectId = object.id
        p.associationConfidence = result?.score ?? 0
        // Name the source, unless the sound's own words already do ("Phone ringing", "Alarm").
        let words = p.label ?? "Sound"
        if !words.lowercased().contains(object.cls.title.lowercased()) { p.label = words + " · " + object.cls.title }
        let inView = abs(Geo.relative(object.worldBearing, heading: i.heading)) < i.hfovDeg / 2
        let state = object.state(now: i.now, inView: inView)
        // On the object (icon and glow) only when it is really in view *and* the attribution is
        // confident; otherwise the sound stays a top-right notification that names its likely source.
        if inView, state == .visible || state == .reacquired, p.associationConfidence >= Tunables.auraAttachConfidence {
            p.objectBox = object.box
            p.objectPrecise = object.precise
        }
    }

    /// A sound's level in `sources`: its own track when the laptop names it (`source_id`) and that track is
    /// live, else the loudest entry near its bearing. nil = nothing matches (the real array's fixed beams
    /// sit at 0 / 180 while a sound's bearing comes from the raw mics), which is not the same as silence.
    static func sourceLevel(of s: SoundTag, among sources: [Source]) -> Double? {
        if let id = s.sourceId, let own = sources.first(where: { $0.id == id }) { return own.energy }
        guard let bearing = s.bearing else { return nil }
        return sources
            .filter { abs(Geo.angDiff($0.bearingDeg, bearing)) <= Tunables.soundSourceMatchDeg }
            .map(\.energy).max()
    }

    /// Speech is only ever raised by the laptop's judgment, never lowered (captions are the point of HERE):
    /// urgent interrupts; important, or clearly addressed to the wearer, ranks like a prioritized person.
    static func raise(speech p: inout SpatialPresence, by e: EnrichmentState?) {
        guard let e else { return }
        if e.attention == .urgent {
            p.level = .important
            p.tier = max(p.tier, Tunables.enrichUrgentTier)
            p.opacityFloor = max(p.opacityFloor, Tunables.enrichSpeechOpacityFloor)
        } else if e.attention == .important || (e.directedAtUser ?? 0) >= Tunables.directedAtUserRaise {
            p.tier = max(p.tier, Tunables.enrichSpeechTier)
            p.opacityFloor = max(p.opacityFloor, Tunables.enrichSpeechOpacityFloor)
        }
    }

    static func isSpeaking(_ c: CaptionState, now: Double) -> Bool {
        !c.final || now - c.updated < Tunables.ringHoldS
    }

    /// Who on camera is talking, just talked, or is pinned.
    private func speakers(_ i: PresenceInput) -> [SpeakerPresence] {
        i.faces.compactMap { face in
            var activity = 0.0, resting = 0.0, loudness = 0.0, level = 0.0
            for c in i.captions {
                guard case let .person(id, _) = c.speaker, id == face.id else { continue }
                loudness = max(loudness, c.loudness)
                if !c.final { level = max(level, Relevance.clamp01(c.loudness)) }
                if c.final {
                    let since = i.now - c.updated
                    activity = max(activity, Relevance.clamp01(1 - since / Tunables.auraFadeS))
                    resting = max(resting, Relevance.clamp01((Tunables.captionHoldS - since) / Tunables.auraRestFadeS))
                } else {
                    activity = 1
                    resting = 1
                }
            }
            let person = i.people.first { $0.currentTrackID == face.id }
            let pinned = person?.isSessionPinned ?? false
            let key = person?.id ?? face.personId ?? -face.id
            let before = aura[key]
            let intensity = before.map { Relevance.smooth($0.value, toward: level, dt: i.now - $0.at) } ?? level
            aura[key] = (intensity, i.now)
            guard activity > 0 || resting > 0 || pinned else {
                aura[key] = nil
                return nil
            }
            return SpeakerPresence(faceId: face.id, personId: person?.id ?? face.personId, label: face.label,
                                   colorIndex: face.colorIndex, activity: activity, resting: resting,
                                   loudness: loudness, isPinned: pinned, intensity: intensity)
        }
    }
}

/// The presentation layer's own state, bundled: the room baseline and the engine. `AppModel` owns one
/// for the live app; the debug scenarios own another, so both run the same code.
final class PresentationCore {
    private(set) var baseline = RoomBaseline()
    private let engine = PresenceEngine()

    /// Every `sources` message with a level feeds the baseline, whether or not anything is on screen.
    /// `"sources": []` (nothing live, repeated as a 1 s keepalive) carries no level: it is not a reading
    /// of 0, so the baseline holds instead of sinking toward silence in a loud room.
    func ingest(sources: [Source], now: Double) {
        guard let level = sources.map(\.energy).max() else {
            baseline.hold(now: now)
            return
        }
        baseline.update(level: level, now: now)
    }

    func step(now: Double, heading: Double, hfovDeg: Double, captions: [CaptionState], alerts: [AlertState],
              sounds: [SoundTag], faces: [TrackedFace], people: [SessionPerson], objects: [SceneObject] = [],
              sources: [Source], focusMode: Bool, associationUncertaintyDeg: Double = 0, room: RoomGeometry = RoomGeometry(),
              enrichments: [String: EnrichmentState] = [:]) -> SpatialScene {
        engine.step(PresenceInput(now: now, heading: heading, hfovDeg: hfovDeg, captions: captions, alerts: alerts,
                                  sounds: sounds, faces: faces, people: people, objects: objects, sources: sources,
                                  baseline: baseline.value, focusMode: focusMode, enrichments: enrichments,
                                  associationUncertaintyDeg: associationUncertaintyDeg, room: room))
    }

    func reset() {
        baseline.reset()
        engine.reset()
    }
}
