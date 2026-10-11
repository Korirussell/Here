import Combine
import Foundation

/// Wires the pieces together: heading + camera → hub, clock sync, and the
/// published state the SwiftUI views read. This is intentionally the only
/// place that knows about all of them — `HeadingProvider`, `CameraCapture`
/// and `HubConnection` don't know about each other.
///
/// `HeadingProvider`, `CameraCapture` and `HubConnection` each call back on
/// their own background queues, so every write to a `@Published` property
/// here is dispatched to the main queue; everything else (sending on the
/// hub, reading config) is cheap enough not to need its own isolation for a
/// hackathon-scale app.
final class AppState: ObservableObject {
    let config: AppConfig
    let clockSync = ClockSync()

    private let hub: HubConnection
    private let heading = HeadingProvider()
    private let camera = CameraCapture()
    private let motionQueue = OperationQueue()

    private var heartbeatTimer: Timer?
    private var clockPingTimer: Timer?
    private var statsTimer: Timer?

    private var framesThisSecond = 0
    private var headingSamplesThisSecond = 0

    // MARK: Published, UI-facing state

    @Published var connectionStatus: HubConnection.Status = .disconnected
    @Published var lastHeadingDeg: Double?
    @Published var headingQuality: String = "—"
    @Published var fps: Double = 0
    @Published var headingHz: Double = 0
    @Published var clockOffsetMs: Double = 0
    @Published var clockRttMs: Double = 0
    @Published var cameraErrorMessage: String?
    @Published var lastAlertLabel: String?
    @Published var lastSearchResultSummary: String?

    init() {
        config = AppConfig.loadOrDefault()
        hub = HubConnection(clockSync: clockSync)
        wire()
    }

    private func wire() {
        hub.onStatusChange = { [weak self] status in
            DispatchQueue.main.async { self?.connectionStatus = status }
        }
        hub.onConfig = { [weak self] msg in
            DispatchQueue.main.async { self?.applyIncomingConfig(msg) }
        }
        hub.onAlert = { [weak self] msg in
            let label = msg["label"] as? String ?? msg["kind"] as? String ?? "alert"
            DispatchQueue.main.async { self?.lastAlertLabel = label }
        }
        hub.onSearchResult = { [weak self] msg in
            let count = (msg["results"] as? [[String: Any]])?.count ?? 0
            DispatchQueue.main.async { self?.lastSearchResultSummary = "\(count) result(s)" }
        }

        heading.signFlip = config.headingSignFlip
        heading.onHeading = { [weak self] sample in
            guard let self else { return }
            let laptopT = self.clockSync.laptopTime(fromUptime: sample.captureUptime)
            self.hub.send(Contract.envelope(type: "heading", t: laptopT, extra: [
                "heading_raw_deg": sample.headingRawDeg,
                "quality": sample.quality,
            ]))
            self.headingSamplesThisSecond += 1
            DispatchQueue.main.async {
                self.lastHeadingDeg = sample.headingRawDeg
                self.headingQuality = sample.quality
            }
        }

        camera.lens = config.lens
        camera.jpegQuality = config.jpegQuality
        camera.targetFps = config.targetFps
        camera.onError = { [weak self] message in
            DispatchQueue.main.async { self?.cameraErrorMessage = message }
        }
        camera.onFrame = { [weak self] frame in
            guard let self else { return }
            let laptopT = self.clockSync.laptopTime(fromUptime: frame.captureUptime)
            let header = Contract.envelope(type: "video_frame", t: laptopT, extra: [
                "frame_id": frame.frameId,
                "width": frame.width,
                "height": frame.height,
                "hfov_deg": self.config.hfovDeg,
                "lens": self.config.lens.rawValue,
                "encoding": "jpeg",
            ])
            self.hub.sendVideoFrame(header: header, jpeg: frame.jpegData)
            self.framesThisSecond += 1
        }
    }

    // MARK: Lifecycle (call from the main thread, e.g. a SwiftUI `.task`/`.onAppear`)

    func start() {
        heading.start(queue: motionQueue)
        camera.start()
        hub.connect(host: config.hubHost, port: config.hubPort)
        startTimers()
    }

    func stop() {
        heading.stop()
        camera.stop()
        hub.disconnect()
        stopTimers()
    }

    private func startTimers() {
        heartbeatTimer = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            self?.sendHeartbeat()
        }
        clockPingTimer = Timer.scheduledTimer(withTimeInterval: 10.0, repeats: true) { [weak self] _ in
            self?.sendClockPingBurst()
        }
        statsTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            self?.tickStats()
        }
        sendClockPingBurst()
    }

    private func stopTimers() {
        heartbeatTimer?.invalidate()
        clockPingTimer?.invalidate()
        statsTimer?.invalidate()
    }

    private func tickStats() {
        fps = Double(framesThisSecond)
        headingHz = Double(headingSamplesThisSecond)
        framesThisSecond = 0
        headingSamplesThisSecond = 0
        clockOffsetMs = clockSync.offset * 1000
        clockRttMs = clockSync.bestRtt.isFinite ? clockSync.bestRtt * 1000 : 0
    }

    private func sendHeartbeat() {
        let status = cameraErrorMessage != nil ? "degraded" : "ok"
        hub.send(Contract.envelope(type: "heartbeat", t: PhoneClock.nowEpoch, extra: [
            "status": status,
            "detail": cameraErrorMessage ?? "",
            "rate_hz": fps,
        ]))
    }

    private func sendClockPingBurst() {
        clockSync.beginBurst()
        for _ in 0..<5 {
            hub.send(Contract.envelope(type: "clock_ping", t: PhoneClock.nowEpoch, extra: [
                "t_phone": PhoneClock.nowEpoch,
            ]))
        }
    }

    // MARK: Actions from the UI (main thread)

    func calibrateHeadingZero() {
        hub.send(Contract.envelope(type: "calibrate", t: PhoneClock.nowEpoch, extra: [
            "kind": "heading_zero",
        ]))
    }

    /// Call after editing Settings: persists, re-applies to the running
    /// camera/heading pipeline, pushes the new `config` to the hub, and
    /// reconnects if the transport target changed.
    func applySettingsChange(transportChanged: Bool) {
        config.save()
        heading.signFlip = config.headingSignFlip
        camera.lens = config.lens
        camera.jpegQuality = config.jpegQuality
        camera.targetFps = config.targetFps
        hub.send(Contract.envelope(type: "config", t: PhoneClock.nowEpoch, extra: config.toWireFields()))
        if transportChanged {
            hub.reconnectNow(host: config.hubHost, port: config.hubPort)
        }
    }

    private func applyIncomingConfig(_ msg: [String: Any]) {
        if let v = msg["profile"] as? String { config.profile = v }
        if let v = msg["user_name"] as? String { config.userName = v }
        if let v = msg["name_aliases"] as? [String] { config.nameAliases = v }
        if let v = msg["belt_strength"] as? Int { config.beltStrength = v }
        if let v = msg["caption_scale"] as? Double { config.captionScale = v }
        if let v = msg["alerts_enabled"] as? [String: Bool] { config.alertsEnabled = v }
        config.save()
    }
}
