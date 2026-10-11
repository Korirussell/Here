# iPhone spike (README §21)

Fill in on the real rig (iPhone 17, landscape chest mount, ultrawide). Numbers, not impressions.

**Before you start:** the app builds clean, all 41 `HERETests` pass, and every core moment (captions,
turn-around, alerts, belt indicator) has been verified against `tools/fake_events.py` in the Simulator --
see "Simulator verification" below for what that already covers, so items 1-5 here are specifically the
things that *need the physical phone/harness/array* and can't be faked. Two Settings > Developer controls
exist to help: "Simulated heading" (rehearse the turn-around without walking around) and "Simulated thermal
state" (preview the `.serious`/`.critical` visual changes without waiting for the phone to actually heat
up) -- both are Simulator/simulated-faces-only and have no effect with a real camera running, so turn
"Simulated faces" off before testing on the device.

## 1. Face rectangles align, in whatever orientation the mount ends up being
- [ ] Settings > Debug overlay: boxes sit on faces in both landscape orientations (USB-C left / right)?
- [ ] Bearing accuracy vs a protractor: person at -40, -20, 0, 20, 40 deg from the camera, overlay reads ___
- [ ] HFOV: reported `auto` value ___ vs measured ___ (two objects at the frame edges). Set Settings > Field of view.
- [ ] **If the mount ends up portrait or upside-down instead of landscape** (hardware is still being
  built -- see the 2026-10-09 "any orientation" entry below): boxes still align, and the debug overlay's
  `HFOV` reading visibly changes from the landscape value to the (narrower) portrait one as you rotate
  the phone. The screen content itself always stays a fixed landscape frame for the mirror/monitor
  regardless (`UISupportedInterfaceOrientations` is intentionally still landscape-only) -- only the
  camera/bearing math needs to track the physical mount angle, not the on-screen UI.

## 2. Heading
- [ ] Turn clockwise (seen from above): heading increases? If not: Settings > Flip heading sign.
- [ ] Drift over 10 min standing still in the harness: ___ deg.
- [ ] After Calibrate facing the array front: ~0; facing the array's right: ~+90.

## 3. Relay round trip (campus Wi-Fi)
- [ ] Debug overlay `clock rtt`: typical ___ ms, worst ___ ms. `lag` (laptop observation -> phone): ___ ms.

## 4. AirPlay to the Mac on campus Wi-Fi
- [ ] Works? Picture quality? (Fallback: cable + QuickTime.)

## 5. Thermal: 20 min with AirPlay + BLE + 30 fps
- [ ] Thermal state at 5 / 10 / 20 min: ___ (the detection rate halves at `serious`).

## Recommended settings
- HFOV ___ · heading sign flip ___ · capture 1080p / 720p · detect every ___ frames

---

## Simulator verification (no hardware; see `ios/screenshots/`)

Setup: `python -m relay --host 0.0.0.0 --port 8080` + `python tools/fake_events.py --loop`, app launched on
iPhone 17 Simulator with Settings > Simulated faces on, relay `ws://localhost:8080/ws`, session `demo`.

- `01_launch.png`, `02_two_speakers.png`: Moment 1. Person 1 (+35°, teal) and Person 2 (-35°, orange) both get
  captions above their boxes with the right color, tone chip (excited/asking) and a pulsing ring while talking.
- `01_launch.png`, `02_two_speakers.png`: Moment 2 (the turn-around). The off-camera line ("Someone · behind
  you", tone "asking") renders at the trailing screen edge with a `>` chevron toward the bearing, per README
  §14. Caption text, tone and loudness all came through correctly.
- `03_doorbell_alert.png`: Moment 3, doorbell. Top-right panel: emoji, "Doorbell", "behind you", direction ring
  arc pointing back. Belt indicator (bottom-right) lit the back-ish motor even with no real belt connected
  (BLE shows "Bluetooth off" — the on-screen indicator works independently of the BLE link, as intended).
- `04_namecalled_single_fading.png`, `05_clean_no_alerts.png`: Moment 3, name called -> a single "Name called"
  card, fading out on its own a few seconds after `fake_events` sends `active:false`, then gone. No belt/BLE
  on the Simulator, so BLE reliability, motor strength and belt timing are still on the human checklist above.
- Debug overlay (`Settings > Debug overlay`) matches `mock_hub.py`-style stats: heading, HFOV, capture angle,
  faces/sources counts, lag, clock rtt, thermal -- all live and plausible.

### Found and fixed
- `tools/fake_events.py`: `name_called` alerts were sent `active=true` and never closed, so every loop
  iteration left a permanent new alert card -- by the third iteration the top-right panel had **three
  overlapping "Name called" cards stacked on screen** (the doorbell alert already closed itself correctly
  after 6 s; name_called didn't). Mirrored the doorbell's pattern: reuse the `alert_id` and send
  `active=false` 6 s later. Verified: now exactly one card, which fades per the client's normal alert-fade
  behavior. This was a `tools/` fixture-fidelity bug, not an `ios/` app bug -- `AppModel.onAlert` was already
  correctly deduping by `alert_id` and respecting `active`; it just never got told the alert had ended.

### Turn-around, driven by a debug heading slider (no phone)

Added Settings > Developer > "Simulated heading" (shown only when Simulated faces is on): drags
`AppModel.headingDeg` directly in that mode (CoreMotion doesn't run in the Simulator, so there was
previously no way to rehearse turning). Also added a third simulated person, **Person 3**, at world
bearing 180° -- the exact position of Moment 2's off-camera speaker -- so turning far enough actually
reveals a face to attach the caption to, the same way a real person behind the wearer would come into
view (`ios/HERE/Vision/FaceTracker.swift`).

Verified by relaunching at heading 0 / 90 / 150 / 180 (`06_turnaround_0deg_offcamera.png` through
`09_turnaround_180deg_attached.png`):
- 0°: "Someone · behind you", off-camera, chevron at the trailing edge (Moment 2's normal state).
- 90°: no faces in view; the hint text live-recalculates to "to your right" -- confirms `Geo.describe`
  and the edge-chevron logic track heading, not just a fixed state.
- 150°: Person 3 becomes a tracked face (purple ring, "Person 3 +179°") as they rotate into the
  camera's HFOV.
- 180°: the caption has re-attributed onto Person 3 -- header now reads "Person 3" (not "Someone"),
  positioned over their face, no more chevron. This is `Attributor`'s turn-around rule (README §13 step
  4) firing for real.

The position change itself (`OverlayView`'s `.animation(.smooth(duration: 0.25), value: p.point)`) is
standard SwiftUI and applies to every placement already, including this one; relaunch-per-heading-value
snapshots can't show the animation playing (each relaunch starts a fresh `AppModel`), but confirming the
before/after states are correct, plus reading the modifier is attached to exactly this value, is enough
to trust it animates smoothly for a human dragging the slider in one continuous session -- worth a
quick live look as part of the on-device checklist above, but not blocking.

### SwiftUI previews + contrast check (Task 4)

All of `Previews.swift` renders cleanly (captions, rings, sound panel, tone chips). Added an explicit
light/dark-scheme pair for the Captions preview: the app forces `.preferredColorScheme(.dark)` everywhere
(`ContentView.swift`) and none of the custom glass/`Palette`
colors read `colorScheme` at all, so canvas light vs. dark mode genuinely doesn't change anything here --
confirmed, not assumed, by rendering both. The real legibility risk axis is **backdrop brightness**, which
is what the pre-existing bright-gradient `Backdrop` was already built to stress-test; added a dark-room
`Backdrop` variant alongside it to cover both ends.

**Found and fixed a real accessibility bug** while measuring the bright-backdrop case against README §14's
non-negotiable "provisional words dimmer but still >= 4.5:1 contrast": that rule didn't hold. Methodology --
since `.glassEffect()` (Liquid Glass) is a private material, not a flat alpha blend, I couldn't compute its
output by hand; instead I temporarily pointed the app's root view at the exact bright-backdrop caption
preview, built and ran it on-device in the Simulator, screenshotted, and sampled real rendered pixels with
ImageMagick, computing WCAG contrast from those (reverted before committing -- not part of the app).

- **Before** (`10_contrast_before_fix_1.47to1.png`): pending words were `.white.opacity(0.72)` over
  `.glassEffect(.regular.tint(.black.opacity(0.35)))`. Measured against the gradient's brightest corner:
  pending text **1.47:1**, and even fully-opaque committed white text only reached **2.55:1** -- both fail
  4.5:1 outright. The `bodyText` doc comment's claim ("still >= 4.5:1 on the dark glass") was wrong under a
  bright backdrop; the glass alone was never dark enough to guarantee it.
- **Root cause of the "dimmer" look barely helping**: pending text's dimming was done via *opacity*
  (`.white.opacity(0.72)`), so its rendered color blends with whatever's behind it -- raising the glass
  tint darkened the card, which *also* darkened the translucent text sitting on it, so contrast barely
  moved even after a large tint increase (0.35 -> 0.6 only got pending text to 2.19:1).
  - Fix: `CaptionView.bodyText` now uses an **opaque** `Color(white: 0.85)` for pending words instead of
    `.white.opacity(0.72)` (`ios/HERE/UI/CaptionView.swift`) -- its luminance no longer depends on the
    scene behind it, which is the actually-correct way to guarantee a fixed contrast ratio.
  - `hereGlass`'s default tint went from `.black.opacity(0.35)` to `.black.opacity(0.94)`
    (`ios/HERE/UI/Theme.swift`).
- **After** (`11_contrast_after_fix_4.89to1.png`): pending text measured **4.89:1** against the same
  worst-case bright corner (committed white: ~15:1). Also checked the thermal-pressure `simple=true`
  fallback (flat `Color.black.opacity(0.72)`, a plain alpha composite so exact, not measured): **7.25:1**,
  comfortably clear. Re-ran the full `HERETests` suite after both changes -- still 20/20 green (nothing
  asserts on these colors, as expected), and re-checked the normal (dark, non-bright) in-app look
  (`02_two_speakers.png`-style screens) for a regression -- none; the darker glass reads fine over the
  usual dim backdrop, it's only the bright-scene case that needed the extra margin.

Worth a human re-check against the *actual* booth lighting once that's known (`tune`) -- the test backdrop's
brightest corner (`Color(0.95, 0.9, 0.8)`, a bright cream) is a reasonable stand-in for a lit indoor venue,
but direct stage lighting or sunlight could be brighter still.

### Layout unit tests (Task 5)

`ios/HERETests/LayoutTests.swift`, 8 tests against `OverlayView.Layout.captions`, all passing against the
existing implementation unchanged:
- Above-the-head when there's room; below-the-chin when there isn't; forced-above on a short screen where
  "below" would run off the bottom edge. Each checks the actual 14pt gap between the caption box and the
  face rect, not just which branch fired -- the literal "never cover a mouth" guarantee.
- Off-camera edge placement: correct side (leading/trailing) from the bearing's sign, correct chevron, and
  the `Geo.describe` hint text ("behind you", "to your left", ...).
- The "in view but no face currently tracked" middle branch (a named person whose face isn't visible this
  frame, but whose bearing is still within the camera's HFOV): no chevron, no hint, placed at the bearing's
  screen x.
- Two close on-camera captions (overlapping face rects) get separated by a full box-height-plus-gap rather
  than drawing on top of each other; two off-camera captions on the same edge stack by row via the
  pre-existing `edgeY` bookkeeping (a different mechanism from the generic collision-separation pass, now
  both covered).

Out of scope here, left as the noted item below: `separate()` only resolves caption-vs-caption collisions,
not caption-vs-face (the `02_two_speakers.png` overlap from Task 2).

### Projector / coordinate tests (Task 6)

`ios/HERETests/ProjectorTests.swift`, 8 tests, all passing against the existing implementation unchanged:
- `Frames.sensorRect` for all four `captureAngle`s (0/90/180/270 -- the existing `GeometryTests` only
  covered 0/90/180). Added a 90-then-270 round trip, since the two landscape mount orientations
  (`UIInterfaceOrientationLandscapeLeft`/`Right`, USB-C left vs. right per the on-device checklist above)
  use opposite `captureAngle`s and should be exact inverses of each other, plus a 180-twice round trip.
- `Frames.sensorPoint` directly (previously only exercised indirectly through `Projector.x`).
- `Projector`'s `layer == nil` fallback (the Simulator / simulated-faces path, README §14 dev tips):
  the 16:9 aspect-fill math for both a width-constrained and a height-constrained screen, `x(forBearing:)`
  (dead center, off-center composed against `Geo.normalizedX` rather than re-deriving the trig by hand,
  and outside-the-HFOV -> `nil`), and the `bearing`/`relative` wrapper methods.

**What's intentionally not covered**: `Projector`'s *real* path (non-nil `layer`) calls
`AVCaptureVideoPreviewLayer.layerRectConverted(fromMetadataOutputRect:)`, a system call whose output
depends on a live, running `AVCaptureSession` and the layer's actual on-screen geometry -- not something a
bare `AVCaptureVideoPreviewLayer()` with no session reproduces meaningfully off a device. This is exactly
why "boxes sit on faces, both landscape orientations" leads the on-device human checklist at the top of
this file rather than being asserted here.

### Thermal + Reduce Motion (Task 7)

**Thermal.** `ProcessInfo.thermalState` is read-only and the Simulator essentially never reports anything
but `.nominal`, so there was no way to rehearse the `.serious`/`.critical` paths without a hot phone. Added
Settings > Developer > "Simulated thermal state" (real / nominal / fair / serious / critical), wired into
`AppModel.thermalChanged()` ahead of the real `ProcessInfo` read, re-applied when Settings closes.

Verified on-device-equivalent in the Simulator (`12_thermal_critical_simple_glass.png`,
`13_thermal_serious_normal_glass.png`):
- `.critical`: captions switch to the flat, non-glass plate (`simpleGlass`/`hereGlass(simple: true)`) --
  visibly a plain dark rectangle instead of the usual blurred/translucent card.
- `.serious`: captions keep the **normal** glass (confirms `simple` is gated on `== .critical` specifically,
  not `.serious` -- only the detection rate halves at `.serious`, per the code, and the screenshot shows
  exactly that distinction holding).
- Debug overlay's `thermal NSProcessInfoThermalState(rawValue: N)` and the status-pill thermometer icon both
  track the override correctly.

**Not verified in the Simulator** (needs a device): `tracker.detectEvery` halving at `.serious`/`.critical`
is a one-line, already-correct ternary in `thermalChanged()`; its *effect* is a change in how often the real
Vision pipeline runs, which only exists with an actual camera -- same class of limitation as Task 6's
Projector real-path note. Worth watching during the 20-minute heat-soak item in the on-device checklist
above.

**Reduce Motion.** Confirmed the Simulator *can* be driven into this mode headlessly:
`defaults write com.apple.Accessibility ReduceMotionEnabled -int 1` inside the Simulator (via
`simctl spawn`), then relaunch. Verified by temporarily pointing the app's root view at a one-line probe
reading `@Environment(\.accessibilityReduceMotion)` and printing it as text -- `14_reduce_motion_off_probe.png`
reads false (default), `15_reduce_motion_on_probe.png` reads true after toggling (reverted before
committing, not part of the app). This is the exact same environment key `OverlayView` and `TypingDots`
already read to gate their `.animation(...)` calls, so confirming the key itself propagates correctly in a
relaunched app is sufficient to trust those call sites; an animation's *absence* isn't something a still
screenshot can show directly either way.

### Relay robustness (Task 8)

**Stop/start the relay while the app runs.** Verified live against a real relay process (killing only its
listening PID, not the app's own socket -- see the postmortem below for why that distinction mattered):
status pill goes `live` (green) -> `offline` (red) within a couple of seconds of the relay dying
(`19_relay_live_before_kill.png` -> `20_relay_offline_after_kill.png`), stale captions stay on screen rather
than blanking, and on restarting the relay the app auto-reconnects within its existing backoff window (no
code change needed here -- `RelayClient`'s reconnect-with-backoff was already correct) back to `live` with
**fresh** captions and alerts flowing again (`18_relay_restart_recovers_live.png`). `config` re-send on
reconnect was also already wired (`AppModel.start()`'s `relay.onConnected = { sendConfig() }` fires on every
successful connect, not just the first), confirmed firing as part of the same reconnect.

**Wrong token -> clear message, not a silent failure.** This one needed a real fix. Before: `RelayClient`
only had `disconnected/connecting/connected`, so a 401 (relay.py: wrong or missing `HERE_RELAY_TOKEN`) just
looked like generic "offline" forever, indistinguishable from a network problem, while the client kept
retrying a token that could never work -- exactly the silent failure the kickoff called out.

Root-caused with a temporary debug print (removed before committing) that `urlSession(_:task:didCompleteWithError:)`
*was* firing with the real 401 response, but my first fix's `task === self.task` identity check always
failed: `receive()`'s own failure callback for the same handshake error reliably wins the race, calls
`retry()` -> `open()`, and swaps `self.task` for a new (also-doomed) task before the slower
`didCompleteWithError` delegate lands. Fix: stopped comparing task identity -- a 401/403 means *this token*
is rejected regardless of which in-flight task instance reports it, so the check is now just "did we get a
401/403, and are we still trying." Added `Status.rejected(Int)`, which the status pill shows as "bad token"
(not generic "offline") and Settings shows with the HTTP code and an explanation. On rejection, `wanted` is
set false, so the backoff loop stops cleanly rather than hammering the relay with the same bad token forever.

Verified: `16_relay_bad_token.png` (pill reads "bad token", stays stable -- re-screenshotted 8 s later with
no flicker back to "connecting", confirming the retry loop actually stopped) and
`17_relay_recovered_after_fixing_token.png` (fixing the token and relaunching reconnects normally, same
`onConnected`/config-resend path as any other reconnect).

**Testing postmortem, for whoever touches this next:** killing a relay process by `lsof -ti :8080` (all
sockets on that port) rather than `lsof -i :8080 -sTCP:LISTEN -t` (the listener only) also kills the *app's*
socket if it's currently connected -- which looks exactly like an app crash (process disappears, back to
the Simulator home screen) and cost real time chasing a nonexistent regression before the actual cause
(an overly broad kill command, not a race or crash in `RelayClient`) was obvious. No crash report was ever
generated, which in hindsight was the tell.

### Noted, not fixed (needs a product/design call, not a code bug)
- In `02_two_speakers.png` the off-camera edge caption (anchored at a fixed screen position, README §14) can
  land on top of a nearby on-camera face box -- `Layout.separate()` (`OverlayView.swift`) only pushes apart
  captions that collide with *each other*, not a caption against a face rect. With the Simulator's abstract
  faces (no mouth rendered) this is cosmetic; with real Vision face boxes it could violate the "never cover a
  mouth" rule (README §14) if a person is near the screen edge while an edge caption lands nearby. Worth a
  deliberate look once real faces are available (Task 5's layout tests cover caption-vs-caption only, per the
  kickoff scope, not caption-vs-face).

---

## 2026-10-09: "11/10 product polish" pass (unattended, by request)

Kori asked for product-polish/AR-quality upgrades ("menu upgrades and hud upgrades... make the ar
beautiful and lightweight and fast") while away, after explicitly answering a short set of scoping
questions first: go big on AR-internals judgment calls (not just conservative polish), restructure
Settings rather than just reskin it, build a real (if simple) app icon/launch screen despite no
design tool being available, and trade some fidelity for thermal/perf headroom. Five commits, each
build+test verified (43/43 green throughout, no regressions):

1. **App icon + launch screen** -- there was no `Assets.xcassets` in the project at all (system
   default icon, blank launch screen). Generated a simple teal ring-and-dot mark with a throwaway
   `swift` CoreGraphics script (no image-gen tool available), wired into `AppIcon.appiconset` +
   `UILaunchScreen`. Verified by `xcodebuild build`, installed + launched in the Simulator, icon
   checked legible at both 1024px and the compiled 60×60@2x size. This is placeholder branding, not
   real design work -- flag for a designer to replace outright if/when one's available.
2. **Settings restructure + Demo Setup wizard** -- split rehearsal-only Developer/debug controls
   (simulated input, raw test triggers) into their own `DeveloperSettingsView`, so a judge paging
   through Settings never lands on "inject test caption." Added `DemoSetupView`, a guided
   step-by-step walkthrough of this file's own §23 2-minute checklist above, with live readouts
   (relay status, faces tracked, newest caption) during the speaker-check step. Settings navigation
   itself is code-review-verified only -- no UI-automation harness set up here to drive Simulator
   taps programmatically, so the sheet-to-sheet flow wasn't screenshotted.
3. **Thermal headroom + haptics** -- `AppModel.simplifyGlass` widens the flat-plate glass fallback to
   `.serious` (previously only `.critical`), matching the detection-rate halving that already
   happens at that tier. Added native phone haptics (not belt) on Calibrate success, belt test
   taps, and new alerts -- the alert haptic is a backup independent of BLE, same spirit as the
   on-screen belt indicator README §23 already calls for.
4. **Ring/alert visual polish** -- `SpeakerRing` got an `AngularGradient` stroke (pulse timing
   untouched); `DirectionRing` got a one-shot ripple on first appear. Both reuse the exact
   `@State` + `onAppear` + `withAnimation` pattern already proven in this file (`EdgeGlow`, the
   pre-existing `SpeakerRing` pulse), so risk is low, but a live-alert Simulator screenshot wasn't
   obtained -- UserDefaults seeding for a non-interactive Simulator run (writing `here.settings.v3`
   via `simctl spawn booted defaults write`) didn't visibly take within the time spent chasing it;
   not worth more time on a visual-only, low-risk change working from the same pattern as already-
   verified code.
5. **"Reset display settings" safety net** -- one tap to undo a fat-fingered Modes/Show/Alerts/belt-
   strength toggle between setup and a demo. Deliberately narrow: doesn't touch Relay config,
   calibration, or the wearer's name, so it can't undo real setup work.

**Still true from before:** everything under "Before you start" above. Nothing in this pass touched
camera/Vision/attribution internals -- by design, per the scoping answer to stay out of code that
can't be verified without the physical phone.

---

## 2026-10-09 (later): bearing accuracy for any mount orientation

Kori's mount hardware is still being built and may not end up landscape -- asked specifically for
CV/bearing robustness to "any orientation of the phone landscape vertical upside down everything."
This one *does* touch geometry internals (the scoping answer from the earlier pass said stay out of
camera/Vision/attribution logic, but this is squarely what was just asked for, and it's pure,
fully-unit-testable trig with no camera/Vision behavior change -- the rotation/Vision pipeline itself
was already correct, see below).

**Found:** `HeadingProvider` already computes heading correctly for any physical mount (it rotates the
camera's look vector through the full attitude matrix, not Euler yaw -- the code comment there already
says "works for any mount, landscape included"). `Frames.sensorRect` already correctly remaps Vision's
upright rect back to sensor space for all four `captureAngle` quantizations (0/90/180/270), and Vision
itself is always handed an already-upright buffer (`orientation: .up` is correct because
`AVCaptureConnection.videoRotationAngle` physically rotates the delivered buffer, not just its
metadata). So the rotation/detection pipeline was already orientation-agnostic.

The actual gap: **bearing-from-pixel-position** (`Geo.bearing`/`Geo.normalizedX`, used for every face's
bearing and for placing off-camera captions at the right screen x) always used
`AVCaptureDevice.Format.videoFieldOfView`, which is *always* the format's native horizontal (landscape)
field of view, regardless of how the phone is actually mounted. Mounted in portrait (captureAngle
90/270, exactly where `Frames.sensorRect` already swaps width and height), the upright buffer's x axis
is actually the sensor's *vertical* extent -- a different, usually narrower, angle of view. Using the
landscape FOV there would skew every bearing by a large, orientation-dependent amount, even though the
rectangles themselves land in the right place.

**Fixed:** `CameraController` now also derives `vfovDeg` (vertical FOV) from the format's actual pixel
dimensions (`CMVideoFormatDescriptionGetDimensions`) via the standard pinhole relationship along each
axis, alongside the existing horizontal `hfovDeg`. New `Geo.effectiveHFOVDeg(hfovDeg:vfovDeg:
captureAngle:)` picks whichever one actually applies to the current upright buffer's x axis; `Frames`
gained `quantizedAngle`/`swapsDimensions` helpers (`sensorRect` refactored to share the angle-snapping
logic rather than duplicate it). `AppModel.hfovDeg` now calls through this instead of always reading
`camera.hfovDeg` directly -- a manual Settings > Field of view override still always wins outright,
same as before, since that's a value measured by hand for one specific setup.

Settings' "Field of view: auto NN°" display was reading the raw landscape `camera.hfovDeg` directly,
which would've shown a confidently wrong number in portrait; switched to the corrected `model.hfovDeg`
so what's displayed matches what bearings are actually computed from.

**Tests:** `GeometryTests.testSwapsDimensionsForPortraitMounts` and
`testEffectiveHFOVPicksTheAxisThatIsActuallyHorizontal`, 2 new pure-logic cases (45/45 total, up from
43). `CameraController`'s actual FOV derivation from the live device format is camera-hardware-
dependent and not unit-testable -- code-review-verified only, same class of limitation as the rest of
the camera/Vision work in this file. **Worth checking on the real rig** if the mount does end up
non-landscape: item 1's new checklist bullet above.

---

## 2026-10-09: FaceTracker label-swap bug, actually found on real hardware (first of its kind)

Kori tested on the iPhone SE 2022 and reported the same person's label flipping between "Person 1"
and "Person 2" ("person / not a person / Person 2"). First attempt (code-review only, no device):
replaced `FaceTracker`'s per-detection greedy matching with a global greedy assignment (best-IoU-
first, then closest-distance-first) and fixed `labelFor` to reacquire the *closest* lost label instead
of the first one found -- both real bugs, both worth fixing, but **not the dominant cause**, confirmed
below. Also tried switching `VNTrackObjectRequest.trackingLevel` from `.fast` to `.accurate`, also not
the fix (reasonable guess, wrong diagnosis).

**What actually found it:** no device access directly, but the Mac has `xcrun devicectl` (modern
Xcode's CLI device bridge) and the phone was connected over USB. Built straight to the physical device
from the command line (`DEVELOPMENT_TEAM`/`CODE_SIGN_STYLE` passed as build setting overrides, team ID
pulled from Xcode's own signing-account plist rather than asking Kori to find it), installed, and
launched attached to a live console (`devicectl device process launch --console`) streaming into a
background Monitor task. Added temporary `print()` tracing to `FaceTracker` (every detect() cycle's
before/after track state, every drop, every new/reacquired label) and watched it live while Kori held
the phone and moved around -- no screenshot tool worked for this iOS version (`idevicescreenshot`
needs a screenshotr lockdown service that the modern "personalized" Developer Disk Image, mounted
fine by `devicectl` itself, doesn't expose; `libimobiledevice` hasn't caught up), so the live log
stream was the only real-time window into on-device behavior, but it turned out to be the *more*
useful signal for this specific class of bug anyway (internal tracking state, not a rendering issue).

**The actual bug:** `VNTrackObjectRequest` (the between-detections tracker) was returning **zero
results on effectively every single call** -- hundreds of track-drop-and-recreate cycles per minute in
the live log, which is what read as "flickering between person / not a person." Root cause: the one
`VNSequenceRequestHandler` was shared for the tracker's entire lifetime, but `detect()` (every
`detectEveryFrames`) processes its frame through a completely separate `VNImageRequestHandler` --
silently skipping that frame from the sequence handler's point of view. `VNSequenceRequestHandler`
expects to see every frame in order to compute motion between them; a shared handler silently missing
every 6th frame broke its internal continuity. Fix: `sequence` is now `var`, reset to a fresh
`VNSequenceRequestHandler()` every time `detect()` runs, so every stretch of `track()` calls starts
with a handler whose "previous frame" is the actual immediately-preceding frame, never stale across a
skipped detect-cycle gap.

**Confirmed live**, same console-streaming setup: before the fix, hundreds of
`DROP ... confidence=-1.0` (zero Vision results) per minute; after, a single track stayed alive
continuously through dozens of consecutive detect cycles with the bearing smoothly tracking real head
movement, one isolated legitimate drop-after-3-missed-detections (someone briefly leaving frame, the
intended grace-period path), and one isolated case of a single face producing a duplicate label
instead of matching its own track (rare now -- maybe once in several minutes vs. constantly -- but not
fully eliminated; worth another look if it recurs noticeably). Kori confirmed "a lot more stable now."

**Also found live, not a bug:** stepping out of frame and back in mints a new "Person N" -- this is
`README.md` §25's own privacy decision ("No facial recognition; people are 'Person 1, 2, 3' only for
the session") working as designed, not a tracking failure. The existing 5 s/25° reacquisition window
only covers *brief* drops at roughly the same bearing; true "recognize me again after walking away"
would require actual face recognition, which this product deliberately doesn't do.

**Residual, not yet fixed:** the rare single-face-splits-into-two-labels case above. `FaceTracker` has
no direct unit tests (needs real `CVPixelBuffer`/`VNObservation` input to test meaningfully) -- further
work here needs either more live-device sessions like this one, or extracting the matching/assignment
logic into more directly-testable pure functions than it already is.
