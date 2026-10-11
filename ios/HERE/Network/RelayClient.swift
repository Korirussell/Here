import Foundation

/// Which relay the phone is on ("two relays, one stream"): the primary (the laptop's
/// local relay, ws://<laptop-ip>:8080/ws) or the fallback (Vultr, wss://<host>/ws). Same stream on both.
enum RelayRoute: Equatable { case primary, fallback }

/// The failover decision, kept pure so it can be tested without sockets or timers. One connection at a
/// time: a failure on the primary (no connection within `Tunables.relayPrimaryConnectS`, or a drop) moves
/// to the fallback; while there, the client probes the primary every `Tunables.relayPrimaryRetryS` and
/// moves back when a probe connects. No fallback configured -> always the primary (the old behaviour).
/// `seq` is never checked for gaps: a `clock_pong` only goes to the relay its ping came from.
struct RelayFailover: Equatable {
    let hasFallback: Bool
    private(set) var route: RelayRoute = .primary

    init(hasFallback: Bool) { self.hasFallback = hasFallback }

    /// The current connection failed or timed out. True = switch to the fallback now (no backoff);
    /// false = retry the same relay with backoff.
    mutating func connectionFailed() -> Bool {
        guard hasFallback, route == .primary else { return false }
        route = .fallback
        return true
    }

    /// Only while on the fallback is the primary probed in the background.
    var shouldProbePrimary: Bool { hasFallback && route == .fallback }

    /// A background probe reached the primary. True = switch back to it.
    mutating func primaryProbeConnected() -> Bool {
        guard shouldProbePrimary else { return false }
        route = .primary
        return true
    }

    /// The primary gets a connect deadline only when there is somewhere to fail over to.
    var primaryHasDeadline: Bool { hasFallback && route == .primary }
}

/// The phone's connection to the relay (SPEC §7, §12):
///
///     wss://<host>/ws?session=<session>&role=phone&token=<token>
///
/// Sends `hello` (role phone, SPEC §12.10 subscriptions) and the current `config`, a heartbeat
/// every 2 s with the thermal state, and a `clock_ping` burst every 10 s for latency measurement.
/// Reconnects automatically (500 ms doubling to 5 s). Decoded messages are delivered on the main queue.
/// With a fallback URL it fails over and back (see `RelayFailover`); `onRoute` says which relay is in use.
final class RelayClient: NSObject {
    /// `rejected`: the server answered the handshake with 401/403 before the WebSocket opened (relay.py:
    /// wrong or missing token). Retrying with the same token can never succeed, so this stops the backoff
    /// loop instead of looping forever behind an indistinguishable "offline".
    enum Status: Equatable { case disconnected, connecting, connected, rejected(Int) }

    var onMessage: ((HereMessage) -> Void)?
    var onStatus: ((Status) -> Void)?
    /// Called right after hello, so the app can (re)send its config.
    var onConnected: (() -> Void)?
    /// Which relay the client is using (or trying), delivered on the main queue.
    var onRoute: ((RelayRoute) -> Void)?
    /// Heartbeat contents, asked for every 2 s.
    var heartbeat: () -> Heartbeat = { Heartbeat(t: Date().timeIntervalSince1970, status: .ok) }

    let clock = ClockSync()

    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private let queue = DispatchQueue(label: "here.relay")
    private var primaryURL: URL?
    private var fallbackURL: URL?
    private var failover = RelayFailover(hasFallback: false)
    private var url: URL? { failover.route == .primary ? primaryURL : fallbackURL }
    private var helloAcked = false               // the current `task` has connected (hello sent)
    /// Background probe of the primary while on the fallback. Never receives; becomes `task` on success.
    private var probe: URLSessionWebSocketTask?
    private var probeEpoch = 0                   // bumped to end the probe loop (like `generation`)
    private var wanted = false
    private var backoff: TimeInterval = 0.5
    private var timers: [Timer] = []
    private var generation = 0
    private let encoder = JSONEncoder()

    override init() {
        super.init()
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 10
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }

    /// The relay URL for a base like `wss://host/ws` (path defaults to /ws). nil = not a ws/wss URL.
    static func relayURL(base: String, session sessionName: String, token: String) -> URL? {
        guard var comps = URLComponents(string: base.trimmingCharacters(in: .whitespaces)),
              let scheme = comps.scheme, ["ws", "wss"].contains(scheme) else { return nil }
        if comps.path.isEmpty { comps.path = "/ws" }
        var q = [URLQueryItem(name: "session", value: sessionName), URLQueryItem(name: "role", value: "phone")]
        if !token.isEmpty { q.append(URLQueryItem(name: "token", value: token)) }
        comps.queryItems = q
        return comps.url
    }

    /// Build the URLs from settings and (re)connect, primary first. Invalid primary -> stays disconnected;
    /// an empty or invalid fallback -> no failover. Both relays get the same token and session.
    func connect(base: String, fallback: String = "", session sessionName: String, token: String) {
        guard let primary = Self.relayURL(base: base, session: sessionName, token: token) else {
            status(.disconnected)
            return
        }
        let second = Self.relayURL(base: fallback, session: sessionName, token: token)
        queue.async {
            self.stopProbing()
            self.primaryURL = primary
            self.fallbackURL = second
            self.failover = RelayFailover(hasFallback: second != nil)
            self.announce(self.failover.route)
            self.wanted = true
            self.backoff = 0.5
            self.open()
        }
    }

    func disconnect() {
        queue.async {
            self.wanted = false
            self.generation += 1
            self.stopProbing()
            self.task?.cancel(with: .goingAway, reason: nil)
            self.task = nil
            self.status(.disconnected)
        }
        DispatchQueue.main.async {
            self.timers.forEach { $0.invalidate() }
            self.timers = []
        }
    }

    // MARK: sending

    func send<T: Encodable>(_ message: T) {
        guard let data = try? encoder.encode(message), let text = String(data: data, encoding: .utf8) else { return }
        queue.async {
            self.task?.send(.string(text)) { _ in }      // failures surface in the receive loop
        }
    }

    // MARK: connection

    /// hello first on every connection (SPEC §12.1).
    private func helloText() -> String? {
        let hello = Hello(t: Date().timeIntervalSince1970, role: .phone,
                          subscribe: HereContract.defaultSubscribe["phone"] ?? [],
                          appVersion: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String)
        guard let data = try? encoder.encode(hello) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private func open() {
        guard wanted, let url else { return }
        generation += 1
        let gen = generation
        task?.cancel(with: .goingAway, reason: nil)
        helloAcked = false
        status(.connecting)
        let t = session.webSocketTask(with: url)
        t.maximumMessageSize = 1 << 20
        task = t
        t.resume()
        receive(t, gen)
        if let text = helloText() {
            t.send(.string(text)) { [weak self] error in
                guard let self else { return }
                self.queue.async {
                    guard gen == self.generation else { return }
                    if error != nil { self.retry(gen); return }
                    self.connected()
                }
            }
        }
        // The primary must connect within the deadline or the fallback takes over (retry() decides).
        // A later generation (connected, retried, switched, disconnected) makes this a no-op.
        if failover.primaryHasDeadline {
            queue.asyncAfter(deadline: .now() + Tunables.relayPrimaryConnectS) { [weak self] in
                guard let self, gen == self.generation, !self.helloAcked else { return }
                self.retry(gen)
            }
        }
    }

    /// The current `task` is up (on `queue`).
    private func connected() {
        helloAcked = true
        backoff = 0.5
        status(.connected)
        DispatchQueue.main.async {
            self.onConnected?()
            self.startTimers()
        }
        pingBurst()
    }

    private func receive(_ t: URLSessionWebSocketTask, _ gen: Int) {
        t.receive { [weak self] result in
            guard let self else { return }
            self.queue.async {
                guard gen == self.generation else { return }
                switch result {
                case .failure:
                    self.retry(gen)
                case .success(let message):
                    var data: Data?
                    switch message {
                    case .string(let s): data = Data(s.utf8)
                    case .data(let d): data = d
                    @unknown default: data = nil
                    }
                    if let data, let msg = try? HereMessage.decode(data) {
                        if case let .clockPong(p) = msg {
                            self.clock.recordPong(tPhoneSent: p.tPhone, tLaptop: p.tLaptop)
                        }
                        DispatchQueue.main.async { self.onMessage?(msg) }
                    }
                    self.receive(t, gen)
                }
            }
        }
    }

    private func retry(_ gen: Int) {
        guard gen == generation, wanted else { return }
        status(.disconnected)
        task?.cancel(with: .abnormalClosure, reason: nil)
        task = nil
        if failover.connectionFailed() {            // primary down: fallback now, primary in the background
            announce(failover.route)
            startProbing()
            open()
            return
        }
        let delay = backoff
        backoff = min(backoff * 2, 5)
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, gen == self.generation else { return }
            self.open()
        }
    }

    private func status(_ s: Status) {
        DispatchQueue.main.async { self.onStatus?(s) }
    }

    private func announce(_ r: RelayRoute) {
        DispatchQueue.main.async { self.onRoute?(r) }
    }

    // MARK: failover (all on `queue`)

    private func startProbing() {
        probeEpoch += 1
        probeLoop(probeEpoch)
    }

    /// Ends the probe loop and cancels any probe in flight. Called by connect(), disconnect(), a
    /// rejection and the switch back, so no probe outlives the state that started it.
    private func stopProbing() {
        probeEpoch += 1
        probe?.cancel(with: .goingAway, reason: nil)
        probe = nil
    }

    private func probeLoop(_ epoch: Int) {
        queue.asyncAfter(deadline: .now() + Tunables.relayPrimaryRetryS) { [weak self] in
            guard let self, epoch == self.probeEpoch, self.wanted, self.failover.shouldProbePrimary else { return }
            self.probePrimary(epoch)
            self.probeLoop(epoch)
        }
    }

    /// Opens a second socket to the primary just long enough to see it connect; on success it becomes
    /// the connection and the fallback is closed (one connection at a time).
    private func probePrimary(_ epoch: Int) {
        guard let primaryURL, let text = helloText() else { return }
        probe?.cancel(with: .goingAway, reason: nil)   // the previous probe never connected
        let p = session.webSocketTask(with: primaryURL)
        p.maximumMessageSize = 1 << 20
        probe = p
        p.resume()
        p.send(.string(text)) { [weak self] error in
            guard let self else { return }
            self.queue.async {
                guard epoch == self.probeEpoch, p === self.probe, self.wanted else { return }
                if error != nil {
                    p.cancel(with: .goingAway, reason: nil)
                    self.probe = nil
                    return
                }
                self.promote(p)
            }
        }
    }

    /// Back to the primary on the probe's socket (on `queue`).
    private func promote(_ p: URLSessionWebSocketTask) {
        guard failover.primaryProbeConnected() else { return }
        probe = nil                                     // p is now the connection: don't cancel it
        stopProbing()
        generation += 1
        let gen = generation
        task?.cancel(with: .goingAway, reason: nil)     // the fallback; its callbacks are a generation behind
        task = p
        announce(failover.route)
        receive(p, gen)
        connected()
    }

    // MARK: heartbeat + clock (main thread timers)

    private func startTimers() {
        timers.forEach { $0.invalidate() }
        timers = [
            Timer.scheduledTimer(withTimeInterval: Tunables.heartbeatS, repeats: true) { [weak self] _ in
                guard let self else { return }
                self.send(self.heartbeat())
            },
            Timer.scheduledTimer(withTimeInterval: Tunables.clockPingS, repeats: true) { [weak self] _ in
                self?.queue.async { self?.pingBurst() }
            },
        ]
    }

    private func pingBurst() {
        clock.beginBurst()
        for i in 0..<3 {
            queue.asyncAfter(deadline: .now() + Double(i) * 0.15) { [weak self] in
                self?.send(ClockPing(tPhone: PhoneClock.nowEpoch))
            }
        }
    }
}

extension RelayClient: URLSessionWebSocketDelegate {
    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask,
                    didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        queue.async {
            if webSocketTask === self.task { self.retry(self.generation) }
        }
    }

    /// The handshake's actual HTTP response is available here even on failure. A 401/403 means the
    /// token is wrong (or missing where one's required) -- stop retrying rather than hammering the
    /// relay with the same bad token every few seconds forever.
    ///
    /// Deliberately doesn't compare `task` against `self.task`: `receive()`'s own failure path (same
    /// underlying error) can win the race and already be a generation ahead by the time this callback
    /// lands, swapping `self.task` out from under it. A 401/403 means *this token* is rejected, which
    /// stays true regardless of which in-flight task instance happens to report it.
    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        queue.async {
            guard error != nil, self.wanted,
                  let code = (task.response as? HTTPURLResponse)?.statusCode, code == 401 || code == 403 else { return }
            // On the fallback, a 401/403 from the primary is a probe (or the abandoned first attempt):
            // keep the working fallback rather than stopping everything.
            if self.failover.route == .fallback, task.originalRequest?.url == self.primaryURL {
                if task === self.probe { self.probe = nil }
                return
            }
            self.wanted = false
            self.stopProbing()
            self.task?.cancel(with: .goingAway, reason: nil)
            self.task = nil
            self.status(.rejected(code))
        }
    }
}
