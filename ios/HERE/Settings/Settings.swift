import Foundation

/// Everything the Settings screen edits (SPEC §14), persisted to UserDefaults as JSON.
/// The `config` contract message is built from it (and applied from it when another phone,
/// e.g. the presenter's remote, changes settings through the relay).
struct Settings: Codable, Equatable {
    // Relay (SPEC §7)
    var relayURL = "wss://YOUR-RELAY-HOST/ws"      // primary: the laptop's local relay, ws://<laptop-ip>:8080/ws
    var relayFallbackURL = ""                      // optional fallback: Vultr, wss://<host>/ws ("" = none)
    var relayToken = ""
    var session = "demo"

    // Who the wearer is (name-called)
    var userName = ""
    var nameAliases: [String] = []

    // Camera + heading
    var hfovOverride = 0.0                         // 0 = use the camera format's field of view
    var headingOffset = 0.0                        // set by Calibrate
    var headingSignFlip = false                    // if turning clockwise makes the heading go down
    // Where the wearer stands relative to the array (0 m = at the array, the original assumption),
    // and how far from the array sources are assumed to be. See RoomGeometry.
    var wearerDistanceM = 0.0
    var wearerBearingDeg = 0.0                     // direction from the array to the wearer, array frame
    var sourceRadiusM = Tunables.assumedSourceRadiusM

    // Modes (SPEC §14)
    var focusMode = false
    var beltOnlyMode = false
    var features = Features()
    var alertsEnabled: [String: Bool] = [:]

    // Belt
    var beltStrength = 200
    var beltAutoConnect = true
    var beltServiceUUID = BeltUUID.service
    var beltCommandUUID = BeltUUID.command

    // Display
    var showDebug = false
    /// Face / body tracking rectangles in the debug overlay. Tracking geometry, never demo UI: its own
    /// switch, off by default, so the stats overlay can be on without boxes on people.
    var debugBoxes = false
    /// "Show Person Recognition Debug": what Vision believes about each person, drawn on them in their
    /// identity color. Testing only; off by default, and nothing below draws unless it is on.
    var personDebug = false
    var personDebugFaceBoxes = true
    var personDebugBodyBoxes = true
    var personDebugIdentity = true
    var personDebugSpeaker = true
    var personDebugAudio = true
    /// Read lip movement from face landmarks as supporting evidence of who is talking.
    var mouthTracking = true
    var simulatedFaces = false                     // Simulator / no camera: fake Person 1 and 2 at +-35 deg
    var simulatedHeadingDeg = 0.0                   // Simulator only: drag to rehearse the turn-around
    /// "" = real ProcessInfo.thermalState; else "nominal"|"fair"|"serious"|"critical". ProcessInfo's
    /// thermal state is read-only and the Simulator rarely reports anything but nominal, so this is how
    /// the .serious/.critical paths (halved detection rate, simplified glass) get rehearsed without a
    /// hot phone.
    var debugThermalOverride = ""
    /// On-device testing only (production is always the back camera, SPEC §5/§14): use the front
    /// camera so you can see yourself and the screen at once with no mount or second person.
    var debugFrontCamera = false
    var sendScene = false                          // extras: attribution + scene to the relay
    /// Which AR layer draws over the camera. The legacy look stays available until Spatial V2 is
    /// proven on the rig (Settings > Developer).
    var visualStyle = SpatialVisualStyle.spatialV2
    /// Hold a person's track on a body detection when their face stops being detected.
    var bodyFallback = Tunables.bodyFallbackEnabled
    /// Find quiet objects (doors, chairs, phones...) with on-device vision, about once a second.
    var objectDetection = true
    /// "Scene Understanding": show what HERE currently recognises. Developer/demo only, off by default.
    var sceneDebug = false
    /// Experimental: recognise explicitly enrolled people when they appear (off by default).
    var knownRecognition = false

    func configMessage(now: Double) -> ConfigMessage {
        ConfigMessage(t: now, userName: userName, nameAliases: nameAliases, focusMode: focusMode,
                      beltOnlyMode: beltOnlyMode, features: features, alertsEnabled: alertsEnabled,
                      beltStrength: beltStrength)
    }

    mutating func apply(_ c: ConfigMessage) {
        userName = c.userName
        nameAliases = c.nameAliases
        focusMode = c.focusMode
        beltOnlyMode = c.beltOnlyMode
        features = c.features
        alertsEnabled = c.alertsEnabled
        beltStrength = c.beltStrength
    }

    // Decoding tolerates settings saved by an older build (missing keys keep their defaults).
    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let d = Settings()
        relayURL = try c.decodeIfPresent(String.self, forKey: .relayURL) ?? d.relayURL
        relayFallbackURL = try c.decodeIfPresent(String.self, forKey: .relayFallbackURL) ?? d.relayFallbackURL
        relayToken = try c.decodeIfPresent(String.self, forKey: .relayToken) ?? d.relayToken
        session = try c.decodeIfPresent(String.self, forKey: .session) ?? d.session
        userName = try c.decodeIfPresent(String.self, forKey: .userName) ?? d.userName
        nameAliases = try c.decodeIfPresent([String].self, forKey: .nameAliases) ?? d.nameAliases
        hfovOverride = try c.decodeIfPresent(Double.self, forKey: .hfovOverride) ?? d.hfovOverride
        headingOffset = try c.decodeIfPresent(Double.self, forKey: .headingOffset) ?? d.headingOffset
        headingSignFlip = try c.decodeIfPresent(Bool.self, forKey: .headingSignFlip) ?? d.headingSignFlip
        wearerDistanceM = try c.decodeIfPresent(Double.self, forKey: .wearerDistanceM) ?? d.wearerDistanceM
        wearerBearingDeg = try c.decodeIfPresent(Double.self, forKey: .wearerBearingDeg) ?? d.wearerBearingDeg
        sourceRadiusM = try c.decodeIfPresent(Double.self, forKey: .sourceRadiusM) ?? d.sourceRadiusM
        focusMode = try c.decodeIfPresent(Bool.self, forKey: .focusMode) ?? d.focusMode
        beltOnlyMode = try c.decodeIfPresent(Bool.self, forKey: .beltOnlyMode) ?? d.beltOnlyMode
        features = try c.decodeIfPresent(Features.self, forKey: .features) ?? d.features
        alertsEnabled = try c.decodeIfPresent([String: Bool].self, forKey: .alertsEnabled) ?? d.alertsEnabled
        beltStrength = try c.decodeIfPresent(Int.self, forKey: .beltStrength) ?? d.beltStrength
        beltAutoConnect = try c.decodeIfPresent(Bool.self, forKey: .beltAutoConnect) ?? d.beltAutoConnect
        beltServiceUUID = try c.decodeIfPresent(String.self, forKey: .beltServiceUUID) ?? d.beltServiceUUID
        beltCommandUUID = try c.decodeIfPresent(String.self, forKey: .beltCommandUUID) ?? d.beltCommandUUID
        showDebug = try c.decodeIfPresent(Bool.self, forKey: .showDebug) ?? d.showDebug
        debugBoxes = try c.decodeIfPresent(Bool.self, forKey: .debugBoxes) ?? d.debugBoxes
        personDebug = try c.decodeIfPresent(Bool.self, forKey: .personDebug) ?? d.personDebug
        personDebugFaceBoxes = try c.decodeIfPresent(Bool.self, forKey: .personDebugFaceBoxes) ?? d.personDebugFaceBoxes
        personDebugBodyBoxes = try c.decodeIfPresent(Bool.self, forKey: .personDebugBodyBoxes) ?? d.personDebugBodyBoxes
        personDebugIdentity = try c.decodeIfPresent(Bool.self, forKey: .personDebugIdentity) ?? d.personDebugIdentity
        personDebugSpeaker = try c.decodeIfPresent(Bool.self, forKey: .personDebugSpeaker) ?? d.personDebugSpeaker
        personDebugAudio = try c.decodeIfPresent(Bool.self, forKey: .personDebugAudio) ?? d.personDebugAudio
        mouthTracking = try c.decodeIfPresent(Bool.self, forKey: .mouthTracking) ?? d.mouthTracking
        simulatedFaces = try c.decodeIfPresent(Bool.self, forKey: .simulatedFaces) ?? d.simulatedFaces
        simulatedHeadingDeg = try c.decodeIfPresent(Double.self, forKey: .simulatedHeadingDeg) ?? d.simulatedHeadingDeg
        debugThermalOverride = try c.decodeIfPresent(String.self, forKey: .debugThermalOverride) ?? d.debugThermalOverride
        debugFrontCamera = try c.decodeIfPresent(Bool.self, forKey: .debugFrontCamera) ?? d.debugFrontCamera
        sendScene = try c.decodeIfPresent(Bool.self, forKey: .sendScene) ?? d.sendScene
        visualStyle = (try? c.decodeIfPresent(SpatialVisualStyle.self, forKey: .visualStyle)) ?? d.visualStyle
        bodyFallback = try c.decodeIfPresent(Bool.self, forKey: .bodyFallback) ?? d.bodyFallback
        objectDetection = try c.decodeIfPresent(Bool.self, forKey: .objectDetection) ?? d.objectDetection
        sceneDebug = try c.decodeIfPresent(Bool.self, forKey: .sceneDebug) ?? d.sceneDebug
        knownRecognition = try c.decodeIfPresent(Bool.self, forKey: .knownRecognition) ?? d.knownRecognition
    }
}

/// Holds the current Settings and saves every change.
final class SettingsStore: ObservableObject {
    private static let key = "here.settings.v3"

    @Published var value: Settings {
        didSet { save() }
    }

    init() {
        if let data = UserDefaults.standard.data(forKey: Self.key),
           let s = try? JSONDecoder().decode(Settings.self, from: data) {
            value = s
        } else {
            value = Settings()
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(value) {
            UserDefaults.standard.set(data, forKey: Self.key)
        }
    }
}
