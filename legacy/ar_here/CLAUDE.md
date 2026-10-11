# ar_here

Read this before changing anything. It is the brief for any AI coding agent working in this repo.

## What HERE is

HERE (Hearing Environmental Reality Engine) helps deaf and hard-of-hearing people perceive the sound around them: who is talking, what they said, how they said it, and where it came from. It is being built as separate projects that get combined at the end:

| Project | Job |
|---|---|
| **ar_here** (this repo) | Renders live video with beautiful, accessible glass captions anchored above each speaker |
| vision project | Real face/person tracking (YOLO + ByteTrack) |
| speech project | Live transcription, tone, loudness |
| audio project | ReSpeaker mic array: direction of sound |
| belt project | ESP32 haptic belt |

ar_here does **not** do detection, transcription or audio. It only draws what the backend tells it. Until the other projects exist, `server/mock_server.py` fakes them.

## Layout

```
server/mock_server.py      fake backend: real camera frames + scripted captions
web/src/contract.ts        THE CONTRACT: shapes the backend sends (source of truth)
web/src/stream.ts          WebSocket client, pairs JSON meta with its JPEG frame
web/src/theme.ts           every visual knob: colors, sizes, glass, motion, tones
web/src/lib/geometry.ts    video pixels <-> screen, bearings <-> pixels
web/src/lib/text.ts        two-line trimming, final vs provisional words
web/src/components/
  VideoPlane.tsx           live video as a texture
  CaptionLayer.tsx         decides where every caption goes, resolves overlaps
  Caption.tsx              one glass caption box (panel, name, tone, text, tail)
  ToneChip.tsx             emoji + word tone indicator
  DebugFaces.tsx           press D to see face boxes, ids, bearings
```

## The contract (do not break it)

For every video frame the server sends two WebSocket messages, in order:

1. text: JSON `FrameMeta` (see `web/src/contract.ts`)
2. binary: the JPEG of that same frame

The client pairs them so captions are always drawn against the frame they were computed from. If you change the contract, change `contract.ts`, `mock_server.py` and this file together. Other HERE projects will be written against it.

Key fields: `faces[].id` is a stable tracking id. `captions[].speaker_id` points at a face id when the speaker is on camera. Captions without a matching face are placed by `bearing`, or at the bottom if they have neither. `text` is final words, `pending` is provisional words. One caption per speaker.

## Accessibility rules (non-negotiable)

- **Never cover a mouth.** Many users lip-read alongside captions. Captions sit above the face, or below the chin if there is no room. Never over the lower face, never over another face.
- **Speaker identity is never color alone.** Every caption shows the speaker's name or "Person N" next to the color dot.
- **Contrast:** white text on the smoked glass tint. Provisional words are dimmer but must stay readable (≥ 4.5:1). If you lighten the glass, darken the tint.
- **Two lines max.** Older words scroll off the front with an ellipsis (`lib/text.ts`).
- **Stability over precision.** Positions are smoothed; captions must glide, never jitter.
- **Respect `prefers-reduced-motion`:** no springy motion when it is set.
- **Tone describes how it sounded** (whispering, shouting, laughing, asking), like professional captioning. The word is always shown next to the emoji. Low `tone_confidence` fades the chip instead of asserting a wrong tone.
- Font is Atkinson Hyperlegible (built for low-vision readers). Keep it.

## Look

Refractive smoked-glass boxes (drei `MeshTransmissionMaterial`) over the live feed, one accent color per speaker used on the dot, name and tail, subtle bloom on accents only. Restraint: one glass style, one accent per speaker, gentle motion. Tune in `theme.ts` rather than hardcoding numbers in components.

Rendering details that matter:
- Orthographic camera at zoom 1, so scene units are screen pixels with origin at the centre. Video pixels map through `toScreen()`.
- Glass uses `transmissionSampler` (shared three.js transmission buffer) to stay cheap with many captions. Keep `samples` low.
- Accent colors are pushed above 1.0 (`glow()` in Caption.tsx) so only they bloom; the video and text never should. Bloom threshold is 1.0.
- `?lite=1` swaps glass and bloom for cheap stand-ins (slow GPUs, screenshots).

## Running

```
python server/mock_server.py --camera 1        # phone webcam app, or 0 for built-in
python server/mock_server.py --test-pattern    # no camera
cd web && npm run dev                          # http://localhost:5173
```

Useful: press **D** for debug face boxes. `?ws=ws://host:8765` points at another server. `--script-offset 12` jumps the fake conversation forward (12–18 s has crosstalk and an off-camera voice).

## Conventions

- TypeScript strict. `npm run build` must pass (it typechecks).
- Don't add dependencies without a clear reason; prefer drei/maath helpers already installed.
- Keep components presentational; placement logic lives in `CaptionLayer.tsx`.

## Next up for ar_here

1. Tune the glass and type on the real GPU with the phone feed (`theme.ts`).
2. Better overlap handling when 3+ people talk (prefer moving the lower-priority caption, never onto faces).
3. Avoid placing captions over *any* face, not just the speaker's.
4. Loudness: a subtle visual for shouting/whispering beyond text size.
5. Edge indicator polish for off-camera speakers (direction arrow strength by angle).
