# HERE iOS: handoff

Written 2026-10-10 for whoever picks up the phone app next (person or AI) with no prior context.
Deeper sources: `../README.md` (the project spec), `CLAUDE.md` in this folder (rules and layout),
`SPIKE.md` (on-device findings up to 2026-10-09), `../CHANGELOG.md` (history, newest first).

## Read first: backend state, 2026-10-10 evening (from the Windows laptop session)

**Setup that works:** ReSpeaker -> Windows laptop (GPU, all models) -> **relay on the Mac** -> iPhone, all on the
"iPhone 2" hotspot. The Windows laptop can't accept inbound connections (CrowdStrike), so it connects OUT to the
Mac (`../relay/MAC_RELAY.md`, `../tools/connect_to_mac_relay.ps1 -MacIp <mac-ip>`). Windows laptop on the hotspot: 172.20.10.5.
The phone connects to `ws://<mac-ip>:8080/ws`, session `demo` (any token while the Mac relay runs without one).

**Swift merged today that has NEVER been compiled** (written on Windows, no toolchain). Build and test first:
`cd ios && xcodegen generate && xcodebuild -scheme HERE -destination 'platform=iOS Simulator,name=iPhone 17' test`
(about 147 tests: 129 + RelayFailoverTests 6 + PresentationTests 5 + AttributionTests 7). Fix compile errors
before installing on the phone. The agents flagged their least certain lines in `../CHANGELOG.md` entries.
- Relay primary + fallback URL with automatic failover (`RelayClient`, `RelayFailover`, Settings > Relay).
- Empty `sources` holds the room baseline; sounds match their `sources` entry by `source_id`, and a failed match
  uses `fallbackProminence` (it was 0, so on the real array side sounds and unknowns never showed).
- `perception_enrichment` wired: attention sets sound prominence, only raises speech, never demotes an alert or
  name_called; `directed_at_user` >= 0.8 can buzz for speech; belt once per event.
- `Attributor` is spread-aware: the match window widens to min(`bearing_spread_deg`, 40).

**What the laptop now sends** (rows in `../IOS_INTEGRATION.md`):
- Speech bearings are the talker's real direction from the raw mics (was a flat 0 / 180 per beam), spread 8-15
  when confident, 30 when it only knows the beam's half. Captions should land on off-centre faces.
- `sound_event.source_id` on every sound (0 front beam, 1 rear).
- Many more sounds: door, knock, alarm, siren, car_horn, dog_barking, phone_ringing, loud_bang (was only doorbell
  and baby_crying), and more `unknown`s. The notification stack will be busier.
- Attention (`perception_enrichment.attention_model`): DeBERTa zero-shot. JEV-9B is set up as the "what deserves an
  alert" decider but not live until its GPU-contention check passes (it may appear as `autotrust/JEV-9B` later).
- Captions: ElevenLabs Scribe for a live A/B (`engine: scribe_realtime` in heartbeats), whisper as fallback.

**Calibration now (in this order):**
1. Laptop side: left/right check of the array (`python -m laptop.audio.localize --check`, someone talks front-left
   then front-right). Front/back is verified; left/right mirroring is not.
2. Phone: Settings > Calibrate (or Demo Setup), facing the array's front so the phone's 0 = the array's 0.
3. Check: talk front-left, front-right, behind. Caption on the right face; behind -> belt buzz + off-camera card.
   Debug overlay ATTRIB line shows the angle error and whether the window was widened by spread.

## Where things stand

- Branch `ios/build`, merged with `main` on 2026-10-10 and pushed to `main`.
- Builds clean. **129/129 `HERETests` pass** on the iPhone 17 Simulator.
- The last build installed on a phone (jennah's iPhone 17) predates the merge with `main`.
- **Almost nothing from 2026-10-10 has been confirmed on a real phone.** It was built and tested in
  the Simulator with scripted scenarios. See "Not verified on a phone" below before trusting any of it.

## What changed on 2026-10-10 (one day, four passes)

The phone's presentation layer and on-device vision were rebuilt. No contract, relay or laptop change
was made from this side.

1. **Spatial V2 UI** (default; the old look is Settings > Developer > Visual style > Legacy).
   Calm when idle. Sounds are notifications stacked top-right, one per active source: icon, color by
   meaning (green environment, red danger, blue unattributed human, gray question mark for unknown, a
   person's color once speech is theirs), a ring with a marker at the same relative bearing the belt
   uses, opacity = current smoothed intensity. Max 5 shown; the rest queue.
2. **Speakers**: a soft blurred glow around the head in the person's color, captions only on a tracked
   face. Speech from out of view is a notification with no floating caption; only a pinned person's
   notification carries their words.
3. **People** (`HERE/Identity/SessionPeople.swift`): `SessionPerson` is the identity; a Vision track
   attaches to it and detaches. Re-identification uses face-region and torso feature prints, where and
   when they were last known, body and audio continuity. Session pins live on the person. "Add to
   People" stores a name, color and priority across sessions.
4. **Objects** (`HERE/Scene/SceneObjects.swift`, `HERE/Vision/ObjectScanner.swift`): quiet objects are
   found by camera alone and remembered as `SceneObject`s. Two sources: Apple's region classifier
   (155 labels, coarse location, the only source for doors) and an optional bundled detector
   (`HERE/Models/`, Apple's YOLOv3: 79 classes with tight boxes).
5. **Fusion** (`Fusion` in `SceneObjects.swift`): a sound attaches to whichever known object occupies
   its direction. The kind of sound does not have to fit the object; it only breaks ties. Several
   candidates with nothing to set one apart: unattached.

## How to run it

```bash
brew install xcodegen                      # once
# optional, for tight object boxes (124 MB, not in git):
curl -L -o ios/HERE/Models/YOLOv3FP16.mlmodel \
  https://ml-assets.apple.com/coreml/models/Image/ObjectDetection/YOLOv3/YOLOv3FP16.mlmodel
cd ios && xcodegen generate
xcodebuild -scheme HERE -destination 'platform=iOS Simulator,name=iPhone 17' test
```

To a phone from the command line (team `UJ98JASRC2`; the phone must already be on the account):

```bash
xcodebuild -scheme HERE -destination 'generic/platform=iOS' -derivedDataPath build/dd-device \
  DEVELOPMENT_TEAM=UJ98JASRC2 CODE_SIGN_STYLE=Automatic build
xcrun devicectl device install app --device <udid> build/dd-device/Build/Products/Debug-iphoneos/HERE.app
xcrun devicectl device process launch --device <udid> --terminate-existing com.here.app
```

A locked phone refuses the launch; unlock it and tap the icon.

## Seeing what it does without hardware

Settings > Developer > Spatial scenarios: 62 scripted scenes (people, speech, sounds, alerts, objects,
turning) played on a loop with no laptop, microphone or belt. The same scenes back the SwiftUI
previews and the tests. `-scenario N -scenarioFreeze T` launch arguments hold one moment for a still.

Developer switches worth knowing:
- **Debug overlay**: stats and per-stage diagnostics (FACE / BODY / AUDIO / ATTRIB / REID / PEOPLE / UI).
- **Show tracking boxes** (under it, off by default): face and body rectangles with identity scores.
- **Scene Understanding**: object boxes/regions with class, confidence, id, state, scan time, plus
  PERSON and AUDIO lines showing what each sound is attributed to.
- **Body fallback**, **Object detection**, **Recognise people in People (experimental)**.

## Not verified on a phone (do these first)

1. **Identity.** Whether people keep their Person N and color when they move, leave and return. The
   mapping from feature-print distance to similarity (`printDistanceSame` 0.35,
   `printDistanceDifferent` 1.05 in `Tunables.swift`) is an estimate. Turn on "Show tracking boxes"
   and read the `reid ... visual 0.xx ... score 0.xx` line under a face; tune from those numbers.
2. **Object boxes.** The detector was only proven to load and run on a blank frame. Check real
   objects in Scene Understanding, and the scan time it reports.
3. **Classifier regions.** What the Apple classifier actually names per strip in a real room,
   especially doors, at `objectMinConfidence` 0.35.
4. **Aura and notifications on a real face and real sounds**, including animation feel.
5. **Heat.** This is the heaviest vision load the app has had (face detect + track, body detection,
   feature prints, detector ~3 Hz, classifier). The 20-minute test with AirPlay has never been run.
6. Everything still open in `SPIKE.md`'s on-device checklist (heading sign, field of view, belt).

## Known limits

- Two similar-looking people can swap after the 2 s "undecided" wait; a similar-looking stranger can
  inherit an absent person's identity.
- Doors get a region, not a tight box (the detector's classes have no door).
- Objects have no distance, are matched by class + bearing + size only, and a moved object is
  dropped and re-created.
- Recognising enrolled people across sessions is off by default and unreliable: Apple's feature print
  is not a face-recognition model. A real one was not added.
- With the built-in direction finding, the laptop's `source_id` is always 0, so audio continuity on
  the phone falls back to time and bearing.

## Things from `main` the phone receives but ignores

- `perception_enrichment.context` (the Gemini note) and `sound_event.model`.

Wired in later on 2026-10-10 (not yet built on a Mac, see CHANGELOG): `perception_enrichment.attention` /
`directed_at_user` set prominence in `PresenceEngine` (alerts, the name, danger and captions are only raised), and
`sound_event.source_id` picks the sound's own `sources` level. `"sources": []` no longer drags the room baseline to 0.

## Docs that are now out of date

`../README.md` §14 (rings, top-right panel, edge glows) and §25 ("no facial recognition") describe
the app before Spatial V2. They are the integrator's to edit.

## House rules

- Contract changes go through `contracts/build_schemas.py` and the integrator, never a hand edit here.
- Every number lives in `HERE/App/Tunables.swift`.
- Accessibility rules in README §14 hold: never cover a mouth, identity never by color alone,
  4.5:1 text contrast, two caption lines, respect Reduce Motion.
- Nothing from `reference/` is imported or copied.
