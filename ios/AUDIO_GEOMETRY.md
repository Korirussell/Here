# HERE: coordinate frames, calibration, and the audio pipeline audit

Written 2026-10-10 from reading the code on `main` (laptop, contracts, phone). Nothing here was
measured on hardware: the laptop's Python tests could not be run on the Mac this was written on
(no numpy), and no array or phone session was available. Where a number is a measurement it says
whose; everything else is derived from the code.

## 1. What the array gives us

**Direction only.** The ReSpeaker XVF3800 (built-in direction finding, or SRP-PHAT on its raw mics,
or ODAS) reports the angle a sound arrives from, measured **at the array**. It never reports a
position or a distance. One fixed array cannot: distance is not observable from a single
direction-of-arrival.

Consequence: "the sound is at +40 deg from the array" does not determine the direction of that
sound from a wearer standing anywhere else. It depends on how far away the source is, which is
unknown.

## 2. Coordinate frames

| Frame | Origin | Zero | Sense | Range | Position or direction |
|---|---|---|---|---|---|
| A. Array device | the array | the device's own 0 | device-dependent | 0...360 | direction |
| B. Array / "world" | **the array** | the array's calibrated front (`audio.array_offset_deg`) | clockwise from above (`array_clockwise`) | (-180, 180] | direction |
| C. Phone heading | **the wearer** | set by Calibrate (`headingOffset`) | clockwise | (-180, 180] | direction |
| D. Wearer-relative | the wearer | the wearer's front | clockwise | (-180, 180] | direction |
| E. Camera image | the phone camera | image center column | right = positive | 0...1 across the frame | direction (via HFOV) |

Transforms in the code:

- A -> B on the laptop: `to_world(raw, offset, ccw)` in `laptop/audio/xvf3800.py`.
- E -> C on the phone: `Geo.bearing(normalizedX:hfovDeg:heading:)` = heading + atan(...).
- C -> D on the phone: `Geo.relative(world, heading)` = wrap(world - heading).
- D -> belt: `Geo.motor(forRelative:)` rounds to 45 deg.

## 3. The flaw

Every laptop message carries a frame-B bearing (origin: the array). Before 2026-10-10 the phone
used that number directly as a frame-C bearing (origin: the wearer): it subtracted the heading to
get a relative bearing, and compared it with face and object bearings that really are measured
from the wearer.

Frames B and C have **different origins**. The comparison is only valid when the wearer stands at
the array. README section 5 says as much ("Wearer stays within ~0.5 m of the array... Error ~
atan(wearer_offset / speaker_distance)"), but nothing enforced or corrected it.

Size of the error (computed, `RoomGeometryTests`):

| Wearer offset from array | Speaker distance from array | Error |
|---|---|---|
| 0.5 m | 1.5 m | 18 deg (already over the 15 deg face-match angle) |
| 1.0 m | 1.5 m | 34 deg |
| 0.5 m | 6 m | under 5 deg |

So a wearer who walks a metre from the array gets captions on the wrong person or on nobody, and
sounds attached to the wrong object, with a "calibration" that is in fact perfect at the mark.

A second, smaller hazard: **the Calibrate instruction is ambiguous.** "Wearer faces the array's
front" can be read as "look at the array" (which is 180 deg from the array's forward direction if
you stand in front of it) or "face the way the array's front points". Heading zero must be the
second. The calibration check below detects the first as "Reversed by 180 deg".

## 4. What the phone does now

`ios/HERE/Geometry/RoomGeometry.swift`. The array is the origin; the wearer has a position
(distance and direction from the array, entered once: Settings > Developer > Calibration).

- **Speech -> person (exact given the face's distance).** For each visible face the phone estimates
  its distance from its apparent height, places it in the room, and computes the angle at which the
  *array* would hear it. Attribution compares the utterance's bearing with those, in the array's
  frame. No assumption about the source's distance from the array is needed.
- **Everything else (approximate).** A sound's bearing is converted to a direction from the wearer
  by assuming the source is `sourceRadiusM` (default 1.5 m) from the array. The phone also computes
  how wrong that could be if the source were half or twice as far, and attaches sounds to objects
  less readily as that uncertainty grows (none at 30 deg).
- With the wearer at 0 m (the default) every conversion is the identity: nothing changes.

**The wearer's position is not tracked.** It is where the tape mark is. If the wearer walks, the
model is wrong again. See section 7.

## 5. Calibration procedure (under a minute)

1. Array on its mark, front arrow along the tape.
2. Wearer on the wearer mark, facing **the same way the array's front arrow points**.
3. Settings > Developer > Calibration > **Set reference**.
4. Enter the wearer mark's distance and direction from the array.
5. Turn on **Run calibration check**. One person stands where the camera sees them (only them) and
   says a sentence; repeat from a second spot on the other side.
6. Read the verdict: GOOD (mean error <= 6 deg), FAIR (<= 12), BAD.

The check compares where the array *should* hear a visible speaker with where it did, per
sentence, and reports mean error, max error, jitter and a diagnosis:

| Diagnosis | Meaning | Fix |
|---|---|---|
| Constant offset of N deg | heading zero is off | tap "Apply correction" or redo step 3 |
| Reversed by 180 deg | Calibrate tapped facing the wrong way | redo step 3 |
| Mirrored | left/right swapped | flip heading sign (phone) or `array_clockwise` (laptop) |
| Error changes with direction | the wearer is not where the model says | fix step 4 |
| Inconsistent from one direction | noisy direction finding | not a calibration problem |

Manual samples (Front / Right / Behind / Left buttons while a helper makes a sound from a marked
spot) check the array alone, independent of the phone.

No calibration error has been measured yet. That is the first thing to do on the rig.

## 6. Sound classification: why typing became a dog

Read from `laptop/sounds/` and `laptop/config.yaml`:

- Model: PANNs CNN14 on AudioSet (527 classes), 1 s windows every 0.5 s.
- 15 HERE labels, each mapped to a few AudioSet classes (`sounds.label_map`). A label's score is the
  **max** of its classes. `dog_barking` = Bark, Dog, Yip, Bow-wow, Growling, Whimper (dog).
- Each label is tested **on its own** against its threshold (default 0.3; doorbell 0.25, knock and
  door 0.2). One window over the bar opens an event immediately.
- AudioSet's "Typing" and "Computer keyboard" classes are **not mapped to anything**, so they are
  never looked at. A typing window where "Dog" reaches 0.3 becomes "Dog barking" even if "Typing"
  scored 0.8.
- The label-to-icon mapping itself is correct end to end (label_map -> contract `SoundLabel` ->
  `SoundIcon.symbol`): this is thresholding, not an index bug.
- Only the winning label and its score are sent (`sound_event.confidence`), not the top-k.

Done on the phone (testable here):

- A label under its bar is shown as **unknown ("?")** with its direction and intensity
  (`Tunables.soundLabelMinConfidence`; dog 0.5, default 0.45; doorbell/knock/door keep the laptop's
  own bars because impacts legitimately score low).
- The same continuing source keeps **one notification** across label changes (same direction
  within 20 deg, within 2.5 s; warnings are never merged).
- Its label is decided by **accumulated evidence**: a challenger needs 1.3x the current label's
  decayed score total, so one stray window does not rename it.

Recommended on the laptop (not done: untestable from here, and it is another owner's code):

1. **Rival check** in `Events.update` / `Labeler.scores`: pass the overall top AudioSet class and
   its probability; if it is not one of the label's own classes and beats the label's score by a
   margin, emit `unknown` instead. This is the direct fix for typing -> dog.
2. Map "Typing" and "Computer keyboard" (and other common desk noises) to a non-published label so
   they compete.
3. Raise `dog_barking` to 0.5 and drop "Dog", "Growling", "Whimper (dog)" from its class list
   (keep Bark, Yip, Bow-wow).
4. Require two consecutive windows before opening an event for non-impact labels.
5. Put the top-3 in the laptop's own log (`top_scores` already formats them) and, if wanted on the
   phone, add them to the contract through the integrator.

## 7. Wearer movement and ARKit

The phone knows its heading (CoreMotion) but not its position. Options, honestly:

- **Fixed mark (what is implemented).** Works if the wearer stays on the mark and only turns.
- **ARKit world tracking** would give position relative to where the session started, so the model
  would follow the wearer. Cost: the camera pipeline is AVFoundation today (ultrawide lens, Vision on
  its frames, a preview layer); ARKit owns the camera, uses the wide lens (narrower view), and the
  preview and Vision input would have to be rebuilt on `ARFrame`. That is a rewrite of the capture
  layer, not an add-on. It would not fix the unknown source distance.
- **CoreMotion dead reckoning**: drifts metres in seconds. Not usable.
- **Vision correcting the audio** (implemented for speech): once a face is matched, its own bearing
  is used, so the caption sits on the person regardless of the radius guess.

Recommendation for judging: keep the wearer on the mark, set the mark's position in the app, run the
check, and treat ARKit as the next step only if walking is part of the demo.

## 8. Transcription: what the code says, what is unknown

- Engine: faster-whisper `large-v3-turbo` on CUDA (float16), English, final beam 5; ElevenLabs
  Scribe v2 Realtime optional with automatic fallback to whisper.
- Audio: 16 kHz, 32 ms blocks, the array's processed output on USB channel 0, `capture: beams` with
  two **fixed beams at 0 and 180 deg** (each covering its half of the room, +-100 deg).
- Segmentation: Silero VAD, start 0.5 / continue 0.35, end after 0.6 s of silence, 0.25 s pre-roll,
  15 s maximum, partials every 0.4 s.
- Bearing: SRP-PHAT on the raw mics, at most one look per 0.3 s, looks under 0.6 coherence dropped.
- The contract carries **no transcription confidence**; `no_speech_drop: 0.6` is applied on the laptop.

Likely contributors to poor distant speech, in the order I would test them (none measured):

1. **Fixed beams.** A talker near +-90 deg is off-axis for both beams; a distant one is also quiet.
2. **VAD start 0.5** with a quiet, reverberant voice: late starts (clipped first word) and early ends.
3. **Level.** Whisper is fed whatever level the beam gives; there is no per-utterance normalization
   visible in the config.
4. **Two talkers in one beam's half** are mixed into one stream.

How to find out: `laptop/speech/bench.py` and `tools/stress_test.py` exist for exactly this; record
with `python -m laptop --real --record` at 1, 2, 3, 4, 5 m and replay. The phone's new transcript
record (Developer > Calibration) shows text, duration, loudness, bearing, spread, overlap, engine,
stage times, end-to-end lag and who it was attributed to, per utterance.

## 9. Fixtures

`fixtures/` has `mock_scene`, `raw_speaker_180`, `array_tts`, `events`, `sounds`. On the phone, the
68 scripted scenarios (Settings > Developer) cover the presentation and fusion logic; none of them
exercises real audio.
