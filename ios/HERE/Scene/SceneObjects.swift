import CoreGraphics
import Foundation

/// What an object *is*: a semantic class, never just "object". The vocabulary is every everyday
/// physical thing Apple's on-device image classifier (VNClassifyImageRequest) can name, checked
/// against its `supportedIdentifiers()`; close variants fold into one class (an armchair is a chair).
struct ObjectClass: Hashable {
    let name: String

    /// Classifier identifiers HERE accepts as objects (155). Scenes, activities, materials and
    /// people in the classifier's taxonomy are deliberately not here.
    static let visionVocabulary: [String] = [
        "door", "window", "stairs", "chair", "armchair", "chair_other", "folding_chair", "swivel_chair",
        "high_chair", "wheelchair", "bench", "stool", "sofa", "bed", "crib", "table",
        "desk", "cabinet", "bookshelf", "closet", "backpack", "bag", "purse", "luggage",
        "suitcase", "briefcase", "wallet", "laptop", "computer", "computer_keyboard", "computer_monitor", "computer_mouse",
        "computer_tower", "phone", "payphone", "headphones", "microphone", "speakers_music", "television", "camera",
        "printer", "cup", "mug", "bottle", "wine_bottle", "drinking_glass", "plate", "bowl",
        "fork", "knife", "spoon", "kettle", "teapot", "toaster", "blender", "microwave",
        "oven", "stove", "refrigerator", "dishwasher", "kitchen_sink", "toilet_seat", "shower", "lamp",
        "lamppost", "light_bulb", "chandelier", "candle", "clock", "clock_tower", "watch", "fireplace",
        "trash_can", "bucket", "cardboard_box", "book", "newspaper", "magazine", "pen", "scissors",
        "whiteboard", "painting", "vase", "decorative_plant", "plant", "flower", "tree", "umbrella",
        "hat", "shoes", "sunglasses", "jacket", "toy", "ball", "balloon", "guitar",
        "piano", "drum", "violin", "car", "truck", "semi_truck", "bus", "van",
        "firetruck", "ambulance", "police_car", "motorcycle", "bicycle", "scooter", "skateboard", "stroller",
        "train", "airplane", "helicopter", "boat", "traffic_light", "street_sign", "mailbox", "fence",
        "bridge", "crosswalk", "dog", "cat", "bird", "horse", "cow", "sheep",
        "fish", "rabbit", "hamster", "elevator", "escalator", "atm", "shopping_cart", "cart",
        "tool", "hammer", "pillow", "curtain", "helmet", "pan", "cutting_board", "fruit",
        "apple", "banana", "sandwich", "pizza", "cake", "bread", "coffee", "soda",
        "medicine", "thermometer", "joystick",
    ]

    /// Variants that are the same thing for HERE's purposes.
    static let canonical: [String: String] = [
        "armchair": "chair", "chair_other": "chair", "folding_chair": "chair", "swivel_chair": "chair", "high_chair": "chair",
        "mug": "cup", "drinking_glass": "cup", "wine_bottle": "bottle", "computer": "laptop",
        "computer_monitor": "monitor", "computer_keyboard": "keyboard", "computer_mouse": "mouse",
        "semi_truck": "truck", "police_car": "car", "van": "car", "decorative_plant": "plant",
        "suitcase": "luggage", "purse": "bag", "briefcase": "bag", "kitchen_sink": "sink", "toilet_seat": "toilet",
        "speakers_music": "speaker", "clock_tower": "clock", "cardboard_box": "box",
    ]

    private static let accepted = Set(visionVocabulary)

    /// What a bundled object detector (the 80-class COCO set that YOLO-family models are trained on)
    /// can put a tight box around, in its own label spelling -> our class names. "person" is left out:
    /// people are SessionPersons, not objects.
    static let detectorLabels: [String: String] = [
        "bicycle": "bicycle", "car": "car", "motorbike": "motorcycle", "motorcycle": "motorcycle", "aeroplane": "airplane",
        "airplane": "airplane", "bus": "bus", "train": "train", "truck": "truck", "boat": "boat",
        "traffic light": "traffic_light", "fire hydrant": "fire_hydrant", "stop sign": "stop_sign",
        "parking meter": "parking_meter", "bench": "bench", "bird": "bird", "cat": "cat", "dog": "dog", "horse": "horse",
        "sheep": "sheep", "cow": "cow", "elephant": "elephant", "bear": "bear", "zebra": "zebra", "giraffe": "giraffe",
        "backpack": "backpack", "umbrella": "umbrella", "handbag": "bag", "tie": "tie", "suitcase": "luggage",
        "frisbee": "frisbee", "skis": "skis", "snowboard": "snowboard", "sports ball": "ball", "kite": "kite",
        "baseball bat": "baseball_bat", "baseball glove": "baseball_glove", "skateboard": "skateboard",
        "surfboard": "surfboard", "tennis racket": "tennis_racket", "bottle": "bottle", "wine glass": "cup", "cup": "cup",
        "fork": "fork", "knife": "knife", "spoon": "spoon", "bowl": "bowl", "banana": "banana", "apple": "apple",
        "sandwich": "sandwich", "orange": "orange", "broccoli": "broccoli", "carrot": "carrot", "hot dog": "hot_dog",
        "pizza": "pizza", "donut": "donut", "cake": "cake", "chair": "chair", "sofa": "sofa", "couch": "sofa",
        "pottedplant": "plant", "potted plant": "plant", "bed": "bed", "diningtable": "table", "dining table": "table",
        "toilet": "toilet", "tvmonitor": "television", "tv": "television", "laptop": "laptop", "mouse": "mouse",
        "remote": "remote", "keyboard": "keyboard", "cell phone": "phone", "microwave": "microwave", "oven": "oven",
        "toaster": "toaster", "sink": "sink", "refrigerator": "refrigerator", "book": "book", "clock": "clock",
        "vase": "vase", "scissors": "scissors", "teddy bear": "toy", "hair drier": "hair_dryer", "toothbrush": "toothbrush",
    ]

    init?(detectorLabel: String) {
        guard let mapped = Self.detectorLabels[detectorLabel.lowercased()] else { return nil }
        name = mapped
    }

    /// Classes a detector boxes tightly: the region classifier defers to it for these.
    static let detectorClasses = Set(detectorLabels.values)

    /// Every class HERE can recognise, from either source.
    static let all: [ObjectClass] = Array(Set(visionVocabulary.map { canonical[$0] ?? $0 }).union(detectorClasses)).sorted()
        .map(ObjectClass.init(name:))

    init(name: String) { self.name = name }

    /// nil for anything the classifier names that isn't an everyday object.
    init?(visionIdentifier: String) {
        guard Self.accepted.contains(visionIdentifier) else { return nil }
        name = Self.canonical[visionIdentifier] ?? visionIdentifier
    }

    static let door = ObjectClass(name: "door"), chair = ObjectClass(name: "chair"), table = ObjectClass(name: "table")
    static let backpack = ObjectClass(name: "backpack"), laptop = ObjectClass(name: "laptop"), phone = ObjectClass(name: "phone")
    static let cup = ObjectClass(name: "cup"), television = ObjectClass(name: "television"), car = ObjectClass(name: "car")
    static let dog = ObjectClass(name: "dog"), monitor = ObjectClass(name: "monitor")

    /// Things that drive on roads, for matching a "car horn"-like sound.
    var isVehicle: Bool { ["car", "truck", "bus", "motorcycle", "firetruck", "ambulance", "bicycle", "scooter", "train"].contains(name) }

    var title: String { name.replacingOccurrences(of: "_", with: " ").capitalized }

    static let symbols: [String: String] = [
        "door": "door.left.hand.closed", "chair": "chair.fill", "table": "table.furniture.fill", "desk": "table.furniture.fill",
        "backpack": "backpack.fill", "bag": "bag.fill", "luggage": "suitcase.fill", "laptop": "laptopcomputer",
        "phone": "iphone", "cup": "cup.and.saucer.fill", "television": "tv.fill", "monitor": "display",
        "keyboard": "keyboard.fill", "car": "car.fill", "truck": "truck.box.fill", "bus": "bus.fill", "bicycle": "bicycle",
        "sofa": "sofa.fill", "bottle": "waterbottle.fill", "book": "book.fill", "window": "window.vertical.closed",
        "bed": "bed.double.fill", "dog": "dog.fill", "cat": "cat.fill", "bird": "bird.fill", "lamp": "lamp.desk.fill",
        "clock": "clock.fill", "refrigerator": "refrigerator.fill", "microwave": "microwave.fill", "oven": "oven.fill",
        "sink": "sink.fill", "trash_can": "trash.fill", "stairs": "stairs", "headphones": "headphones", "camera": "camera.fill",
        "printer": "printer.fill", "tree": "tree.fill", "umbrella": "umbrella.fill", "airplane": "airplane", "train": "tram.fill",
    ]

    var symbol: String { Self.symbols[name] ?? "cube.fill" }
}

/// One detector result from one scan: a class, roughly where, roughly how big.
struct ObjectObservation: Equatable {
    var cls: ObjectClass
    var confidence: Double
    var box: CGRect              // Vision normalized (bottom-left origin), upright frame
    var bearing: Double          // world degrees of the region's center
    var angularWidth: Double     // degrees it spans
    var precise = false          // a detector's tight box (true) or a classified region of the frame (false)
}

/// The same physical object in the room over time: silent or not, in view or not. It exists because
/// it was seen, not because it made a sound. On-device only.
struct SceneObject: Identifiable, Equatable {
    enum State: String { case visible, temporarilyLost, dormant, reacquired }

    let id: Int
    var cls: ObjectClass
    var confidence: Double            // smoothed classifier confidence
    var box: CGRect?                  // where it was in the frame the last time it was seen
    var worldBearing: Double          // smoothed over sightings
    var angularWidth: Double
    var approximateDistance: Double? = nil   // not measured: no depth source in this pipeline
    var firstSeenAt: Double
    var lastSeenAt: Double
    var lastExpectedAt: Double        // last time it should have been in view (seen or not)
    var sightings = 1
    var identityConfidence = 1.0      // score of the last reacquisition
    var associatedSoundID: String?    // the sound currently attributed to it
    var reacquiredAt: Double?
    var precise = false               // `box` is a tight detector box, not just the region it was named in

    /// Seen often enough to be believed.
    var isConfirmed: Bool { sightings >= Tunables.objectConfirmSightings }

    func state(now: Double, inView: Bool) -> State {
        let unseen = now - lastSeenAt
        if unseen <= Tunables.objectVisibleS {
            if let r = reacquiredAt, now - r <= Tunables.objectReacquiredFlagS { return .reacquired }
            return .visible
        }
        return inView && unseen <= Tunables.objectLostS ? .temporarilyLost : .dormant
    }
}

/// Is this observation an object we already know? Objects mostly stay put, so where it is carries
/// the most weight; class must agree; size and (when available) appearance refine it.
enum ObjectReID {
    static func score(observation o: ObjectObservation, object: SceneObject, visualSimilarity: Double? = nil) -> Double {
        guard o.cls == object.cls else { return 0 }
        // A tight box says where the object is; a classified strip only says roughly where.
        let slack = o.precise && object.precise ? Tunables.objectBearingTolerancePreciseDeg : Tunables.objectBearingToleranceDeg
        let tolerance = slack + max(o.angularWidth, object.angularWidth) / 2
        let bearing = max(0, 1 - abs(Geo.angDiff(o.bearing, object.worldBearing)) / tolerance)
        guard bearing > 0 else { return 0 }                       // same kind of thing somewhere else: a different one
        let big = max(o.angularWidth, object.angularWidth, 1e-6)
        let size = min(o.angularWidth, object.angularWidth) / big
        let look = visualSimilarity ?? bearing
        return Tunables.objectBearingWeight * bearing + Tunables.objectSizeWeight * size + Tunables.objectVisualWeight * look
    }

    /// Index into `objects` of the one this is, or nil for a new object. Two same-class objects side
    /// by side resolve to the nearer one: a duplicate would be worse than treating neighbours alike.
    static func match(_ o: ObjectObservation, among objects: [SceneObject], taken: Set<Int> = [],
                      similarity: (SceneObject) -> Double? = { _ in nil }) -> (index: Int, score: Double)? {
        objects.enumerated()
            .filter { !taken.contains($0.element.id) }
            .map { (index: $0.offset, score: score(observation: o, object: $0.element, visualSimilarity: similarity($0.element))) }
            .filter { $0.score >= Tunables.objectReacquireScore }
            .max { $0.score < $1.score }
    }
}

/// A thing an audio source might physically be: a known object or a person in view.
enum EntityRef: Hashable {
    case object(Int)
    case person(Int)
}

struct FusionCandidate: Equatable {
    var ref: EntityRef
    var bearing: Double
    var halfWidth: Double            // degrees it spans either side of its bearing
    var visible: Bool                // seen right now, not just remembered
    var spatialConfidence: Double    // 0...1: how sure HERE is of where it is
    var semantic = 0.0               // 0...1: optional, how plausibly this kind of sound comes from it
    var prior = false                // this source was already attributed to it
}

/// Audio says "something happened in this direction". Vision says "this is what is physically in
/// that direction". Fusion puts them together on spatial correspondence: which known person or
/// object occupies the direction the sound comes from. What kind of sound it is does not have to
/// make sense for the object; it only helps break a tie, as does an existing attribution. If several
/// things occupy the direction and nothing sets one apart, the sound stays unattached.
enum Fusion {
    /// 1 when the sound comes from the middle of the candidate, falling to 0 at the edge of tolerance.
    static func agreement(_ c: FusionCandidate, bearing: Double) -> Double {
        max(0, 1 - abs(Geo.angDiff(bearing, c.bearing)) / (Tunables.fusionBearingToleranceDeg + c.halfWidth))
    }

    static func score(_ c: FusionCandidate, bearing: Double) -> Double {
        let a = agreement(c, bearing: bearing)
        guard a > 0 else { return 0 }
        return a * (Tunables.fusionBearingWeight + (c.visible ? Tunables.fusionVisibilityWeight : 0)
                    + Tunables.fusionSpatialWeight * min(1, max(0, c.spatialConfidence))
                    + Tunables.fusionSemanticWeight * c.semantic)
            + (c.prior ? Tunables.fusionPriorWeight : 0)
    }

    static func associate(bearing: Double?, candidates: [FusionCandidate]) -> (ref: EntityRef, score: Double)? {
        guard let bearing else { return nil }
        let scored = candidates.map { (c: $0, s: score($0, bearing: bearing)) }.filter { $0.s > 0 }.sorted { $0.s > $1.s }
        guard let best = scored.first, best.s >= Tunables.fusionAcceptScore else { return nil }
        // Everything that really occupies this direction. More than one, and bearing alone cannot
        // say which: only something that sets one apart can -- it already owned this source, or the
        // kind of sound fits it and none of the others.
        let occupying = scored.filter { agreement($0.c, bearing: bearing) >= Tunables.fusionOccupyAgreement }
        if occupying.count > 1 {
            let owners = occupying.filter(\.c.prior)
            if owners.count == 1 { return (owners[0].c.ref, owners[0].s) }
            let fit = occupying.map(\.c.semantic).max() ?? 0
            let fitting = occupying.filter { $0.c.semantic == fit }
            if fit > 0, fitting.count == 1, fitting[0].s >= Tunables.fusionAcceptScore { return (fitting[0].c.ref, fitting[0].s) }
            return nil
        }
        if scored.count > 1, best.s - scored[1].s < Tunables.fusionAmbiguityMargin { return nil }
        return (best.c.ref, best.s)
    }

    /// Optional semantic hint, 0...1. Never required for an association, never enough on its own.
    static func compatibility(label: SoundLabel, text: String, object: ObjectClass) -> Double {
        switch label {
        case .knock, .doorbell, .door: return object == .door ? 1 : 0
        case .vehicle, .carHorn, .siren: return object.isVehicle ? 1 : 0
        case .phoneRinging: return object == .phone ? 1 : 0
        case .tv: return object == .television || object == .monitor ? 1 : 0
        case .music: return object == .television ? 0.6 : (object == .laptop || object == .phone || object.name == "speaker" ? 0.5 : 0)
        case .dogBarking: return object == .dog ? 1 : 0
        case .other:
            switch SoundIcon.keyword(text) {
            case "car.fill": return object.isVehicle ? 1 : 0
            case "door.left.hand.closed": return object == .door ? 1 : 0
            case "phone.fill": return object == .phone ? 1 : 0
            case "dog.fill": return object == .dog ? 1 : 0
            default: return 0
            }
        default: return 0
        }
    }

    static func compatibility(alert: AlertKind, object: ObjectClass) -> Double {
        switch alert {
        case .doorbell, .knock, .door: object == .door ? 1 : 0
        case .carHorn, .siren: object.isVehicle ? 1 : 0
        case .alarm: object.name == "alarm" ? 1 : 0
        case .dogBarking: object == .dog ? 1 : 0
        default: 0
        }
    }

    /// Candidates from the scene: confirmed objects (remembered ones count, with less weight than ones
    /// in view) and people currently in view (who can also be what's making a sound).
    static func candidates(objects: [SceneObject], faces: [TrackedFace], heading: Double, hfovDeg: Double, now: Double,
                           prior: EntityRef?, semantic: (ObjectClass) -> Double) -> [FusionCandidate] {
        let things = objects.filter(\.isConfirmed).map { o -> FusionCandidate in
            let inView = abs(Geo.relative(o.worldBearing, heading: heading)) < hfovDeg / 2
            let state = o.state(now: now, inView: inView)
            return FusionCandidate(ref: .object(o.id), bearing: o.worldBearing, halfWidth: o.angularWidth / 2,
                                   visible: inView && (state == .visible || state == .reacquired),
                                   spatialConfidence: min(1, o.confidence * Double(min(o.sightings, 5)) / 5 + 0.2),
                                   semantic: semantic(o.cls), prior: prior == .object(o.id))
        }
        let people = faces.map { f in
            FusionCandidate(ref: .person(f.personId ?? f.id), bearing: f.bearing, halfWidth: Tunables.fusionPersonHalfWidthDeg,
                            visible: true, spatialConfidence: 1, prior: prior == .person(f.personId ?? f.id))
        }
        return things + people
    }
}

/// The room's objects. Scans attach to known objects or create new ones; objects out of view stay
/// in memory and are found again when the wearer looks back. Thread-safe.
final class SceneObjects {
    private let lock = NSLock()
    private var objects: [SceneObject] = []
    private var nextId = 1

    private func locked<T>(_ body: () -> T) -> T {
        lock.lock()
        defer { lock.unlock() }
        return body()
    }

    var all: [SceneObject] { locked { objects } }

    /// One scan's results. `inView` says whether a world bearing is inside the camera's view right
    /// now, so an object that should be visible but wasn't found can lose confidence, while one the
    /// wearer simply isn't looking at keeps its place in memory.
    func observe(_ observations: [ObjectObservation], now: Double, inView: (Double) -> Bool,
                 similarity: (ObjectObservation, SceneObject) -> Double? = { _, _ in nil }) {
        locked {
            var taken = Set<Int>()
            for o in observations.sorted(by: { $0.confidence > $1.confidence }) {
                if let m = ObjectReID.match(o, among: objects, taken: taken, similarity: { similarity(o, $0) }) {
                    var object = objects[m.index]
                    if now - object.lastSeenAt > Tunables.objectVisibleS { object.reacquiredAt = now }
                    let k = Tunables.objectSmoothing
                    object.worldBearing = Geo.wrap(object.worldBearing + Geo.angDiff(o.bearing, object.worldBearing) * k)
                    object.angularWidth += (o.angularWidth - object.angularWidth) * k
                    object.confidence += (o.confidence - object.confidence) * k
                    // A tight box is never replaced by a coarse region, only refreshed by another tight one.
                    if o.precise || !object.precise { object.box = o.box }
                    object.precise = object.precise || o.precise
                    object.lastSeenAt = now
                    object.lastExpectedAt = now
                    object.sightings += 1
                    object.identityConfidence = m.score
                    objects[m.index] = object
                    taken.insert(object.id)
                } else {
                    objects.append(SceneObject(id: nextId, cls: o.cls, confidence: o.confidence, box: o.box,
                                               worldBearing: o.bearing, angularWidth: o.angularWidth,
                                               firstSeenAt: now, lastSeenAt: now, lastExpectedAt: now, precise: o.precise))
                    taken.insert(nextId)
                    nextId += 1
                }
            }
            // Out of view: remembered. In view but not found for a long time: it's gone (or was never
            // there: an unconfirmed single sighting goes quickly).
            objects = objects.compactMap { object in
                var o = object
                if !inView(o.worldBearing) { o.lastExpectedAt = now }
                let missing = now - max(o.lastSeenAt, o.lastExpectedAt)
                if missing > (o.isConfirmed ? Tunables.objectGoneS : Tunables.objectUnconfirmedGoneS) { return nil }
                if now - o.lastSeenAt > Tunables.objectExpiryS { return nil }
                return o
            }
        }
    }

    func link(sound: String?, to objectId: Int) { locked { if let i = objects.firstIndex(where: { $0.id == objectId }) { objects[i].associatedSoundID = sound } } }

    func unlinkAll(except linked: [Int: String]) {
        locked { for i in objects.indices { objects[i].associatedSoundID = linked[objects[i].id] } }
    }

    func reset() {
        locked {
            objects = []
            nextId = 1
        }
    }
}
