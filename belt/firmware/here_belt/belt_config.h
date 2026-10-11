// HERE belt: pins, BLE identifiers and per-motor calibration (SPEC §16).
// The BLE UUIDs are mirrored in ios/HERE/Belt/BeltProtocol.swift (BeltUUID). Change both together.
#pragma once

#define BELT_NAME            "HERE-Belt"
#define BELT_SERVICE_UUID    "bfedc85a-c49b-4aed-91cf-69b7e34b2772"
#define BELT_COMMAND_UUID    "bfedc85b-c49b-4aed-91cf-69b7e34b2772"   // write without response, 6 bytes

#define PACKET_VERSION       0x01

// Motors 1..8 clockwise from the front (1 Front, 2 Front-right, 3 Right, 4 Back-right, 5 Back,
// 6 Back-left, 7 Left, 8 Front-left). Each GPIO drives one ULN2803 input (IN1..IN8).
// Avoid strapping pins (0, 2, 5, 12, 15) and input-only pins (34..39).
static const uint8_t MOTOR_PINS[8] = {13, 14, 25, 26, 27, 32, 33, 23};

// Per-motor strength calibration, 0..255 multiplier /255 (SPEC §16: back motors often feel weaker
// through clothes). Tune with the serial "cal" command, then copy the values here.
static uint8_t MOTOR_GAIN[8] = {255, 255, 255, 255, 255, 255, 255, 255};

// Below this PWM duty the coin motors stall; any non-zero strength is lifted to at least this.
#define MOTOR_MIN_DUTY       90

#define PWM_FREQ_HZ          5000
#define PWM_RESOLUTION_BITS  8

// WS2812B status ring
#define LED_PIN              18
#define LED_COUNT            12
#define LED_BRIGHTNESS       40      // 0..255; keep low: it's for the wearer's helpers, not the room

// An emergency pattern stops by itself after this (SPEC §16: max 10 s).
#define EMERGENCY_MAX_MS     10000
