import CoreML
import CoreVideo
import Foundation
import Vision

/// Finds quiet, stationary things in the room with Apple's built-in vision only (no added model):
/// `VNClassifyImageRequest` names what is in a region, so each scan classifies a row of overlapping
/// vertical strips plus the regions Vision's objectness saliency picks out. A named region becomes an
/// `ObjectObservation` (class, direction, rough size). Runs about once a second on its own queue and
/// drops frames while busy; `SceneObjects` turns observations into persistent objects.
///
/// This localizes to a region, not a tight box: enough for "there is a door about 35 degrees to the
/// right", not for outlining it.
final class ObjectScanner {
    var enabled = true
    var interval = Tunables.objectScanIntervalS
    var onObservations: (([ObjectObservation], Double) -> Void)?
    /// Called on the scan queue with (scan duration in ms, regions classified): for diagnostics.
    var onTiming: ((Double, Int) -> Void)?

    /// A bundled object-detection model, if the app was built with one (any `.mlmodel` whose name
    /// starts with "YOLO" dropped into `HERE/Models/`). With it, objects get tight boxes several times
    /// a second and the region classifier only fills in what the detector's classes lack (doors,
    /// windows...). Without it, everything below still works from the classifier alone.
    private(set) lazy var detector: VNCoreMLModel? = {
        let urls = Bundle.main.urls(forResourcesWithExtension: "mlmodelc", subdirectory: nil) ?? []
        guard let url = urls.first(where: { $0.lastPathComponent.uppercased().hasPrefix("YOLO") }) else { return nil }
        let config = MLModelConfiguration()
        config.computeUnits = .all
        guard let model = try? MLModel(contentsOf: url, configuration: config), let vision = try? VNCoreMLModel(for: model) else { return nil }
        detectorName = url.deletingPathExtension().lastPathComponent
        return vision
    }()
    private(set) var detectorName: String?
    private var scans = 0

    private let queue = DispatchQueue(label: "here.objects", qos: .utility)
    private let lock = NSLock()
    private var busy = false
    private var lastScan = -Double.infinity

    func submit(_ pixelBuffer: CVPixelBuffer, heading: Double, hfovDeg: Double, now: Double) {
        lock.lock()
        let skip = !enabled || busy || now - lastScan < (detector == nil ? interval : Tunables.objectDetectIntervalS)
        if !skip {
            busy = true
            lastScan = now
        }
        lock.unlock()
        if skip { return }
        queue.async {
            let started = CFAbsoluteTimeGetCurrent()
            let (observations, regions) = self.scan(pixelBuffer, heading: heading, hfovDeg: hfovDeg)
            self.onTiming?((CFAbsoluteTimeGetCurrent() - started) * 1000, regions)
            self.onObservations?(observations, now)
            self.lock.lock(); self.busy = false; self.lock.unlock()
        }
    }

    private func scan(_ pb: CVPixelBuffer, heading: Double, hfovDeg: Double) -> ([ObjectObservation], Int) {
        let handler = VNImageRequestHandler(cvPixelBuffer: pb, orientation: .up, options: [:])
        scans += 1
        var boxed: [ObjectObservation] = []
        if let detector {
            let request = VNCoreMLRequest(model: detector)
            request.imageCropAndScaleOption = .scaleFill
            if (try? handler.perform([request])) != nil {
                for result in (request.results as? [VNRecognizedObjectObservation]) ?? [] {
                    guard let top = result.labels.first, Double(top.confidence) >= Tunables.objectDetectorMinConfidence,
                          let cls = ObjectClass(detectorLabel: top.identifier) else { continue }
                    var o = Self.observation(cls, Double(top.confidence), result.boundingBox, heading: heading, hfovDeg: hfovDeg)
                    o.precise = true
                    boxed.append(o)
                }
            }
            // The classifier pass is the expensive one and now only a supplement: run it less often.
            if scans % Tunables.objectClassifierEveryScans != 0 { return (boxed, 1) }
        }
        let hasDetector = detector != nil
        var regions = Self.strips()
        let saliency = VNGenerateObjectnessBasedSaliencyImageRequest()
        if (try? handler.perform([saliency])) != nil,
           let salient = (saliency.results?.first as? VNSaliencyImageObservation)?.salientObjects {
            regions += salient.prefix(Tunables.objectSalientRegions).map(\.boundingBox)
        }
        var found: [ObjectObservation] = []
        for region in regions where region.width > 0.03 && region.height > 0.03 {
            let request = VNClassifyImageRequest()
            request.regionOfInterest = region
            guard (try? handler.perform([request])) != nil else { continue }
            var seen = Set<ObjectClass>()
            for result in (request.results ?? []).prefix(Tunables.objectLabelsConsidered) {
                guard Double(result.confidence) >= Tunables.objectMinConfidence,
                      let cls = ObjectClass(visionIdentifier: result.identifier), seen.insert(cls).inserted,
                      !(hasDetector && ObjectClass.detectorClasses.contains(cls.name)) else { continue }   // the detector does these better
                found.append(Self.observation(cls, Double(result.confidence), region, heading: heading, hfovDeg: hfovDeg))
            }
        }
        return (boxed + Self.merge(found, heading: heading, hfovDeg: hfovDeg), regions.count + (hasDetector ? 1 : 0))
    }

    /// Overlapping full-height strips across the frame.
    static func strips() -> [CGRect] {
        let n = Tunables.objectStripCount
        let width = Tunables.objectStripWidth
        guard n > 1 else { return [CGRect(x: 0, y: 0, width: 1, height: 1)] }
        return (0..<n).map { i in CGRect(x: (1 - width) * CGFloat(i) / CGFloat(n - 1), y: 0, width: width, height: 1) }
    }

    static func observation(_ cls: ObjectClass, _ confidence: Double, _ box: CGRect, heading: Double, hfovDeg: Double) -> ObjectObservation {
        let left = Geo.bearing(normalizedX: Double(box.minX), hfovDeg: hfovDeg, heading: heading)
        let right = Geo.bearing(normalizedX: Double(box.maxX), hfovDeg: hfovDeg, heading: heading)
        return ObjectObservation(cls: cls, confidence: confidence, box: box,
                                 bearing: Geo.bearing(normalizedX: Double(box.midX), hfovDeg: hfovDeg, heading: heading),
                                 angularWidth: abs(Geo.angDiff(right, left)))
    }

    /// The same class named in overlapping regions is one object: keep the tightest, most confident
    /// region as where it is (a salient-object box beats a strip).
    static func merge(_ found: [ObjectObservation], heading: Double, hfovDeg: Double) -> [ObjectObservation] {
        var out: [ObjectObservation] = []
        for o in found.sorted(by: { ($0.box.width, -$0.confidence) < ($1.box.width, -$1.confidence) }) {
            if let i = out.firstIndex(where: { $0.cls == o.cls && $0.box.insetBy(dx: -0.02, dy: 0).intersects(o.box) }) {
                out[i].confidence = max(out[i].confidence, o.confidence)
            } else {
                out.append(o)
            }
        }
        return out
    }
}
