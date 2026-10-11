import AVFoundation
import Combine
import QuartzCore
import UIKit
import Vision

/// What a caption box shows (one per utterance; the overlay shows the newest per speaker).
struct CaptionState: Identifiable, Equatable {
    let uttId: String
    var speaker: Speaker
    var bearing: Double
    var text = ""
    var pending = ""
    var final = false
    var tone: Tone?
    var toneConfidence = 0.0
    var loudness = 0.5
    var started: Double
    var updated: Double
    var sourceId: Int? = nil             // the laptop's source id for this utterance
    /// The direction the *array* heard it from, exactly as sent. `bearing` is that direction as seen
    /// from where the wearer stands (the same thing only when the wearer is at the array).
    var micBearing: Double? = nil
    /// The SessionPerson this utterance's audio source is locked to (SpeakerLocks). While set, the
    /// caption follows that person: on their face when they're in view, as their notification when not.
    var speakerPersonId: Int? = nil
    /// Out of view: the SessionPerson this voice belongs to, when that's known (their audio source,
    /// or the one credible person at that bearing). nil = unattributed.
    var ownerPersonId: Int? = nil

    var id: String { uttId }
    var typing: Bool { text.isEmpty && pending.isEmpty && !final }
}

/// An alert in the top-right panel.
struct AlertState: Identifiable, Equatable {
    let alertId: String
    var kind: AlertKind
    var label: String
    var emoji: String
    var priority: Int
    var bearing: Double?
    var active: Bool
    var count: Int
    var updated: Double
    var lastBuzz: Double
    var refUttId: String? = nil      // the utterance this alert is about (name called), when the laptop says
    var refEventId: String? = nil    // the sound_event this alert is about (doorbell...), when the laptop says
    var id: String { alertId }
}

/// A non-alert sound (extras: music, TV, dog...) shown as a small tag at its direction.
struct SoundTag: Identifiable, Equatable {
    let eventId: String
    var label: SoundLabel
    var category: SoundCategory
    var emoji: String
    var text: String
    var bearing: Double?
    var count: Int
    var updated: Double
    var confidence = 1.0
    var sourceId: Int? = nil         // the `sources` track it came from, when the laptop says (`source_id`)
    var votes: [SoundLabel: Double] = [:]    // accumulated classifier evidence per label, for a stable name
    var micBearing: Double? = nil    // the direction the array reported, before conversion to the wearer's frame
    var heardAs: SoundLabel? = nil   // the laptop's own label for the latest window (diagnostics)
    var model: String? = nil
    var id: String { eventId }
}

/// Thread-safe box for values the camera/Vision queues read (heading, field of view).
final class Locked<T> {
    private var value: T
    private let lock = NSLock()
    init(_ v: T) { value = v }
    func get() -> T { lock.lock(); defer { lock.unlock() }; return value }
    func set(_ v: T) { lock.lock(); value = v; lock.unlock() }
}

/// The phone app's state and wiring (SPEC §6 "iPhone": camera, faces, heading, attribution, AR, belt).
/// Everything @Published is touched on the main thread only.
final class AppModel: ObservableObject {
    // Sensors
    @Published private(set) var faces: [TrackedFace] = []
    @Published private(set) var headingRaw = 0.0
    @Published private(set) var headingQuality = "settling"

    // From the laptop
    @Published private(set) var captions: [String: CaptionState] = [:]
    @Published private(set) var alerts: [AlertState] = []
    @Published private(set) var sounds: [String: SoundTag] = [:]
    @Published private(set) var sources: [Source] = []
    @Published private(set) var laptopEngine: Engine?
    @Published private(set) var laptopStatus: Heartbeat.Status?
    @Published private(set) var laptopDetail: String?
    @Published private(set) var lastHeard: Double = 0        // last message from the laptop (phone clock)
    @Published private(set) var lagMs: Double?               // laptop observation -> phone, when the clock is synced
    @Published private(set) var stageMs: [String: Double] = [:]

    // Status
    @Published private(set) var relayStatus: RelayClient.Status = .disconnected
    @Published private(set) var relayRoute: RelayRoute = .primary
    @Published private(set) var thermal = ProcessInfo.processInfo.thermalState
    @Published private(set) var now = CACurrentMediaTime()

    // Spatial V2 presentation state (derived on the phone, never sent anywhere)
    @Published private(set) var scene = SpatialScene.empty
    @Published private(set) var sessionPeople: [SessionPerson] = []
    @Published private(set) var trackerDiagnostics = TrackerDiagnostics()
    @Published private(set) var laptopDoaSource: Heartbeat.DoaSource?
    @Published private(set) var objects: [SceneObject] = []
    @Published private(set) var objectScanMs = 0.0
    /// The array's sources converted to directions from the wearer (what presentation and fusion use).
    @Published private(set) var listenerSources: [Source] = []
    /// Measured agreement between where sounds should come from and where the array says they do.
    @Published private(set) var calibration = CalibrationCheck()
    @Published var calibrationRunning = false
    /// The last few finished utterances with everything known about them (Developer > Calibration).
    @Published private(set) var transcripts: [TranscriptRecord] = []
    /// Which person each active speech source belongs to. Per source, so two people can talk at once.
    let speakerLocks = SpeakerLocks()
    /// The bundled detection model's name, or nil when objects come from the region classifier alone.
    var objectDetectorName: String? { scanner.detector == nil ? nil : scanner.detectorName }
    @Published private(set) var scenario: SpatialScenario?     // Settings > Developer: a scripted scene is playing

    let settings: SettingsStore
    let camera = CameraController()
    let belt = BeltController()
    let relay = RelayClient()
    private let tracker = FaceTracker()
    private let heading = HeadingProvider()
    private let attributor = Attributor()
    private let beltPolicy = BeltPolicy()
    private let presentation = PresentationCore()
    /// The session's identities, shared with the tracker (which resolves them on the Vision queue).
    let people = SessionPeople()
    let known = KnownPeopleStore()
    private let knownProfiles = Locked<[KnownPerson]>([])
    /// What the room contains, silent or not (doors, chairs, phones...), found by `ObjectScanner`.
    let sceneObjects = SceneObjects()
    private let scanner = ObjectScanner()
    private var scenarioHarness: ScenarioHarness?
    private var scenarioStart = 0.0
    private var scenarioHeading: Double?
    private var scenarioFreeze: Double?
    private let motionQueue = OperationQueue()
    private let headingBox = Locked(0.0)
    private let hfovBox = Locked(Tunables.defaultHFOVUltraWide)
    private var tick: Timer?
    private var lastScene = 0.0
    private var buzzPending: Set<String> = []          // speech_start too unsure to buzz: wait for the utterance
    /// The laptop's attention judgments (`perception_enrichment`), by `EnrichmentState.key`. Presentation only.
    private var enrichments: [String: EnrichmentState] = [:]
    private var enrichmentBuzzed: Set<String> = []     // judged important/urgent and already buzzed for
    private var bag = Set<AnyCancellable>()
    weak var previewLayer: AVCaptureVideoPreviewLayer?

    init(settings: SettingsStore) {
        self.settings = settings
    }

    // MARK: derived

    /// World bearing the wearer faces. Belt-only mode locks it to 0 (judge faces the array's front).
    /// Simulated faces (no CoreMotion in the Simulator): driven by Settings > Developer's heading slider,
    /// so the turn-around ("someone behind -> turn -> caption slides onto their face") can be rehearsed
    /// without a phone.
    var headingDeg: Double {
        if let scenarioHeading { return scenarioHeading }
        if settings.value.beltOnlyMode { return 0 }
        if settings.value.simulatedFaces { return Geo.wrap(settings.value.simulatedHeadingDeg) }
        return Geo.wrap(headingRaw - settings.value.headingOffset)
    }

    /// Auto: the FOV across whichever sensor axis is horizontal in the *current* upright buffer, not
    /// always the camera's native landscape FOV -- correct for any mount (landscape, portrait, upside
    /// down), matching `HeadingProvider`'s own "any mount" guarantee on the heading side. A manual
    /// Settings override always wins outright (it's a value measured by hand for that specific setup).
    var hfovDeg: Double {
        if settings.value.hfovOverride > 0 { return settings.value.hfovOverride }
        return Geo.effectiveHFOVDeg(hfovDeg: camera.hfovDeg, vfovDeg: camera.vfovDeg, captureAngle: camera.captureAngle)
    }

    var faceRefs: [FaceRef] { faces.map { FaceRef(id: $0.id, label: $0.label, bearing: $0.bearing) } }

    // MARK: room geometry (array frame <-> wearer frame)

    /// Where the wearer stands relative to the array, and how far away sources are assumed to be.
    var room: RoomGeometry {
        RoomGeometry(wearerDistanceM: settings.value.wearerDistanceM, wearerBearingDeg: settings.value.wearerBearingDeg,
                     sourceRadiusM: settings.value.sourceRadiusM)
    }

    /// The camera's vertical field of view for the way the phone is mounted right now.
    private var verticalFOVDeg: Double {
        Frames.swapsDimensions(captureAngle: camera.captureAngle) ? camera.hfovDeg : camera.vfovDeg
    }

    func distance(to face: TrackedFace) -> Double {
        RoomGeometry.faceDistance(boxHeight: Double(face.box.height), vfovDeg: verticalFOVDeg)
    }

    /// Each visible face as the *array* would hear it: the bearing a voice from that face would have
    /// in the laptop's messages. Attribution compares in this frame, so it stays right when the
    /// wearer is not standing at the array.
    var micFaceRefs: [FaceRef] {
        let room = room
        return faces.map { FaceRef(id: $0.id, label: $0.label, bearing: room.micBearing(ofEntityAt: $0.bearing, distance: distance(to: $0))) }
    }

    /// A bearing from the laptop (measured at the array) as a direction from the wearer.
    func listenerBearing(_ micBearing: Double) -> Double { room.listenerBearing(micBearing: micBearing) }

    /// How unsure the wearer-frame direction of a sound is (unknown source distance, measured
    /// calibration error). Fusion with objects backs off as this grows.
    func spatialUncertaintyDeg(micBearing: Double?) -> Double {
        let parallax = micBearing.map { room.listenerUncertainty(micBearing: $0) } ?? 0
        let measured = calibration.quality == .unknown ? 0 : calibration.meanAbsError
        return max(parallax, measured)
    }

    func projector(size: CGSize) -> Projector {
        Projector(layer: usesBackdrop ? nil : previewLayer, captureAngle: camera.captureAngle,
                  hfovDeg: hfovDeg, heading: headingDeg, size: size)
    }

    /// Nothing heard from the laptop for a while (the relay can be up with no laptop behind it).
    var laptopQuiet: Bool { CACurrentMediaTime() - lastHeard > 5 }

    /// Something the wearer should know about: HERE isn't hearing the room, or the phone is hot.
    var statusNeedsAttention: Bool {
        if scenario != nil { return false }
        return relayStatus != .connected || laptopQuiet || laptopStatus == .degraded || laptopStatus == .error
            || thermal == .serious || thermal == .critical
    }

    /// No camera picture to draw over: simulated faces, or a debug scenario supplying its own people.
    var usesBackdrop: Bool { settings.value.simulatedFaces || scenario != nil }

    var visualStyle: SpatialVisualStyle { settings.value.visualStyle }

    /// A scenario can ask for the Reduce Motion look without changing the system setting.
    var scenarioReduceMotion: Bool { scenario?.reduceMotion ?? false }

    /// The captions to draw: the newest per speaker, without expired ones.
    var visibleCaptions: [CaptionState] { Self.newestPerSpeaker(captions.values) }

    static func newestPerSpeaker<C: Collection>(_ captions: C) -> [CaptionState] where C.Element == CaptionState {
        var newest: [String: CaptionState] = [:]
        for c in captions {
            let key: String
            switch c.speaker {
            case let .person(id, _): key = "p\(id)"
            case .offCamera: key = "o\(Int((Geo.wrap(c.bearing) + 180) / 20))"   // one per ~20 deg sector
            }
            if let existing = newest[key], existing.updated >= c.updated { continue }
            newest[key] = c
        }
        return newest.values.sorted { ($0.started, $0.uttId) < ($1.started, $1.uttId) }
    }

    /// Speakers currently talking (for rings): person id -> loudness.
    var activeSpeakers: [Int: Double] {
        var out: [Int: Double] = [:]
        for c in captions.values {
            guard case let .person(id, _) = c.speaker else { continue }
            if !c.final || now - c.updated < Tunables.ringHoldS { out[id] = max(out[id] ?? 0, c.loudness) }
        }
        return out
    }

    var visibleAlerts: [AlertState] {
        alerts.filter { $0.active || now - $0.updated < Tunables.alertFadeS }
            .sorted { ($0.active ? 1 : 0, $0.priority, $0.updated) > ($1.active ? 1 : 0, $1.priority, $1.updated) }
    }

    var visibleSounds: [SoundTag] {
        guard !settings.value.focusMode else { return [] }        // focus mode: speech + alerts only
        return sounds.values.filter { now - $0.updated < 4 }.sorted { $0.updated > $1.updated }
    }

    // MARK: start

    func start() {
        UIApplication.shared.isIdleTimerDisabled = true

        heading.signFlip = settings.value.headingSignFlip
        heading.onHeading = { [weak self] sample in
            DispatchQueue.main.async {
                guard let self else { return }
                self.headingRaw = sample.headingRawDeg
                self.headingQuality = sample.quality
                self.headingBox.set(self.headingDeg)
            }
        }
        motionQueue.maxConcurrentOperationCount = 1
        heading.start(queue: motionQueue)

        tracker.people = people
        tracker.onFaces = { [weak self] faces in
            DispatchQueue.main.async { self?.setFaces(faces) }
        }
        tracker.onDiagnostics = { [weak self] d in
            DispatchQueue.main.async { if self?.trackerDiagnostics != d { self?.trackerDiagnostics = d } }
        }
        known.$people.sink { [weak self] list in self?.knownProfiles.set(list) }.store(in: &bag)
        applyRecognitionSetting()
        camera.onFrame = { [weak self] pb, _ in
            guard let self else { return }
            let t = CACurrentMediaTime()
            self.tracker.submit(pb, heading: self.headingBox.get(), hfovDeg: self.hfovBox.get(), now: t)
            self.scanner.submit(pb, heading: self.headingBox.get(), hfovDeg: self.hfovBox.get(), now: t)
        }
        scanner.enabled = settings.value.objectDetection
        scanner.onObservations = { [weak self] observations, t in
            guard let self else { return }
            // Heading and field of view as they are now: close enough over one scan (about a second).
            let heading = self.headingBox.get(), half = self.hfovBox.get() / 2
            self.sceneObjects.observe(observations, now: t) { abs(Geo.relative($0, heading: heading)) < half }
        }
        scanner.onTiming = { [weak self] ms, _ in DispatchQueue.main.async { self?.objectScanMs = ms } }
        camera.$hfovDeg.sink { [weak self] _ in
            DispatchQueue.main.async { if let self { self.hfovBox.set(self.hfovDeg) } }
        }.store(in: &bag)
        camera.position = settings.value.debugFrontCamera ? .front : .back
        if !settings.value.simulatedFaces { camera.start() }

        relay.onStatus = { [weak self] s in self?.relayStatus = s }
        relay.onRoute = { [weak self] r in self?.relayRoute = r }
        relay.onMessage = { [weak self] m in self?.handle(m) }
        relay.onConnected = { [weak self] in self?.sendConfig() }
        relay.heartbeat = { [weak self] in
            var hb = Heartbeat(t: Date().timeIntervalSince1970, status: .ok)
            hb.thermal = self?.thermalState
            if self?.camera.error != nil { hb.status = .degraded; hb.detail = self?.camera.error }
            return hb
        }
        connectRelay()

        belt.configure(serviceUUID: settings.value.beltServiceUUID, commandUUID: settings.value.beltCommandUUID)
        if settings.value.beltAutoConnect { belt.connect() }

        NotificationCenter.default.publisher(for: ProcessInfo.thermalStateDidChangeNotification)
            .receive(on: DispatchQueue.main)
            .sink { [weak self] _ in self?.thermalChanged() }
            .store(in: &bag)
        thermalChanged()

        tick = Timer.scheduledTimer(withTimeInterval: 1.0 / 15, repeats: true) { [weak self] _ in self?.onTick() }
    }

    func connectRelay() {
        let s = settings.value
        relay.connect(base: s.relayURL, fallback: s.relayFallbackURL, session: s.session, token: s.relayToken)
    }

    /// After the Settings sheet closes: apply transport, belt, heading and camera changes; share config.
    func applySettings(previous: Settings) {
        let s = settings.value
        if s.relayURL != previous.relayURL || s.relayFallbackURL != previous.relayFallbackURL
            || s.relayToken != previous.relayToken || s.session != previous.session {
            connectRelay()
        }
        if s.beltServiceUUID != previous.beltServiceUUID || s.beltCommandUUID != previous.beltCommandUUID {
            belt.configure(serviceUUID: s.beltServiceUUID, commandUUID: s.beltCommandUUID)
        }
        heading.signFlip = s.headingSignFlip
        hfovBox.set(hfovDeg)
        headingBox.set(headingDeg)
        if s.debugThermalOverride != previous.debugThermalOverride { thermalChanged() }
        applyRecognitionSetting()
        camera.position = s.debugFrontCamera ? .front : .back
        if s.simulatedFaces != previous.simulatedFaces {
            if s.simulatedFaces { camera.stop(); faces = [] } else { camera.start() }
        } else if s.debugFrontCamera != previous.debugFrontCamera, !s.simulatedFaces {
            camera.stop()
            camera.start()
        }
        sendConfig()
    }

    func sendConfig() {
        relay.send(settings.value.configMessage(now: Date().timeIntervalSince1970))
    }

    /// SPEC §8: wearer at the tape mark facing the array's front, presenter taps Calibrate.
    func calibrate() {
        settings.value.headingOffset = headingRaw          // heading = wrap(raw - offset) -> 0 facing the array
        headingBox.set(headingDeg)
        tracker.reset()
        people.reset()                                     // a new session: identities and pins start over
        speakerLocks.reset()
        sceneObjects.reset()                               // world bearings are measured from the new zero
        sessionPeople = []
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    func testBelt() {
        belt.send(beltPolicy.test(strength: settings.value.beltStrength))
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
    }

    func testBelt(motor: Int) {
        belt.send(BeltCommand(pattern: .speech, motor: motor, strength: settings.value.beltStrength, priority: 5))
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    // MARK: debug injection (Settings > Developer: no laptop or relay needed)
    //
    // Each routes through the same handler real laptop messages hit (onUtterance/onAlert), so attribution,
    // belt buzzing and alert dedup/fade all behave exactly as they would live -- nothing is reimplemented.

    /// Attaches to whichever face is currently tracked (so it lands on you, not off-camera), same as a
    /// real utterance would once its bearing matches a visible face.
    func debugInjectTestCaption() {
        let bearing = faces.first?.bearing ?? headingDeg
        let t = Date().timeIntervalSince1970
        onUtterance(Utterance(t: t, uttId: "debug-caption", tStart: t - 1, tEnd: t,
                              text: "Hey, my name's Kori, how are you?", pending: "", final: true,
                              bearingDeg: bearing, tone: .questioning, toneConfidence: 0.9, loudness: 0.6))
    }

    /// Lands at a fixed off-to-the-side bearing (not on a tracked face) so the ambient-sound "shading"
    /// treatment (OverlayView's `SoundGlow`/edge glow) is visible regardless of whether a face is
    /// currently tracked -- unlike the caption/alert injectors, a real ambient sound isn't attached
    /// to a person at all.
    func debugInjectTestSound() {
        let t = Date().timeIntervalSince1970
        let bearing = Geo.wrap(headingDeg + 60)
        let e = SoundEvent(t: t, eventId: "debug-sound", label: .music, category: .media,
                           emoji: "🎵", text: "Music playing", confidence: 0.85, bearingDeg: bearing,
                           tStart: t, tEnd: t, count: 1)
        sounds[e.eventId] = SoundTag(eventId: e.eventId, label: e.label, category: e.category, emoji: e.emoji,
                                     text: e.text, bearing: e.bearingDeg, count: e.count, updated: now,
                                     confidence: e.confidence)
    }

    // MARK: calibration check + transcription record

    /// One finished utterance, for judging transcription and direction after the fact.
    struct TranscriptRecord: Identifiable, Equatable {
        let id: String
        var text: String
        var durationS: Double
        var loudness: Double
        var micBearing: Double           // as the array reported it
        var listenerBearing: Double      // as a direction from the wearer
        var spreadDeg: Double
        var overlap: Bool
        var sourceId: Int
        var engine: String
        var lagMs: Double?               // end of speech on the laptop -> final text on the phone
        var stages: String
        var assigned: String             // the SessionPerson it landed on, or why it didn't
    }

    private func record(_ u: Utterance, speaker: Speaker) {
        let why = attributor.lastDecision
        let assigned = speaker.label ?? ("off camera" + (why.map { " (\($0.reason))" } ?? ""))
        let stages = (u.stageMs ?? [:]).map { "\($0.key) \(Int($0.value))" }.sorted().joined(separator: ", ")
        let lag = relay.clock.sampleCount > 0 ? (relay.clock.laptopTime(forPhoneEpoch: PhoneClock.nowEpoch) - u.tEnd) * 1000 : nil
        transcripts = Array((transcripts + [TranscriptRecord(
            id: u.uttId, text: u.text, durationS: u.tEnd - u.tStart, loudness: u.loudness, micBearing: u.bearingDeg,
            listenerBearing: listenerBearing(u.bearingDeg), spreadDeg: u.bearingSpreadDeg, overlap: u.overlap,
            sourceId: u.sourceId, engine: u.engine.rawValue, lagMs: lag, stages: stages, assigned: assigned)])
            .suffix(Tunables.transcriptLogSize))

        // Calibration check: someone the camera can see just spoke, and they are the only person in
        // view, so we know where the array *should* have heard that from. Compare.
        guard calibrationRunning, faces.count == 1, let face = faces.first else { return }
        let expected = room.micBearing(ofEntityAt: face.bearing, distance: distance(to: face))
        calibration.add(expected: expected, measured: u.bearingDeg, note: face.label)
    }

    /// A helper is making a sound from a marked spot at a known angle from the array's front
    /// (front 0, right 90, behind 180, left -90): take the array's loudest current source as its answer.
    /// This checks the array alone, independent of the phone's heading and camera.
    @discardableResult
    func calibrationSample(expectedMicBearing: Double) -> Bool {
        guard let loudest = sources.max(by: { $0.energy < $1.energy }), loudest.energy > 0.1 else { return false }
        calibration.add(expected: expectedMicBearing, measured: loudest.bearingDeg, note: "known spot")
        return true
    }

    func resetCalibrationCheck() { calibration.reset() }

    /// A constant offset was measured: fold it into the heading zero instead of asking for a re-tap.
    func applyCalibrationCorrection() {
        settings.value.headingOffset = Geo.wrap(settings.value.headingOffset - calibration.suggestedHeadingCorrection)
        headingBox.set(headingDeg)
        calibration.reset()
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    // MARK: session people: pins, audio continuity, enrollment

    /// Why the latest fresh attribution was accepted or rejected (developer overlay).
    var attributionDecision: Attributor.Decision? { attributor.lastDecision }

    /// The laptop's source ids only identify anything with ODAS; the built-in DoA calls every source 0
    /// (SPEC §12.5). With meaningless ids, continuity falls back to time and bearing, with confidence.
    var sourceIdsMeaningful: Bool { laptopDoaSource == .odas }

    /// Faces whose person is already known to own this audio source: attribution trusts them further.
    private func establishedFaces(sourceId: Int) -> Set<Int> {
        guard sourceIdsMeaningful else { return [] }
        return Set(people.all.filter { $0.audioSourceID == sourceId }.compactMap(\.currentTrackID))
    }

    /// Records audio continuity for this utterance and says whose it is when the speaker is out of view.
    private func owner(of speaker: Speaker, bearing: Double, sourceId: Int) -> Int? {
        if case let .person(trackId, _) = speaker {
            if sourceIdsMeaningful { people.associate(trackId: trackId, sourceId: sourceId, now: now) }
            return nil
        }
        return people.heard(bearing: bearing, sourceId: sourceIdsMeaningful ? sourceId : nil, now: now)?.id
    }

    /// Pin for now: keep this SessionPerson important for the rest of the session. The pin is on the
    /// person, not on a Vision track, so it survives them leaving view and coming back.
    func togglePin(personId: Int) {
        if let scenarioHarness {
            scenarioHarness.people.togglePin(personId: personId)
            sessionPeople = scenarioHarness.people.all
        } else {
            people.togglePin(personId: personId)
            sessionPeople = people.all
        }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    }

    func clearPins() {
        for p in people.all where p.isSessionPinned { people.setPinned(false, personId: p.id) }
        sessionPeople = people.all
    }

    /// Add to People: an explicit, persistent enrollment of someone in view. Stores a name, a color,
    /// a priority and (if one has been captured) their appearance descriptor, on this phone only.
    func enroll(personId: Int) {
        guard scenario == nil, let person = people.person(id: personId), person.knownPersonID == nil else { return }
        var entry = KnownPerson(chosenName: person.displayName, preferredColor: person.colorIndex)
        tracker.appearance(ofPerson: personId) { [weak self] data in
            guard let self else { return }
            if let data { entry.recognitionProfile = [data] }
            self.known.people.append(entry)
            self.people.apply(entry, to: personId)
            self.sessionPeople = self.people.all
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        }
    }

    /// Edits made in Settings > People reach anyone in this session who is that person.
    func knownPeopleChanged() {
        for p in people.all {
            guard let kid = p.knownPersonID, let k = known.people.first(where: { $0.id == kid }) else { continue }
            people.apply(k, to: p.id)
        }
        sessionPeople = people.all
    }

    /// Experimental and off by default: check each new person against the enrolled people's stored
    /// appearance. Vision's general feature print is not a face-recognition model, so this needs a
    /// strong match with a clear lead before it puts a name on anyone (KnownPeople.match).
    private func applyRecognitionSetting() {
        tracker.bodyFallback = settings.value.bodyFallback
        tracker.mouthTracking = settings.value.mouthTracking
        scanner.enabled = settings.value.objectDetection
        guard settings.value.knownRecognition else {
            tracker.knownMatch = nil
            return
        }
        let profiles = knownProfiles
        tracker.knownMatch = { print in
            let list = profiles.get()
            let sims = list.compactMap { k -> (id: UUID, similarity: Double)? in
                let best = k.recognitionProfile.compactMap { data -> Double? in
                    guard let stored = try? NSKeyedUnarchiver.unarchivedObject(ofClass: VNFeaturePrintObservation.self, from: data)
                    else { return nil }
                    var d: Float = 0
                    guard (try? print.computeDistance(&d, to: stored)) != nil else { return nil }
                    return ReID.similarity(printDistance: Double(d))
                }.max()
                return best.map { (k.id, $0) }
            }
            return KnownPeople.match(sims).flatMap { id in list.first { $0.id == id } }
        }
    }

    // MARK: debug scenarios (Settings > Developer: scripted scenes, no laptop / microphone / belt)

    /// Plays a scripted scene on a loop in place of the camera and the relay. The scenario supplies
    /// people, speech, sounds, alerts and the wearer's turning; the same attribution and presentation
    /// code as the live app turns that into what's drawn. Pass nil to stop. `freezeAt` holds the scene
    /// at one moment instead of looping (launch with `-scenario 13 -scenarioFreeze 3` for a still).
    func debugRun(_ s: SpatialScenario?, freezeAt: Double? = nil) {
        scenarioFreeze = freezeAt
        captions = [:]
        alerts = []
        sounds = [:]
        sources = []
        enrichments = [:]
        enrichmentBuzzed = []
        scene = .empty
        scenario = s
        scenarioHeading = nil
        scenarioHarness = s.map { ScenarioHarness($0, hfovDeg: hfovDeg) }
        scenarioStart = CACurrentMediaTime()
        now = CACurrentMediaTime()
        sessionPeople = s == nil ? people.all : []
        if s == nil, !settings.value.simulatedFaces { faces = [] }
    }

    private func playScenario(_ harness: ScenarioHarness) {
        var t = CACurrentMediaTime() - scenarioStart
        var harness = harness
        if let scenarioFreeze {
            t = min(t * 8, scenarioFreeze)                  // run up to the moment quickly, then hold it
        } else if t > harness.scenario.duration {           // loop
            harness = ScenarioHarness(harness.scenario, hfovDeg: hfovDeg)
            scenarioHarness = harness
            scenarioStart = CACurrentMediaTime()
            t = 0
        }
        let snap = harness.advance(to: t)
        now = snap.t                                         // scenario time, so fades and holds line up
        scenarioHeading = snap.heading
        faces = snap.faces
        captions = snap.captions
        alerts = snap.alerts
        sounds = snap.sounds
        sources = snap.sources
        scene = snap.scene
        sessionPeople = snap.people
        objects = snap.objects
    }

    func debugInjectTestAlert(kind: AlertKind) {
        let id = "debug-\(kind.rawValue)"
        let label = kind == .doorbell ? "Doorbell" : "Name called"
        let emoji = kind == .doorbell ? "🔔" : "🗣️"
        let bearing = faces.first?.bearing
        onAlert(Alert(t: Date().timeIntervalSince1970, alertId: id, kind: kind, label: label, emoji: emoji,
                      priority: 2, bearingDeg: bearing, active: true, confidence: 0.9))
        DispatchQueue.main.asyncAfter(deadline: .now() + 6) { [weak self] in
            self?.onAlert(Alert(t: Date().timeIntervalSince1970, alertId: id, kind: kind, label: label, emoji: emoji,
                                priority: 2, bearingDeg: bearing, active: false, confidence: 0.9))
        }
    }

    // MARK: per frame

    private func setFaces(_ f: [TrackedFace]) {
        guard scenario == nil else { return }               // a scenario supplies its own people
        relinkCaptions(to: f)
        if settings.value.simulatedFaces {                  // simulated faces arrive already labelled
            people.adopt(f, now: now)
            faces = f.map { var face = $0; face.personId = people.person(trackId: $0.id)?.id; return face }
        } else {
            faces = f
        }
        let bearings = captions.mapValues { $0.micBearing ?? $0.bearing }     // compared in the array's frame
        let changed = attributor.refresh(faces: micFaceRefs, bearings: bearings, now: now)
        for (utt, var c) in captions {
            if c.speakerPersonId != nil {
                // Locked to a person: the caption goes where they are, whatever is nearest the sound now.
                bind(&c, fallback: c.speaker)
            } else if let speaker = changed[utt] {
                // No holder yet. The turn-around: off-camera -> the face that came into view there.
                if case .offCamera = speaker, case let .person(_, label) = c.speaker {
                    c.ownerPersonId = people.all.first { $0.displayName == label }?.id
                }
                applyLock(&c, fresh: speaker, speaking: !c.final, heardNow: false)
            }
            if c != captions[utt] { captions[utt] = c }
        }
    }

    /// Where the array would hear this face from (for debug displays).
    func micBearing(of face: TrackedFace) -> Double {
        room.micBearing(ofEntityAt: face.bearing, distance: distance(to: face))
    }

    /// People in view as speaker candidates, each at the angle the array would hear them from.
    private var lockCandidates: [SpeakerCandidate] {
        let room = room
        return faces.compactMap { f in
            f.personId.map { SpeakerCandidate(personId: $0, micBearing: room.micBearing(ofEntityAt: f.bearing, distance: distance(to: f)),
                                              mouthActivity: f.mouthActivity) }
        }
    }

    /// Put the source's speaker lock on top of this update's attribution: a source that already
    /// belongs to someone stays theirs; a new one takes the attributed person (if any).
    private func applyLock(_ c: inout CaptionState, fresh: Speaker, speaking: Bool, heardNow: Bool = true) {
        var attributed: Int?
        if case let .person(id, _) = fresh { attributed = faces.first { $0.id == id }?.personId }
        let r = speakerLocks.resolve(utt: c.uttId, sourceId: c.sourceId ?? 0, micBearing: c.micBearing ?? c.bearing, speaking: speaking,
                                     candidates: lockCandidates, attributed: attributed, heardNow: heardNow, now: now)
        c.speakerPersonId = r.personId
        bind(&c, fallback: fresh)
    }

    /// Where a caption sits, from who holds its source: on their face if they're in view, otherwise
    /// off camera and owned by them (their color, their notification).
    private func bind(_ c: inout CaptionState, fallback: Speaker) {
        guard let pid = c.speakerPersonId else {
            c.speaker = fallback
            return
        }
        if let f = faces.first(where: { $0.personId == pid }) {
            c.speaker = .person(id: f.id, label: f.label)
            c.ownerPersonId = nil
        } else {
            c.speaker = .offCamera
            c.ownerPersonId = pid
        }
    }

    /// A caption attached to a Vision track that no longer exists, whose person is in view on a new
    /// track (re-identified after a tracking reset): move it across now, so the caption and aura never
    /// blink off the person.
    private func relinkCaptions(to newFaces: [TrackedFace]) {
        let ids = Set(newFaces.map(\.id))
        for (utt, c) in captions {
            guard case let .person(old, label) = c.speaker, !ids.contains(old) else { continue }
            let personId = faces.first { $0.id == old }?.personId
            guard let now = newFaces.first(where: { ($0.personId != nil && $0.personId == personId) || $0.label == label }) else { continue }
            attributor.retarget(from: old, to: now.id, label: now.label)
            captions[utt]?.speaker = .person(id: now.id, label: now.label)
        }
    }

    private func onTick() {
        if let scenarioHarness {
            playScenario(scenarioHarness)
            return
        }
        now = CACurrentMediaTime()
        if settings.value.simulatedFaces {
            setFaces(SimulatedFaces.faces(heading: headingDeg, hfovDeg: hfovDeg, now: now))
        }
        // Expire captions: finals after the hold, typing dots that never got words.
        for (utt, c) in captions {
            let expired = (c.final && now - c.updated > Tunables.captionHoldS)
                || (c.typing && now - c.updated > Tunables.typingTimeoutS)
                || (!c.final && now - c.updated > 12)
            if expired {
                captions[utt] = nil
                attributor.forget(utt)
            }
        }
        alerts.removeAll { !$0.active && now - $0.updated > Tunables.alertFadeS + 1 }
        sounds = sounds.filter { now - $0.value.updated < 6 }
        enrichments = enrichments.filter { now - $0.value.updated < Tunables.enrichmentKeepS }
        enrichmentBuzzed = enrichmentBuzzed.filter { enrichments[$0] != nil }

        // Spatial V2: decide what earns a place on screen. Everything above was received and is
        // tracked regardless; this only derives what to draw.
        if visualStyle == .spatialV2 {
            people.expire(now: now)
            speakerLocks.expire(now: now)
            let everyone = people.all
            if sessionPeople != everyone { sessionPeople = everyone }
            let things = sceneObjects.all
            if objects != things { objects = things }
            scene = presentation.step(now: now, heading: headingDeg, hfovDeg: hfovDeg, captions: visibleCaptions,
                                      alerts: alerts, sounds: Array(sounds.values), faces: faces, people: everyone,
                                      objects: things, sources: listenerSources, focusMode: settings.value.focusMode,
                                      associationUncertaintyDeg: spatialUncertaintyDeg(micBearing: nil), room: room,
                                      enrichments: enrichments)
            sceneObjects.unlinkAll(except: scene.objectLinks)
        }

        if settings.value.sendScene, now - lastScene > 1 / Tunables.sceneHz, relayStatus == .connected {
            lastScene = now
            let speaking = Set(activeSpeakers.keys)
            relay.send(Scene(t: Date().timeIntervalSince1970, headingDeg: headingDeg,
                             people: faces.map { ScenePerson(label: $0.label, bearingDeg: $0.bearing,
                                                             speaking: speaking.contains($0.id)) }))
        }
    }

    private func thermalChanged() {
        thermal = Self.thermalState(override: settings.value.debugThermalOverride) ?? ProcessInfo.processInfo.thermalState
        // SPEC §14 heat: serious -> halve the detection rate.
        tracker.detectEvery = thermal == .serious || thermal == .critical
            ? Tunables.detectEveryFramesHot : Tunables.detectEveryFrames
    }

    private static func thermalState(override: String) -> ProcessInfo.ThermalState? {
        switch override {
        case "nominal": .nominal
        case "fair": .fair
        case "serious": .serious
        case "critical": .critical
        default: nil
        }
    }

    /// Liquid Glass (`hereGlass`) falls back to a flat plate one thermal tier earlier than detection
    /// slows down -- cheap insurance against the 20-minute-with-AirPlay thermal risk (SPEC §24, the
    /// on-device checklist) that's unverified on hardware. A plate under "serious" is a
    /// small, reversible fidelity trade for headroom before anything visibly degrades.
    var simplifyGlass: Bool { thermal == .serious || thermal == .critical }

    var thermalState: Heartbeat.Thermal {
        switch thermal {
        case .nominal: .nominal
        case .fair: .fair
        case .serious: .serious
        case .critical: .critical
        @unknown default: .nominal
        }
    }

    // MARK: messages from the laptop

    func handle(_ message: HereMessage) {
        switch message {
        case .sources(let s):
            sources = s.sources                                 // raw, as the array reported them (diagnostics)
            // Everything downstream works in directions from the wearer.
            let room = room
            listenerSources = s.sources.map { var c = $0; c.bearingDeg = room.listenerBearing(micBearing: $0.bearingDeg); return c }
            presentation.ingest(sources: listenerSources, now: now)   // the room's baseline adapts to everything heard
            if sourceIdsMeaningful { people.follow(sources: listenerSources, now: now) }
            heard(s.t)
        case .speechStart(let s):
            heard(s.t)
            let speaker = attributor.attribute(utt: s.uttId, bearing: s.bearingDeg, faces: micFaceRefs, now: now,
                                               provisional: true, established: establishedFaces(sourceId: s.sourceId))
            var caption = CaptionState(uttId: s.uttId, speaker: speaker, bearing: listenerBearing(s.bearingDeg), started: now, updated: now)
            caption.micBearing = s.bearingDeg
            caption.sourceId = s.sourceId
            caption.ownerPersonId = owner(of: speaker, bearing: listenerBearing(s.bearingDeg), sourceId: s.sourceId)
            applyLock(&caption, fresh: speaker, speaking: true)
            captions[s.uttId] = caption
            // A confident onset buzzes now (fast path); an unsure one waits ~0.4 s for the utterance's
            // averaged bearing rather than pointing the wearer the wrong way.
            if s.confidence >= Tunables.beltOnsetMinConfidence {
                buzzForSpeech(speaker: speaker, bearing: listenerBearing(s.bearingDeg))
            } else {
                buzzPending.insert(s.uttId)
            }
        case .utterance(let u):
            heard(u.tEnd)
            onUtterance(u)
        case .alert(let a):
            heard(a.t)
            onAlert(a)
        case .soundEvent(let e):
            heard(e.t)
            guard e.category != .alert else { return }             // alerts have their own panel
            // The same continuing source can arrive under a new event id; keep it as one sound.
            // What the classifier said, held to a bar: a weak guess is shown as unknown, not as a
            // confident wrong icon. Then the same continuing source keeps one notification, and its
            // label is decided by accumulated evidence rather than by whichever window came last.
            let heardAs = SoundContinuity.confidentLabel(e.label, confidence: e.confidence)
            let bearing = e.bearingDeg.map(listenerBearing)
            let key = SoundContinuity.sourceKey(forEvent: e.eventId, label: heardAs, bearing: bearing,
                                                among: Array(sounds.values), now: now)
            let existing = sounds[key]
            var votes = existing?.votes ?? [:]
            let label = SoundContinuity.vote(&votes, label: heardAs, confidence: e.confidence, current: existing?.label)
            let sameWords = label == e.label
            let repeats = key == e.eventId ? e.count : max(e.count, (existing?.count ?? 0) + (heardAs == existing?.label ? 1 : 0))
            sounds[key] = SoundTag(eventId: key, label: label, category: sameWords ? e.category : (label == existing?.label ? existing!.category : .other),
                                   emoji: sameWords ? e.emoji : (label == existing?.label ? existing!.emoji : "❔"),
                                   text: sameWords ? e.text : (label == existing?.label ? existing!.text : "Unknown sound"),
                                   bearing: bearing ?? existing?.bearing, count: max(1, repeats), updated: now,
                                   confidence: e.confidence, sourceId: e.sourceId ?? existing?.sourceId, votes: votes,
                                   micBearing: e.bearingDeg ?? existing?.micBearing, heardAs: e.label, model: e.model)
        case .perceptionEnrichment(let e):
            heard(e.t)
            onEnrichment(e)
        case .config(let c):
            if c != settings.value.configMessage(now: c.t) {      // another phone (presenter remote) changed it
                var s = settings.value
                s.apply(c)
                settings.value = s
            }
        case .heartbeat(let h) where h.src == "laptop":
            laptopEngine = h.engine
            laptopStatus = h.status
            laptopDetail = h.detail
            laptopDoaSource = h.doaSource
            lastHeard = now
        default:
            break
        }
    }

    private func heard(_ laptopT: Double) {
        lastHeard = now
        if relay.clock.sampleCount > 0 {
            lagMs = (relay.clock.laptopTime(forPhoneEpoch: PhoneClock.nowEpoch) - laptopT) * 1000
        }
    }

    private func onUtterance(_ u: Utterance) {
        if u.final && u.text.isEmpty {                              // retracted: it was noise
            buzzPending.remove(u.uttId)
            captions[u.uttId] = nil
            attributor.forget(u.uttId)
            return
        }
        let speaker = attributor.attribute(utt: u.uttId, bearing: u.bearingDeg, faces: micFaceRefs, now: now,
                                           established: establishedFaces(sourceId: u.sourceId), spread: u.bearingSpreadDeg)
        let heardFrom = listenerBearing(u.bearingDeg)
        var c = captions[u.uttId] ?? CaptionState(uttId: u.uttId, speaker: speaker, bearing: heardFrom,
                                                  started: now, updated: now)
        if captions[u.uttId] == nil || buzzPending.remove(u.uttId) != nil {  // missed or unsure speech_start
            buzzForSpeech(speaker: speaker, bearing: heardFrom)
        }
        c.speaker = speaker
        c.bearing = heardFrom
        c.micBearing = u.bearingDeg
        c.sourceId = u.sourceId
        c.ownerPersonId = owner(of: speaker, bearing: heardFrom, sourceId: u.sourceId) ?? c.ownerPersonId
        applyLock(&c, fresh: speaker, speaking: !u.final)
        c.text = u.text
        c.pending = u.pending
        c.final = u.final
        c.tone = settings.value.features.tone ? u.tone : nil
        c.toneConfidence = u.toneConfidence
        c.loudness = u.loudness
        c.updated = now
        captions[u.uttId] = c
        if let s = u.stageMs { stageMs = s }
        if u.final { record(u, speaker: c.speaker) }

        if u.final, settings.value.sendScene {
            relay.send(Attribution(t: Date().timeIntervalSince1970, ref: Ref(uttId: u.uttId),
                                   personLabel: speaker.label, method: speaker.label == nil ? .offCamera : .bearing,
                                   score: attributor.score(for: u.uttId)))
        }
    }

    private func onAlert(_ a: Alert) {
        let s = settings.value
        guard s.features.alerts, s.alertsEnabled[a.kind.rawValue] != false else { return }
        if let i = alerts.firstIndex(where: { $0.alertId == a.alertId }) {
            alerts[i].active = a.active
            alerts[i].bearing = a.bearingDeg.map(listenerBearing) ?? alerts[i].bearing
            alerts[i].refUttId = a.ref?.uttId ?? alerts[i].refUttId
            alerts[i].refEventId = a.ref?.eventId ?? alerts[i].refEventId
            alerts[i].updated = now
            if a.active {
                alerts[i].count += 1
                if now - alerts[i].lastBuzz > 4 {                    // repeats: buzz again, not every update
                    alerts[i].lastBuzz = now
                    buzzForAlert(alerts[i])
                }
            }
        } else if a.active {
            let state = AlertState(alertId: a.alertId, kind: a.kind, label: a.label, emoji: a.emoji,
                                   priority: a.priority, bearing: a.bearingDeg.map(listenerBearing), active: true, count: 1,
                                   updated: now, lastBuzz: now, refUttId: a.ref?.uttId, refEventId: a.ref?.eventId)
            alerts.append(state)
            buzzForAlert(state)
            // A phone-side nudge independent of the belt (SPEC §23 backup: belt can disconnect) --
            // the wearer's pocket buzzes even if BLE drops or Settings > Belt is off.
            UINotificationFeedbackGenerator().notificationOccurred(.warning)
        }
    }

    /// `perception_enrichment`: the laptop's attention judgment for an earlier caption or sound. Kept for the
    /// presentation (PresenceEngine sets prominence from it); an important or urgent one may buzz once.
    private func onEnrichment(_ e: PerceptionEnrichment) {
        let state = EnrichmentState(attention: e.attention, confidence: e.attentionConfidence,
                                    directedAtUser: e.directedAtUser, updated: now)
        if let u = e.ref.uttId { enrichments[EnrichmentState.key(uttId: u)] = state }
        if let ev = e.ref.eventId { enrichments[EnrichmentState.key(eventId: ev)] = state }
        buzzForEnrichment(state, uttId: e.ref.uttId, eventId: e.ref.eventId)
    }

    // MARK: belt (SPEC §14 belt logic, on the phone)

    /// important = full card plus belt; urgent = interrupt. At most once per referenced event. An alert about
    /// the same event already buzzed by the rules, so it buzzes again only when urgent lifts it to the
    /// emergency pattern. Speech buzzes only when urgent, or when it is clearly addressed to the wearer.
    private func buzzForEnrichment(_ e: EnrichmentState, uttId: String?, eventId: String?) {
        guard settings.value.features.belt, e.attention == .important || e.attention == .urgent else { return }
        let urgent = e.attention == .urgent
        guard let key = uttId.map({ EnrichmentState.key(uttId: $0) }) ?? eventId.map({ EnrichmentState.key(eventId: $0) }),
              !enrichmentBuzzed.contains(key) else { return }
        let bearing: Double?
        if let alert = alerts.first(where: { ($0.refUttId != nil && $0.refUttId == uttId) || ($0.refEventId != nil && $0.refEventId == eventId) }) {
            guard urgent, alert.priority < Tunables.enrichBeltPriorityUrgent else { return }
            bearing = alert.bearing
        } else if let uttId, let c = captions[uttId] {
            guard urgent || (e.directedAtUser ?? 0) >= Tunables.directedAtUserRaise else { return }
            bearing = c.bearing
        } else if let eventId, let s = sounds[eventId] {
            bearing = s.bearing
        } else {
            return                                                  // nothing on the phone to point at
        }
        enrichmentBuzzed.insert(key)
        let priority = urgent ? Tunables.enrichBeltPriorityUrgent : Tunables.enrichBeltPriorityImportant
        belt.send(beltPolicy.attention(priority: priority, relative: bearing.map { Geo.relative($0, heading: headingDeg) },
                                       strength: settings.value.beltStrength))
    }

    private func buzzForSpeech(speaker: Speaker, bearing: Double) {
        guard settings.value.features.belt else { return }
        let key: String
        switch speaker {
        case let .person(id, _): key = "p\(id)"
        case .offCamera: key = "o\(Int((Geo.wrap(bearing) + 180) / 30))"
        }
        if let cmd = beltPolicy.speechStarted(personKey: key, relative: Geo.relative(bearing, heading: headingDeg),
                                              strength: settings.value.beltStrength, now: now) {
            belt.send(cmd)
        }
    }

    private func buzzForAlert(_ a: AlertState) {
        guard settings.value.features.belt else { return }
        belt.send(beltPolicy.alert(kind: a.kind, priority: a.priority,
                                   relative: a.bearing.map { Geo.relative($0, heading: headingDeg) },
                                   strength: settings.value.beltStrength))
    }
}
