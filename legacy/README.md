# legacy (not maintained)

v1 code kept for reference during the v3 overhaul (see MIGRATION_PLAN.md). Nothing outside `legacy/`
imports from here. Don't fix or extend it; port what you need into the v3 layout instead.

| Folder | What it was | Why it's here |
|---|---|---|
| `ar_here/` | R3F web display of JPEG video with glass captions, plus a mock video server (copied from ../Here/ar_here) | the phone renders AR natively in v3; the styling may inform `extras/dashboard` |
| `vision_here/` | laptop YOLO + ByteTrack + MediaPipe lip-motion score on phone frames | v3 does faces on the phone; lip motion could come back as an iOS extra |
| `world_here/` | the v1 hub (routing, binary pairing) and laptop-side attribution (`fusion.py` + tests) | replaced by `relay/`; attribution ported to `ios/HERE/Attribution` |
| `tools/` | webcam / mic / DoA stand-ins, OpenCV debug view, v1 record/replay, dev.ps1 | they fed the v1 hub with video and binary audio |
| `ios_here/` | v1 iPhone app files (JPEG streaming, ws:// hub client, untyped contract), mock hub, v1 spike | v3: no video on the network, wss relay, Codable contract |
| `HERE_PLAN_v1.md` | the v1 master plan | superseded by README.md (v3) |
| `OVERHAUL_PROMPT.md` | the prompt for this overhaul | history |
