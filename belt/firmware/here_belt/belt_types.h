// Types shared by the sketch. In a header because the Arduino build generates function prototypes
// at the top of the .ino, before any struct defined in it.
#pragma once
#include <stdint.h>

enum PatternId : uint8_t { STOP = 0, SPEECH = 1, NAME_CALLED = 2, ALERT = 3, EMERGENCY = 4, TICK = 5, TEST = 6 };

// A pattern is a list of (on_ms, off_ms) steps on the selected motor(s).
struct Step { uint16_t on_ms; uint16_t off_ms; };


struct Pattern { const Step* steps; uint8_t count; bool repeat; };

struct Command { uint8_t pattern, motor, motor2, strength, priority; };
