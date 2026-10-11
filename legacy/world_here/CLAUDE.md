# world_here

Part of HERE. Read ../HERE_PLAN.md (sections 6–9 at minimum) and ../contracts/ first (until `contracts/` exists, HERE_PLAN.md §8 is the contract). Laptop setup: ../SETUP.md.

## Job
The hub. WebSocket server on :8765 that every service connects to. Routes messages by each client's `hello.subscribe`, answers `clock_ping`, holds calibration and the active `config`, runs fusion (speech attribution, §9.1) and builds one `frame` message per video frame for ar_here. Logs every inbound JSON message to `logs/world_here.jsonl`.

Mouth-movement scoring is in: `detections.speaking` breaks ties between people near the speech bearing, and picks the talker when there's no DoA (§9.1, `LIPS_*` in config.py).

Not done yet: sound-event attribution (§9.2) using `sound_doa` (join on `event_id`), alerts (§10), belt decisions (§9.4), memory forwarding, use of `doa.beams` for overlapping talkers.

## Inputs / outputs
Consumes: everything. Publishes: `frame` (+ JPEG), `config`, `calibrate` (rebroadcast with value), `clock_pong`; forwards all other messages to subscribers unchanged.

## Run
`python -m world_here` from the repo root. Full stack with stand-ins: `.\tools\dev.ps1`. Tests: `python -m pytest tests`.

## Files
- `hub.py`: server, routing, binary pairing, health, logging
- `fusion.py`: `World`: entities, DoA history, attribution, `frame_meta()`
- `config.py`: all thresholds (§9 values marked *tune*)

## Rules
- Follow the contract exactly. Contract changes go through the integrator (this project's owner) and bump `v`.
- Timestamps are observation times on the laptop clock (§7). Attribution uses message times; caption display ages from hub receive time so late finals still show.
- Bearings: world frame, clockwise positive, (−180, 180].
- Drop stale real-time data (`DROPPABLE` in hub.py) instead of queueing.

## Contract clarification (needs integrator sign-off)
The phone sends raw heading; the hub stores the heading-zero offset (§6). vision_here also needs that offset to emit world bearings, so on calibration the hub **rebroadcasts `calibrate {kind: "heading_zero", value: <offset>}` with `src: "world_here"`**, and sends the current one to any client that subscribes to `calibrate` on connect.

Optional additions approved at v=1 (2026-10-08, additive, old consumers unaffected): `doa.beams`, the `sound_doa` message, `detections.objects[].speaking`. See HERE_PLAN.md §8.5–8.6.

## Current milestone
M0 walking skeleton is done with stand-ins (webcam, mic/WAV, slider DoA). Next: M3 attribution ≥ 90% on fixture 1 once fixtures exist; then alerts and belt commands.
