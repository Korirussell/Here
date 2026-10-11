import Foundation

/// All tunables for ios_here in one place (HERE_PLAN.md §17: "Tunables live in
/// one config file, not scattered in code"). Values marked (tune) are starting
/// points; see ios_here/SPIKE.md for the measurements that should replace them.
///
/// `AppConfig` is also the Swift mirror of the `Config` contract message
/// (HERE_PLAN.md §8.4) that the Settings UI edits and that gets sent to the
/// hub. It persists to `UserDefaults` so settings survive relaunch.
final class AppConfig: ObservableObject, Codable {

    // MARK: Transport (not part of the `config` wire message; local only)

    /// The laptop's IP address on the USB link (Personal Hotspot adapter, or
    /// localhost when using the `iproxy` fallback). This varies per machine,
    /// hence editable in Settings rather than hardcoded.
    @Published var hubHost: String = "172.20.10.1"
    @Published var hubPort: Int = 8765

    // MARK: Camera (tune)

    enum Lens: String, Codable, CaseIterable, Identifiable, Hashable {
        case wide, ultrawide
        var id: String { rawValue }
    }

    /// Ultrawide recommended by HERE_PLAN.md §4 to fit two people at
    /// conversation distance in portrait; confirm after the HFOV spike.
    @Published var lens: Lens = .ultrawide
    @Published var captureWidth: Int = 720
    @Published var captureHeight: Int = 1280
    @Published var targetFps: Double = 30
    @Published var jpegQuality: Double = 0.6

    /// Horizontal FOV of the *upright* frame, per lens, in degrees. (tune) —
    /// replace with the measured value from the HFOV spike (HERE_PLAN.md §6,
    /// §16 "ios_here"). These are rough nominal starting points, not measured.
    @Published var hfovDegByLens: [Lens: Double] = [.wide: 69.0, .ultrawide: 102.0]
    var hfovDeg: Double { hfovDegByLens[lens] ?? 69.0 }

    // MARK: Heading

    /// `CMAttitude`'s sign convention relative to "clockwise positive" can
    /// only be confirmed on-device (HERE_PLAN.md §6: "Verify the sign/
    /// transposition empirically: turning clockwise ... must increase the
    /// heading."). Flip this from Settings if a clockwise turn decreases the
    /// reported heading.
    @Published var headingSignFlip: Bool = false

    // MARK: `config` message fields (HERE_PLAN.md §8.4)

    @Published var profile: String = "deaf" // 'deaf' | 'custom'
    @Published var userName: String = ""
    @Published var nameAliases: [String] = []

    struct Features: Codable, Equatable {
        var captions: Bool = true
        var tone: Bool = true
        var soundLabels: Bool = true
        var alerts: Bool = true
        var belt: Bool = true
        var memory: Bool = true
        var lookingAwaySummary: Bool = false
        var typeToSpeak: Bool = false
    }
    @Published var features: Features = Features()

    @Published var alertsEnabled: [String: Bool] = [
        "doorbell": true, "knock": true, "baby_crying": true, "dog_barking": true,
        "name_called": true, "smoke_alarm": true, "alarm": true, "siren": true,
        "loud_bang": true, "footsteps": false, "phone_ringing": true,
    ]

    @Published var beltStrength: Int = 180 // 0..255
    @Published var captionScale: Double = 1.0 // 0.75..1.5

    // MARK: Persistence

    private static let defaultsKey = "ios_here.config.v1"

    static func loadOrDefault() -> AppConfig {
        if let data = UserDefaults.standard.data(forKey: defaultsKey),
           let decoded = try? JSONDecoder().decode(AppConfig.self, from: data) {
            return decoded
        }
        return AppConfig()
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: Self.defaultsKey)
        }
    }

    // MARK: Codable (manual, because @Published doesn't synthesize for free
    // alongside ObservableObject in a class we also want Codable)

    private enum CodingKeys: String, CodingKey {
        case hubHost, hubPort, lens, captureWidth, captureHeight, targetFps,
             jpegQuality, hfovDegByLens, headingSignFlip, profile, userName,
             nameAliases, features, alertsEnabled, beltStrength, captionScale
    }

    init() {}

    required init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        hubHost = try c.decode(String.self, forKey: .hubHost)
        hubPort = try c.decode(Int.self, forKey: .hubPort)
        lens = try c.decode(Lens.self, forKey: .lens)
        captureWidth = try c.decode(Int.self, forKey: .captureWidth)
        captureHeight = try c.decode(Int.self, forKey: .captureHeight)
        targetFps = try c.decode(Double.self, forKey: .targetFps)
        jpegQuality = try c.decode(Double.self, forKey: .jpegQuality)
        let rawHfov = try c.decode([String: Double].self, forKey: .hfovDegByLens)
        hfovDegByLens = Dictionary(uniqueKeysWithValues: rawHfov.compactMap { k, v in
            Lens(rawValue: k).map { ($0, v) }
        })
        headingSignFlip = try c.decode(Bool.self, forKey: .headingSignFlip)
        profile = try c.decode(String.self, forKey: .profile)
        userName = try c.decode(String.self, forKey: .userName)
        nameAliases = try c.decode([String].self, forKey: .nameAliases)
        features = try c.decode(Features.self, forKey: .features)
        alertsEnabled = try c.decode([String: Bool].self, forKey: .alertsEnabled)
        beltStrength = try c.decode(Int.self, forKey: .beltStrength)
        captionScale = try c.decode(Double.self, forKey: .captionScale)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(hubHost, forKey: .hubHost)
        try c.encode(hubPort, forKey: .hubPort)
        try c.encode(lens, forKey: .lens)
        try c.encode(captureWidth, forKey: .captureWidth)
        try c.encode(captureHeight, forKey: .captureHeight)
        try c.encode(targetFps, forKey: .targetFps)
        try c.encode(jpegQuality, forKey: .jpegQuality)
        let rawHfov = Dictionary(uniqueKeysWithValues: hfovDegByLens.map { ($0.key.rawValue, $0.value) })
        try c.encode(rawHfov, forKey: .hfovDegByLens)
        try c.encode(headingSignFlip, forKey: .headingSignFlip)
        try c.encode(profile, forKey: .profile)
        try c.encode(userName, forKey: .userName)
        try c.encode(nameAliases, forKey: .nameAliases)
        try c.encode(features, forKey: .features)
        try c.encode(alertsEnabled, forKey: .alertsEnabled)
        try c.encode(beltStrength, forKey: .beltStrength)
        try c.encode(captionScale, forKey: .captionScale)
    }

    /// The wire shape of the `config` message (HERE_PLAN.md §8.4), as a JSON
    /// object ready to be merged with the envelope and sent to the hub.
    func toWireFields(knownPeople: [[String: Any]] = []) -> [String: Any] {
        [
            "profile": profile,
            "user_name": userName,
            "name_aliases": nameAliases,
            "known_people": knownPeople,
            "features": [
                "captions": features.captions,
                "tone": features.tone,
                "sound_labels": features.soundLabels,
                "alerts": features.alerts,
                "belt": features.belt,
                "memory": features.memory,
                "looking_away_summary": features.lookingAwaySummary,
                "type_to_speak": features.typeToSpeak,
            ],
            "alerts_enabled": alertsEnabled,
            "belt_strength": beltStrength,
            "caption_scale": captionScale,
        ]
    }
}
