# ios_here

Part of HERE. Read ../HERE_PLAN.md (sections 6–9 at minimum) and ../contracts/ first (`contracts/` doesn't exist yet; until it does, HERE_PLAN.md §8 is the contract).

## Job

The iPhone app, chest-mounted (portrait, upside down in the harness), wired to the Windows laptop over USB-C. It is **a camera and a heading sensor**, plus a small settings UI. It streams upright JPEG frames and the wearer's heading to the hub, keeps its clock synced to the laptop, and lets the wearer calibrate and change settings.

It does **not** do vision, AR, transcription or rendering. All processing happens on the laptop. **No ARKit** (decided in HERE_PLAN.md §22).

## Inputs / outputs

Connects to the hub (world_here) at `ws://<laptop-ip>:8765`, the laptop's IP on the USB hotspot link.

- **Publishes:** `hello`, `heartbeat`, `clock_ping`, `video_frame` (+ binary JPEG), `heading`, `calibrate`, `config`, `search_request` (later)
- **Consumes:** `clock_pong`, `config`, `search_result` (later), `alert` (optional, for an on-phone indicator)

Every JSON message carries the envelope: `{ type, v: 1, src: "ios_here", t, seq }`. `t` is the **observation time on the laptop clock** (unix seconds, float), not the send time.

### video_frame (HERE_PLAN.md §8.5)

```json
{"type":"video_frame","v":1,"src":"ios_here","t":1760000000.12,"seq":881,
 "frame_id":881,"width":720,"height":1280,"hfov_deg":0,"lens":"ultrawide","encoding":"jpeg"}
```
followed **immediately** by one binary WebSocket message with the JPEG bytes, on the same socket.

- `t` = capture time (from the sample buffer's presentation timestamp), converted to laptop time.
- Rotate to **upright** before encoding; `width`/`height`/`hfov_deg` describe the upright frame.
- Target 720p, 24–30 fps. **Drop, don't queue**: if the previous frame is still sending, skip this one.

### heading (§8.5, method in §6)

```json
{"type":"heading","v":1,"src":"ios_here","t":1760000000.11,"heading_raw_deg":112.4,"quality":"good"}
```
~60 Hz, independent of video frames.

**Do not use `attitude.yaw`** (Euler angles gimbal-lock with the phone upright). Instead:

1. `CMMotionManager.startDeviceMotionUpdates(using: .xArbitraryZVertical)` (no magnetometer; indoor compass is unreliable).
2. Take `attitude.rotationMatrix` (or quaternion). The back camera looks along device **−Z**; rotate `(0, 0, −1)` into the reference frame. With CoreMotion's matrix `R` (device → reference is `Rᵀ`), the reference-frame vector is `v = (−R.m31, −R.m32, −R.m33)`. **Verify the sign/transposition empirically**: turning clockwise (viewed from above) must increase the heading.
3. Drop the vertical (z) component; `heading_raw_deg = atan2(v.y, v.x)` in degrees, then adjust sign so clockwise is positive, normalized to (−180, 180].
4. Send it raw. world_here stores the calibration offset; the phone never subtracts it.

`quality`: `settling` for the first second(s) after start, `bad` if the horizontal component of `v` is tiny (camera pointing straight up or down), otherwise `good`.

### Clock sync (§7)

On connect and every 10 s, send a burst of `clock_ping {t_phone}`. Hub replies `clock_pong {t_phone, t_hub}`. For each reply: `rtt = now − t_phone`, `offset = t_hub − (t_phone + rtt/2)`. Keep the offset from the sample with the **smallest rtt**. Laptop time = phone time + offset. Use one monotonic-ish phone clock consistently (convert `CMTime`/`CMDeviceMotion.timestamp` uptime-based values to the same base before applying the offset).

### Session

- Send `hello {role: "ios_here", subscribe: ["clock_pong","config","search_result","alert"]}` first.
- `heartbeat` every 2 s: `status` ok/degraded/error, `detail`, `rate_hz` (frames sent per second).
- Reconnect automatically: 500 ms doubling to 5 s.
- `calibrate {kind: "heading_zero"}` when the wearer taps **Calibrate** while facing the array's front.

## Transport

**Primary: Personal Hotspot over USB.** Phone shares its hotspot over the cable; Windows sees an Apple network adapter. The app connects out to the laptop's IP on that adapter. Laptop setup (one time):

- Install Apple Devices (or iTunes) for the driver.
- Set the Apple adapter's IPv4 interface metric high (100) and Wi-Fi low (10); mark the Apple connection metered. Check `route print`: the default route must use Wi-Fi.

**Fallback: `iproxy`** (libimobiledevice) port-forwarding. Here the **phone listens** on a port and the laptop connects in. Keep the transport behind a small interface so this swap is cheap.

## Run

- The Xcode project is generated with [XcodeGen](https://github.com/yonaskolb/XcodeGen) from `project.yml` — it is not checked in. After cloning, or after adding/removing a Swift file: `brew install xcodegen` (once), then `xcodegen generate` from this directory to produce `ios_here.xcodeproj`.
- Open `ios_here.xcodeproj` in Xcode, select your phone as the run destination, set your team under Signing & Capabilities (first run only), and Run. Build also works headless: `xcodebuild -project ios_here.xcodeproj -scheme ios_here -destination 'generic/platform=iOS' build`.
- Deployment target iOS 16. No third-party dependencies (CoreMotion, AVFoundation, URLSessionWebSocketTask only — no ARKit, per HERE_PLAN.md §22).
- Laptop test harness, no hub needed: `python ios_here/mock_hub.py` (needs `pip install websockets`). It answers clock pings, prints heading/fps/clock offset, and saves the latest frame to `latest.jpg`. For the iproxy fallback: `python ios_here/mock_hub.py --connect ws://localhost:<port>`.
- In the app's Settings (gear icon), set **Laptop IP** to the address `mock_hub.py` is listening on (the laptop's IP on the USB/Personal Hotspot adapter — run `ipconfig` on the Windows side and look for the Apple Mobile Device Ethernet adapter; `127.0.0.1` + forwarded port for the `iproxy` fallback) and tap Save.

### What's implemented

- `HeadingProvider` — quaternion-based heading (§6), ~60 Hz, with `good`/`settling`/`bad` quality.
- `CameraCapture` — back camera (wide/ultrawide, switchable in Settings) → upright JPEG via `.portraitUpsideDown` connection orientation, drop-not-queue if the previous frame is still encoding.
- `HubConnection` — WebSocket transport: `hello` first, reconnect with 500 ms→5 s backoff, `video_frame` framed as JSON header + binary JPEG in order.
- `ClockSync` — round-trip-midpoint offset from `clock_ping`/`clock_pong`, smallest-RTT-wins per burst.
- `AppState` — wires the above, sends `heartbeat` every 2 s and a `clock_ping` burst every 10 s.
- `ContentView` — on-screen heading number + quality + fps + clock offset/RTT (so M1 can be eyeballed without the laptop), Calibrate button.
- `SettingsView` — hub host/port, lens, fps, JPEG quality, heading sign-flip, `config` message fields (name/aliases, feature toggles, alert toggles, belt strength, caption scale).

**Not yet verified on hardware** (needs the physical rig — see `SPIKE.md`): the heading sign convention (`headingSignFlip` in Settings exists specifically because this can only be confirmed by turning the actual phone), real USB throughput/latency, thermal behavior, and the true per-lens HFOV. The `hfovDegByLens` defaults in `Config.swift` are nominal placeholders, not measurements.

## Rules

- Follow the contract exactly. Do not change message shapes; ask the integrator (world_here owner).
- Timestamps are observation times on the laptop clock (HERE_PLAN.md §7).
- Heading is raw, arbitrary zero, clockwise positive, (−180, 180].
- Drop stale data instead of queueing.
- Tunables (hub IP/port, resolution, fps, JPEG quality, lens, HFOV per lens) live in one config file, not scattered in code.
- Settings UI edits a `config` message (§8.4): user name + aliases, feature toggles, alert toggles, belt strength, caption scale.

## Spike (write results to ios_here/SPIKE.md)

1. Heading stability in the chest mount: drift over 10 min, walking, turning.
2. Hotspot-over-USB on Windows: setup steps, throughput, latency; `iproxy` fallback.
3. Thermal: 720p @ 30 fps for 20 min in the harness.
4. HFOV in portrait + ultrawide, measured in the harness (two objects at frame edges, measure their angle apart); edge distortion.

## Current milestone

**M1: phone streams frames + heading to the laptop over USB.** Done when `mock_hub.py` shows ~30 fps of upright frames, a ~60 Hz heading that increases when turning clockwise and doesn't jump when the phone is upright, and a stable clock offset.

Suggested order: (1) heading-only app showing the number on screen; (2) WebSocket + `hello` + heading to `mock_hub.py` over the USB link; (3) clock sync; (4) camera frames; (5) calibrate button + settings UI.

**Status:** all five steps above are implemented and the app builds clean (`xcodebuild ... build` succeeds, verified in Simulator — camera permission prompt and UI confirmed working). What's left to close M1 is on-device verification against the real rig: run against `mock_hub.py` over the actual USB/Personal Hotspot link, confirm ~30 fps / ~60 Hz / stable offset in its printed stats, and — critically — turn the phone clockwise and check the on-screen heading increases; flip **Settings → Heading → Flip sign** if it doesn't. Record the results in `SPIKE.md`.

Open questions: which iPhone model (USB-C vs Lightning, ultrawide?); is Personal Hotspot available on its carrier plan?
