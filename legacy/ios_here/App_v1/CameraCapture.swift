import AVFoundation
import CoreImage
import Foundation

struct FrameSample {
    let frameId: Int
    let width: Int
    let height: Int
    let jpegData: Data
    let captureUptime: TimeInterval // CMSampleBuffer presentation time, uptime domain
}

/// Captures from the back camera and delivers upright JPEG frames
/// (HERE_PLAN.md §8.5, §4). The phone is chest-mounted **portrait, upside
/// down** in the harness; we compensate by setting the capture connection's
/// orientation to `.portraitUpsideDown`, which is the standard trick for a
/// camera in a known, fixed, non-upright mount — AVFoundation delivers
/// already-rotated buffers, so "upright" here means upright relative to the
/// real world, not relative to the device.
///
/// Drops rather than queues: a new frame is skipped if the previous one is
/// still being encoded/sent, per the "drop, don't queue" rule (§8.1).
final class CameraCapture: NSObject {
    private let session = AVCaptureSession()
    private let videoOutput = AVCaptureVideoDataOutput()
    private let captureQueue = DispatchQueue(label: "ios_here.camera.capture")
    private let ciContext = CIContext()

    private var isBusy = false
    private var frameCounter = 0

    var lens: AppConfig.Lens = .ultrawide
    var jpegQuality: Double = 0.6
    var targetFps: Double = 30

    /// Called on an arbitrary background queue.
    var onFrame: ((FrameSample) -> Void)?
    var onError: ((String) -> Void)?

    var isAuthorized: Bool {
        AVCaptureDevice.authorizationStatus(for: .video) == .authorized
    }

    func requestAccessIfNeeded(_ completion: @escaping (Bool) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            completion(true)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { granted in
                DispatchQueue.main.async { completion(granted) }
            }
        default:
            completion(false)
        }
    }

    func start() {
        requestAccessIfNeeded { [weak self] granted in
            guard granted else {
                self?.onError?("Camera access denied")
                return
            }
            self?.captureQueue.async { self?.configureAndStart() }
        }
    }

    func stop() {
        captureQueue.async { [weak self] in
            self?.session.stopRunning()
        }
    }

    private func deviceType(for lens: AppConfig.Lens) -> AVCaptureDevice.DeviceType {
        switch lens {
        case .wide: return .builtInWideAngleCamera
        case .ultrawide: return .builtInUltraWideCamera
        }
    }

    private func configureAndStart() {
        let preferredType = deviceType(for: lens)
        let device = AVCaptureDevice.default(preferredType, for: .video, position: .back)
            ?? AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)

        guard let device, let input = try? AVCaptureDeviceInput(device: device) else {
            onError?("No back camera available")
            return
        }

        // `startRunning()` must never be called while a begin/commit
        // transaction is open, so every early return below commits first —
        // nothing after this point returns without pairing the commit.
        session.beginConfiguration()

        session.inputs.forEach { session.removeInput($0) }
        session.outputs.forEach { session.removeOutput($0) }

        guard session.canAddInput(input) else {
            session.commitConfiguration()
            onError?("Could not add camera input")
            return
        }
        session.addInput(input)

        configureFrameRate(device: device)

        videoOutput.setSampleBufferDelegate(self, queue: captureQueue)
        videoOutput.alwaysDiscardsLateVideoFrames = true
        videoOutput.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA]

        guard session.canAddOutput(videoOutput) else {
            session.commitConfiguration()
            onError?("Could not add video output")
            return
        }
        session.addOutput(videoOutput)

        if let connection = videoOutput.connection(with: .video) {
            // Compensate for the known fixed upside-down portrait mount so
            // delivered buffers are upright in the real world.
            if connection.isVideoOrientationSupported {
                connection.videoOrientation = .portraitUpsideDown
            }
            if connection.isVideoMirroringSupported {
                connection.isVideoMirrored = false
            }
        }

        session.sessionPreset = .hd1280x720

        session.commitConfiguration()

        if !session.isRunning {
            session.startRunning()
        }
    }

    private func configureFrameRate(device: AVCaptureDevice) {
        guard (try? device.lockForConfiguration()) != nil else { return }
        defer { device.unlockForConfiguration() }
        let desired = CMTime(value: 1, timescale: Int32(targetFps))
        for range in device.activeFormat.videoSupportedFrameRateRanges {
            if targetFps >= range.minFrameRate && targetFps <= range.maxFrameRate {
                device.activeVideoMinFrameDuration = desired
                device.activeVideoMaxFrameDuration = desired
                break
            }
        }
    }
}

extension CameraCapture: AVCaptureVideoDataOutputSampleBufferDelegate {
    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        if isBusy { return } // drop, don't queue (§8.1)
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        isBusy = true

        let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let captureUptime = CMTimeGetSeconds(presentationTime)
        frameCounter += 1
        let frameId = frameCounter

        let ciImage = CIImage(cvPixelBuffer: pixelBuffer)
        let width = Int(ciImage.extent.width)
        let height = Int(ciImage.extent.height)
        let colorSpace = CGColorSpaceCreateDeviceRGB()
        let qualityKey = CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String)

        guard let jpegData = ciContext.jpegRepresentation(
            of: ciImage,
            colorSpace: colorSpace,
            options: [qualityKey: jpegQuality]
        ) else {
            isBusy = false
            return
        }

        onFrame?(FrameSample(
            frameId: frameId,
            width: width,
            height: height,
            jpegData: jpegData,
            captureUptime: captureUptime
        ))
        isBusy = false
    }
}
