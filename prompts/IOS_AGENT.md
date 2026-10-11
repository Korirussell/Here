# Kickoff: iPhone app agent

Paste everything below the line into Claude Code on a **Mac with Xcode 26** (iOS 26 SDK), started at the repo
root. The human will check in rarely and has an iPhone available only occasionally: work autonomously, verify
with the compiler, the unit tests and the Simulator, and save on-device checks for a short human checklist.

---

You own the **HERE iPhone app** in `ios/`: the "glasses" (camera, Vision faces, heading, attribution, AR
captions with Liquid Glass, rings, top-right sound panel, belt over CoreBluetooth). Another agent owns `laptop/`;
don't touch `laptop/`, `relay/`, `belt/` or the contract shapes.

**Important context:** the whole app was written on Windows **without a Swift compiler**. It has never been
built. Expect compile errors; your first job is to make it build and pass its tests without changing behaviour.

## Read first (in this order)
1. `README.md` (source of truth; §4 core moments, §6 architecture, §8 coordinates, §12 contract, §13 attribution,
   §14 the iPhone app, incl. the accessibility rules, §16 belt BLE).
2. `CLAUDE.md`, `ios/CLAUDE.md`, `contracts/CLAUDE.md`, `ios/SPIKE.md`, `CHANGELOG.md`.
3. `contracts/swift/HereContract.swift` (compiled into the app), then `ios/HERE/` top-down: `App/AppModel.swift`,
   `Attribution/`, `Geometry/`, `Camera/`, `Vision/`, `UI/`.

## Setup
- `git fetch && git checkout overhaul/v3 && git pull`, then `git checkout -b ios/build`. Commit small and often;
  push your branch after each task. Never force-push, never push to `main`.
- `brew install xcodegen` (once), `cd ios && xcodegen generate`. The `.xcodeproj` is generated: don't commit it;
  edit `ios/project.yml` instead.
- Build/test headless:
  `xcodebuild -project HERE.xcodeproj -scheme HERE -destination 'platform=iOS Simulator,name=iPhone 17' test`
  (pick any available iOS 26 simulator: `xcrun simctl list devices`).
- No laptop needed for data: a relay + scripted events from Python:
  `python -m relay --host 0.0.0.0 --port 8080` and `python tools/fake_events.py --relay ws://localhost:8080/ws --loop`
  (needs `pip install websockets pyyaml jsonschema`). In the app: Settings > Relay `ws://localhost:8080/ws`
  (Simulator) and **Settings > Simulated faces** (on by default in the Simulator).

## Ground rules
- **Contract first:** don't change `contracts/swift/HereContract.swift` shapes. Compile fixes that keep the
  JSON identical are fine; run `python -m pytest contracts tests` after touching it. Shape proposals go in
  `prompts/CONTRACT_PROPOSALS.md`.
- Keep the logic that's unit-tested (Geo, Frames, Attributor, BeltPolicy) behaviourally identical unless a
  test proves it wrong; if so, fix the test and the code together and say why in CHANGELOG.
- Accessibility rules in README §14 are non-negotiable.
- No ARKit. No video or audio on the network. Tunables in `HERE/App/Tunables.swift`.
- `tests/test_consistency.py` checks belt UUIDs/patterns and README thresholds against the Swift sources: keep it passing.

## Tasks, in order (each ends with: builds, tests pass, CHANGELOG entry, commit, push)

1. **Make it build.** Fix compile errors with the smallest changes. Likely suspects: iOS 26 API names
   (`glassEffect`, `AVCaptureDevice.RotationCoordinator`), Swift 6 toolchain strictness, `Text` interpolation,
   switch expressions. Then make `HERETests` pass (including `ContractTests` decoding all 14 examples).
2. **Simulator end-to-end.** With the relay + `fake_events.py --loop` and simulated faces: captions land on
   Person 1 (+35°) and Person 2 (-35°), the behind-you line shows at the screen edge with a chevron, typing dots
   on speech_start, rings pulse, the doorbell and name-called cards appear top-right with the direction ring,
   the belt indicator lights the right motor. Fix what's wrong. Capture screenshots
   (`xcrun simctl io booted screenshot`) into `ios/screenshots/` and describe each in `ios/SPIKE.md`.
3. **Turn-around in the Simulator.** Add a debug control (Settings > Developer) to rotate the simulated heading
   (e.g. a slider), so "someone behind → turn → caption slides onto their face" can be exercised without a phone.
   Verify the caption animates from the edge onto the face.
4. **SwiftUI previews** all render (captions, rings, panel, tone chips) in light and dark scenes; check contrast
   of pending words against a bright background (≥ 4.5:1 per README §14).
5. **Layout tests:** unit-test `Layout.captions` (above-head vs below-chin, edge placement for off-camera, no
   overlap between two close speakers, never over the mouth region).
6. **Projector / coordinate tests** for captureAngle 0/90/180/270 and the simulated fallback mapping; this is
   where on-device bugs will hide.
7. **Thermal + reduce-motion paths:** simulate `.serious` / `.critical` (debug toggle) and Reduce Motion; verify
   the simplified glass and halved detection rate kick in.
8. **Relay robustness:** stop/start the relay while the app runs: status pill goes red → green, captions resume,
   config is re-sent. Wrong token → clear message in the status pill/settings, not a silent failure.

## Human checklist (write it, don't wait for it)
You can't verify the camera, Vision on real faces, heading, BLE or thermals without a device. Keep a short,
ordered checklist at the top of `ios/SPIKE.md`, e.g. "1. Install on iPhone, landscape, Debug overlay on: do the
face boxes sit on faces? (both landscape orientations)". The human runs it in 10 minutes when they can; put the
first-milestone check first: **plain rectangles on faces that line up in landscape** (README §14).

## When you're blocked
Write it under "Blocked / needs a human" at the top of `ios/SPIKE.md` with exactly what to do, then move on.

## Done means
Builds clean, all tests pass, Simulator flow verified with screenshots, the human checklist is ready, and every
task has a CHANGELOG line and a pushed commit.
