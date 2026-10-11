# ios (the HERE app: the glasses)

Part of HERE. Read ../README.md §6, §8, §13, §14, §16 first. The contract is
../contracts/swift/HereContract.swift, compiled straight into the app (one copy). Owner: Kori.

## Job
Native Swift/SwiftUI, iOS 26, iPhone 17 in a landscape chest mount, ultrawide lens. No ARKit.
Camera preview (video never passes through our code for display), Vision face detection + tracking
(Person 1, 2, 3 with fixed colors), heading from the CoreMotion quaternion, **attribution on the phone**
(direction -> face), the AR overlay (Liquid Glass captions, typing dots, speaker rings, top-right sound
panel with a direction ring, off-camera chevrons and edge glow, status pill, debug overlay), and **the belt
over CoreBluetooth** (the phone picks motor and pattern). Mirrored to the judges' monitor with AirPlay.

## Inputs / outputs
Relay `wss://<host>/ws?session=&role=phone&token=`. Receives `sources`, `speech_start`, `utterance`,
`sound_event`, `alert`, `config`, `clock_pong`, `heartbeat`. Sends `hello`, `config`, `heartbeat`
(with thermal), `clock_ping`; `attribution` + `scene` only with Settings > "Send scene" (extras).
BLE: 6-byte packets to the belt (UUIDs in `HERE/Belt/BeltProtocol.swift`, mirrored in belt_config.h).

## Layout (`HERE/`)
- `App/`: HEREApp, AppModel (state, wiring, belt rules), Tunables
- `Camera/`: session + rotation coordinator, preview, Projector (rect/bearing <-> screen)
- `Vision/`: FaceTracker (detect every few frames, track in between), SimulatedFaces
- `Motion/`: HeadingProvider (quaternion method, Kori's v1 code)
- `Geometry/`: Geo (wrap, pixel <-> bearing, motor, words), Frames (Vision -> sensor rects)
- `Network/`: RelayClient, ClockSync
- `Attribution/`: Attributor (README §13 rules)
- `Belt/`: BeltProtocol (UUIDs, packet, BeltPolicy), BeltController (CoreBluetooth)
- `UI/`: ContentView, OverlayView + Layout, CaptionView, SoundPanel + DebugOverlay, Indicators, Theme
- `Identity/`: SessionPeople (SessionPerson above Vision tracks, ReID scoring, audio continuity, session
  pins) and KnownPerson (explicit, persistent "People"). On-device only; never sent to the relay.
- `Scene/` + `Vision/ObjectScanner`: quiet objects. SceneObjects (persistent SceneObject, ObjectReID,
  Fusion: sound -> whatever occupies its direction); the scanner classifies frame regions with Apple Vision about once a second.
- `Presentation/`: Spatial V2's derived state. SpatialPresence/SpatialScene, RoomBaseline + Relevance,
  PresenceEngine (one notification per active source; opacity = current intensity; uniform lifecycle).
  Presentation only: never filters ingestion, never touches the contract.
- `UI/Spatial*`: the V2 layer (top-right notification stack with direction rings, aura, face captions,
  pin / add-to-people). `Settings.visualStyle` picks V2 (default) or the legacy `OverlayView`.
- `Settings/`, `Previews/` (captions, rings, panel with mock data; `SpatialScenarios` = 72 scripted
  scenes for Settings > Developer, previews and tests)
- Tests in `HERETests/`: geometry, attribution, belt rules, contract decoding (all 14 examples), presentation

## Run (on the Mac)
```bash
brew install xcodegen        # once
cd ios && xcodegen generate && open HERE.xcodeproj
```
Set your team (Signing & Capabilities), pick the iPhone, Run. Tests: Cmd+U.
No laptop needed: `python tools/fake_events.py --loop` drives captions, rings, the panel and the belt.
In the Simulator (or Settings > Simulated faces) two fake people stand at +-35 deg so fake_events lands on them.

## Rules
- **First milestone: plain rectangles on faces that line up in landscape** (Settings > Debug overlay shows
  boxes + bearings). Don't polish captions before that works (README §14).
- Never Euler yaw: heading = wrap(raw - offset) from the quaternion method.
- Vision gets upright buffers (rotation coordinator); rects go back through `Frames.sensorRect` and
  `layerRectConverted(fromMetadataOutputRect:)`. Bugs live here; GeometryTests cover the math.
- Bearing-from-pixel math (`Geo.bearing`/`normalizedX`) needs the FOV for whichever sensor axis is
  currently horizontal, not always the camera's native landscape FOV -- `Geo.effectiveHFOVDeg` picks
  horizontal vs vertical FOV from `captureAngle`, so any mount (landscape/portrait/upside down) stays
  correct, matching `HeadingProvider`'s own "any mount" heading guarantee.
- README §14 accessibility rules are non-negotiable (never cover a mouth; identity never by color alone;
  >= 4.5:1 contrast; two lines max; respect Reduce Motion).
- Tunables in `HERE/App/Tunables.swift`; user-facing settings in `Settings`.
- Contract types only from HereContract.swift; shape changes go through the integrator.

## Status
Builds clean, 126/126 `HERETests` pass (`xcodegen generate` + `xcodebuild ... test`, Xcode 26 / iOS 26 SDK).
Every README §4 core moment verified against `tools/fake_events.py` in the Simulator with screenshots
(`ios/screenshots/`); details and bugs found-and-fixed along the way (stacked `name_called` alerts in
the fixture, a caption-contrast violation under bright scenes, a silent "wrong token" relay failure,
caption tracking lag/freeze-in-place, face-label cycling, duplicate-person tracking) are in `SPIKE.md`.
Has a real app icon + launch screen (`HERE/Assets.xcassets`, placeholder branding -- not design work).
Settings is split: the main screen is demo-facing only (Calibrate, You, Modes, Show, Alerts, Belt,
Relay, plus a "Demo Setup" guided walkthrough of README §23's checklist); rehearsal-only controls
(simulated heading/thermal, test-injection buttons) live under a separate "Developer" screen.
Unverified on hardware: heading sign, real HFOV, face-box alignment in both landscape orientations,
BLE in a crowded room, 20-minute thermals with AirPlay -- the on-device checklist at the top of SPIKE.md.
The 2026-10-09 AR/HUD polish pass (thermal-headroom glass fallback, haptics, ring/alert visual tweaks)
deliberately stayed out of camera/Vision/attribution internals; see SPIKE.md's dated entry for what
was and wasn't screenshotted.
