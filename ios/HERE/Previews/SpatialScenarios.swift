import CoreGraphics
import Foundation

/// A scripted scene for the Spatial V2 layer: who is in the room, what is said, which sounds happen
/// and how loud, and how the wearer turns. Deterministic, and needs no laptop, microphone, camera or
/// belt. Played by Settings > Developer, rendered by the SwiftUI previews, asserted by PresentationTests,
/// all through `ScenarioHarness`.
struct SpatialScenario: Identifiable, Equatable {
    struct Window: Equatable { var from: Double; var to: Double }
    struct Level: Equatable { var t: Double; var energy: Double }

    struct Person: Equatable {
        var name: String                     // ground truth, never shown: who this really is
        var bearing: Double                  // world degrees
        var present: [Window] = [Window(from: 0, to: .infinity)]   // in the room at all (else: walked out)
        var bodyOnly: [Window] = []          // face not detectable, body still is
        var faceScale = 1.0                  // < 1: far away, a small face
        var looksLike: String? = nil         // appearance nearly identical to this other person
        var known: String? = nil             // explicitly enrolled under this name
        var path: [Turn] = []                // they walk: world bearing over time (empty = stand at `bearing`)
        var hidden: [Window] = []            // in the room but not detectable at all (behind something/someone)
        var resets: [Double] = []            // moments Vision loses their track and has to start a new one

        func bearing(at t: Double) -> Double {
            guard let first = path.first else { return bearing }
            if t <= first.t { return first.heading }
            for (a, b) in zip(path, path.dropFirst()) where t <= b.t {
                return a.heading + (b.heading - a.heading) * (t - a.t) / max(b.t - a.t, 1e-6)
            }
            return path.last?.heading ?? bearing
        }
    }

    struct Speech: Equatable {
        var id: String
        var by: String? = nil                // nil: nobody in the scene (a voice with no known owner)
        var bearing: Double
        var text: String
        var start: Double
        var end: Double
        var loudness = 0.6
        var tone: Tone? = nil
        var source = 0                       // the laptop's stream / source id (two talkers at once: two streams)
    }

    struct Sound: Equatable {
        var id: String
        var label: SoundLabel
        var category: SoundCategory = .other
        var text: String
        var bearing: Double?
        var start: Double
        var end: Double
        var levels: [Level]                  // `sources` energy over time while it sounds (interpolated)
        var gaps: [Window] = []              // silences inside start...end (a dip that resumes)
        var confidence = 0.85

        init(_ id: String, _ label: SoundLabel, _ text: String, at bearing: Double?, from start: Double,
             to end: Double, energy: Double, category: SoundCategory = .other) {
            self.id = id; self.label = label; self.text = text; self.bearing = bearing
            self.start = start; self.end = end; self.category = category
            levels = [Level(t: start, energy: energy)]
        }

        func energy(at t: Double) -> Double {
            guard let first = levels.first else { return 0 }
            if t <= first.t { return first.energy }
            for (a, b) in zip(levels, levels.dropFirst()) where t <= b.t {
                return a.energy + (b.energy - a.energy) * (t - a.t) / max(b.t - a.t, 1e-6)
            }
            return levels.last?.energy ?? 0
        }

        func sounding(at t: Double) -> Bool {
            t >= start && t <= end + 1e-9 && !gaps.contains { t >= $0.from && t < $0.to }
        }
    }

    struct Alarm: Equatable {
        var id: String
        var kind: AlertKind
        var label: String
        var priority: Int
        var bearing: Double?
        var start: Double
        var end: Double
    }

    /// A quiet thing in the room. It is "seen" by the scripted scanner whenever it is in view.
    struct Thing: Equatable {
        var cls: ObjectClass
        var bearing: Double
        var width = 14.0                     // degrees it spans
        var present = Window(from: 0, to: .infinity)
        var hidden: [Window] = []            // still there, but something is in front of it
        var precise = true                   // found by the detector (tight box) rather than the region classifier
    }

    struct PinAction: Equatable { var name: String; var at: Double }
    struct Turn: Equatable { var t: Double; var heading: Double }

    let number: Int
    let name: String
    let summary: String
    var duration = 8.0
    var previewTime = 2.0
    var ambient = 0.05
    var people: [Person] = []
    var speech: [Speech] = []
    var sounds: [Sound] = []
    var alerts: [Alarm] = []
    var pins: [PinAction] = []
    var turns: [Turn] = [Turn(t: 0, heading: 0)]
    var reduceMotion = false
    var things: [Thing] = []

    var id: Int { number }

    func heading(at t: Double) -> Double {
        guard let first = turns.first else { return 0 }
        if t <= first.t { return first.heading }
        for (a, b) in zip(turns, turns.dropFirst()) where t <= b.t {
            return a.heading + (b.heading - a.heading) * (t - a.t) / max(b.t - a.t, 1e-6)
        }
        return turns.last?.heading ?? 0
    }
}

extension SpatialScenario {
    private static let a = Person(name: "A", bearing: 22)
    private static let b = Person(name: "B", bearing: -26)
    private static let away = [Turn(t: 1.5, heading: 0), Turn(t: 3.0, heading: -95)]
    private static let awayAndBack = [Turn(t: 1.5, heading: 0), Turn(t: 3.0, heading: -95), Turn(t: 7.0, heading: -95),
                                      Turn(t: 8.5, heading: 0)]

    private static func sound(_ id: String, _ label: SoundLabel, _ text: String, _ bearing: Double, _ from: Double,
                              _ to: Double, _ energy: Double) -> Sound {
        Sound(id, label, text, at: bearing, from: from, to: to, energy: energy)
    }

    static let all: [SpatialScenario] = [
        SpatialScenario(number: 1, name: "Quiet room", summary: "Two people, nobody talking. Nothing on screen.",
                        duration: 6, people: [a, b]),
        SpatialScenario(number: 2, name: "One weak environmental sound", summary: "A faint dog bark: dim, green, on the left.",
                        duration: 7, previewTime: 2.0, people: [a, b],
                        sounds: [sound("dog", .dogBarking, "Dog barking", -80, 0.5, 4, 0.14)]),
        SpatialScenario(number: 3, name: "One loud environmental sound", summary: "The same bark, loud: solid.",
                        duration: 7, previewTime: 2.0, people: [a, b],
                        sounds: [sound("dog", .dogBarking, "Dog barking", -80, 0.5, 4, 0.75)]),
        SpatialScenario(number: 4, name: "Weak and loud together", summary: "Both stay; only their opacity differs.",
                        duration: 7, previewTime: 2.0, people: [a, b],
                        sounds: [sound("weak", .dogBarking, "Dog barking", -80, 0.5, 4, 0.14),
                                 sound("loud", .knock, "Knock", 100, 0.5, 4, 0.75)]),
        SpatialScenario(number: 5, name: "Three sounds, three bearings", summary: "Footsteps left, door right, phone behind.",
                        duration: 8, previewTime: 2.5, people: [a, b],
                        sounds: [sound("steps", .other, "Footsteps", -90, 0.5, 5, 0.45),
                                 sound("door", .knock, "Door", 90, 0.8, 5, 0.6),
                                 sound("phone", .phoneRinging, "Phone ringing", 170, 1.1, 5, 0.5)]),
        SpatialScenario(number: 6, name: "Five sounds at once", summary: "All five show; the stack is full.",
                        duration: 8, previewTime: 2.8, people: [a, b],
                        sounds: [sound("steps", .other, "Footsteps", -90, 0.5, 5, 0.45),
                                 sound("door", .knock, "Door", 90, 0.7, 5, 0.6),
                                 sound("phone", .phoneRinging, "Phone ringing", 170, 0.9, 5, 0.5),
                                 sound("dog", .dogBarking, "Dog barking", -140, 1.1, 5, 0.55),
                                 sound("car", .other, "Car horn", 130, 1.3, 5, 0.7)]),
        {
            var s = sound("dog", .dogBarking, "Dog barking", -100, 0.5, 6, 0.15)
            s.levels = [Level(t: 0.5, energy: 0.15), Level(t: 5, energy: 0.8)]
            return SpatialScenario(number: 7, name: "Sound gets louder", summary: "Opacity rises with it, continuously.",
                                   duration: 8, previewTime: 4.5, people: [a, b], sounds: [s])
        }(),
        {
            var s = sound("dog", .dogBarking, "Dog barking", -100, 0.5, 6, 0.8)
            s.levels = [Level(t: 0.5, energy: 0.8), Level(t: 5, energy: 0.15)]
            return SpatialScenario(number: 8, name: "Sound gets quieter", summary: "Opacity falls with it; it does not leave early.",
                                   duration: 8, previewTime: 4.5, people: [a, b], sounds: [s])
        }(),
        SpatialScenario(number: 9, name: "Loud sound stops", summary: "Held briefly, fades, gone.",
                        duration: 7, previewTime: 3.4, people: [a, b],
                        sounds: [sound("s", .knock, "Knock", 100, 0.5, 3, 0.8)]),
        SpatialScenario(number: 10, name: "Quiet sound stops", summary: "Exactly the same timing as the loud one.",
                        duration: 7, previewTime: 3.4, people: [a, b],
                        sounds: [sound("s", .knock, "Knock", 100, 0.5, 3, 0.14)]),
        {
            var s = sound("phone", .phoneRinging, "Phone ringing", 150, 0.5, 6, 0.6)
            s.gaps = [Window(from: 2.5, to: 3.4)]
            return SpatialScenario(number: 11, name: "Dip, then resumes", summary: "A short silence doesn't remove it.",
                                   duration: 9, previewTime: 3.0, people: [a, b], sounds: [s])
        }(),
        SpatialScenario(number: 12, name: "Unknown sound", summary: "A question mark, neutral, with its direction.",
                        duration: 7, previewTime: 2.0, people: [a, b],
                        sounds: [sound("x", .other, "Sound", 120, 0.5, 4, 0.6)]),
        SpatialScenario(number: 13, name: "Alarm behind", summary: "Red, top of the stack, never faint.",
                        duration: 9, previewTime: 3.0, people: [a, b],
                        alerts: [Alarm(id: "alarm", kind: .alarm, label: "Alarm", priority: 3, bearing: 170, start: 1, end: 7)]),
        SpatialScenario(number: 14, name: "Unidentified speech offscreen", summary: "Blue voice notification, no floating caption.",
                        duration: 8, previewTime: 3.0, people: [a, b],
                        speech: [Speech(id: "v", bearing: 160, text: "Excuse me, is this seat taken?", start: 0.8, end: 4.5)]),
        SpatialScenario(number: 15, name: "Speech: blue, then a person's color",
                        summary: "Person A speaks, the wearer turns away, they speak again: now it's theirs.",
                        duration: 9, previewTime: 5.5, people: [a],
                        speech: [Speech(id: "s1", by: "A", bearing: 22, text: "I'll grab the door.", start: 0.4, end: 1.4),
                                 Speech(id: "s2", by: "A", bearing: 24, text: "Do you want anything from the kitchen?",
                                        start: 4.0, end: 7.0)],
                        turns: away),
        SpatialScenario(number: 16, name: "Visible speaker", summary: "Aura, speaking mark and caption on the person.",
                        duration: 13, previewTime: 3.0, people: [Person(name: "A", bearing: 0)],
                        speech: [Speech(id: "s", by: "A", bearing: 1, text: "Hey, did you see the email about the internship fair?",
                                        start: 0.8, end: 4.5, tone: .questioning)]),
        SpatialScenario(number: 17, name: "Speaker leaves view", summary: "Face caption becomes a top-right notification in their color.",
                        duration: 9, previewTime: 4.0, people: [a],
                        speech: [Speech(id: "s", by: "A", bearing: 22, text: "So what I was thinking is we could leave around six and still make it.",
                                        start: 0.5, end: 6.0)],
                        turns: away),
        SpatialScenario(number: 18, name: "Speaker returns", summary: "The notification resolves back into aura and caption.",
                        duration: 12, previewTime: 8.6, people: [a],
                        speech: [Speech(id: "s", by: "A", bearing: 22, text: "So what I was thinking is we could leave around six and still make it there before the doors open.",
                                        start: 0.5, end: 10.0)],
                        turns: awayAndBack),
        SpatialScenario(number: 19, name: "Pinned, visible", summary: "A quiet tag stays with pinned Person 2; soft speech still shows.",
                        duration: 14, previewTime: 2.0, people: [a, b],
                        speech: [Speech(id: "soft", by: "B", bearing: -26, text: "I'll be right back.", start: 4.0, end: 6.0,
                                        loudness: 0.15, tone: .whispering)],
                        pins: [PinAction(name: "B", at: 0.5)]),
        SpatialScenario(number: 20, name: "Pinned, offscreen", summary: "A person notification keeps their direction, then says when it no longer knows.",
                        duration: 16, previewTime: 5.0, people: [a], pins: [PinAction(name: "A", at: 0.4)], turns: away),
        SpatialScenario(number: 21, name: "Pinned, offscreen, speaking", summary: "Their captions continue inside the notification.",
                        duration: 10, previewTime: 6.0, people: [a],
                        speech: [Speech(id: "s", by: "A", bearing: 24, text: "Over here, when you have a second. I found the charger.",
                                        start: 4.0, end: 8.0)],
                        pins: [PinAction(name: "A", at: 0.4)], turns: away),
        SpatialScenario(number: 22, name: "Pinned person returns", summary: "Notification back to face caption and aura, still pinned.",
                        duration: 13, previewTime: 9.5, people: [a],
                        speech: [Speech(id: "s", by: "A", bearing: 24, text: "Over here, when you have a second. I found the charger under the couch.",
                                        start: 4.0, end: 11.0)],
                        pins: [PinAction(name: "A", at: 0.4)], turns: awayAndBack),
        SpatialScenario(number: 23, name: "Leaves and re-enters", summary: "Person 1 walks out and back: still Person 1.",
                        duration: 12, previewTime: 9.0,
                        people: [Person(name: "A", bearing: 22, present: [Window(from: 0, to: 2), Window(from: 8, to: .infinity)]), b]),
        SpatialScenario(number: 24, name: "Two people cross (ambiguous)",
                        summary: "Two lookalikes return at swapped spots: nobody is guessed, both get new identities.",
                        duration: 12, previewTime: 9.0,
                        people: [Person(name: "A", bearing: 22, present: [Window(from: 0, to: 2)]),
                                 Person(name: "B", bearing: -26, present: [Window(from: 0, to: 2)], looksLike: "A"),
                                 Person(name: "A", bearing: -2, present: [Window(from: 8, to: .infinity)]),
                                 Person(name: "B", bearing: 2, present: [Window(from: 8.2, to: .infinity)], looksLike: "A")]),
        SpatialScenario(number: 25, name: "Distant small face", summary: "Far away and small, still the same person with a caption.",
                        duration: 10, previewTime: 3.0, people: [Person(name: "A", bearing: 10, faceScale: 0.35)],
                        speech: [Speech(id: "s", by: "A", bearing: 11, text: "Can you hear me from over here?", start: 0.8, end: 4.0)]),
        SpatialScenario(number: 26, name: "Face lost, body remains", summary: "They turn around; the body keeps them Person 1.",
                        duration: 10, previewTime: 4.5,
                        people: [Person(name: "A", bearing: 15, bodyOnly: [Window(from: 2.5, to: 6.5)])],
                        speech: [Speech(id: "s", by: "A", bearing: 15, text: "Give me a second, I'm looking for it.", start: 3.0, end: 6.0)]),
        SpatialScenario(number: 27, name: "Known person recognized", summary: "Someone added to People comes in with their name and color.",
                        duration: 10, previewTime: 4.0,
                        people: [b, Person(name: "A", bearing: 22, present: [Window(from: 1.5, to: .infinity)], known: "Maya")],
                        speech: [Speech(id: "s", by: "A", bearing: 22, text: "Sorry I'm late!", start: 3.0, end: 5.0, tone: .excited)]),
        SpatialScenario(number: 28, name: "Danger + speech + environment", summary: "All three at once, each with its own color and direction.",
                        duration: 10, previewTime: 3.5, people: [a, b],
                        speech: [Speech(id: "v", bearing: -150, text: "Hey, hold the door!", start: 1.5, end: 5.0)],
                        sounds: [sound("steps", .other, "Footsteps", -90, 0.8, 6, 0.45)],
                        alerts: [Alarm(id: "alarm", kind: .alarm, label: "Alarm", priority: 3, bearing: 170, start: 1, end: 7)]),
        SpatialScenario(number: 29, name: "More sounds than places", summary: "Eight relevant sounds; five show, three wait and take over as places free up.",
                        duration: 12, previewTime: 2.8, people: [a, b],
                        sounds: [sound("s1", .knock, "Knock", 60, 0.5, 3.0, 0.7), sound("s2", .phoneRinging, "Phone ringing", 100, 0.5, 3.0, 0.65),
                                 sound("s3", .doorbell, "Doorbell", 140, 0.5, 3.0, 0.7), sound("s4", .dogBarking, "Dog barking", 180, 0.5, 8, 0.6),
                                 sound("s5", .other, "Footsteps", -140, 0.5, 8, 0.5), sound("s6", .other, "Car horn", -100, 0.5, 8, 0.45),
                                 sound("s7", .music, "Music", -60, 0.5, 8, 0.6), sound("s8", .tv, "TV", -75, 0.5, 8, 0.6)]),
        SpatialScenario(number: 30, name: "Reduce Motion", summary: "Scenario 18 with fades instead of travel.",
                        duration: 12, previewTime: 8.6, people: [a],
                        speech: [Speech(id: "s", by: "A", bearing: 22, text: "So what I was thinking is we could leave around six and still make it there before the doors open.",
                                        start: 0.5, end: 10.0)],
                        turns: awayAndBack, reduceMotion: true),

        // MARK: aura (with scenario 16: one centered speaker)
        SpatialScenario(number: 31, name: "Aura: person near the edge", summary: "The glow stays soft and whole at the edge of the frame.",
                        duration: 10, previewTime: 3.0, people: [Person(name: "A", bearing: 46)],
                        speech: [Speech(id: "s", by: "A", bearing: 46, text: "I'm over on this side.", start: 0.8, end: 5.0)]),
        SpatialScenario(number: 32, name: "Aura: two speakers", summary: "Two people talking at once, each in their own color.",
                        duration: 10, previewTime: 3.0, people: [a, b],
                        speech: [Speech(id: "s1", by: "A", bearing: 22, text: "No, I think it was Thursday.", start: 0.8, end: 5.0, loudness: 0.8),
                                 Speech(id: "s2", by: "B", bearing: -26, text: "It was definitely Friday.", start: 1.4, end: 5.0, loudness: 0.4)]),
        SpatialScenario(number: 33, name: "Aura: moving speaker", summary: "The glow and caption travel with them.",
                        duration: 10, previewTime: 3.5,
                        people: [Person(name: "A", bearing: 30, path: [Turn(t: 1, heading: 30), Turn(t: 6, heading: -30)])],
                        speech: [Speech(id: "s", by: "A", bearing: 30, text: "Let me just get past you here, sorry.", start: 0.8, end: 6.0)]),
        SpatialScenario(number: 34, name: "Aura: quiet speech", summary: "Low intensity: a fainter glow, a smaller pulse.",
                        duration: 10, previewTime: 3.0, people: [Person(name: "A", bearing: 0)],
                        speech: [Speech(id: "s", by: "A", bearing: 0, text: "Can you keep a secret?", start: 0.8, end: 5.0, loudness: 0.08,
                                        tone: .whispering)]),
        SpatialScenario(number: 35, name: "Aura: loud speech", summary: "High intensity: a stronger glow, still not aggressive.",
                        duration: 10, previewTime: 3.0, people: [Person(name: "A", bearing: 0)],
                        speech: [Speech(id: "s", by: "A", bearing: 0, text: "Hey! Over here!", start: 0.8, end: 5.0, loudness: 1.0, tone: .shouting)]),
        SpatialScenario(number: 36, name: "Aura: Reduce Motion", summary: "No pulsing; strength shows as opacity only.",
                        duration: 10, previewTime: 3.0, people: [Person(name: "A", bearing: 0)],
                        speech: [Speech(id: "s", by: "A", bearing: 0, text: "Hey! Over here!", start: 0.8, end: 5.0, loudness: 1.0)],
                        reduceMotion: true),

        // MARK: identity continuity (same physical person -> same Person N, same color)
        SpatialScenario(number: 37, name: "Identity: moving left to right", summary: "Vision loses the track twice on the way; still Person 1.",
                        duration: 9, previewTime: 5.0, people: [Person(name: "A", bearing: -35, path: [Turn(t: 1, heading: -35), Turn(t: 6, heading: 35)],
                                                                       resets: [2.5, 4.5])]),
        SpatialScenario(number: 38, name: "Identity: turning their head", summary: "Face gone, body still there, face back.",
                        duration: 9, previewTime: 4.0, people: [Person(name: "A", bearing: 10, bodyOnly: [Window(from: 2, to: 5)]), b]),
        SpatialScenario(number: 39, name: "Identity: briefly occluded", summary: "Hidden for a moment, back as themselves.",
                        duration: 9, previewTime: 5.0, people: [Person(name: "A", bearing: 10, hidden: [Window(from: 2, to: 3.5)]), b]),
        SpatialScenario(number: 40, name: "Identity: out left, back left", summary: "Walks out of frame and returns the same way.",
                        duration: 14, previewTime: 11.0,
                        people: [Person(name: "A", bearing: -20, path: [Turn(t: 1, heading: -20), Turn(t: 3, heading: -80), Turn(t: 8, heading: -80),
                                                                       Turn(t: 10, heading: -20)]), b]),
        SpatialScenario(number: 41, name: "Identity: out right, back right", summary: "The mirror case.",
                        duration: 14, previewTime: 11.0,
                        people: [Person(name: "A", bearing: 20, path: [Turn(t: 1, heading: 20), Turn(t: 3, heading: 80), Turn(t: 8, heading: 80),
                                                                      Turn(t: 10, heading: 20)]), b]),
        SpatialScenario(number: 42, name: "Identity: out one side, back the other", summary: "Bearing can't help; appearance does.",
                        duration: 16, previewTime: 14.0,
                        people: [Person(name: "A", bearing: -20, present: [Window(from: 0, to: 3), Window(from: 11, to: .infinity)],
                                        path: [Turn(t: 1, heading: -20), Turn(t: 3, heading: -70), Turn(t: 11, heading: 70), Turn(t: 13, heading: 25)]), b]),
        SpatialScenario(number: 43, name: "Identity: two people cross paths", summary: "Tracks reset mid-crossing; nobody swaps color.",
                        duration: 10, previewTime: 6.5,
                        people: [Person(name: "A", bearing: 28, path: [Turn(t: 1, heading: 28), Turn(t: 6, heading: -28)], resets: [3.5]),
                                 Person(name: "B", bearing: -28, path: [Turn(t: 1, heading: -28), Turn(t: 6, heading: 28)], resets: [3.5])]),
        SpatialScenario(number: 44, name: "Identity: two people overlap briefly", summary: "One passes behind the other and comes out as themselves.",
                        duration: 10, previewTime: 6.5,
                        people: [Person(name: "A", bearing: 5),
                                 Person(name: "B", bearing: -30, path: [Turn(t: 1, heading: -30), Turn(t: 6, heading: 35)],
                                        hidden: [Window(from: 3.2, to: 4.4)])]),
        SpatialScenario(number: 45, name: "Identity: leaves while still speaking", summary: "Caption on the face, then their notification, same color.",
                        duration: 10, previewTime: 5.0,
                        people: [Person(name: "A", bearing: 20, path: [Turn(t: 2, heading: 20), Turn(t: 4, heading: 90)])],
                        speech: [Speech(id: "s", by: "A", bearing: 20, text: "I'll be right back, I just need to grab something from the other room.",
                                        start: 0.5, end: 7.0)]),
        SpatialScenario(number: 46, name: "Identity: pinned person leaves", summary: "The pin goes with them into the top-right.",
                        duration: 9, previewTime: 5.0,
                        people: [Person(name: "A", bearing: 20, path: [Turn(t: 2, heading: 20), Turn(t: 4, heading: 90)])],
                        pins: [PinAction(name: "A", at: 0.5)]),
        SpatialScenario(number: 47, name: "Identity: pinned person returns", summary: "Back in frame: still pinned, still Person 1, same color.",
                        duration: 14, previewTime: 11.5,
                        people: [Person(name: "A", bearing: 20, path: [Turn(t: 2, heading: 20), Turn(t: 4, heading: 90), Turn(t: 8, heading: 90),
                                                                      Turn(t: 10, heading: 15)]), b],
                        pins: [PinAction(name: "A", at: 0.5)]),
        SpatialScenario(number: 48, name: "Identity: moves farther away", summary: "Small face, track lost and found; same person.",
                        duration: 9, previewTime: 5.0, people: [Person(name: "A", bearing: 8, faceScale: 0.35, resets: [2.0, 3.5, 5.0]), b]),
        SpatialScenario(number: 49, name: "Identity: face gone, body remains", summary: "Held by the body detection for as long as it's there.",
                        duration: 9, previewTime: 5.0, people: [Person(name: "A", bearing: 12, bodyOnly: [Window(from: 2, to: .infinity)]), b]),
        SpatialScenario(number: 50, name: "Identity: body continuity, face returns", summary: "Body-held, then a fresh face track on the same person.",
                        duration: 10, previewTime: 7.0,
                        people: [Person(name: "A", bearing: 12, bodyOnly: [Window(from: 2, to: 5)], resets: [5.0]), b]),
        SpatialScenario(number: 51, name: "Identity: in and out repeatedly", summary: "Three exits, three returns, one Person 1.",
                        duration: 16, previewTime: 14.0,
                        people: [Person(name: "A", bearing: 22, present: [Window(from: 0, to: 2), Window(from: 4, to: 6), Window(from: 9, to: 10.5),
                                                                         Window(from: 13, to: .infinity)]), b]),

        // MARK: scene objects (quiet things, and the sounds that turn out to be theirs)
        SpatialScenario(number: 52, name: "Objects: quiet room", summary: "A door, a chair and a phone are known. Nothing is drawn.",
                        duration: 6, previewTime: 4.0, people: [b],
                        things: [Thing(cls: .door, bearing: 38, width: 16), Thing(cls: .chair, bearing: -5), Thing(cls: .phone, bearing: 14, width: 5)]),
        SpatialScenario(number: 53, name: "Objects: look away and back", summary: "The door is still object #1 when it comes back into view.",
                        duration: 12, previewTime: 10.5,
                        turns: [Turn(t: 2.5, heading: 0), Turn(t: 4.5, heading: -120), Turn(t: 7.5, heading: -120), Turn(t: 9.5, heading: 0)],
                        things: [Thing(cls: .door, bearing: 38, width: 16), Thing(cls: .chair, bearing: -5)]),
        SpatialScenario(number: 54, name: "Objects: a knock at the known door",
                        summary: "Out of view it's \"Knock · Door\" top-right; turn back and it sits on the door.",
                        duration: 13, previewTime: 6.0,
                        sounds: [sound("knock", .knock, "Knock", 41, 5.0, 11.0, 0.6)],
                        turns: [Turn(t: 2.5, heading: 0), Turn(t: 4.0, heading: -100), Turn(t: 7.0, heading: -100), Turn(t: 8.5, heading: 0)],
                        things: [Thing(cls: .door, bearing: 38, width: 16), Thing(cls: .chair, bearing: -5)]),
        SpatialScenario(number: 55, name: "Objects: the phone rings", summary: "Ringing from where the phone is: indicated on the phone.",
                        duration: 10, previewTime: 5.0, people: [b],
                        sounds: [sound("ring", .phoneRinging, "Phone ringing", 16, 3.0, 8.0, 0.6)],
                        things: [Thing(cls: .phone, bearing: 14, width: 5), Thing(cls: .cup, bearing: 22, width: 4), Thing(cls: .chair, bearing: -30)]),
        SpatialScenario(number: 56, name: "Objects: unknown sound among several", summary: "It could be anything there: it stays unknown, unattached.",
                        duration: 10, previewTime: 5.0,
                        sounds: [sound("x", .other, "Sound", 100, 3.0, 8.0, 0.6)],
                        turns: [Turn(t: 2.5, heading: 100), Turn(t: 3.0, heading: 0)],
                        things: [Thing(cls: .door, bearing: 95, width: 16), Thing(cls: .chair, bearing: 105), Thing(cls: .backpack, bearing: 100, width: 8)]),
        SpatialScenario(number: 57, name: "Objects: speech is never an object", summary: "A voice from where the chair is stays a voice.",
                        duration: 10, previewTime: 5.5,
                        speech: [Speech(id: "v", bearing: 100, text: "Is anyone sitting here?", start: 4.0, end: 7.5)],
                        turns: [Turn(t: 2.5, heading: 100), Turn(t: 3.0, heading: 0)],
                        things: [Thing(cls: .chair, bearing: 100)]),
        SpatialScenario(number: 58, name: "Objects: two chairs", summary: "Two of the same thing far apart are two objects, each with its own id.",
                        duration: 6, previewTime: 4.0, things: [Thing(cls: .chair, bearing: -30), Thing(cls: .chair, bearing: 30)]),
        SpatialScenario(number: 59, name: "Objects: unknown sound, one object there",
                        summary: "HERE can't name the sound, but it knows what is in that direction: \"Sound · Door\".",
                        duration: 12, previewTime: 9.5,
                        sounds: [sound("x", .other, "Sound", 40, 5.0, 11.0, 0.6)],
                        turns: [Turn(t: 2.5, heading: 0), Turn(t: 4.0, heading: -100), Turn(t: 7.0, heading: -100), Turn(t: 8.5, heading: 0)],
                        things: [Thing(cls: .door, bearing: 38, width: 16), Thing(cls: .chair, bearing: -5)]),
        SpatialScenario(number: 60, name: "Objects: sound with nothing there", summary: "No known object in that direction: it stays a direction.",
                        duration: 9, previewTime: 4.5,
                        sounds: [sound("k", .knock, "Knock", -120, 3.0, 7.0, 0.6)],
                        things: [Thing(cls: .door, bearing: 38, width: 16), Thing(cls: .chair, bearing: -5)]),
        SpatialScenario(number: 61, name: "Full scene: person, door, chair, phone, table",
                        summary: "Two sounds at once, each attributed to its own entity; the rest stay silent and unseen.",
                        duration: 12, previewTime: 6.0, people: [Person(name: "A", bearing: -40)],
                        speech: [Speech(id: "s", by: "A", bearing: -40, text: "Can you get that?", start: 4.5, end: 7.0)],
                        sounds: [sound("knock", .knock, "Knock", 40, 3.5, 9.0, 0.6), sound("ring", .phoneRinging, "Phone ringing", 12, 4.0, 9.0, 0.5)],
                        things: [Thing(cls: .door, bearing: 38, width: 16), Thing(cls: .chair, bearing: -12), Thing(cls: .phone, bearing: 12, width: 5),
                                 Thing(cls: .table, bearing: 5, width: 10)]),
        SpatialScenario(number: 62, name: "Quiet scene, many objects", summary: "Chair, table, phone, door, laptop and a car are all known. Nothing is drawn.",
                        duration: 30, previewTime: 25.0, people: [b],
                        things: [Thing(cls: .chair, bearing: -40), Thing(cls: .table, bearing: -10, width: 20), Thing(cls: .phone, bearing: 8, width: 5),
                                 Thing(cls: .door, bearing: 40, width: 16), Thing(cls: .laptop, bearing: 20, width: 10), Thing(cls: .car, bearing: 30, width: 6)]),

        // MARK: sounding things glow
        SpatialScenario(number: 63, name: "Glow: dog barking", summary: "The dog in view glows green while it barks.",
                        duration: 10, previewTime: 5.0, people: [b],
                        sounds: [sound("bark", .dogBarking, "Dog barking", 21, 3.0, 7.0, 0.6)],
                        things: [Thing(cls: .dog, bearing: 20, width: 10), Thing(cls: .chair, bearing: -5)]),
        SpatialScenario(number: 64, name: "Glow: television", summary: "Sound from the TV's direction: the TV glows.",
                        duration: 10, previewTime: 5.0,
                        sounds: [sound("tv", .tv, "TV", -19, 3.0, 8.0, 0.5)],
                        things: [Thing(cls: .television, bearing: -20, width: 18), Thing(cls: ObjectClass(name: "sofa"), bearing: 25, width: 20)]),
        SpatialScenario(number: 65, name: "Glow: alarm on the wall",
                        summary: "A sounding alarm glows red, harder than ordinary sounds. (Scripted: no detector HERE has names a wall alarm yet.)",
                        duration: 11, previewTime: 5.0,
                        alerts: [Alarm(id: "alarm", kind: .alarm, label: "Alarm", priority: 3, bearing: 30, start: 3.0, end: 9.0)],
                        things: [Thing(cls: ObjectClass(name: "alarm"), bearing: 30, width: 6), Thing(cls: .door, bearing: -25, width: 16, precise: false)]),
        SpatialScenario(number: 66, name: "Glow: person, phone and alarm at once", summary: "Three sources, three glows, each its own color.",
                        duration: 12, previewTime: 6.0, people: [Person(name: "A", bearing: -35)],
                        speech: [Speech(id: "s", by: "A", bearing: -35, text: "Is that the fire alarm?", start: 4.5, end: 8.0)],
                        sounds: [sound("ring", .phoneRinging, "Phone ringing", 5, 3.5, 9.0, 0.5)],
                        alerts: [Alarm(id: "alarm", kind: .alarm, label: "Alarm", priority: 3, bearing: 35, start: 3.0, end: 9.5)],
                        things: [Thing(cls: .phone, bearing: 5, width: 5), Thing(cls: ObjectClass(name: "alarm"), bearing: 35, width: 6)]),
        SpatialScenario(number: 67, name: "Glow: sounding object hidden, then back",
                        summary: "Someone walks in front of the ringing phone: back to the top-right, then the same phone glows again.",
                        duration: 14, previewTime: 11.0,
                        sounds: [sound("ring", .phoneRinging, "Phone ringing", 12, 2.5, 13.0, 0.6)],
                        things: [Thing(cls: .phone, bearing: 12, width: 5, hidden: [Window(from: 5.0, to: 9.0)])]),
        SpatialScenario(number: 68, name: "Glow: quiet again", summary: "The ringing stops: the glow fades, the phone is still known, nothing is drawn.",
                        duration: 12, previewTime: 10.0, people: [b],
                        sounds: [sound("ring", .phoneRinging, "Phone ringing", 12, 2.5, 5.0, 0.6)],
                        things: [Thing(cls: .phone, bearing: 12, width: 5), Thing(cls: .door, bearing: 38, width: 16, precise: false)]),

        // MARK: speaker locks (who a speech source belongs to)
        SpatialScenario(number: 69, name: "Lock: someone walks in front of the speaker",
                        summary: "Person 1 keeps talking while Person 2 crosses in front of them. The words stay Person 1's.",
                        duration: 12, previewTime: 5.0,
                        people: [Person(name: "A", bearing: 15, hidden: [Window(from: 4.0, to: 6.0)]),
                                 Person(name: "B", bearing: -40, path: [Turn(t: 2.5, heading: -40), Turn(t: 4.0, heading: 15), Turn(t: 6.0, heading: 15),
                                                                         Turn(t: 7.5, heading: 45)])],
                        speech: [Speech(id: "s1", by: "A", bearing: 15, text: "So what I was saying is that we should leave by six.", start: 1.0, end: 4.6),
                                 Speech(id: "s2", by: "A", bearing: 15, text: "Otherwise we will hit all the traffic.", start: 5.0, end: 8.0)]),
        SpatialScenario(number: 70, name: "Lock: pause mid-thought", summary: "A pause between two sentences; someone else is standing nearer the sound. Still Person 1.",
                        duration: 11, previewTime: 6.0, people: [Person(name: "A", bearing: 20), Person(name: "B", bearing: 6)],
                        speech: [Speech(id: "s1", by: "A", bearing: 21, text: "Hey, can you", start: 1.0, end: 2.5),
                                 Speech(id: "s2", by: "A", bearing: 12, text: "grab that for me?", start: 4.0, end: 6.5)]),
        SpatialScenario(number: 71, name: "Lock: two people talking at once",
                        summary: "Two streams, two locks, two captions. One stopping does not disturb the other.",
                        duration: 13, previewTime: 4.0, people: [a, b],
                        speech: [Speech(id: "s1", by: "A", bearing: 22, text: "I think the earlier train is better because we get seats.", start: 1.0, end: 7.0, source: 0),
                                 Speech(id: "s2", by: "B", bearing: -26, text: "No, the later one is direct.", start: 2.0, end: 4.5, source: 1)]),
        SpatialScenario(number: 72, name: "Lock: two talking, a third crosses", summary: "Person 3 walks past both speakers. Neither caption moves to them.",
                        duration: 12, previewTime: 5.0,
                        people: [Person(name: "A", bearing: 25), Person(name: "B", bearing: -25, hidden: [Window(from: 4.2, to: 5.4)]),
                                 Person(name: "C", bearing: -50, path: [Turn(t: 3.0, heading: -50), Turn(t: 7.0, heading: 50)])],
                        speech: [Speech(id: "s1", by: "A", bearing: 25, text: "We could split the bill three ways.", start: 1.0, end: 8.0, source: 0),
                                 Speech(id: "s2", by: "B", bearing: -25, text: "Or I can just get this one.", start: 1.5, end: 8.0, source: 1)]),
    ]

    static func numbered(_ n: Int) -> SpatialScenario { all.first { $0.number == n }! }
}

/// Runs a `SpatialScenario` in fixed steps and produces exactly the state the app would hold at that
/// moment, using the app's own logic: identities from `SessionPeople` (re-identification with
/// scripted appearance), captions from `Attributor`, the scene from `PresentationCore`. Time starts at 0.
final class ScenarioHarness {
    struct Snapshot {
        var t = 0.0
        var heading = 0.0
        var faces: [TrackedFace] = []
        var captions: [String: CaptionState] = [:]
        var alerts: [AlertState] = []
        var sounds: [String: SoundTag] = [:]
        var sources: [Source] = []
        var scene = SpatialScene.empty
        var people: [SessionPerson] = []
        var objects: [SceneObject] = []

        var visibleCaptions: [CaptionState] { AppModel.newestPerSpeaker(captions.values) }
    }

    static let step = 1.0 / 15

    let scenario: SpatialScenario
    let hfovDeg: Double
    let people = SessionPeople()
    let sceneObjects = SceneObjects()
    let locks = SpeakerLocks()
    private let attributor = Attributor()
    private let core = PresentationCore()
    private var started = Set<String>()
    private var ended = Set<String>()
    private var firedPins = Set<Int>()
    private var tracks: [Int: Int] = [:]              // scenario person index -> current track id
    private var truth: [Int: String] = [:]            // SessionPerson id -> who they really are
    private(set) var faceTruth: [Int: String] = [:]   // current track id -> who is really on it

    func truthName(ofPerson id: Int) -> String? { truth[id] }
    private var pending: [Int: Double] = [:]          // track id -> since when its identity is undecided
    private var nextTrack = 100
    private var steps = 0
    private(set) var snapshot = Snapshot()

    init(_ scenario: SpatialScenario, hfovDeg: Double = Tunables.defaultHFOVUltraWide) {
        self.scenario = scenario
        self.hfovDeg = hfovDeg
    }

    @discardableResult
    func advance(to target: Double) -> Snapshot {
        while Double(steps + 1) * Self.step <= target + 1e-9 {
            steps += 1
            tick(Double(steps) * Self.step)
        }
        return snapshot
    }

    /// The SessionPerson currently standing in for a scripted person, if they've been seen.
    func sessionPerson(named name: String) -> SessionPerson? {
        people.all.first { truth[$0.id] == name }
    }

    private static func bind(_ c: inout CaptionState, faces: [TrackedFace], fallback: Speaker) {
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

    private func tick(_ now: Double) {
        var s = snapshot
        s.t = now
        s.heading = Geo.wrap(scenario.heading(at: now))

        // People -> Vision tracks. A person coming into view is a *new* track; who it is gets decided
        // by the same re-identification the live tracker uses, with appearance scripted.
        var faces: [TrackedFace] = []
        for (index, p) in scenario.people.enumerated() {
            let here = p.present.contains { now >= $0.from && now < $0.to } && !p.hidden.contains { now >= $0.from && now < $0.to }
            let bearing = p.bearing(at: now)
            guard here, let nx = Geo.normalizedX(bearing: bearing, hfovDeg: hfovDeg, heading: s.heading) else {
                if let old = tracks.removeValue(forKey: index) { people.detach(trackId: old, now: now); faceTruth[old] = nil; pending[old] = nil }
                continue
            }
            // Vision drops the track here: the next thing it sees of them is a brand-new track.
            if p.resets.contains(where: { $0 > now - Self.step && $0 <= now }), let old = tracks.removeValue(forKey: index) {
                people.detach(trackId: old, now: now)
                faceTruth[old] = nil
            }
            let looks: (SessionPerson) -> Double? = { candidate in
                guard let who = self.truth[candidate.id] else { return nil }
                if who == p.name { return 0.92 }
                let alike = self.scenario.people.contains { ($0.name == who && $0.looksLike == p.name) || ($0.name == p.name && $0.looksLike == who) }
                return alike ? 0.9 : 0.2
            }
            var decided: SessionPerson?
            if tracks[index] == nil {
                nextTrack += 1
                tracks[index] = nextTrack
                faceTruth[nextTrack] = p.name
                decided = people.attach(trackId: nextTrack, bearing: bearing, now: now, screenX: nx, mode: .wait, similarity: looks)
                if decided == nil { pending[nextTrack] = now }
            } else if let id = tracks[index], let since = pending[id] {
                decided = people.attach(trackId: id, bearing: bearing, now: now, screenX: nx,
                                        mode: now - since >= Tunables.reidPendingS ? .takeBest : .wait, similarity: looks)
                if decided != nil { pending[id] = nil }
            }
            if let person = decided {
                if truth[person.id] == nil {
                    truth[person.id] = p.name
                    if let name = p.known {       // scripted: the enrolled match succeeded
                        people.apply(KnownPerson(chosenName: name, preferredColor: 3, priority: .whenSpeaking), to: person.id)
                    }
                }
            }
            guard let trackId = tracks[index], pending[trackId] == nil else { continue }   // undecided: not shown as anyone
            let bodyOnly = p.bodyOnly.contains { now >= $0.from && now < $0.to }
            people.seen(trackId: trackId, bearing: bearing, viaBody: bodyOnly, now: now, screenX: nx)
            guard let person = people.person(trackId: trackId) else { continue }
            let w = 0.09 * p.faceScale, h = 0.16 * p.faceScale
            faces.append(TrackedFace(id: trackId, label: person.displayName, colorIndex: person.colorIndex,
                                     box: CGRect(x: nx - w / 2, y: 0.52, width: w, height: h), bearing: bearing,
                                     personId: person.id, viaBody: bodyOnly, confidence: bodyOnly ? 0.6 : 0.95,
                                     pixelHeight: h * 1080))
        }
        s.faces = faces
        let refs = faces.map { FaceRef(id: $0.id, label: $0.label, bearing: $0.bearing) }
        // The same relink the app does: a caption follows its person onto a new track.
        for (utt, c) in s.captions {
            guard case let .person(old, label) = c.speaker, !faces.contains(where: { $0.id == old }),
                  let now_ = faces.first(where: { $0.label == label }) else { continue }
            attributor.retarget(from: old, to: now_.id, label: now_.label)
            s.captions[utt]?.speaker = .person(id: now_.id, label: now_.label)
        }
        var sources = [Source(id: 0, bearingDeg: 0, energy: scenario.ambient, speech: false, confidence: 0.5)]

        // Speech: typing dots, then words arriving, then final -- the shape of speech_start + utterance.
        for script in scenario.speech where now >= script.start {
            var sp = script
            if let who = sp.by, let mover = scenario.people.first(where: { $0.name == who && !$0.path.isEmpty }) {
                sp.bearing = mover.bearing(at: now)           // their voice comes from wherever they are now
            }
            let first = started.insert("utt:\(sp.id)").inserted
            if first {
                let speaker = attributor.attribute(utt: sp.id, bearing: sp.bearing, faces: refs, now: now, provisional: true)
                s.captions[sp.id] = CaptionState(uttId: sp.id, speaker: speaker, bearing: sp.bearing, started: now, updated: now)
            }
            guard var c = s.captions[sp.id], !c.final else { continue }
            sources.append(Source(id: sources.count, bearingDeg: sp.bearing, energy: scenario.ambient + 0.5 * sp.loudness,
                                  speech: true, confidence: 0.8))
            let fresh = attributor.attribute(utt: sp.id, bearing: sp.bearing, faces: refs, now: now, provisional: now - sp.start < 0.4)
            c.speaker = fresh
            c.sourceId = sp.source
            if case .offCamera = fresh {
                c.ownerPersonId = people.heard(bearing: sp.bearing, sourceId: nil, now: now)?.id ?? c.ownerPersonId
            }
            // The same per-source speaker lock the app applies.
            var attributed: Int?
            if case let .person(id, _) = fresh { attributed = faces.first { $0.id == id }?.personId }
            c.speakerPersonId = locks.resolve(utt: sp.id, sourceId: sp.source, micBearing: sp.bearing, speaking: now < sp.end,
                                              candidates: faces.compactMap { f in f.personId.map { SpeakerCandidate(personId: $0, micBearing: f.bearing) } },
                                              attributed: attributed, now: now).personId
            Self.bind(&c, faces: faces, fallback: fresh)
            if now - sp.start >= 0.4 {
                let words = sp.text.split(separator: " ").map(String.init)
                let progress = min(1, (now - sp.start) / max(sp.end - sp.start, 1e-6))
                let committed = now >= sp.end ? words.count : min(words.count, Int(progress * Double(words.count)))
                c.text = words.prefix(committed).joined(separator: " ")
                c.pending = now >= sp.end ? "" : words.dropFirst(committed).prefix(2).joined(separator: " ")
                c.final = now >= sp.end
                c.tone = sp.tone
                c.toneConfidence = 0.85
            }
            c.loudness = sp.loudness
            c.bearing = sp.bearing
            c.updated = now
            s.captions[sp.id] = c
        }
        locks.expire(now: now)
        let changed = attributor.refresh(faces: refs, bearings: s.captions.mapValues(\.bearing), now: now)
        for (utt, var c) in s.captions {
            if c.speakerPersonId != nil {
                Self.bind(&c, faces: faces, fallback: c.speaker)
            } else if let speaker = changed[utt] {
                if case .offCamera = speaker, case let .person(_, label) = c.speaker {
                    c.ownerPersonId = people.all.first { $0.displayName == label }?.id
                }
                var attributed: Int?
                if case let .person(id, _) = speaker { attributed = faces.first { $0.id == id }?.personId }
                if !c.final || attributed != nil {
                    c.speakerPersonId = locks.resolve(utt: utt, sourceId: c.sourceId ?? 0, micBearing: c.bearing, speaking: !c.final,
                                                      candidates: faces.compactMap { f in f.personId.map { SpeakerCandidate(personId: $0, micBearing: f.bearing) } },
                                                      attributed: attributed, heardNow: false, now: now).personId
                }
                Self.bind(&c, faces: faces, fallback: speaker)
            }
            s.captions[utt] = c
        }
        for (utt, c) in s.captions where c.final && now - c.updated > Tunables.captionHoldS {
            s.captions[utt] = nil
            attributor.forget(utt)
        }

        // Sounds: one tag per event id, refreshed for as long as it is sounding, like repeated `sound_event`s.
        for so in scenario.sounds where now >= so.start {
            let sounding = so.sounding(at: now)
            if sounding, let b = so.bearing {
                sources.append(Source(id: sources.count, bearingDeg: b, energy: so.energy(at: now), speech: false, confidence: 0.8))
            }
            if started.insert("snd:\(so.id)").inserted {
                s.sounds[so.id] = SoundTag(eventId: so.id, label: so.label, category: so.category, emoji: "",
                                           text: so.text, bearing: so.bearing, count: 1, updated: now,
                                           confidence: so.confidence)
            } else if sounding {
                s.sounds[so.id]?.updated = now
            }
        }
        s.sounds = s.sounds.filter { now - $0.value.updated < 6 }

        for al in scenario.alerts where now >= al.start {
            if started.insert("alr:\(al.id)").inserted {
                s.alerts.append(AlertState(alertId: al.id, kind: al.kind, label: al.label, emoji: "", priority: al.priority,
                                           bearing: al.bearing, active: true, count: 1, updated: now, lastBuzz: now))
            }
            if now >= al.end, ended.insert("alr:\(al.id)").inserted, let i = s.alerts.firstIndex(where: { $0.alertId == al.id }) {
                s.alerts[i].active = false
                s.alerts[i].updated = now
            }
            if now < al.end, let bearing = al.bearing {
                sources.append(Source(id: sources.count, bearingDeg: bearing, energy: 0.7, speech: false, confidence: 0.8))
            }
        }
        s.alerts.removeAll { !$0.active && now - $0.updated > Tunables.alertFadeS + 1 }

        for (i, action) in scenario.pins.enumerated() where now >= action.at {
            guard let person = sessionPerson(named: action.name), firedPins.insert(i).inserted else { continue }
            people.setPinned(true, personId: person.id)
        }

        // The scripted object scan: once a second, whatever is in view is observed (nothing else is).
        if steps % Int((Tunables.objectScanIntervalS / Self.step).rounded()) == 0 {
            let half = hfovDeg / 2
            let seenNow = scenario.things.filter { thing in
                now >= thing.present.from && now < thing.present.to && !thing.hidden.contains { now >= $0.from && now < $0.to }
                    && abs(Geo.relative(thing.bearing, heading: s.heading)) < half - thing.width / 2
            }
                .compactMap { thing -> ObjectObservation? in
                    guard let l = Geo.normalizedX(bearing: thing.bearing - thing.width / 2, hfovDeg: hfovDeg, heading: s.heading),
                          let r = Geo.normalizedX(bearing: thing.bearing + thing.width / 2, hfovDeg: hfovDeg, heading: s.heading) else { return nil }
                    let height = min(0.5, (r - l) * 16 / 9 * 1.2)      // roughly as tall as it is wide, on a 16:9 frame
                    return ObjectObservation(cls: thing.cls, confidence: 0.8, box: CGRect(x: l, y: 0.45 - height / 2, width: r - l, height: height),
                                             bearing: thing.bearing, angularWidth: thing.width, precise: thing.precise)
                }
            sceneObjects.observe(seenNow, now: now) { abs(Geo.relative($0, heading: s.heading)) < half }
        }
        s.objects = sceneObjects.all

        s.sources = sources
        s.people = people.all
        core.ingest(sources: sources, now: now)
        s.scene = core.step(now: now, heading: s.heading, hfovDeg: hfovDeg, captions: s.visibleCaptions,
                            alerts: s.alerts, sounds: s.sounds.values.sorted { $0.eventId < $1.eventId },
                            faces: faces, people: s.people, objects: s.objects, sources: sources, focusMode: false)
        sceneObjects.unlinkAll(except: s.scene.objectLinks)
        snapshot = s
    }
}
