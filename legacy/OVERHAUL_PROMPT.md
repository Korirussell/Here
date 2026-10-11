# Prompt: overhaul the HERE codebase to match README.md

Copy everything below the line into your coding agent (Claude Code or similar), run from the repository root, with the new `README.md` already placed at the root.

---

You are overhauling the HERE codebase so it matches `README.md` at the repository root. **README.md is the source of truth.** Read it fully before touching any code, especially sections 3, 6, 7, 8, 11, 12, 13, 14, 16, 19, 20 and 22.

## Context

HERE is a hackathon project for deaf and hard-of-hearing users, demo in about five days. Architecture (README §6):

- **Windows laptop (hearing module):** ReSpeaker XVF3800 directions (built-in DoA baseline; ODAS optional), voice activity, streaming transcription (faster-whisper now, ElevenLabs Scribe later behind an interface), tone, name-called, doorbell classifier. Sends contract messages to the relay.
- **Vultr relay:** small TLS WebSocket server; routes JSON messages between clients in a session by type; keeps latest `config`; health endpoint.
- **iPhone app (Swift/SwiftUI, iOS 26):** camera preview, Vision face tracking, heading from a CoreMotion quaternion, attribution (direction → face), AR captions with Liquid Glass, rings, top-right sound panel, focus mode, belt-only mode, and **belt control over CoreBluetooth**.
- **ESP32 belt:** BLE peripheral, 6-byte packets, patterns timed on-device.
- **No audio or video crosses the network.** No ARKit. The laptop never needs the phone's heading.

The existing code may include: an `ar_here` web prototype (React Three Fiber + Python mock server streaming JPEG frames), an early iOS app, ReSpeaker test scripts, and a clone of another team's project ("Ranger"). Ranger has **no license**: it is reference reading only. **Never copy code from it.**

## Rules

1. **Do not delete working functionality without preserving it.** In particular, the friend's working ReSpeaker direction-reading code must keep working at every step. Move, wrap and adapt it; don't rewrite it from scratch.
2. **Work in phases** (below). At the end of each phase: everything runs, a short `CHANGELOG.md` entry says what changed, and you stop to report before starting the next phase if anything is ambiguous.
3. **Contract first.** All messages follow README §12 exactly (contract `v: 3`). If you think the contract needs a change, stop and propose it; do not change shapes silently.
4. **Ask before guessing** about hardware details (XVF3800 control commands, channel layout, BLE UUIDs, Vultr hostname). Put unknowns in config with clear TODOs.
5. **Secrets** (relay token, ElevenLabs key) only via `.env`/environment; add `.env.example` files; never commit real values.
6. **Every component runs without hardware** via mocks or recorded fixtures.
7. **Keep it simple.** This ships in five days. No frameworks or abstractions beyond what the README calls for.
8. Move external projects to `reference/` (gitignored) and make sure no file outside `reference/` imports from it or contains code copied from it.

## Phase 0: audit (no code changes)

1. List every directory and what it currently does, what runs, and how.
2. Map each existing piece to its new home per README §22, or mark it for `legacy/`.
3. List conflicts with the README (e.g. JPEG video streaming to the laptop, laptop-side vision, ARKit usage, laptop driving the belt, `ws://` hardcoded hosts, contract v1/v2 shapes).
4. Output the plan as `MIGRATION_PLAN.md` and stop for review.

## Phase 1: structure and contracts

1. Create the layout from README §22: `contracts/`, `relay/`, `laptop/{audio,speech,sounds,client}/`, `ios/`, `belt/firmware/`, `tools/`, `fixtures/`, `extras/`, `reference/`, `legacy/`.
2. Move existing code into place (git moves, so history is kept). The old `ar_here` web prototype and its mock video server go to `legacy/ar_here/` untouched.
3. Write `contracts/`:
   - JSON Schema per message type in README §12 (envelope, hello, heartbeat, clock_ping/pong, config, sources, speech_start, utterance, sound_event, alert, attribution, scene, search_request/result).
   - `contracts/python/here_contract.py`: dataclasses or pydantic models + `to_json`/`from_json` + validation.
   - `contracts/swift/HereContract.swift`: `Codable` structs mirroring the schemas.
   - `contracts/ts/contract.ts`: types for the extras.
   - `contracts/examples/*.json`: one valid example per type (use README §12.11), plus a test that validates every example against its schema in all three languages where feasible.
4. Add a root `CLAUDE.md` and one per component (template: job, inputs/outputs, run commands, rules, current milestone), each pointing to README.md.

## Phase 2: relay

Implement `relay/` per README §7:
- Python asyncio (`websockets`, or FastAPI if simpler), single file plus config.
- `wss` via **Caddy** in front (`relay/Caddyfile`), app listens on localhost.
- Query params `session`, `role`, `token`; reject bad tokens.
- Route by message `type` using each client's `hello.subscribe` (defaults per role from README §12.10).
- Keep the latest `config` per session; send it to new clients.
- Drop stale messages for slow clients instead of buffering; set `TCP_NODELAY`.
- `GET /health`.
- JSON-lines log of every message.
- `relay/DEPLOY.md`: step-by-step for a fresh Vultr Ubuntu instance in Atlanta (packages, systemd unit, Caddy, firewall, hostname via domain or sslip.io).
- `tools/relay_tap.py` (print a session's traffic) and `tools/fake_events.py` (scripted `sources` / `speech_start` / `utterance` / `alert` sequence for phone development; include the three core moments from README §4, with bearings for Person 1 front-right, Person 2 front-left, and someone behind).
- Tests: routing, auth rejection, config replay to new clients.

## Phase 3: laptop

`python -m laptop --real | --mock | --replay fixtures/<name>`

1. **audio:** wrap the existing working XVF3800 direction code behind an interface `DirectionSource` producing `Sources` messages (one source for built-in DoA). Apply `array_offset_deg` and clockwise/counter-clockwise from config to produce world bearings in (-180, 180]. Add a stub `OdasSource` that reads ODAS tracked-source output if/when available (behind config, off by default). Expose the processed audio stream to speech and sounds in-process (no network).
2. **speech:** Silero VAD + streaming faster-whisper (large-v3-turbo, CUDA, float16) re-running every 0.3–0.5 s on the current utterance; emit `speech_start` at onset (with the current bearing) and `utterance` updates with `text`/`pending`/`final`, circular-mean bearing, spread, overlap flag, loudness, simple tone (loudness + punctuation rules: shouting, whispering, questioning, neutral), and `engine`. Put the engine behind a `Transcriber` interface with a `ScribeRealtimeTranscriber` stub ready for an API key; fall back to whisper automatically on failure.
3. **name-called:** fuzzy match of `config.user_name` + aliases → `alert` of kind `name_called` with the utterance's bearing.
4. **sounds:** doorbell detection with YAMNet or PANNs on GPU, 0.5–1 s overlapping windows → `sound_event` + `alert` with bearing from recent sources. Other classes behind config, off by default.
5. **client:** one relay connection, `hello` as role `laptop`, heartbeats with `engine` and `doa_source`, reconnect with backoff, answer `clock_ping`.
6. All tunables in `laptop/config.yaml`. Logs in `logs/`.
7. Document Windows setup in `laptop/README.md`: Python env, CUDA 12 PyTorch, faster-whisper cuBLAS/cuDNN DLL fix, XVF3800 driver steps.

## Phase 4: iPhone app

In `ios/`, restructure the app (keep what works) into:
- `Camera/`: AVFoundation session, ultrawide, **landscape**, 30 fps, 1080p or 720p, preview layer, `alwaysDiscardsLateVideoFrames`.
- `Vision/`: face detection every few frames + `VNTrackObjectRequest` between; stable Person 1/2/3 labels with fixed colors (README §13).
- `Motion/`: heading via the quaternion method (README §8), calibrate offset. **No Euler yaw.**
- `Geometry/`: pixel↔bearing, wrap, relative bearing, motor selection, with unit tests.
- `Network/`: `URLSessionWebSocketTask` relay client (wss, token, session, hello as `phone`, reconnect, heartbeats with thermal state, clock ping), decoding `HereContract`.
- `Attribution/`: README §13 rules, unit-tested with synthetic faces and bearings.
- `Belt/`: CoreBluetooth central, scan/connect by service UUID (from config), 6-byte packets, belt rules from README §14, reconnect, on-screen indicator of the last motor fired.
- `UI/`: SwiftUI overlays: Liquid Glass captions (header with color dot, "Person N", tone chip; two-line body with dimmer pending words; tail), typing indicator on `speech_start`, speaker rings pulsing with loudness, top-right sound panel with direction ring, edge chevrons/glow for off-camera, status pill, debug overlay (face boxes, bearings, source markers, latency). Smoothed positions. Respect Reduce Motion.
- `Settings/`: relay URL/token/session, user name + aliases, HFOV, Calibrate, focus mode, belt-only mode, belt strength, belt connect/test.
- `Previews/`: SwiftUI previews of captions, rings and the panel with mock data.
- Thermal step-down per README §14.
- Remove any code that streams video to the laptop or depends on ARKit (move to `legacy/` if it's worth keeping).

**First milestone in this phase:** plain rectangles on faces that line up in landscape, with bearings shown in the debug overlay. Do not build captions before that works.

## Phase 5: belt firmware

`belt/firmware/`: ESP32 Arduino sketch: BLE peripheral with one service and one write-without-response characteristic; parse the 6-byte packet; patterns from README §16 timed on-device with priority interruption; LEDC PWM per motor through the ULN2803 with per-motor strength calibration constants; WS2812B status colors; `test` pattern. Include a wiring diagram in `belt/README.md` (pins, ULN2803 COM to motor supply, shared ground, separate power bank ports). UUIDs in one header file, mirrored in the iOS config.

## Phase 6: tools, fixtures, checks

- `tools/replay.py`: replay recorded `sources` + audio fixtures through the laptop pipeline into the relay.
- `fixtures/README.md`: how to record the sessions listed in README §20/§21.
- A root `Makefile` or `justfile` with: `relay`, `laptop-mock`, `laptop-real`, `fake-events`, `tap`, `test`.
- End-to-end smoke test: relay + `fake_events.py` + a headless test client asserting the phone-side subscription receives the expected sequence.

## Definition of done for the overhaul

- Layout matches README §22; nothing outside `reference/` uses Ranger code.
- `contracts/` validated by tests in Python and Swift.
- Relay deployable to Vultr from `relay/DEPLOY.md`.
- `make fake-events` makes the iPhone app show captions, rings, the top-right panel, and drive the belt (or the on-screen belt indicator) with no audio hardware.
- `python -m laptop --real` sends real `sources`, `speech_start`, `utterance` and doorbell/name-called `alert` messages to the relay.
- Each component has a `CLAUDE.md`; `CHANGELOG.md` and `MIGRATION_PLAN.md` describe what moved where.

Start with Phase 0 and stop for review.
