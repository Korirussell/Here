import CoreVideo
import Foundation
import Vision

/// One tracked face in a frame.
struct TrackedFace: Identifiable, Equatable {
    let id: Int              // tracker id
    let label: String        // "Person 1", "Person 2"... (first-appearance order, README §13)
    let colorIndex: Int
    let box: CGRect          // Vision normalized, upright buffer, bottom-left origin
    let bearing: Double      // world degrees at this frame's heading
    var personId: Int? = nil     // the SessionPerson this track is attached to
    var viaBody = false          // held by a body detection; `box` is an estimated head region
    var confidence: Float = 1    // Vision's confidence in this observation
    var pixelHeight = 0.0        // face height in buffer pixels (diagnostics: "too small?")
    var mouthActivity = 0.0      // 0...1 smoothed lip movement: supporting evidence that this person is talking
    var bodyBox: CGRect? = nil   // the person detection this face sits in (Vision normalized): tracking evidence only
    var bodyConfidence: Float = 0
}

/// What the tracker saw last, for the developer overlay.
struct TrackerDiagnostics: Equatable {
    var facesDetected = 0            // raw face detections in the last detect cycle
    var bodies: [CGRect] = []        // person detections (Vision normalized), last detect cycle
    var bodyHeld = 0                 // tracks currently held by a body rather than a face
    var lastDrop = ""                // why the most recent track ended
}

/// Face detection every few frames + Vision object tracking in between (README §14 pipeline).
/// Runs on its own serial queue; drops frames while busy (the camera discards late frames too).
///
/// Identity is not decided here. A track is a temporary Vision observation; who it belongs to is
/// asked of `SessionPeople` when the track is born (re-identification from appearance, bearing, time,
/// body and audio continuity) and the track is detached from that person, not the person destroyed,
/// when it ends.
///
/// Body fallback: a person detection on each detect cycle is linked to the face inside it. When the
/// face stops being detected (turned away, too small, occluded) but that body is still there, the
/// track is held on an estimated head region instead of being dropped.
final class FaceTracker {
    private struct Track {
        let id: Int
        let label: String
        let colorIndex: Int
        var observation: VNDetectedObjectObservation
        /// Exponentially-smoothed render box, separate from `observation.boundingBox` (which stays
        /// raw -- Vision's own tracking continuity should see real, unsmoothed data). `.accurate`
        /// tracking in particular reports box *size* that visibly breathes frame to frame even when
        /// the face itself isn't moving; this is purely a display smoother so the on-screen box/ring
        /// doesn't expand and shrink with it.
        var smoothedBox: CGRect
        var bearing: Double
        var missedDetections = 0
        var personId: Int? = nil
        var bodyBox: CGRect? = nil       // the person detection this face sits inside
        var viaBody = false
        var confidence: Float = 1
        var detectsSincePrint = 0
        var pendingSince: Double? = nil  // who this is hasn't been decided yet (two people are plausible)
        var bodyConfidence: Float = 0
        var mouth: [Double] = []         // recent lip opening / face height
        var mouthActivity = 0.0
    }

    var detectEvery = Tunables.detectEveryFrames
    /// Called on the vision queue with the faces of each processed frame.
    var onFaces: (([TrackedFace]) -> Void)?
    var onDiagnostics: ((TrackerDiagnostics) -> Void)?
    /// The session's identities. Set once before frames arrive.
    var people: SessionPeople?
    var bodyFallback = Tunables.bodyFallbackEnabled
    /// Read lip movement from face landmarks on each detect cycle (one extra Vision request).
    var mouthTracking = true
    /// Explicitly enrolled people to check a brand-new person against (nil = recognition off).
    var knownMatch: ((VNFeaturePrintObservation) -> KnownPerson?)?
    /// What a person looks like, for this session: a few face-region and torso (clothing) feature
    /// prints taken at different moments, so a different pose or angle still finds a close one.
    private struct Appearance {
        var face: [VNFeaturePrintObservation] = []
        var torso: [VNFeaturePrintObservation] = []
    }
    private typealias Prints = (face: VNFeaturePrintObservation?, torso: VNFeaturePrintObservation?)
    private var gallery: [Int: Appearance] = [:]                   // SessionPerson id -> appearance
    private var diagnostics = TrackerDiagnostics()

    private let queue = DispatchQueue(label: "here.vision", qos: .userInitiated)
    /// `var`, not `let`: reset to a fresh handler every time `detect()` runs (see there). A
    /// `VNSequenceRequestHandler` expects to see every frame in order to track motion between them;
    /// `detect()` processes its frame through a totally separate `VNImageRequestHandler`, silently
    /// skipping that frame from this handler's point of view every `detectEveryFrames`. On real
    /// (noisy, continuously-moving chest-mounted) footage this reliably broke tracking continuity --
    /// confirmed live on device: `VNTrackObjectRequest` returned zero results on effectively every
    /// call, immediately dropping and re-detecting every track, constantly, which is what read as
    /// "flickering between person / not a person" (found 2026-10-09, live console debugging).
    private var sequence = VNSequenceRequestHandler()
    private var tracks: [Int: Track] = [:]
    private var nextId = 1
    private var frame = 0
    private var busy = false
    private let busyLock = NSLock()

    /// Camera queue: hand a frame over unless Vision is still busy with the previous one.
    func submit(_ pixelBuffer: CVPixelBuffer, heading: Double, hfovDeg: Double, now: Double) {
        busyLock.lock()
        if busy { busyLock.unlock(); return }
        busy = true
        busyLock.unlock()
        queue.async {
            let faces = self.process(pixelBuffer, heading: heading, hfovDeg: hfovDeg, now: now)
            self.onFaces?(faces)
            self.busyLock.lock(); self.busy = false; self.busyLock.unlock()
        }
    }

    func reset() {
        queue.async {
            self.tracks.removeAll()
            self.gallery.removeAll()
        }
    }

    // MARK: per frame (vision queue)

    private func process(_ pb: CVPixelBuffer, heading: Double, hfovDeg: Double, now: Double) -> [TrackedFace] {
        frame += 1
        let bearingOf = { (box: CGRect) in Geo.bearing(normalizedX: Double(box.midX), hfovDeg: hfovDeg, heading: heading) }
        if frame % max(1, detectEvery) == 0 || tracks.isEmpty {
            detect(pb, bearingOf: bearingOf, now: now)
        } else {
            track(pb, bearingOf: bearingOf, now: now)
        }
        mergeOverlappingTracks(now: now)
        diagnostics.bodyHeld = tracks.values.filter(\.viaBody).count
        onDiagnostics?(diagnostics)
        let height = Double(CVPixelBufferGetHeight(pb))
        return tracks.values
            .filter { $0.pendingSince == nil }               // undecided: no label, no color, nothing to get wrong
            .sorted { $0.observation.boundingBox.midX < $1.observation.boundingBox.midX }
            .map { t in
                people?.seen(trackId: t.id, bearing: t.bearing, viaBody: t.viaBody, now: now,
                             screenX: Double(t.smoothedBox.midX))
                // Name and color come from the person, so an enrollment or rename shows at once.
                let person = people?.person(trackId: t.id)
                return TrackedFace(id: t.id, label: person?.displayName ?? t.label,
                                   colorIndex: person?.colorIndex ?? t.colorIndex, box: t.smoothedBox,
                                   bearing: t.bearing, personId: person?.id, viaBody: t.viaBody,
                                   confidence: t.confidence, pixelHeight: Double(t.smoothedBox.height) * height,
                                   mouthActivity: t.viaBody ? 0 : t.mouthActivity, bodyBox: t.bodyBox,
                                   bodyConfidence: t.bodyConfidence)
            }
    }

    /// Blends `previous` toward `new` rather than snapping straight to it (display-only -- see
    /// `Track.smoothedBox`). `factor` is how much of the gap to close each frame: 1 = no smoothing.
    private static func smooth(_ previous: CGRect, toward new: CGRect, factor: CGFloat = 0.45) -> CGRect {
        CGRect(x: previous.minX + (new.minX - previous.minX) * factor,
               y: previous.minY + (new.minY - previous.minY) * factor,
               width: previous.width + (new.width - previous.width) * factor,
               height: previous.height + (new.height - previous.height) * factor)
    }

    private func detect(_ pb: CVPixelBuffer, bearingOf: (CGRect) -> Double, now: Double) {
        let request = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(cvPixelBuffer: pb, orientation: .up, options: [:])
        guard (try? handler.perform([request])) != nil else { return }
        // Weak detections are kept for now: a small, far-away face scoring low may still be someone
        // already tracked. Whether it may start a new person is decided after matching.
        let faces = Array(dedup((request.results ?? []).filter { $0.confidence >= Tunables.faceWeakConfidence })
            .sorted { $0.boundingBox.width * $0.boundingBox.height > $1.boundingBox.width * $1.boundingBox.height }
            .prefix(Tunables.maxFaces))
        // The upcoming track() calls (until the next detect()) need a handler whose "previous frame"
        // is this one, not some frame several detect-cycles back -- see `sequence`'s doc comment.
        sequence = VNSequenceRequestHandler()

        let people = bodyFallback ? detectBodies(handler) : []
        let bodies = people.map(\.box)
        diagnostics.facesDetected = faces.count
        diagnostics.bodies = bodies
        func body(around box: CGRect) -> CGRect? {
            bodies.filter { $0.contains(CGPoint(x: box.midX, y: box.midY)) }.min { $0.width * $0.height < $1.width * $1.height }
        }
        func confidence(of body: CGRect?) -> Float { people.first { $0.box == body }?.confidence ?? 0 }
        let frameHeight = Double(CVPixelBufferGetHeight(pb))

        // Lip opening per detected face, from landmarks: how far apart the inner lips are, relative to the face.
        var openness: [Int: Double] = [:]
        if mouthTracking, !faces.isEmpty {
            let landmarks = VNDetectFaceLandmarksRequest()
            landmarks.inputFaceObservations = faces
            if (try? handler.perform([landmarks])) != nil {
                for (i, result) in (landmarks.results ?? []).enumerated() where i < faces.count {
                    guard let lips = result.landmarks?.innerLips?.normalizedPoints, lips.count >= 3 else { continue }
                    let ys = lips.map { Double($0.y) }
                    openness[i] = (ys.max() ?? 0) - (ys.min() ?? 0)       // landmark points are relative to the face box
                }
            }
        }

        var assignment = assignDetections(faces.map(\.boundingBox))

        // Geometry couldn't place some detections (someone moved a long way between detections, or
        // came out from behind something) while some tracks found no detection. Before treating those
        // as "a person vanished" and "a new person appeared", ask whether they look like the same
        // person: if so the track simply follows them there, identity untouched.
        var detectionPrints: [Int: Prints] = [:]
        let orphans = faces.indices.filter { assignment[$0] == nil }
        let missing = tracks.values.filter { t in !assignment.values.contains(t.id) && t.personId != nil }
        if !orphans.isEmpty, !missing.isEmpty {
            var pairs: [(face: Int, track: Int, score: Double)] = []
            for i in orphans {
                let p = prints(handler, face: faces[i].boundingBox)
                detectionPrints[i] = p
                for t in missing {
                    guard let pid = t.personId, let known = gallery[pid], let sim = similarity(p, to: known),
                          sim >= Tunables.assocVisualMin else { continue }
                    pairs.append((i, t.id, sim))
                }
            }
            var usedFaces = Set<Int>(), usedTracks = Set<Int>()
            for pair in pairs.sorted(by: { $0.score > $1.score })
            where !usedFaces.contains(pair.face) && !usedTracks.contains(pair.track) {
                assignment[pair.face] = pair.track
                usedFaces.insert(pair.face)
                usedTracks.insert(pair.track)
            }
        }
        var matched = Set<Int>()
        for (i, face) in faces.enumerated() {
            let box = face.boundingBox
            if let id = assignment[i], var t = tracks[id] {
                t.observation = VNDetectedObjectObservation(boundingBox: box)
                t.smoothedBox = Self.smooth(t.smoothedBox, toward: box)
                t.bearing = bearingOf(box)
                t.missedDetections = 0
                t.viaBody = false
                t.confidence = face.confidence
                if let open = openness[i] {
                    t.mouth = Array((t.mouth + [open]).suffix(Tunables.mouthHistory))
                    t.mouthActivity = MouthActivity.smooth(t.mouthActivity, toward: MouthActivity.score(t.mouth))
                }
                t.bodyBox = body(around: box) ?? t.bodyBox
                t.bodyConfidence = confidence(of: t.bodyBox)
                t.detectsSincePrint += 1
                if let since = t.pendingSince {
                    // Still undecided: look again with this frame's appearance; after the wait, take the best.
                    let who = identify(trackId: t.id, bearing: t.bearing, screenX: Double(box.midX),
                                       facePixels: Double(box.height) * frameHeight, prints: prints(handler, face: box),
                                       now: now, mode: now - since >= Tunables.reidPendingS ? .takeBest : .wait)
                    if let who {
                        t.personId = who.personId
                        t.pendingSince = nil
                    }
                } else if t.detectsSincePrint >= Tunables.printRefreshEveryDetects, let pid = t.personId {
                    // More poses and angles of them, but only from a good view: a small, blurred or
                    // turned-away face must not overwrite what they look like.
                    let height = Double(box.height) * Double(CVPixelBufferGetHeight(pb))
                    if ReID.isGoodSample(faceConfidence: Double(face.confidence), facePixels: height, viaBody: false) {
                        remember(prints(handler, face: box), personId: pid)
                    }
                    t.detectsSincePrint = 0
                }
                tracks[t.id] = t
                matched.insert(t.id)
            } else if face.confidence < Tunables.faceMinConfidence {
                continue                                     // too weak to be a new person; it matched nobody we know
            } else {
                let b = bearingOf(box)
                let who = identify(trackId: nextId, bearing: b, screenX: Double(box.midX), facePixels: Double(box.height) * frameHeight,
                                   prints: detectionPrints[i] ?? prints(handler, face: box), now: now, mode: .wait)
                var t = Track(id: nextId, label: who?.label ?? "", colorIndex: who?.colorIndex ?? 0,
                              observation: VNDetectedObjectObservation(boundingBox: box), smoothedBox: box, bearing: b)
                t.personId = who?.personId
                t.pendingSince = who == nil ? now : nil
                t.confidence = face.confidence
                t.bodyBox = body(around: box)
                t.bodyConfidence = confidence(of: t.bodyBox)
                nextId += 1
                tracks[t.id] = t
                matched.insert(t.id)
            }
        }
        for id in tracks.keys where !matched.contains(id) {
            guard var t = tracks[id] else { continue }
            // The face wasn't found this time. If the body it belonged to is still there, the person
            // hasn't left: hold the track on where their head should be.
            if let last = t.bodyBox,
               let still = bodies.map({ ($0, iou($0, last)) }).filter({ $0.1 >= Tunables.bodyMatchIoU }).max(by: { $0.1 < $1.1 })?.0 {
                let w = still.width * Tunables.bodyHeadWidthFraction
                let head = CGRect(x: still.midX - w / 2, y: still.maxY - w * 1.25, width: w, height: w * 1.25)
                t.observation = VNDetectedObjectObservation(boundingBox: head)
                t.smoothedBox = Self.smooth(t.smoothedBox, toward: head)
                t.bearing = bearingOf(head)
                t.bodyBox = still
                t.bodyConfidence = confidence(of: still)
                t.viaBody = true
                t.missedDetections = 0
                tracks[id] = t
                continue
            }
            t.missedDetections += 1
            tracks[id] = t
            if t.missedDetections >= Tunables.missedDetectionsBeforeDrop { drop(t, now: now, why: "face not detected x\(t.missedDetections)") }
        }
    }

    /// Upper-body person detections, Vision normalized. One extra request per detect cycle (~5 Hz).
    private func detectBodies(_ handler: VNImageRequestHandler) -> [(box: CGRect, confidence: Float)] {
        let request = VNDetectHumanRectanglesRequest()
        request.upperBodyOnly = true
        guard (try? handler.perform([request])) != nil else { return [] }
        return (request.results ?? []).filter { $0.confidence > 0.5 }.map { ($0.boundingBox, $0.confidence) }
    }

    /// Appearance descriptors for the person at `face`: Apple's general image feature print of the
    /// face region and of the torso below it (clothing is a strong within-session cue). Not a
    /// face-recognition embedding: one signal among several (see `ReID`), kept in memory for the session.
    private func prints(_ handler: VNImageRequestHandler, face: CGRect) -> Prints {
        let frame = CGRect(x: 0, y: 0, width: 1, height: 1)
        let fw = face.width * Tunables.printFaceExpand, fh = face.height * Tunables.printFaceExpand
        let faceRegion = CGRect(x: face.midX - fw / 2, y: face.midY - fh / 2, width: fw, height: fh).intersection(frame)
        let tw = face.width * Tunables.printTorsoWidthFaces, th = face.height * Tunables.printTorsoHeightFaces
        let torsoRegion = CGRect(x: face.midX - tw / 2, y: face.minY - th, width: tw, height: th).intersection(frame)
        func print(_ region: CGRect) -> VNFeaturePrintObservation? {
            guard !region.isNull, region.width > 0.02, region.height > 0.02 else { return nil }
            let request = VNGenerateImageFeaturePrintRequest()
            request.regionOfInterest = region
            guard (try? handler.perform([request])) != nil else { return nil }
            return request.results?.first
        }
        return (print(faceRegion), print(torsoRegion))
    }

    private func nearest(_ print: VNFeaturePrintObservation?, in stored: [VNFeaturePrintObservation]) -> Float? {
        guard let print else { return nil }
        return stored.compactMap { other -> Float? in
            var d: Float = 0
            return (try? print.computeDistance(&d, to: other)) != nil ? d : nil
        }.min()
    }

    /// How alike a detection and a known person look, 0...1: the closest face descriptor and the
    /// closest torso descriptor in their gallery, averaged over whichever are available.
    private func similarity(_ p: Prints, to known: Appearance, facePixels: Double = Tunables.faceClosePixels) -> Double? {
        let face = nearest(p.face, in: known.face).map { ReID.similarity(printDistance: Double($0)) }
        let torso = nearest(p.torso, in: known.torso).map { ReID.similarity(printDistance: Double($0)) }
        return ReID.appearance(face: face, torso: torso, facePixels: facePixels)     // far away: mostly clothing
    }

    /// Keep a descriptor only if it shows something the gallery doesn't already have; oldest goes first.
    private func remember(_ p: Prints, personId: Int) {
        var a = gallery[personId] ?? Appearance()
        func add(_ print: VNFeaturePrintObservation?, to list: inout [VNFeaturePrintObservation]) {
            guard let print else { return }
            if let d = nearest(print, in: list), d < Tunables.printNoveltyDistance { return }
            list.append(print)
            if list.count > Tunables.printGallerySize { list.remove(at: 1) }    // keep the very first sighting
        }
        add(p.face, to: &a.face)
        add(p.torso, to: &a.torso)
        gallery[personId] = a
        people?.noteDescriptors(a.face.count + a.torso.count, personId: personId)
    }

    /// Who is this new track? Someone we already know coming back, or a new person.
    private func identify(trackId: Int, bearing: Double, screenX: Double, facePixels: Double, prints p: Prints, now: Double,
                          mode: SessionPeople.AmbiguityMode) -> (personId: Int?, label: String, colorIndex: Int)? {
        guard let people else { return (nil, "Person \(trackId)", (trackId - 1) % Palette.people.count) }
        guard let person = people.attach(trackId: trackId, bearing: bearing, now: now, screenX: screenX, mode: mode, similarity: { candidate in
            self.gallery[candidate.id].flatMap { self.similarity(p, to: $0, facePixels: facePixels) }
        }) else { return nil }
        // First time we have their appearance: are they someone explicitly enrolled?
        if gallery[person.id] == nil, person.knownPersonID == nil, let face = p.face, let known = knownMatch?(face) {
            people.apply(known, to: person.id)
        }
        remember(p, personId: person.id)
        let current = people.person(id: person.id) ?? person
        return (current.id, current.displayName, current.colorIndex)
    }

    /// The archived appearance of a person, for explicit enrollment only.
    func appearance(ofPerson id: Int, completion: @escaping (Data?) -> Void) {
        queue.async {
            let data = self.gallery[id]?.face.first.flatMap {
                try? NSKeyedArchiver.archivedData(withRootObject: $0, requiringSecureCoding: true)
            }
            DispatchQueue.main.async { completion(data) }
        }
    }

    /// New detection index -> existing track id. Globally greedy, not "each detection picks its own
    /// best track independently": the old per-detection approach picked the single highest-IoU track
    /// for a box, then separately fell back to a center-distance check *against that same track* if
    /// the IoU was low -- but when two tracks both have ~zero IoU with a new box (ordinary noise on
    /// real, handheld/chest-mounted footage: a head turn, a dedup'd box jumping slightly, far more
    /// common than in the Simulator's smooth synthetic motion), `.max` on all-zero scores effectively
    /// picks by dictionary iteration order, not by who's actually closest -- silently assigning a
    /// detection to the *wrong* person's track and swapping their "Person N" labels. Two global
    /// greedy passes instead: lock in every pair that clears the IoU bar, best-IoU-first; then, among
    /// what's left, lock in every pair that clears the distance bar, closest-first.
    private func assignDetections(_ faces: [CGRect]) -> [Int: Int] {
        var result: [Int: Int] = [:]
        var usedFaces = Set<Int>()
        var usedTracks = Set<Int>()

        func greedily(_ pairs: [(face: Int, track: Int, score: Double)], bestFirst: (Double, Double) -> Bool) {
            for p in pairs.sorted(by: { bestFirst($0.score, $1.score) }) {
                guard !usedFaces.contains(p.face), !usedTracks.contains(p.track) else { continue }
                result[p.face] = p.track
                usedFaces.insert(p.face)
                usedTracks.insert(p.track)
            }
        }

        var iouPairs: [(face: Int, track: Int, score: Double)] = []
        for (i, box) in faces.enumerated() {
            for t in tracks.values {
                let score = iou(t.observation.boundingBox, box)
                if score >= Tunables.faceMatchIoU { iouPairs.append((i, t.id, Double(score))) }
            }
        }
        greedily(iouPairs, bestFirst: >)

        var distPairs: [(face: Int, track: Int, score: Double)] = []
        for (i, box) in faces.enumerated() where !usedFaces.contains(i) {
            for t in tracks.values where !usedTracks.contains(t.id) {
                let d = centerDistance(t.observation.boundingBox, box)
                if d < max(box.width, box.height) * 0.6 { distPairs.append((i, t.id, Double(d))) }
            }
        }
        greedily(distPairs, bestFirst: <)   // closest (smallest distance) first

        return result
    }

    private func track(_ pb: CVPixelBuffer, bearingOf: (CGRect) -> Double, now: Double) {
        // Body-held tracks have no face to follow between detections; they update on detect cycles.
        let followed = tracks.values.filter { !$0.viaBody }
        guard !followed.isEmpty else { return }
        let requests = followed.map { t -> VNTrackObjectRequest in
            let r = VNTrackObjectRequest(detectedObjectObservation: t.observation)
            // .fast trades robustness for speed -- it's the likely source of the identity confusion
            // on real (noisier) footage, not Vision's face *detection* (which only ever runs in
            // detect(), every detectEveryFrames, and isn't this request at all). .accurate costs more
            // CPU per frame but this only runs on however many faces are currently tracked (a handful),
            // not the whole frame, so the cost is bounded.
            r.trackingLevel = .accurate
            return r
        }
        try? sequence.perform(requests, on: pb, orientation: .up)
        for (t, r) in zip(followed, requests) {
            guard let obs = r.results?.first as? VNDetectedObjectObservation, obs.confidence >= Tunables.trackMinConfidence else {
                let c = (r.results?.first as? VNDetectedObjectObservation)?.confidence
                drop(t, now: now, why: c.map { "tracking confidence \(String(format: "%.2f", $0))" } ?? "tracker returned nothing")
                continue
            }
            var t = t
            t.confidence = obs.confidence
            t.observation = obs
            t.smoothedBox = Self.smooth(t.smoothedBox, toward: obs.boundingBox)
            t.bearing = bearingOf(obs.boundingBox)
            tracks[t.id] = t
        }
    }

    /// The Vision track is over; the SessionPerson is only detached and keeps their identity.
    private func drop(_ t: Track, now: Double, why: String = "merged into an overlapping track") {
        tracks[t.id] = nil
        people?.detach(trackId: t.id, now: now)
        diagnostics.lastDrop = "\(t.label): \(why)"
    }

    private func iou(_ a: CGRect, _ b: CGRect) -> Double {
        let i = a.intersection(b)
        guard !i.isNull else { return 0 }
        let inter = i.width * i.height
        return Double(inter / (a.width * a.height + b.width * b.height - inter))
    }

    private func centerDistance(_ a: CGRect, _ b: CGRect) -> CGFloat {
        hypot(a.midX - b.midX, a.midY - b.midY)
    }

    /// Non-max suppression: Vision can return more than one overlapping box for a single real face
    /// (partial occlusion, an angled profile, motion blur), each individually clearing the confidence
    /// filter. Left alone, two overlapping detections in the same frame each match (or spawn) their
    /// own track -- the same person becomes two simultaneous "Person N"s. Keeps the highest-confidence
    /// box in each cluster of mutually-overlapping detections.
    private func dedup(_ faces: [VNFaceObservation]) -> [VNFaceObservation] {
        var kept: [VNFaceObservation] = []
        for f in faces.sorted(by: { $0.confidence > $1.confidence }) {
            if kept.contains(where: { iou($0.boundingBox, f.boundingBox) > Tunables.faceDedupIoU }) { continue }
            kept.append(f)
        }
        return kept
    }

    /// Defensive cleanup for tracks that already duplicated before `dedup` ran (e.g. two tracks that
    /// drifted onto the same face during a previous bad frame): if two *tracks* end up heavily
    /// overlapping, keep the older (lower id, i.e. established first) and drop the other.
    private func mergeOverlappingTracks(now: Double) {
        let ordered = tracks.values.sorted { $0.id < $1.id }
        var dropped = Set<Int>()
        for i in 0..<ordered.count {
            guard !dropped.contains(ordered[i].id) else { continue }
            for j in (i + 1)..<ordered.count {
                guard !dropped.contains(ordered[j].id) else { continue }
                if iou(ordered[i].observation.boundingBox, ordered[j].observation.boundingBox) > Tunables.faceDedupIoU {
                    drop(ordered[j], now: now)
                    dropped.insert(ordered[j].id)
                }
            }
        }
    }
}

/// Simulator / no-camera stand-in: Person 1 front-right, Person 2 front-left, Person 3 (Speaker B,
/// README §4/§19) directly behind (README §4 positions), placed by bearing so fake_events.py lands
/// captions on them. Person 3 only appears once the wearer turns far enough toward them, same as a
/// real person stepping into frame -- drag Settings > Developer > Simulated heading to rehearse the
/// turn-around without a phone (Task 3, prompts/IOS_AGENT.md).
enum SimulatedFaces {
    static func faces(heading: Double, hfovDeg: Double, now: Double) -> [TrackedFace] {
        let people: [(String, Double)] = [("Person 1", 35), ("Person 2", -35), ("Person 3", 180)]
        return people.enumerated().compactMap { i, p in
            let sway = sin(now * 0.7 + Double(i)) * 1.5
            guard let nx = Geo.normalizedX(bearing: p.1 + sway, hfovDeg: hfovDeg, heading: heading) else { return nil }
            let w = 0.09, h = 0.16
            let box = CGRect(x: nx - w / 2, y: 0.52, width: w, height: h)      // bottom-left origin
            return TrackedFace(id: 100 + i, label: p.0, colorIndex: i, box: box, bearing: p.1 + sway)
        }
    }
}
