# vision_here

Part of HERE. Read ../HERE_PLAN.md (sections 6–9 at minimum) and ../contracts/ first. Laptop setup: ../SETUP.md.

## Job
Detect and track people and sound-source objects (TV, laptop, phone, dog, cat, vehicles) in the wearer's camera frames with YOLO + ByteTrack, and give each a world bearing from its pixel position, the frame's HFOV and the heading at the frame's capture time (§6). Processes only the newest frame; drops the rest.

For the largest people in view (`MAX_FACES`), `lips.py` runs MediaPipe's face landmarker on the head region of the person box. That gives `face_bbox` and a lip-motion `speaking` score: the spread of the `jawOpen` blendshape over `LIP_WINDOW_S`. world_here uses it to pick the talker when two people stand close together (§9.1). The model (`face_landmarker.task`, ~4 MB) downloads next to `lips.py` on first run. If MediaPipe fails, detections still flow and the heartbeat is degraded.

Does **not** do facial recognition (§2, §21): landmarks are used for the mouth and face box only.

## Inputs / outputs
Consumes: `video_frame` (+ JPEG), `heading`, `calibrate` (hub's rebroadcast with the heading offset). Publishes: `detections`.

## Run
`python -m vision_here`. Without the phone: `python tools/fake_phone.py --hfov <measured>` (webcam stand-in).

## Rules
- Follow the contract exactly; ask the integrator before changing message shapes.
- `t` of `detections` = the frame's capture time; bearings use heading interpolated at that time.
- Tunables (model, confidence, class map) live in `config.py`.
- Requires the CUDA 12.1 torch build on the demo laptop (SETUP.md); falls back to CPU with a degraded heartbeat.

## Current milestone
Working on webcam frames; face boxes + `speaking` checked on a still photo only. Next (spike, §16): FPS vs model size at the phone's resolution, track-ID stability when people turn or cross, and tuning `LIP_STD_FULL` on fixture 1 (talking vs listening people).
