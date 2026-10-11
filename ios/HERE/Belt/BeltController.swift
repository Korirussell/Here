import CoreBluetooth
import Foundation

/// CoreBluetooth central for the ESP32 belt (SPEC §16): scan by service UUID, connect, find the
/// command characteristic, write the 6-motor firmware's timed commands (BeltSixMotor), reconnect automatically.
///
/// `lastCommand` is published whether or not the belt is connected, so the on-screen belt
/// indicator shows what the wearer *would* feel (SPEC §23 backup: "app shows the intended motor").
final class BeltController: NSObject, ObservableObject {
    enum State: String { case off = "Bluetooth off", unauthorized = "Bluetooth not allowed",
                         scanning = "searching", connecting = "connecting", ready = "connected" }

    @Published private(set) var state: State = .off
    @Published private(set) var lastCommand: BeltCommand?
    @Published private(set) var lastCommandAt: Date?
    @Published private(set) var peripheralName: String?

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var command: CBCharacteristic?
    private let queue = DispatchQueue(label: "here.belt")
    private var serviceUUID = CBUUID(string: BeltUUID.service)
    private var commandUUID = CBUUID(string: BeltUUID.command)
    private var wanted = false
    /// Guards against getting stuck in "connecting" forever: `didConnect` can fire at the BLE link
    /// layer and then service/characteristic discovery can just silently never finish (wrong firmware,
    /// a UUID mismatch, a flaky discovery in a crowded room -- SPEC §16/§24's own stated BLE risk).
    /// Without a deadline, `peripheral` stays non-nil and `scanIfPossible`'s `peripheral == nil` guard
    /// blocks any retry forever, with no automatic way out short of the peripheral itself dropping the
    /// link. Cancelled the moment the real characteristic is found.
    private var connectTimeout: DispatchWorkItem?

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: queue, options: [CBCentralManagerOptionShowPowerAlertKey: true])
    }

    func configure(serviceUUID: String, commandUUID: String) {
        queue.async {
            self.serviceUUID = CBUUID(string: BeltUUID.resolve(serviceUUID, current: BeltUUID.service, legacy: BeltUUID.legacyService))
            self.commandUUID = CBUUID(string: BeltUUID.resolve(commandUUID, current: BeltUUID.command, legacy: BeltUUID.legacyCommand))
        }
    }

    func connect() {
        queue.async {
            self.wanted = true
            self.scanIfPossible()
        }
    }

    func disconnect() {
        queue.async {
            self.wanted = false
            self.central.stopScan()
            if let p = self.peripheral { self.central.cancelPeripheralConnection(p) }
        }
    }

    /// Send (or, when disconnected, just display) a command. The 6-motor belt has no patterns of its own, so each
    /// pattern goes out as timed writes (`BeltSixMotor.steps`). A command of lower priority than the one still
    /// playing is dropped; an equal or higher one (and STOP) cancels what's left of it.
    func send(_ cmd: BeltCommand) {
        DispatchQueue.main.async {
            self.lastCommand = cmd
            self.lastCommandAt = Date()
        }
        queue.async {
            let now = DispatchTime.now().uptimeNanoseconds
            if cmd.pattern != .stop, now < self.playingUntil, cmd.priority < self.playingPriority { return }
            self.generation &+= 1
            let gen = self.generation
            self.playingPriority = cmd.priority
            self.playingUntil = now + UInt64(BeltSixMotor.length(of: cmd) * 1e9)
            for step in BeltSixMotor.steps(for: cmd) {
                self.queue.asyncAfter(deadline: .now() + step.at) {
                    guard gen == self.generation, let p = self.peripheral, let c = self.command,
                          p.state == .connected else { return }
                    let type: CBCharacteristicWriteType = c.properties.contains(.writeWithoutResponse) ? .withoutResponse : .withResponse
                    p.writeValue(Data(step.bytes), for: c, type: type)
                }
            }
        }
    }

    private var generation = 0
    private var playingPriority = 0
    private var playingUntil: UInt64 = 0

    private func publish(_ s: State) {
        DispatchQueue.main.async { self.state = s }
    }

    private func scanIfPossible() {
        guard wanted, central.state == .poweredOn, peripheral == nil else { return }
        publish(.scanning)
        central.scanForPeripherals(withServices: [serviceUUID], options: nil)
    }
}

extension BeltController: CBCentralManagerDelegate, CBPeripheralDelegate {
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        switch central.state {
        case .poweredOn: scanIfPossible()
        case .unauthorized: publish(.unauthorized)
        default:
            peripheral = nil
            command = nil
            publish(.off)
        }
    }

    func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        central.stopScan()
        self.peripheral = peripheral
        peripheral.delegate = self
        publish(.connecting)
        central.connect(peripheral, options: nil)
    }

    func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        DispatchQueue.main.async { self.peripheralName = peripheral.name }
        peripheral.discoverServices([serviceUUID])
        let timeout = DispatchWorkItem { [weak self] in
            guard let self, self.peripheral === peripheral, self.command == nil else { return }
            self.central.cancelPeripheralConnection(peripheral)   // -> didDisconnectPeripheral -> reset() -> rescan
        }
        connectTimeout = timeout
        queue.asyncAfter(deadline: .now() + 10, execute: timeout)
    }

    func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        reset()
    }

    func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral,
                        error: Error?) {
        reset()
    }

    private func reset() {
        connectTimeout?.cancel()
        connectTimeout = nil
        peripheral = nil
        command = nil
        DispatchQueue.main.async { self.peripheralName = nil }
        // Reconnect automatically after a short pause (SPEC §24: BLE flaky in crowded rooms).
        queue.asyncAfter(deadline: .now() + 0.5) { self.scanIfPossible() }
        if !wanted { publish(.off) }
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let service = peripheral.services?.first(where: { $0.uuid == serviceUUID }) else { return }
        peripheral.discoverCharacteristics([commandUUID], for: service)
    }

    func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard let c = service.characteristics?.first(where: { $0.uuid == commandUUID }) else { return }
        connectTimeout?.cancel()
        connectTimeout = nil
        command = c
        publish(.ready)
    }
}
