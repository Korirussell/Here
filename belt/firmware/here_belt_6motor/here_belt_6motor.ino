#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

// ============================================================
// HERE BELT — 6 MOTOR BLE FIRMWARE
//
// Working physical channels:
// old 1, 2, 3, 4, 7, 8
//
// Logical motors exposed over Bluetooth:
// 0 = front
// 1 = front-right-ish
// 2 = back-right-ish
// 3 = back
// 4 = back-left-ish
// 5 = front-left-ish
//
// SINGLE MOTOR COMMAND:
// [motor, strength, duration]
// motor:    0-5
// strength: 0-255
// duration: units of 10 ms
//
// Example:
// 00 BE 28
// motor 0, strength 190, duration 400ms
//
// GROUP COMMAND:
// [FE, bitmask, strength, duration]
//
// Example:
// FE 05 BE 32
//
// 05 binary = 000101
// motors 0 and 2 buzz simultaneously
// strength 190
// duration 500ms
//
// STOP ALL:
// FF 00 00
// ============================================================

const int MOTOR_COUNT = 6;

const int MOTORS[MOTOR_COUNT] = {
  13,   // logical motor 0 = old motor 1
  18,   // logical motor 1 = old motor 2
  19,   // logical motor 2 = old motor 3
  21,   // logical motor 3 = old motor 4
  32,   // logical motor 4 = old motor 7
  33    // logical motor 5 = old motor 8
};

const int MAX_DUTY = 220;

// BLE UUIDs
#define SERVICE_UUID "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
#define CMD_UUID     "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

unsigned long offAt[MOTOR_COUNT] = {0};

bool connected = false;


// ------------------------------------------------------------
// MOTOR CONTROL
// ------------------------------------------------------------

void setMotor(int motor, int strength) {

  if (motor < 0 || motor >= MOTOR_COUNT)
    return;

  strength = constrain(strength, 0, MAX_DUTY);

  ledcWrite(MOTORS[motor], strength);
}


void buzzMotor(int motor, int strength, int durationMs) {

  if (motor < 0 || motor >= MOTOR_COUNT)
    return;

  setMotor(motor, strength);

  offAt[motor] = millis() + durationMs;

  Serial.printf(
    "Motor %d ON | strength=%d | duration=%dms\n",
    motor + 1,
    strength,
    durationMs
  );
}


void stopMotor(int motor) {

  if (motor < 0 || motor >= MOTOR_COUNT)
    return;

  setMotor(motor, 0);

  offAt[motor] = 0;
}


void stopAll() {

  Serial.println("STOP ALL");

  for (int i = 0; i < MOTOR_COUNT; i++) {
    stopMotor(i);
  }
}


// ------------------------------------------------------------
// MULTI-MOTOR COMMAND
// ------------------------------------------------------------

void buzzMask(
  uint8_t mask,
  uint8_t strength,
  int durationMs
) {

  Serial.printf(
    "GROUP mask=%02X strength=%d duration=%dms\n",
    mask,
    strength,
    durationMs
  );

  for (int i = 0; i < MOTOR_COUNT; i++) {

    if (mask & (1 << i)) {
      buzzMotor(i, strength, durationMs);
    }
  }
}


// ------------------------------------------------------------
// BLE CALLBACKS
// ------------------------------------------------------------

class ServerCallbacks : public BLEServerCallbacks {

  void onConnect(BLEServer *server) {

    connected = true;

    Serial.println("BLE CONNECTED");
  }


  void onDisconnect(BLEServer *server) {

    connected = false;

    stopAll();

    Serial.println("BLE DISCONNECTED");

    BLEDevice::startAdvertising();
  }
};


class CommandCallbacks : public BLECharacteristicCallbacks {

  void onWrite(BLECharacteristic *characteristic) {

    String value = characteristic->getValue();

    if (value.length() < 3)
      return;

    uint8_t command = value[0];


    // ========================================================
    // STOP EVERYTHING
    // ========================================================

    if (command == 0xFF) {

      stopAll();

      return;
    }


    // ========================================================
    // GROUP COMMAND
    //
    // FE MASK STRENGTH DURATION
    // ========================================================

    if (command == 0xFE) {

      if (value.length() < 4)
        return;

      uint8_t mask     = value[1];
      uint8_t strength = value[2];
      uint8_t duration = value[3];

      buzzMask(
        mask,
        strength,
        duration * 10
      );

      return;
    }


    // ========================================================
    // SINGLE MOTOR COMMAND
    //
    // MOTOR STRENGTH DURATION
    // ========================================================

    uint8_t motor    = value[0];
    uint8_t strength = value[1];
    uint8_t duration = value[2];

    if (motor >= MOTOR_COUNT)
      return;

    buzzMotor(
      motor,
      strength,
      duration * 10
    );
  }
};


// ------------------------------------------------------------
// SETUP
// ------------------------------------------------------------

void setup() {

  Serial.begin(115200);

  delay(500);

  Serial.println();
  Serial.println("==============================");
  Serial.println("HERE BELT — 6 MOTOR BLE");
  Serial.println("==============================");


  // Set up motors

  for (int i = 0; i < MOTOR_COUNT; i++) {

    ledcAttach(
      MOTORS[i],
      1000,
      8
    );

    ledcWrite(
      MOTORS[i],
      0
    );
  }


  // Start Bluetooth

  BLEDevice::init("HERE-Belt");

  BLEServer *server =
    BLEDevice::createServer();

  server->setCallbacks(
    new ServerCallbacks()
  );


  BLEService *service =
    server->createService(
      SERVICE_UUID
    );


  BLECharacteristic *command =
    service->createCharacteristic(
      CMD_UUID,

      BLECharacteristic::PROPERTY_WRITE |
      BLECharacteristic::PROPERTY_WRITE_NR
    );


  command->setCallbacks(
    new CommandCallbacks()
  );


  service->start();


  BLEAdvertising *advertising =
    BLEDevice::getAdvertising();

  advertising->addServiceUUID(
    SERVICE_UUID
  );

  advertising->setScanResponse(true);

  BLEDevice::startAdvertising();


  Serial.println("Bluetooth advertising as HERE-Belt");
}


// ------------------------------------------------------------
// MAIN LOOP
// ------------------------------------------------------------

void loop() {

  unsigned long now = millis();


  // Independently shut each motor off
  // when its timer expires.

  for (int i = 0; i < MOTOR_COUNT; i++) {

    if (
      offAt[i] != 0 &&
      (long)(now - offAt[i]) >= 0
    ) {

      stopMotor(i);
    }
  }


  delay(2);
}
