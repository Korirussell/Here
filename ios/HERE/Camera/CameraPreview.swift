import AVFoundation
import SwiftUI

/// Full-screen camera preview. The overlay is drawn in the same coordinate space, so a rect
/// converted by `Projector` lands exactly on the video.
struct CameraPreview: UIViewRepresentable {
    let camera: CameraController
    let onLayer: (AVCaptureVideoPreviewLayer) -> Void

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    func makeUIView(context: Context) -> PreviewView {
        let v = PreviewView()
        v.backgroundColor = .black
        camera.attach(previewLayer: v.previewLayer)
        onLayer(v.previewLayer)
        return v
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {}
}

/// Maps between the camera's upright frame, world bearings and screen points (SPEC §8, §14).
struct Projector {
    weak var layer: AVCaptureVideoPreviewLayer?
    var captureAngle: Double
    var hfovDeg: Double
    var heading: Double
    /// Screen size, for simulated faces (no camera) where we assume a 16:9 frame filling the width.
    var size: CGSize

    /// Vision bounding box (normalized, upright buffer, bottom-left origin) -> screen rect.
    func rect(forVisionBox box: CGRect) -> CGRect {
        let tl = Frames.topLeft(box)
        guard let layer, layer.session != nil else { return fallbackRect(tl) }
        return layer.layerRectConverted(fromMetadataOutputRect:
            Frames.sensorRect(fromUprightTopLeft: tl, captureAngle: captureAngle))
    }

    /// World bearing -> screen x, or nil when outside the camera's view.
    func x(forBearing bearing: Double) -> CGFloat? {
        guard let nx = Geo.normalizedX(bearing: bearing, hfovDeg: hfovDeg, heading: heading) else { return nil }
        guard let layer, layer.session != nil else { return fallbackRect(CGRect(x: nx, y: 0.5, width: 0, height: 0)).minX }
        let p = layer.layerPointConverted(fromCaptureDevicePoint:
            Frames.sensorPoint(fromUprightTopLeft: CGPoint(x: nx, y: 0.5), captureAngle: captureAngle))
        return p.x
    }

    /// Face center -> world bearing (upright frame; Vision's bottom-left origin doesn't change x).
    func bearing(forVisionBox box: CGRect) -> Double {
        Geo.bearing(normalizedX: Double(box.midX), hfovDeg: hfovDeg, heading: heading)
    }

    func relative(_ worldBearing: Double) -> Double { Geo.relative(worldBearing, heading: heading) }

    private func fallbackRect(_ tl: CGRect) -> CGRect {
        // Aspect-fill a 16:9 frame into `size`.
        let frameAspect = 16.0 / 9.0
        let w = max(size.width, size.height * frameAspect)
        let h = w / frameAspect
        let ox = (size.width - w) / 2, oy = (size.height - h) / 2
        return CGRect(x: ox + tl.minX * w, y: oy + tl.minY * h, width: tl.width * w, height: tl.height * h)
    }
}
