import Foundation

/// WebSocket transport to the world_here hub (HERE_PLAN.md §8.1): connects,
/// sends `hello` first, frames `video_frame` as a JSON header immediately
/// followed by one binary JPEG message on the same socket, and reconnects
/// automatically with backoff (500 ms doubling to 5 s) when dropped.
///
/// This class is transport only. Scheduling of heading/frame/heartbeat/
/// clock_ping sends lives in `AppState`; this just moves bytes and parses
/// incoming envelopes, dispatching `clock_pong` into `ClockSync` and
/// everything else to callbacks for the UI/settings layer.
final class HubConnection: NSObject {
    enum Status: Equatable {
        case disconnected
        case connecting
        case connected
    }

    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private let queue = DispatchQueue(label: "ios_here.hub")

    private var wantsConnection = false
    private var currentHost = ""
    private var currentPort = 0
    private var reconnectDelay: TimeInterval = 0.5
    private let maxReconnectDelay: TimeInterval = 5.0
    private var reconnectWorkItem: DispatchWorkItem?
    private var generation = 0 // invalidates callbacks from a superseded task

    private let clockSync: ClockSync

    var onStatusChange: ((Status) -> Void)?
    var onConfig: (([String: Any]) -> Void)?
    var onSearchResult: (([String: Any]) -> Void)?
    var onAlert: (([String: Any]) -> Void)?

    private(set) var status: Status = .disconnected {
        didSet { if status != oldValue { onStatusChange?(status) } }
    }

    init(clockSync: ClockSync) {
        self.clockSync = clockSync
        super.init()
        session = URLSession(configuration: .default)
    }

    // MARK: Lifecycle

    func connect(host: String, port: Int) {
        currentHost = host
        currentPort = port
        wantsConnection = true
        reconnectDelay = 0.5
        reconnectWorkItem?.cancel()
        openSocket()
    }

    func disconnect() {
        wantsConnection = false
        reconnectWorkItem?.cancel()
        generation += 1
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        status = .disconnected
    }

    /// Call if the host/port changes in Settings while already connected.
    func reconnectNow(host: String, port: Int) {
        currentHost = host
        currentPort = port
        generation += 1
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        reconnectDelay = 0.5
        reconnectWorkItem?.cancel()
        openSocket()
    }

    private func openSocket() {
        guard wantsConnection, let url = URL(string: "ws://\(currentHost):\(currentPort)") else { return }
        status = .connecting
        generation += 1
        let myGeneration = generation

        let newTask = session.webSocketTask(with: url)
        task = newTask
        newTask.resume()

        let hello = Contract.envelope(type: "hello", t: PhoneClock.nowEpoch, extra: [
            "role": "ios_here",
            "subscribe": ["clock_pong", "config", "search_result", "alert"],
        ])
        rawSend(hello, on: newTask) { [weak self] error in
            guard let self, myGeneration == self.generation else { return }
            if let error {
                self.handleFailure(error)
            } else {
                self.status = .connected
                self.reconnectDelay = 0.5
            }
        }

        listen(task: newTask, generation: myGeneration)
    }

    private func listen(task: URLSessionWebSocketTask, generation myGeneration: Int) {
        task.receive { [weak self] result in
            guard let self, myGeneration == self.generation else { return }
            switch result {
            case .failure(let error):
                self.handleFailure(error)
            case .success(let message):
                if case .string(let text) = message {
                    self.handleIncoming(text)
                }
                // Binary messages inbound (none expected from the hub to
                // ios_here today) would need header pairing like the
                // outbound video_frame; not needed for this client yet.
                self.listen(task: task, generation: myGeneration)
            }
        }
    }

    private func handleFailure(_ error: Error) {
        guard status != .disconnected else { return }
        status = .disconnected
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        guard wantsConnection else { return }
        let delay = reconnectDelay
        reconnectDelay = min(reconnectDelay * 2, maxReconnectDelay)
        let item = DispatchWorkItem { [weak self] in self?.openSocket() }
        reconnectWorkItem = item
        queue.asyncAfter(deadline: .now() + delay, execute: item)
    }

    // MARK: Incoming

    private func handleIncoming(_ text: String) {
        guard let msg = Contract.parse(text), let type = msg["type"] as? String else { return }
        switch type {
        case "clock_pong":
            guard let tPhone = msg["t_phone"] as? Double, let tHub = msg["t_hub"] as? Double else { return }
            clockSync.recordPong(tPhoneSent: tPhone, tHub: tHub)
        case "config":
            onConfig?(msg)
        case "search_result":
            onSearchResult?(msg)
        case "alert":
            onAlert?(msg)
        default:
            break
        }
    }

    // MARK: Outgoing

    private func rawSend(_ dict: [String: Any], on task: URLSessionWebSocketTask, completion: ((Error?) -> Void)? = nil) {
        guard let text = Contract.jsonText(dict) else { completion?(nil); return }
        task.send(.string(text)) { error in completion?(error) }
    }

    /// Send any JSON-only contract message (heading, heartbeat, calibrate,
    /// config, clock_ping, search_request).
    func send(_ dict: [String: Any]) {
        guard let task, status == .connected else { return }
        rawSend(dict, on: task) { [weak self] error in
            if let error { self?.handleFailure(error) }
        }
    }

    /// `video_frame`: JSON header, then one binary JPEG message, in order,
    /// on the same socket (HERE_PLAN.md §8.1 / §8.5).
    func sendVideoFrame(header: [String: Any], jpeg: Data) {
        guard let task, status == .connected, let text = Contract.jsonText(header) else { return }
        task.send(.string(text)) { [weak self] error in
            if let error {
                self?.handleFailure(error)
                return
            }
            task.send(.data(jpeg)) { error in
                if let error { self?.handleFailure(error) }
            }
        }
    }
}
