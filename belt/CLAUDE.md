# belt

Part of HERE. Read ../README.md §14 (belt logic, on the phone) and §16 (hardware, patterns, BLE) first.
Owner: teammate 3. Wiring and bench tests: belt/README.md.

## Job
ESP32 BLE peripheral playing haptic patterns on 8 coin motors (45 deg apart, motor 1 at the front,
clockwise) through a ULN2803, with a WS2812B status ring. The phone decides when and where to buzz; the
ESP32 owns pattern timing so BLE jitter can't smear the rhythm.

## Inputs / outputs
In: 6-byte packets on one write-without-response characteristic:
`[0x01, pattern_id, motor 1..8, motor2 (0 = none), strength 0..255, priority]`.
Patterns: 0 stop, 1 speech, 2 name_called, 3 alert, 4 emergency (max 10 s), 5 tick, 6 test.
Out: motors; LEDs (green connected, blue speech, yellow alert, red emergency, dim white pulse waiting).
Serial bench commands (115200) for tests without a phone.

## Run
Arduino IDE, esp32 core 3.x, board "ESP32 Dev Module", library Adafruit NeoPixel. Open
`firmware/here_belt/here_belt.ino`, upload, open the serial monitor.

## Rules
- UUIDs live in `belt_config.h` and `ios/HERE/Belt/BeltProtocol.swift`; change both
  (tests/test_consistency.py checks the UUIDs and pattern ids match).
- Higher priority interrupts lower; STOP always wins; a BLE disconnect stops the motors.
- Per-motor gains in `belt_config.h` (the `cal` serial command prints them).

## Status
Firmware written but **not compiled yet** (no Arduino toolchain on the laptop it was written on). Next:
compile, bench-test 2 motors + ESP32 (`p 1 1`), then all 8 + ULN2803 + power, then the phone
(Settings > Belt > Connect).
