import Foundation

// HERE contract v3 for Swift (SPEC §12). Mirrors contracts/schemas/*.schema.json.
// Compiled straight into the iOS app (ios/project.yml lists this folder as a source),
// so there is exactly one copy. Explicit CodingKeys everywhere: no key strategies,
// which would also rewrite the keys of `alerts_enabled` / `stage_ms`.

public enum HereContract {
    public static let version = 3

    /// §12.10 default subscriptions per role.
    public static let defaultSubscribe: [String: [String]] = [
        "phone": ["sources", "speech_start", "utterance", "sound_event", "alert", "perception_enrichment", "config",
              "clock_pong", "heartbeat"],
        "laptop": ["config", "clock_ping"],
        "dashboard": ["*"],
        "memory": ["utterance", "alert", "sound_event", "perception_enrichment", "attribution", "search_request"],
        "tool": ["*"],
    ]
}

// MARK: - Shared types (§12.3)

public enum Tone: String, Codable, CaseIterable, Sendable {
    case neutral, excited, laughing, shouting, whispering, questioning, sad
}

public enum SoundLabel: String, Codable, CaseIterable, Sendable {
    case speech, doorbell, knock, alarm, music, tv, laughter, vehicle, siren, door, other, unknown
    case dogBarking = "dog_barking"
    case babyCrying = "baby_crying"
    case phoneRinging = "phone_ringing"
    case loudBang = "loud_bang"
    case carHorn = "car_horn"
}

public enum AlertKind: String, Codable, CaseIterable, Sendable {
    case doorbell, knock, alarm, siren, door
    case nameCalled = "name_called"
    case dogBarking = "dog_barking"
    case babyCrying = "baby_crying"
    case loudBang = "loud_bang"
    case carHorn = "car_horn"
}

public enum SoundCategory: String, Codable, CaseIterable, Sendable {
    case speech, alert, animal, media, other
}

public enum Attention: String, Codable, CaseIterable, Sendable {
    case ambient, notice, important, urgent
}

public enum Engine: String, Codable, Sendable {
    case fasterWhisper = "faster_whisper"
    case scribeRealtime = "scribe_realtime"
}

public enum Role: String, Codable, Sendable {
    case laptop, phone, dashboard, memory, tool
}

public struct Ref: Codable, Equatable, Sendable {
    public var uttId: String?
    public var eventId: String?
    public init(uttId: String? = nil, eventId: String? = nil) { self.uttId = uttId; self.eventId = eventId }
    enum CodingKeys: String, CodingKey { case uttId = "utt_id", eventId = "event_id" }
}

/// Fields every message carries (§12.2). Decoded first to find `type`.
public struct Envelope: Codable, Sendable {
    public var type: String
    public var v: Int
    public var src: String
    public var t: Double
    public var seq: Int?
    public var session: String?
}

// MARK: - Session (§12.4)

public struct Hello: Codable, Sendable {
    public var type = "hello"
    public var v = HereContract.version
    public var src = "phone"
    public var t: Double
    public var seq: Int?
    public var role: Role
    public var subscribe: [String]
    public var appVersion: String?

    public init(t: Double, role: Role, subscribe: [String], appVersion: String? = nil, src: String = "phone") {
        self.t = t; self.role = role; self.subscribe = subscribe; self.appVersion = appVersion; self.src = src
    }
    enum CodingKeys: String, CodingKey { case type, v, src, t, seq, role, subscribe, appVersion = "app_version" }
}

public struct Heartbeat: Codable, Sendable {
    public enum Status: String, Codable, Sendable { case ok, degraded, error }
    public enum Thermal: String, Codable, Sendable { case nominal, fair, serious, critical }
    public enum DoaSource: String, Codable, Sendable { case builtin, odas }

    public var type = "heartbeat"
    public var v = HereContract.version
    public var src = "phone"
    public var t: Double
    public var seq: Int?
    public var session: String?
    public var status: Status
    public var detail: String?
    public var thermal: Thermal?
    public var engine: Engine?
    public var doaSource: DoaSource?

    public init(t: Double, status: Status, detail: String? = nil, thermal: Thermal? = nil) {
        self.t = t; self.status = status; self.detail = detail; self.thermal = thermal
    }
    enum CodingKeys: String, CodingKey {
        case type, v, src, t, seq, session, status, detail, thermal, engine, doaSource = "doa_source"
    }
}

public struct ClockPing: Codable, Sendable {
    public var type = "clock_ping"
    public var v = HereContract.version
    public var src = "phone"
    public var t: Double
    public var tPhone: Double
    public init(tPhone: Double) { self.t = tPhone; self.tPhone = tPhone }
    enum CodingKeys: String, CodingKey { case type, v, src, t, tPhone = "t_phone" }
}

public struct ClockPong: Codable, Sendable {
    public var type = "clock_pong"
    public var v = HereContract.version
    public var src = "laptop"
    public var t: Double
    public var tPhone: Double
    public var tLaptop: Double
    enum CodingKeys: String, CodingKey { case type, v, src, t, tPhone = "t_phone", tLaptop = "t_laptop" }
}

public struct Features: Codable, Equatable, Sendable {
    public var captions = true
    public var tone = true
    public var rings = true
    public var alerts = true
    public var belt = true
    public init() {}
}

public struct ConfigMessage: Codable, Equatable, Sendable {
    public var type = "config"
    public var v = HereContract.version
    public var src = "phone"
    public var t: Double
    public var userName: String
    public var nameAliases: [String]
    public var focusMode: Bool
    public var beltOnlyMode: Bool
    public var features: Features
    /// Keys are AlertKind raw values ("doorbell", "name_called", ...).
    public var alertsEnabled: [String: Bool]
    public var beltStrength: Int

    public init(t: Double, userName: String, nameAliases: [String], focusMode: Bool, beltOnlyMode: Bool,
                features: Features, alertsEnabled: [String: Bool], beltStrength: Int) {
        self.t = t; self.userName = userName; self.nameAliases = nameAliases; self.focusMode = focusMode
        self.beltOnlyMode = beltOnlyMode; self.features = features; self.alertsEnabled = alertsEnabled
        self.beltStrength = beltStrength
    }
    enum CodingKeys: String, CodingKey {
        case type, v, src, t, features
        case userName = "user_name", nameAliases = "name_aliases", focusMode = "focus_mode"
        case beltOnlyMode = "belt_only_mode", alertsEnabled = "alerts_enabled", beltStrength = "belt_strength"
    }
}

// MARK: - Laptop → phone (§12.5–12.7)

public struct Source: Codable, Equatable, Sendable {
    public var id: Int
    public var bearingDeg: Double
    public var energy: Double
    public var speech: Bool
    public var confidence: Double
    public init(id: Int, bearingDeg: Double, energy: Double, speech: Bool, confidence: Double) {
        self.id = id; self.bearingDeg = bearingDeg; self.energy = energy; self.speech = speech
        self.confidence = confidence
    }
    enum CodingKeys: String, CodingKey { case id, energy, speech, confidence, bearingDeg = "bearing_deg" }
}

public struct Sources: Codable, Sendable {
    public var type = "sources"
    public var v = HereContract.version
    public var src = "laptop"
    public var t: Double
    public var sources: [Source]
    public init(t: Double, sources: [Source]) { self.t = t; self.sources = sources }
}

public struct SpeechStart: Codable, Sendable {
    public var type = "speech_start"
    public var v = HereContract.version
    public var src = "laptop"
    public var t: Double
    public var uttId: String
    public var bearingDeg: Double
    public var sourceId: Int
    public var confidence: Double
    public init(t: Double, uttId: String, bearingDeg: Double, sourceId: Int = 0, confidence: Double) {
        self.t = t; self.uttId = uttId; self.bearingDeg = bearingDeg; self.sourceId = sourceId
        self.confidence = confidence
    }
    enum CodingKeys: String, CodingKey {
        case type, v, src, t, confidence, uttId = "utt_id", bearingDeg = "bearing_deg", sourceId = "source_id"
    }
}

public struct Utterance: Codable, Sendable {
    public var type = "utterance"
    public var v = HereContract.version
    public var src = "laptop"
    public var t: Double
    public var uttId: String
    public var tStart: Double
    public var tEnd: Double
    public var text: String
    public var pending: String
    public var final: Bool
    public var bearingDeg: Double
    public var bearingSpreadDeg: Double
    public var sourceId: Int
    public var overlap: Bool
    public var tone: Tone?
    public var toneConfidence: Double
    public var loudness: Double
    public var engine: Engine
    public var stageMs: [String: Double]?

    public init(t: Double, uttId: String, tStart: Double, tEnd: Double, text: String, pending: String,
                final: Bool, bearingDeg: Double, bearingSpreadDeg: Double = 3, sourceId: Int = 0,
                overlap: Bool = false, tone: Tone? = nil, toneConfidence: Double = 0, loudness: Double = 0.5,
                engine: Engine = .fasterWhisper) {
        self.t = t; self.uttId = uttId; self.tStart = tStart; self.tEnd = tEnd; self.text = text
        self.pending = pending; self.final = final; self.bearingDeg = bearingDeg
        self.bearingSpreadDeg = bearingSpreadDeg; self.sourceId = sourceId; self.overlap = overlap
        self.tone = tone; self.toneConfidence = toneConfidence; self.loudness = loudness; self.engine = engine
    }
    enum CodingKeys: String, CodingKey {
        case type, v, src, t, text, pending, final, overlap, tone, loudness, engine
        case uttId = "utt_id", tStart = "t_start", tEnd = "t_end", bearingDeg = "bearing_deg"
        case bearingSpreadDeg = "bearing_spread_deg", sourceId = "source_id"
        case toneConfidence = "tone_confidence", stageMs = "stage_ms"
    }
}

public struct SoundEvent: Codable, Sendable {
    public var type = "sound_event"
    public var v = HereContract.version
    public var src = "laptop"
    public var t: Double
    public var eventId: String
    public var label: SoundLabel
    public var category: SoundCategory
    public var emoji: String
    public var text: String
    public var confidence: Double
    public var bearingDeg: Double?
    public var tStart: Double
    public var tEnd: Double
    public var count: Int
    public var sourceId: Int?
    public var model: String?
    enum CodingKeys: String, CodingKey {
        case type, v, src, t, label, category, emoji, text, confidence, count, model
        case eventId = "event_id", bearingDeg = "bearing_deg", tStart = "t_start", tEnd = "t_end"
        case sourceId = "source_id"
    }
}

public struct Alert: Codable, Sendable {
    public var type = "alert"
    public var v = HereContract.version
    public var src = "laptop"
    public var t: Double
    public var alertId: String
    public var kind: AlertKind
    public var label: String
    public var emoji: String
    public var priority: Int
    public var bearingDeg: Double?
    public var active: Bool
    public var confidence: Double
    public var ref: Ref?

    public init(t: Double, alertId: String, kind: AlertKind, label: String, emoji: String, priority: Int,
                bearingDeg: Double?, active: Bool, confidence: Double, ref: Ref? = nil) {
        self.t = t; self.alertId = alertId; self.kind = kind; self.label = label; self.emoji = emoji
        self.priority = priority; self.bearingDeg = bearingDeg; self.active = active
        self.confidence = confidence; self.ref = ref
    }
    enum CodingKeys: String, CodingKey {
        case type, v, src, t, kind, label, emoji, priority, active, confidence, ref
        case alertId = "alert_id", bearingDeg = "bearing_deg"
    }
}

// MARK: - Phone → relay, extras (§12.8–12.9)

public struct Attribution: Codable, Sendable {
    public enum Method: String, Codable, Sendable { case bearing, offCamera = "off_camera" }
    public var type = "attribution"
    public var v = HereContract.version
    public var src = "phone"
    public var t: Double
    public var ref: Ref
    public var personLabel: String?     // null = off camera; always sent
    public var method: Method
    public var score: Double

    public init(t: Double, ref: Ref, personLabel: String?, method: Method, score: Double) {
        self.t = t; self.ref = ref; self.personLabel = personLabel; self.method = method; self.score = score
    }
    enum CodingKeys: String, CodingKey { case type, v, src, t, ref, method, score, personLabel = "person_label" }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type); try c.encode(v, forKey: .v); try c.encode(src, forKey: .src)
        try c.encode(t, forKey: .t); try c.encode(ref, forKey: .ref); try c.encode(method, forKey: .method)
        try c.encode(score, forKey: .score)
        try c.encode(personLabel, forKey: .personLabel)   // encodes null, not omitted
    }
}

public struct ScenePerson: Codable, Sendable {
    public var label: String
    public var bearingDeg: Double
    public var speaking: Bool
    public init(label: String, bearingDeg: Double, speaking: Bool) {
        self.label = label; self.bearingDeg = bearingDeg; self.speaking = speaking
    }
    enum CodingKeys: String, CodingKey { case label, speaking, bearingDeg = "bearing_deg" }
}

public struct Scene: Codable, Sendable {
    public var type = "scene"
    public var v = HereContract.version
    public var src = "phone"
    public var t: Double
    public var headingDeg: Double
    public var people: [ScenePerson]
    public init(t: Double, headingDeg: Double, people: [ScenePerson]) {
        self.t = t; self.headingDeg = headingDeg; self.people = people
    }
    enum CodingKeys: String, CodingKey { case type, v, src, t, people, headingDeg = "heading_deg" }
}

public struct SearchRequest: Codable, Sendable {
    public var type = "search_request"
    public var v = HereContract.version
    public var src = "phone"
    public var t: Double
    public var queryId: String
    public var text: String
    public var limit: Int
    enum CodingKeys: String, CodingKey { case type, v, src, t, text, limit, queryId = "query_id" }
}

public struct SearchHit: Codable, Sendable {
    public var t: Double
    public var kind: String
    public var personLabel: String?
    public var text: String
    public var tone: Tone?
    public var bearingDeg: Double?
    public var score: Double
    enum CodingKeys: String, CodingKey { case t, kind, text, tone, score, personLabel = "person_label", bearingDeg = "bearing_deg" }
}

public struct SearchResult: Codable, Sendable {
    public var type = "search_result"
    public var v = HereContract.version
    public var src = "memory"
    public var t: Double
    public var queryId: String
    public var results: [SearchHit]
    enum CodingKeys: String, CodingKey { case type, v, src, t, results, queryId = "query_id" }
}

/// Optional enrichment of an earlier event (`ref`): attention (Hugging Face model), Gemini context. Arrives after the event;
/// never decides whether an event exists or where it is. `attentionModel` "rules" = deterministic fallback.
public struct PerceptionEnrichment: Codable, Sendable {
    public var type = "perception_enrichment"
    public var v = HereContract.version
    public var src = "laptop"
    public var t: Double
    public var ref: Ref
    public var attention: Attention
    public var attentionConfidence: Double
    public var attentionModel: String
    public var directedAtUser: Double?
    public var context: String?
    public var contextModel: String?
    enum CodingKeys: String, CodingKey {
        case type, v, src, t, ref, attention, context
        case attentionConfidence = "attention_confidence", attentionModel = "attention_model"
        case directedAtUser = "directed_at_user", contextModel = "context_model"
    }
}

// MARK: - Decoding by type

/// Every message the phone can receive, decoded by its `type`. Unknown types are kept, not errors.
public enum HereMessage: Sendable {
    case hello(Hello)
    case heartbeat(Heartbeat)
    case clockPing(ClockPing)
    case clockPong(ClockPong)
    case config(ConfigMessage)
    case sources(Sources)
    case speechStart(SpeechStart)
    case utterance(Utterance)
    case soundEvent(SoundEvent)
    case alert(Alert)
    case perceptionEnrichment(PerceptionEnrichment)
    case attribution(Attribution)
    case scene(Scene)
    case searchRequest(SearchRequest)
    case searchResult(SearchResult)
    case unknown(type: String)

    public static func decode(_ data: Data) throws -> HereMessage {
        let d = JSONDecoder()
        let env = try d.decode(Envelope.self, from: data)
        switch env.type {
        case "hello": return .hello(try d.decode(Hello.self, from: data))
        case "heartbeat": return .heartbeat(try d.decode(Heartbeat.self, from: data))
        case "clock_ping": return .clockPing(try d.decode(ClockPing.self, from: data))
        case "clock_pong": return .clockPong(try d.decode(ClockPong.self, from: data))
        case "config": return .config(try d.decode(ConfigMessage.self, from: data))
        case "sources": return .sources(try d.decode(Sources.self, from: data))
        case "speech_start": return .speechStart(try d.decode(SpeechStart.self, from: data))
        case "utterance": return .utterance(try d.decode(Utterance.self, from: data))
        case "sound_event": return .soundEvent(try d.decode(SoundEvent.self, from: data))
        case "alert": return .alert(try d.decode(Alert.self, from: data))
        case "perception_enrichment": return .perceptionEnrichment(try d.decode(PerceptionEnrichment.self, from: data))
        case "attribution": return .attribution(try d.decode(Attribution.self, from: data))
        case "scene": return .scene(try d.decode(Scene.self, from: data))
        case "search_request": return .searchRequest(try d.decode(SearchRequest.self, from: data))
        case "search_result": return .searchResult(try d.decode(SearchResult.self, from: data))
        default: return .unknown(type: env.type)
        }
    }

    public static func decode(_ text: String) throws -> HereMessage {
        try decode(Data(text.utf8))
    }
}

/// Wrap any angle into (-180, 180].
public func wrapDeg(_ a: Double) -> Double {
    var x = (a + 180).truncatingRemainder(dividingBy: 360)
    if x < 0 { x += 360 }
    x -= 180
    return x == -180 ? 180 : x
}
