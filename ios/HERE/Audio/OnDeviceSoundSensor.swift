import AVFoundation
import Foundation
import SoundAnalysis

/// The phone's own sound classifier: a second sensor that has to agree with the laptop's `sound_event`
/// before a card escalates. Apple's built-in classifier (SNClassifySoundRequest .version1) on the phone's
/// mic; audio never leaves the phone, only the label and confidence are compared locally.
///
/// Not started by AppModel yet: call `start()` and check `agrees(with:)` when a `sound_event` arrives.
struct OnDeviceSound {
    let label: String       // Apple's identifier, e.g. "door_bell", "speech", "car_horn"
    let confidence: Double
    let uptime: TimeInterval
}

final class OnDeviceSoundSensor: NSObject, SNResultsObserving {
    /// Contract sound labels -> Apple classifier identifiers that count as agreement.
    static let agreeing: [String: Set<String>] = [
        "doorbell": ["door_bell", "ding_dong", "chime"],
        "knock": ["knock"],
        "alarm": ["alarm_clock", "smoke_detector", "fire_alarm"],
        "siren": ["siren", "police_siren", "ambulance_siren", "fire_engine_siren"],
        "car_horn": ["car_horn", "vehicle_horn"],
        "dog_barking": ["dog_bark", "dog"],
        "baby_crying": ["baby_crying"],
        "phone_ringing": ["telephone_bell_ringing", "ringtone"],
    ]
    static let minConfidence = 0.5
    static let windowS: TimeInterval = 3.0

    private let engine = AVAudioEngine()
    private var analyzer: SNAudioStreamAnalyzer?
    private let queue = DispatchQueue(label: "here.sound-analysis")
    private var recent: [OnDeviceSound] = []

    var onSound: ((OnDeviceSound) -> Void)?

    func start() throws {
        let format = engine.inputNode.outputFormat(forBus: 0)
        let analyzer = SNAudioStreamAnalyzer(format: format)
        try analyzer.add(SNClassifySoundRequest(classifierIdentifier: .version1), withObserver: self)
        self.analyzer = analyzer
        engine.inputNode.installTap(onBus: 0, bufferSize: 8192, format: format) { [weak self] buffer, time in
            self?.queue.async { analyzer.analyze(buffer, atAudioFramePosition: time.sampleTime) }
        }
        try engine.start()
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        analyzer = nil
    }

    /// True when the phone heard a matching sound within the last `windowS` seconds.
    func agrees(with contractLabel: String, now: TimeInterval = ProcessInfo.processInfo.systemUptime) -> Bool {
        guard let ids = Self.agreeing[contractLabel] else { return false }
        return queue.sync {
            recent.contains { ids.contains($0.label) && now - $0.uptime <= Self.windowS }
        }
    }

    func request(_ request: SNRequest, didProduce result: SNResult) {
        guard let result = result as? SNClassificationResult,
              let top = result.classifications.first, top.confidence >= Self.minConfidence else { return }
        let now = ProcessInfo.processInfo.systemUptime
        let sound = OnDeviceSound(label: top.identifier, confidence: top.confidence, uptime: now)
        recent.append(sound)
        recent.removeAll { now - $0.uptime > Self.windowS }
        onSound?(sound)
    }
}
