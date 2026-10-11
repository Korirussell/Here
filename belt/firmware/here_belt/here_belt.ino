// HERE haptic belt: ESP32 BLE peripheral driving 8 coin motors through a ULN2803 (SPEC §16).
//
// The phone (BLE central) writes 6-byte packets; this sketch owns all pattern timing so BLE
// jitter can't smear the rhythm:
//   [0] version 0x01  [1] pattern_id  [2] motor 1..8  [3] motor2 (0 = none)  [4] strength 0..255  [5] priority
// A higher-priority pattern interrupts a lower one; equal or lower priority waits its turn
// only if the current pattern is still running (otherwise it starts immediately).
//
// Board: ESP32 Dev Module (Arduino-ESP32 core 3.x: ledcAttach/ledcWrite take the pin).
// Library: Adafruit NeoPixel (Library Manager).
// Serial (115200) commands for bench tests without a phone:
//   p <pattern> <motor> [strength]   e.g. "p 1 3" = speech on motor 3, "p 6 0" = test sweep
//   cal <motor> <gain>               per-motor gain 0..255 (prints the array to paste into belt_config.h)
//   ?                                status

#include <Adafruit_NeoPixel.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

#include "belt_config.h"
#include "belt_types.h"   // types live in a header: Arduino generates prototypes above the sketch body

// ---- patterns (SPEC §16) ------------------------------------------------------------------

static const Step P_SPEECH[]      = {{80, 80}, {80, 0}};                                  // buzz-buzz
static const Step P_NAME_CALLED[] = {{100, 80}, {100, 80}, {100, 0}};                     // buzz-buzz-buzz
static const Step P_ALERT[]       = {{60, 60}, {60, 60}, {60, 200}, {60, 60}, {60, 60}, {60, 0}};  // 3 quick x2
static const Step P_EMERGENCY[]   = {{50, 50}};                                           // repeats until stop / 10 s
static const Step P_TICK[]        = {{40, 0}};

static Pattern patternFor(uint8_t id) {
  switch (id) {
    case SPEECH:      return {P_SPEECH, 2, false};
    case NAME_CALLED: return {P_NAME_CALLED, 3, false};
    case ALERT:       return {P_ALERT, 6, false};
    case EMERGENCY:   return {P_EMERGENCY, 1, true};
    case TICK:        return {P_TICK, 1, false};
    default:          return {nullptr, 0, false};
  }
}

// ---- state -----------------------------------------------------------------------------------


static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
static volatile bool hasPending = false;
static Command pending;                       // written by the BLE task, read by loop()

static Command active;
static bool running = false;
static uint8_t stepIndex = 0;
static bool stepOn = false;
static uint32_t stepStartedMs = 0;
static uint32_t patternStartedMs = 0;
static uint8_t testMotor = 0;                 // TEST sweeps motors 1..8

static bool connected = false;
static uint32_t ledFlashUntilMs = 0;
static uint32_t ledFlashColor = 0;

Adafruit_NeoPixel ring(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);

// ---- motors ----------------------------------------------------------------------------------

static void motorWrite(uint8_t motor, uint8_t strength) {
  if (motor < 1 || motor > 8) return;
  uint32_t duty = 0;
  if (strength > 0) {
    duty = (uint32_t)strength * MOTOR_GAIN[motor - 1] / 255;
    if (duty < MOTOR_MIN_DUTY) duty = MOTOR_MIN_DUTY;
  }
  ledcWrite(MOTOR_PINS[motor - 1], duty);
}

static void allOff() {
  for (uint8_t m = 1; m <= 8; m++) motorWrite(m, 0);
}

static void setMotors(const Command& c, bool on) {
  uint8_t s = on ? c.strength : 0;
  if (c.pattern == TEST) { motorWrite(testMotor, s); return; }
  motorWrite(c.motor, s);
  if (c.motor2 >= 1 && c.motor2 <= 8 && c.motor2 != c.motor) motorWrite(c.motor2, s);
}

// ---- LED ring (SPEC §16: green connected, blue speech, yellow alert, red emergency, dim white waiting)

static uint32_t colorForPattern(uint8_t p) {
  switch (p) {
    case SPEECH: case TICK: return ring.Color(0, 80, 255);
    case NAME_CALLED: case ALERT: return ring.Color(255, 190, 0);
    case EMERGENCY: return ring.Color(255, 0, 0);
    default: return ring.Color(255, 255, 255);
  }
}

static void updateLeds(uint32_t now) {
  static uint32_t lastMs = 0;
  if (now - lastMs < 30) return;
  lastMs = now;
  uint32_t c;
  if (now < ledFlashUntilMs) {
    c = ledFlashColor;
  } else if (connected) {
    c = ring.Color(0, 160, 40);
  } else {
    uint8_t level = 8 + (uint8_t)(20 * (0.5f + 0.5f * sinf(now / 600.0f)));   // dim white pulse: waiting
    c = ring.Color(level, level, level);
  }
  for (uint8_t i = 0; i < LED_COUNT; i++) ring.setPixelColor(i, c);
  ring.show();
}

// ---- pattern engine --------------------------------------------------------------------------

static void stopPattern() {
  allOff();
  running = false;
}

static void startPattern(const Command& c, uint32_t now) {
  allOff();
  if (c.pattern == STOP) { running = false; return; }
  active = c;
  running = true;
  stepIndex = 0;
  stepOn = true;
  testMotor = 1;
  stepStartedMs = patternStartedMs = now;
  setMotors(active, true);
  ledFlashColor = colorForPattern(c.pattern);
  ledFlashUntilMs = now + 600;
}

static void tickPattern(uint32_t now) {
  if (!running) return;
  if (active.pattern == TEST) {                       // each motor in turn: 300 ms on, 150 ms off
    uint32_t dur = stepOn ? 300 : 150;
    if (now - stepStartedMs < dur) return;
    stepStartedMs = now;
    if (stepOn) { motorWrite(testMotor, 0); stepOn = false; return; }
    if (++testMotor > 8) { stopPattern(); return; }
    stepOn = true;
    motorWrite(testMotor, active.strength);
    return;
  }
  Pattern p = patternFor(active.pattern);
  if (p.count == 0) { stopPattern(); return; }
  if (active.pattern == EMERGENCY && now - patternStartedMs > EMERGENCY_MAX_MS) { stopPattern(); return; }
  const Step& s = p.steps[stepIndex];
  uint32_t dur = stepOn ? s.on_ms : s.off_ms;
  if (now - stepStartedMs < dur) return;
  stepStartedMs = now;
  if (stepOn) {
    setMotors(active, false);
    stepOn = false;
    if (s.off_ms > 0) return;
  }
  // next step
  stepIndex++;
  if (stepIndex >= p.count) {
    if (!p.repeat) { stopPattern(); return; }
    stepIndex = 0;
  }
  stepOn = true;
  setMotors(active, true);
}

static void takePending(uint32_t now) {
  if (!hasPending) return;
  Command c;
  portENTER_CRITICAL(&mux);
  c = pending;
  hasPending = false;
  portEXIT_CRITICAL(&mux);
  // Higher priority interrupts; a finished (or no) pattern is always replaced; STOP always wins.
  if (!running || c.pattern == STOP || c.priority >= active.priority) {
    startPattern(c, now);
  }
}

static void queueCommand(const uint8_t* d, size_t n) {
  if (n < 6 || d[0] != PACKET_VERSION || d[1] > TEST) return;
  Command c = {d[1], d[2], d[3], d[4], d[5]};
  portENTER_CRITICAL(&mux);
  pending = c;
  hasPending = true;
  portEXIT_CRITICAL(&mux);
}

// ---- BLE -------------------------------------------------------------------------------------

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override { connected = true; }
  void onDisconnect(BLEServer* server) override {
    connected = false;
    uint8_t stop[6] = {PACKET_VERSION, STOP, 0, 0, 0, 255};
    queueCommand(stop, 6);                      // never leave a motor running without a phone
    server->getAdvertising()->start();          // let the phone reconnect
  }
};

class CommandCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* ch) override {
    String v = ch->getValue();                  // Arduino-ESP32 3.x returns String
    queueCommand((const uint8_t*)v.c_str(), v.length());
  }
};

static void setupBle() {
  BLEDevice::init(BELT_NAME);
  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());
  BLEService* service = server->createService(BELT_SERVICE_UUID);
  BLECharacteristic* cmd = service->createCharacteristic(
      BELT_COMMAND_UUID, BLECharacteristic::PROPERTY_WRITE_NR | BLECharacteristic::PROPERTY_WRITE);
  cmd->setCallbacks(new CommandCallbacks());
  service->start();
  BLEAdvertising* adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(BELT_SERVICE_UUID);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
}

// ---- serial bench commands -------------------------------------------------------------------

static void serialCommands() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.startsWith("p ")) {
    int pat = 0, motor = 0, strength = 200;
    sscanf(line.c_str() + 2, "%d %d %d", &pat, &motor, &strength);
    uint8_t pkt[6] = {PACKET_VERSION, (uint8_t)pat, (uint8_t)motor, 0, (uint8_t)strength, 10};
    queueCommand(pkt, 6);
    Serial.printf("pattern %d motor %d strength %d\n", pat, motor, strength);
  } else if (line.startsWith("cal ")) {
    int motor = 0, gain = 255;
    sscanf(line.c_str() + 4, "%d %d", &motor, &gain);
    if (motor >= 1 && motor <= 8) MOTOR_GAIN[motor - 1] = (uint8_t)constrain(gain, 0, 255);
    Serial.print("MOTOR_GAIN = {");
    for (int i = 0; i < 8; i++) Serial.printf("%d%s", MOTOR_GAIN[i], i < 7 ? ", " : "};\n");
    uint8_t pkt[6] = {PACKET_VERSION, SPEECH, (uint8_t)motor, 0, 200, 10};
    queueCommand(pkt, 6);
  } else if (line == "?") {
    Serial.printf("connected=%d running=%d pattern=%d motor=%d\n", connected, running, active.pattern, active.motor);
  }
}

// ---- main ------------------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < 8; i++) {
    ledcAttach(MOTOR_PINS[i], PWM_FREQ_HZ, PWM_RESOLUTION_BITS);
    ledcWrite(MOTOR_PINS[i], 0);
  }
  ring.begin();
  ring.setBrightness(LED_BRIGHTNESS);
  ring.show();
  setupBle();
  Serial.println("HERE belt ready: advertising " BELT_NAME);
}

void loop() {
  uint32_t now = millis();
  takePending(now);
  tickPattern(now);
  updateLeds(now);
  serialCommands();
  delay(2);
}
