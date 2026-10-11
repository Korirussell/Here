import AVFoundation
import UIKit

/// The glasses' camera (README §14 pipeline): back ultrawide, landscape, 30 fps, 1080p or 720p.
/// The preview layer shows video directly (frames never pass through our code for display); the data
/// output hands upright pixel buffers to Vision on its own queue, dropping late frames.
///
/// Orientation: `AVCaptureDevice.RotationCoordinator` gives the horizon-level angles for both the
/// preview and the data output, so whichever way the phone sits in the landscape mount, the preview
/// and the Vision buffers are upright. `captureAngle` is kept so face rects can be mapped back.
final class CameraController: NSObject, ObservableObject {
    enum Quality { case hd1080, hd720 }

    let session = AVCaptureSession()
    @Published private(set) var hfovDeg: Double = Tunables.defaultHFOVUltraWide
    /// Field of view across the sensor's *other* axis (height when `hfovDeg` is measured across
    /// width, i.e. the format's native landscape orientation) -- the one that actually applies to
    /// bearing-from-pixel math when the phone is mounted in portrait (`Geo.effectiveHFOVDeg`).
    @Published private(set) var vfovDeg: Double = Tunables.defaultVFOVUltraWide
    @Published private(set) var error: String?
    @Published private(set) var running = false
    @Published private(set) var lensName = ""

    /// Rotation applied to the data output's buffers (degrees, clockwise). Read on any thread.
    private(set) var captureAngle: Double = 0

    /// Called on the capture queue with each upright frame. Return quickly (hand off to Vision).
    var onFrame: ((CVPixelBuffer, CMTime) -> Void)?

    private let output = AVCaptureVideoDataOutput()
    private let captureQueue = DispatchQueue(label: "here.camera.capture", qos: .userInteractive)
    private let sessionQueue = DispatchQueue(label: "here.camera.session")
    private var device: AVCaptureDevice?
    private var coordinator: AVCaptureDevice.RotationCoordinator?
    private var observations: [NSKeyValueObservation] = []
    private weak var previewLayer: AVCaptureVideoPreviewLayer?
    var quality: Quality = .hd1080
    /// Production is always `.back` (the chest-mounted "glasses" camera, README §5/§14). `.front` exists
    /// only for solo on-device testing (Settings > Developer > "Use front camera"), so you can see
    /// yourself and the screen at the same time without a mount or a second person.
    var position: AVCaptureDevice.Position = .back

    func start() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            sessionQueue.async { self.configure() }
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { granted in
                if granted { self.sessionQueue.async { self.configure() } } else { self.fail("Camera access denied") }
            }
        default:
            fail("Camera access denied: Settings > Privacy > Camera > HERE")
        }
    }

    func stop() {
        sessionQueue.async {
            self.session.stopRunning()
            DispatchQueue.main.async { self.running = false }
        }
    }

    /// Called by CameraPreview once its layer exists.
    func attach(previewLayer: AVCaptureVideoPreviewLayer) {
        previewLayer.session = session
        previewLayer.videoGravity = .resizeAspectFill
        self.previewLayer = previewLayer
        DispatchQueue.main.async { self.makeCoordinator() }
    }

    private func fail(_ message: String) {
        DispatchQueue.main.async { self.error = message }
    }

    private func configure() {
        // Front cameras don't have an ultrawide option on any current iPhone, so only try it on the back.
        let device = (position == .back ? AVCaptureDevice.default(.builtInUltraWideCamera, for: .video, position: .back) : nil)
            ?? AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: position)
        guard let device, let input = try? AVCaptureDeviceInput(device: device) else {
            fail("No \(position == .front ? "front" : "back") camera (Simulator? turn on Settings > Simulated faces)")
            return
        }
        self.device = device

        session.beginConfiguration()
        session.inputs.forEach { session.removeInput($0) }
        session.outputs.forEach { session.removeOutput($0) }
        let preset: AVCaptureSession.Preset = quality == .hd1080 ? .hd1920x1080 : .hd1280x720
        session.sessionPreset = session.canSetSessionPreset(preset) ? preset : .high
        guard session.canAddInput(input) else {
            session.commitConfiguration()
            fail("Can't use the camera")
            return
        }
        session.addInput(input)

        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarFullRange]
        output.setSampleBufferDelegate(self, queue: captureQueue)
        guard session.canAddOutput(output) else {
            session.commitConfiguration()
            fail("Can't read camera frames")
            return
        }
        session.addOutput(output)
        session.commitConfiguration()

        do {
            try device.lockForConfiguration()
            let frame = CMTime(value: 1, timescale: Tunables.fps)
            if device.activeFormat.videoSupportedFrameRateRanges.contains(where: { $0.maxFrameRate >= Double(Tunables.fps) }) {
                device.activeVideoMinFrameDuration = frame
                device.activeVideoMaxFrameDuration = frame
            }
            device.unlockForConfiguration()
        } catch {
            fail("Camera frame rate: \(error.localizedDescription)")
        }

        // The format's horizontal field of view (landscape sensor = horizontal). Settings can override.
        let fov = Double(device.activeFormat.videoFieldOfView)
        // Vertical FOV isn't reported directly; derive it from the format's actual pixel aspect ratio
        // (the standard pinhole relationship along each axis) so a portrait mount uses the sensor's
        // real narrower angle of view, not a guess.
        let dims = CMVideoFormatDescriptionGetDimensions(device.activeFormat.formatDescription)
        let aspect = dims.width > 0 ? Double(dims.height) / Double(dims.width) : 9.0 / 16.0
        let vfov = 2 * atan(tan(fov / 2 * .pi / 180) * aspect) * 180 / .pi
        DispatchQueue.main.async {
            if fov > 10 { self.hfovDeg = fov; self.vfovDeg = vfov }
            self.lensName = device.deviceType == .builtInUltraWideCamera ? "ultrawide" : "wide"
        }
        DispatchQueue.main.async { self.makeCoordinator() }
        session.startRunning()
        DispatchQueue.main.async { self.running = self.session.isRunning }
    }

    /// Main thread. Rebuilt when the device or the preview layer changes (the camera can start
    /// before the view exists).
    private func makeCoordinator() {
        guard let device else { return }
        if let c = coordinator, c.device === device, c.previewLayer === previewLayer { return }
        let c = AVCaptureDevice.RotationCoordinator(device: device, previewLayer: previewLayer)
        coordinator = c
        observations = [
            c.observe(\.videoRotationAngleForHorizonLevelPreview, options: [.initial, .new]) { [weak self] c, _ in
                let angle = c.videoRotationAngleForHorizonLevelPreview
                DispatchQueue.main.async {
                    if let conn = self?.previewLayer?.connection, conn.isVideoRotationAngleSupported(angle) {
                        conn.videoRotationAngle = angle
                    }
                }
            },
            c.observe(\.videoRotationAngleForHorizonLevelCapture, options: [.initial, .new]) { [weak self] c, _ in
                let angle = c.videoRotationAngleForHorizonLevelCapture
                self?.sessionQueue.async {
                    guard let self, let conn = self.output.connection(with: .video) else { return }
                    if conn.isVideoRotationAngleSupported(angle) {
                        conn.videoRotationAngle = angle
                        self.captureAngle = Double(angle)
                    }
                }
            },
        ]
    }
}

extension CameraController: AVCaptureVideoDataOutputSampleBufferDelegate {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let pb = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        onFrame?(pb, CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
    }
}
