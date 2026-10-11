# ios_here spike

Required by HERE_PLAN.md §16. Fill in the measurements below on the actual
rig — the app is built and ready for each test, but none of these numbers
can be produced without the physical phone/harness/laptop, so this file is
currently a template, not a report.

## 1. Heading stability in the chest mount

- [ ] Drift over 10 min stationary: record `heading_raw_deg` at t=0 and t=10min, report delta.
- [ ] Behavior while walking: does quality stay `good`, does the number jitter?
- [ ] Behavior while turning in place: **does a clockwise turn (viewed from above) increase the heading?** If not, enable Settings → Heading → "Flip sign" and re-test. `HeadingProvider.swift`'s sign (`v = (-R.m31, -R.m32, -R.m33)`, `atan2(vy, vx)`) is HERE_PLAN.md §6's best guess, explicitly flagged there as needing empirical confirmation.
- [ ] Gimbal lock check: confirm no jumps/freezes with the phone upright in the harness (this is the whole reason we use the quaternion method instead of `attitude.yaw`).

## 2. Personal Hotspot over USB (Windows)

- [ ] Setup steps that worked (Apple Devices driver install, adapter metric config, `route print` confirming Wi-Fi stays default).
- [ ] Laptop's IP on the Apple adapter (what to type into Settings → Laptop IP in the app).
- [ ] Throughput: sustained JPEG fps at target quality — does `mock_hub.py`'s fps line hold ~24–30?
- [ ] Latency: `mock_hub.py`'s `capture->hub` figure, typical and worst-case.
- [ ] `iproxy` fallback: confirm it works end-to-end (phone listens, `mock_hub.py --connect ws://localhost:<port>`), and note the port.

## 3. Thermal

- [ ] 720p @ 30 fps for 20 min in the harness: does the OS throttle the camera (fps drop, `AVCaptureSession` interruption notifications)? Note phone model and ambient temp.

## 4. HFOV (horizontal field of view)

- [ ] Portrait + ultrawide, measured in the harness: place two objects at the left/right edges of the frame, measure the angle between them from the camera's position. Update `AppConfig.hfovDegByLens[.ultrawide]` in `Config.swift` (currently a nominal placeholder, 102°, not measured) and in Settings.
- [ ] Repeat for the wide lens if it ends up in use.
- [ ] Edge distortion: does the ultrawide's barrel distortion push bearing error up near the frame edges? If so, note roughly how much.

## Recommended settings (fill in after the above)

- Lens: _(wide | ultrawide)_
- Target fps: _tune_
- JPEG quality: _tune_
- `headingSignFlip`: _on | off_
- HFOV (wide / ultrawide): _ / _
