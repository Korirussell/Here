# HERE belt

8 coin motors at 45° around the waist, driven by an ESP32 through a ULN2803, with a WS2812B
status ring and an Anker power bank (README §16). The phone is the BLE central and decides *what*
to buzz; the ESP32 owns *how* (pattern timing), so BLE jitter can't smear the rhythm.

## Wiring

```
                 Anker power bank
            port A (5 V)        port B (5 V)          <- separate ports: motor spikes can't reset the ESP32
               │                     │
               │                ┌────┴─────┐
               │                │ ESP32    │
               │                │ 5V/VIN   │
               │                │      GND ├──────────────┐
               │                │          │              │  SHARED GROUND (all GNDs tied together)
               │                │  GPIO13 ─┼─► IN1        │
               │                │  GPIO14 ─┼─► IN2        │
               │                │  GPIO25 ─┼─► IN3        │
               │                │  GPIO26 ─┼─► IN4   ULN2803A
               │                │  GPIO27 ─┼─► IN5        │
               │                │  GPIO32 ─┼─► IN6        │
               │                │  GPIO33 ─┼─► IN7        │
               │                │  GPIO23 ─┼─► IN8        │
               │                │  GPIO18 ─┼──────────────┼──► WS2812B DIN (ring 5V -> port A, GND shared)
               │                └──────────┘              │
               │                                  GND (pin 9) ─┘
               ├──────────────────────────────► COM (pin 10)   <- COM TO THE MOTOR SUPPLY: enables the
               │                                                   ULN2803's flyback diodes
               ├──► motor 1 (+) ── motor 1 (−) ──► OUT1 (pin 18)
               ├──► motor 2 (+) ── motor 2 (−) ──► OUT2 (pin 17)
               │      ...                              ...
               └──► motor 8 (+) ── motor 8 (−) ──► OUT8 (pin 11)
```

| Motor | Direction | GPIO | ULN2803 in → out |
|---|---|---|---|
| 1 | Front | 13 | IN1 → OUT1 |
| 2 | Front-right | 14 | IN2 → OUT2 |
| 3 | Right | 25 | IN3 → OUT3 |
| 4 | Back-right | 26 | IN4 → OUT4 |
| 5 | Back | 27 | IN5 → OUT5 |
| 6 | Back-left | 32 | IN6 → OUT6 |
| 7 | Left | 33 | IN7 → OUT7 |
| 8 | Front-left | 23 | IN8 → OUT8 |

Checklist (README §16):
- **ULN2803 COM (pin 10) to the motor supply (+5 V)**, or the flyback diodes do nothing.
- **Shared ground** between the ESP32, the ULN2803 and both power bank ports.
- Separate power bank ports for motors and ESP32.
- Some power banks switch off at low draw: run the belt idle (just the dim LED pulse) for 30 minutes and
  check it stays on. If it doesn't, a small always-on load resistor or a bank with "trickle" mode fixes it.
- Motors at 45° steps, motor 1 at the navel, numbered clockwise as seen from above.

## Firmware

`firmware/here_belt/` (Arduino):

1. Arduino IDE → Boards Manager: **esp32 by Espressif, 3.x**. Board: *ESP32 Dev Module*.
2. Library Manager: **Adafruit NeoPixel**.
3. Open `here_belt.ino`, upload. The serial monitor (115200) prints `HERE belt ready`.

`belt_config.h` holds the pins, the BLE UUIDs (mirrored in `ios/HERE/Belt/BeltProtocol.swift`) and the
per-motor gains.

### Packet (BLE write without response)

`[version 0x01, pattern_id, motor 1..8, motor2 (0 = none), strength 0..255, priority]`

| ID | Pattern | Rhythm |
|---|---|---|
| 0 | stop | all off |
| 1 | speech | buzz-buzz (80 on / 80 off / 80 on) |
| 2 | name_called | buzz-buzz-buzz (100 / 80) |
| 3 | alert | 3 quick pulses ×2 (60 / 60) |
| 4 | emergency | fast pulses (50 / 50) until stopped, max 10 s |
| 5 | tick | single 40 ms |
| 6 | test | each motor in turn |

A higher-priority command interrupts the running pattern; STOP always wins. Losing the phone stops the
motors and restarts advertising.

### Bench test without a phone

Serial monitor, 115200, newline:

```
p 6 0          test: every motor in turn
p 1 3          speech on motor 3 (right)
p 3 5 255      alert on motor 5 (back), full strength
cal 5 255      set motor 5's gain and buzz it; prints MOTOR_GAIN = {...} to paste into belt_config.h
?              status
```

### Per-motor calibration

Through a shirt, back motors often feel weaker. With someone wearing the belt, buzz each motor
(`cal <m> 255`), lower the gains of the strong ones until all feel equal, then paste the printed
`MOTOR_GAIN` into `belt_config.h` and re-upload.

## From the phone

Settings → Belt: Connect, Strength, Test pattern, Front/Right/Back/Left. The belt indicator (bottom right
of the app) shows the last motor fired even when BLE is down.
