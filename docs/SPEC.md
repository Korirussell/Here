# HERE technical spec

The engineering detail behind the [README](../README.md): frames, timing, the message contract, attribution, the
phone, alerts and the belt. Code comments cite it as `SPEC §N`. Section numbers are stable (gaps are sections that
were planning notes and have been removed). The wire contract itself lives in `contracts/`; §12 describes it.

Markers: *(tune)* = starting value, adjust after testing.

---

## 1. The idea

Deaf and hard-of-hearing people miss more than words. They miss **where** a sound came from, **who** is speaking in a group, **how** something was said, and that something happened at all while they were looking away: a doorbell, their name being called.

Caption apps turn speech into text but don't say who said it or where they are. Sound-alert features say "doorbell" but not which way to turn.

**HERE fuses a microphone array with a camera** into one spatial picture of the room. Every sound and every visible person becomes something with a direction. Then:

| Output | What it gives the user |
|---|---|
| **AR captions** (iPhone, standing in for AR glasses) | What was said, by whom, how, floating above the person who said it |
| **Rings** | Thin colored circles around whoever or whatever is making sound |
| **Top-right sound panel** | What the sound is (doorbell, name called…) and a direction ring |
| **Haptic belt** | A buzz in the direction to turn |

The iPhone plays the glasses: its camera is the glasses' camera, its screen is the glasses' display, mirrored to a monitor for judges. The laptop is the hearing module.

### Principles

1. **Everything becomes a direction** in one shared coordinate system.
2. **The belt says direction, not information.** Short patterns, never continuous buzzing.
3. **Commit fast, then refine.** Show something instantly; let slower models fill in.
4. **Confidence is visible.** Uncertain things look tentative.
5. **Don't overload.** Rings are thin, captions stay above heads, focus mode hides background noise.

### Pitch (one sentence)

> HERE puts captions on the person who said them and turns you toward sounds behind you: a microphone array, a camera, and a haptic belt working as one spatial awareness system for deaf and hard-of-hearing people.

---

## 3. Scope

### Core (must work every time)
- Sound direction from the ReSpeaker XVF3800
- Streaming transcription (ElevenLabs Scribe Realtime, key in hand and measured; local faster-whisper as the automatic mid-session fallback)
- **Captions attached to the right person** in the iPhone's camera view (Person 1, 2, 3), with tone
- **Rings** around the active speaker
- **Top-right sound panel** with direction ring, for **doorbell** and **name called**
- **Haptic belt** driven by the phone over Bluetooth
- **Off-camera** speech shown at the screen edge with a chevron
- **Focus mode** toggle
- **Belt-only mode** so anyone can try the belt
- Liquid Glass styling (cheap natively; applied once captions land on the right person)

### Extras (never on the live reaction path, §17)
- Memory search finale
- Plain dashboard page
- More sound classes (dog barking, baby crying, TV, music) with rings on the source
- Further polish (animations, nicer direction ring)
- Core ML YOLO object detection for rings on dogs/TVs

### Not doing
- Jetson, Meta glasses, blind mode, type-to-speak
- Names on captions (we use Person 1, 2, 3)
- ~~Separating two people talking at the exact same time~~: done with the XVF3800's two focused beams + masking; ODAS not needed
- Facial recognition

---

## 4. The three core moments

These are what we rehearse until they work every time.

1. **Captions pop up over the right person.** Person 1 and Person 2 talk; each line appears above whoever said it, with a ring around them and a tone chip.
2. **The turn-around.** Someone speaks behind the wearer → belt buzzes toward them → wearer turns → their caption slides into view. **This is the best moment.** Make the line said behind them funny.
3. **Doorbell / name called from behind.** Top-right panel shows "Doorbell" or "Name called" with a ring pointing back; belt pulses that direction.

Plus, for judges: **"Close your eyes. Turn when you feel the buzz."** (belt-only mode, section 14).

---

## 5. The rig

| Device | Role |
|---|---|
| **iPhone 17** (chest mount, **landscape**, ultrawide lens) | The glasses: camera, face tracking, heading, attribution, AR rendering, **belt control over BLE** |
| **Windows laptop** (RTX 5000 Ada) | The hearing module: ReSpeaker, transcription, sound classification, alerts; sends events to the relay |
| **ReSpeaker XVF3800** (on the desk, USB to the laptop) | Sound direction + clean audio. Its forward direction defines the world frame. |
| **Vultr server** (Atlanta region) | Relay between laptop and phone; later memory/search and dashboard |
| **Haptic belt** | ESP32 + ULN2803 + 8 coin motors + WS2812B ring + Anker power bank; quick-fit strap |
| **Mac + monitor** | AirPlay receiver for the phone (QuickTime over cable as backup) |
| **Power bank in the harness** | Keeps the phone charged (USB-C is free since mirroring is wireless) |
| **Bluetooth speaker** | Plays the doorbell on cue from a rehearsed position |
| **Floor tape** | Marks array, wearer and speaker positions for fast setup |

**Spare phones:** iPhone 16 = hot spare with the app installed. iPhone SE = presenter's remote (settings, calibrate). iPhone 15 Pro = second spare.

### Physical constraints
- **Wearer stays within ~0.5 m of the array.** It measures direction from where it sits. Speakers stand **1–1.5 m** away in the loud hall, facing the array. Error ≈ `atan(wearer_offset / speaker_distance)`.
- **Landscape + ultrawide** fits two or three speakers. Remeasure HFOV in this exact setup.
- **Heat:** camera + vision + rendering + AirPlay + BLE. Power bank, no case, 1080p or 720p capture, thermal step-down, 20-minute test.

---

## 6. Architecture

```
ReSpeaker ──USB──► Windows laptop (hearing module)
                     audio_here   : sources (directions), speech flag, audio
                     speech_here  : streaming transcription, tone, name detection
                     sound_here   : doorbell (+ extras)
                     hub_client   : builds events, sends to relay
                          │
                          │  wss (outbound)
                          ▼
                   Vultr relay (Atlanta)  ── extras: memory/search, dashboard
                          │
                          │  wss (outbound)
                          ▼
                   iPhone 17 (the glasses)
                     camera + Vision faces + tracking
                     heading (CoreMotion quaternion)
                     attribution (direction → face)
                     AR: captions, rings, top-right panel, chevrons (Liquid Glass)
                     belt logic → CoreBluetooth ──BLE──► ESP32 belt
                          │
                          │  AirPlay (local)
                          ▼
                   Mac → external monitor
```

### Who does what

| Laptop | Relay | iPhone |
|---|---|---|
| Reads ReSpeaker directions + audio | Forwards messages by type | Camera + preview |
| Builds `speech_start` instantly | Holds latest `config` | Face detection + tracking |
| Streams transcription → `utterance` | Health endpoint | Heading |
| Tone, loudness, name-called | (extra) memory + search | **Attribution** (direction → person) |
| Doorbell detection → `alert` | (extra) dashboard page | AR rendering |
| Sends everything in **world bearings** | | **Belt: chooses motor and pattern, writes BLE** |

### Data flow
- **Laptop → relay → phone:** what was heard, what was said, from which world direction. Small JSON. **No audio and no video ever cross the network.**
- **Phone → relay (optional):** heartbeat, attribution and scene, used only by the extras (memory, dashboard). The core works without them.
- **The laptop never needs the phone's heading:** the phone converts world bearings to body bearings itself, for both rendering and the belt.

---

## 7. Network and the relay

**Every device makes outbound connections to the relay**, which campus Wi-Fi allows. No device-to-device networking, no router, no hotspot tricks.

### Relay
- Small Python (`websockets` or FastAPI) or Node server on the **cheapest Vultr instance, Atlanta region** (closest to Alabama).
- **TLS required:** iOS blocks plain `ws://` to internet hosts. Put **Caddy** in front for automatic HTTPS; point a domain or a free hostname (e.g. sslip.io) at the server.
- URL: `wss://<host>/ws?session=<session>&role=<role>&token=<token>`
  - `session`: e.g. `demo`, isolates rigs.
  - `role`: `laptop`, `phone`, `dashboard`, `memory`, `tool`.
  - `token`: shared secret from env; reject anything else.
- **Routing:** forward each message to every other client in the same session that subscribed to its `type` (from their `hello`). Default subscriptions per role are in section 12.10.
- **State:** keep the latest `config` per session and send it to new clients on connect.
- **No queues:** if a client is slow, drop old messages for it rather than buffering.
- `GET /health` → `{ok, clients:[{role, connected_s}]}`.
- Logs every message as JSON lines (also our debug record).

### Mirroring
- **AirPlay** phone → Mac. Needs the two to see each other locally: **test on campus Wi-Fi early.**
- **Backup:** cable phone → Mac, QuickTime → New Movie Recording → choose iPhone. Sharper than AirPlay; use it from the start if AirPlay is flaky.
- AirPlay delay doesn't matter; picture quality does. Mac at the monitor's native resolution.

### Backups
- Campus Wi-Fi bad → phone hotspot carries everything (traffic is tiny), except AirPlay → use the cable.
- Internet down → whisper fallback for transcription, but the relay also needs internet. **Local fallback relay:** the same relay code runs on the laptop; the phone's settings switch the URL. Only works if devices can reach each other locally; test once.
- **Vultr is never on the reflex path.** The laptop publishes to the local relay (primary) and mirrors the identical
  stream to the Vultr relay at the same time, each on its own connection and bounded outbox
  (`laptop/client/fanout.py`, `relay.mirrors` / `HERE_RELAY_MIRRORS` / `--mirror`). A dead or slow mirror never
  delays the local relay. `demo-up` does this by itself when `laptop/.env` names a `wss://` relay. The phone tries the
  local relay first and falls back to the Vultr URL (one connection at a time, same stream on both); the cloud extras
  (memory, dashboards) read the Vultr copy.

---

## 8. Coordinates and calibration

### Frames

**World frame:** degrees from the **ReSpeaker's calibrated forward direction**, **clockwise positive** viewed from above, range **(-180, 180]**. Fixed, because the array sits on the desk.

**Body frame:** relative to the wearer's torso front. The chest phone and the belt live here.

**Heading:** the world bearing the wearer faces. The phone computes it.

```
wrap(a)          = ((a + 180) mod 360) - 180, mapped into (-180, 180]
relative_bearing = wrap(world_bearing - heading)
```

All laptop messages use **world bearings**. The phone does every conversion.

### Pixels ↔ bearings (phone)

For an upright landscape frame of width `W` and horizontal field of view `HFOV`, camera pointing at `heading`:

```
f          = (W / 2) / tan(HFOV / 2)
bearing(x) = heading + atan((x - W/2) / f)
pixel_x(b) = W/2 + f * tan(wrap(b - heading))     // on screen only if |wrap(b - heading)| < HFOV/2
```

### Belt motor (phone)

Motors clockwise from the front: 1 Front, 2 Front-right, 3 Right, 4 Back-right, 5 Back, 6 Back-left, 7 Left, 8 Front-left.

```
rel   = relative_bearing mod 360
motor = (round(rel / 45) mod 8) + 1
```

### Heading

**Never use CoreMotion's Euler `attitude.yaw`** (a vertical phone gimbal-locks it).
1. `CMAttitude` quaternion/rotation matrix, reference frame `.xArbitraryZVertical`.
2. Back camera looks along device **−Z**; rotate `(0, 0, −1)` into the reference frame.
3. Drop the vertical component; `atan2` → `heading_raw`.
4. `heading = wrap(heading_raw - heading_offset)`.

### Calibration

| What | When | How |
|---|---|---|
| **Array forward** | Once, saved in audio config | Speak from directly in front of the array's marked front; reported angle = offset. Speak from the right to confirm clockwise. |
| **HFOV** | Once, saved in the app | Phone in the harness, landscape, ultrawide: two objects at the frame edges, measure their angle apart. |
| **Heading zero** | **Every demo, < 10 s** | Wearer at the tape mark facing the array's front, presenter taps **Calibrate**. |
| **Check** | Every demo, 10 s | Someone says "check" from Person 1's mark; the ring should land on them. |

---

## 9. Time

- Laptop services timestamp with the laptop clock (Unix seconds, float). Every message carries `t` = when the observation happened.
- The phone doesn't need clock sync to display. For latency measurement it sends `clock_ping`, the laptop answers `clock_pong` through the relay, and the phone estimates the offset.
- Messages carry `stage_ms` (time spent per stage) for the debug overlay.

---

## 10. Speed

**Fast path (target ≤ 250 ms from sound to response):** the laptop sends `speech_start` the moment the ReSpeaker sees speech start at a direction, before any words exist. The phone immediately rings the matched person, shows their caption box with a typing indicator, and buzzes the belt if they're not in front.

**Slow path:** transcription partials, final text and tone fill the box that's already there.

| Stage | Tactic | Target |
|---|---|---|
| Directions | Read at the device's max rate; short smoothing; send on change | tens of ms |
| Laptop → relay → phone | Persistent wss, Atlanta region, no batching, `TCP_NODELAY` | ~50–100 ms total |
| Phone → belt | BLE write-without-response; ESP32 owns pattern timing | tens of ms |
| Transcription | Stream partials: whisper re-run every 0.3–0.5 s on the current utterance; Scribe partials (measured on `array_tts`: first words 1.24 s, final 0.82 s after the line; GPU whisper 0.33 / 0.84 s) | first words 0.3–0.7 s |
| Doorbell | 0.5–1 s classifier windows with overlap | ~0.5–1 s |
| Phone render | Apply on arrival; caption already anchored | one frame |

---

## 11. Sound direction: built-in DoA vs ODAS

Settled by a head-to-head test, not opinion. The contract (`sources` message) works with either, so switching costs nothing downstream.

| | Built-in DoA (XVF3800) | ODAS |
|---|---|---|
| Sources at once | Two (the focused beams, routed to USB ch 0/1: `audio.capture: beams`) | Several, each tracked with an ID |
| Overlap / TV + person | Jumps or averages | Can follow both |
| Input | Works today on Windows | **Raw mic channels** required |
| Platform | Windows ✓ | Linux; on Windows only indirectly |
| Visualization | — | ODAS Studio (live sphere of tracked sources) |
| Status | **Working (baseline), demo choice** | Runs (raw mics, WSL2, `--odas`); fails 4 of 5 gates |

Result: built-in DoA with the two masked beams is the live path. ODAS runs (raw mics, WSL2, `python -m laptop --odas`)
but failed 4 of 5 head-to-head gates in crowd noise, so it stays opt-in.

---

## 12. The contract

Source of truth: `contracts/` (JSON Schema + generated or hand-mirrored types for Python, Swift and TypeScript). Bump `v` on breaking changes and update this section, the mocks and every client in the same commit.

### 12.1 Transport
- All messages are **JSON text frames** over the relay. No binary on the network.
- First message from each client: `hello`.
- Send immediately; never batch; receivers keep only the newest state; never queue stale data.
- Clients reconnect automatically (500 ms, doubling to 5 s).

### 12.2 Envelope

```ts
interface Envelope {
  type: string
  v: 3
  src: 'laptop' | 'phone' | 'relay' | 'dashboard' | 'memory' | 'tool'
  t: number            // observation time, laptop clock (phone uses its own clock + offset if known)
  seq?: number
  session?: string     // set by the relay
}
```

### 12.3 Shared types

```ts
type WorldBearing = number   // degrees, world frame, clockwise, (-180, 180]
type RelBearing   = number   // degrees, body frame, 0 = wearer's front

type Tone = 'neutral' | 'excited' | 'laughing' | 'shouting' | 'whispering' | 'questioning' | 'sad'

type SoundLabel =
  | 'speech' | 'doorbell' | 'dog_barking' | 'baby_crying' | 'knock' | 'alarm'
  | 'music' | 'tv' | 'laughter' | 'phone_ringing' | 'loud_bang' | 'car_horn' | 'vehicle' | 'siren' | 'door'
  | 'other'      // recognised, but not one of ours
  | 'unknown'    // heard, not classifiable: uncertainty lowers specificity, never hides the direction

type AlertKind = 'doorbell' | 'name_called' | 'dog_barking' | 'baby_crying' | 'knock' | 'alarm' | 'loud_bang'
  | 'car_horn' | 'siren' | 'door'

type SoundCategory = 'speech' | 'alert' | 'animal' | 'media' | 'other'   // drives ring color
```

### 12.4 Session

```ts
interface Hello extends Envelope {
  type: 'hello'
  role: 'laptop' | 'phone' | 'dashboard' | 'memory' | 'tool'
  subscribe: string[]          // ['*'] for all
  app_version?: string
}

interface Heartbeat extends Envelope {      // every 2 s
  type: 'heartbeat'
  status: 'ok' | 'degraded' | 'error'
  detail?: string              // "scribe offline → whisper", "doa source: builtin"
  thermal?: 'nominal' | 'fair' | 'serious' | 'critical'   // phone only
  engine?: 'faster_whisper' | 'scribe_realtime'           // laptop only
  doa_source?: 'builtin' | 'odas'                         // laptop only
}

interface ClockPing extends Envelope { type: 'clock_ping'; t_phone: number }
interface ClockPong extends Envelope { type: 'clock_pong'; t_phone: number; t_laptop: number }

/** Phone → relay → all; relay re-sends the latest to new clients. */
interface Config extends Envelope {
  type: 'config'
  user_name: string            // for name-called
  name_aliases: string[]
  focus_mode: boolean
  belt_only_mode: boolean
  features: { captions: boolean; tone: boolean; rings: boolean; alerts: boolean; belt: boolean }
  alerts_enabled: Partial<Record<AlertKind, boolean>>
  belt_strength: number        // 0..255
}
```

### 12.5 Laptop → phone: directions

```ts
/** 10–30 Hz while anything is active; on change otherwise. Built-in DoA: one source, or two in beams mode (id = focused beam); ODAS may send several. */
interface Sources extends Envelope {
  type: 'sources'
  sources: {
    id: number                 // stable while tracked (built-in: always 0)
    bearing_deg: WorldBearing
    energy: number             // 0..1
    speech: boolean
    confidence: number         // 0..1
  }[]
}
```

### 12.6 Laptop → phone: speech

```ts
/** Fast path: the moment speech starts at a direction. */
interface SpeechStart extends Envelope {
  type: 'speech_start'
  utt_id: string
  bearing_deg: WorldBearing
  source_id: number
  confidence: number
}

/** Sent repeatedly while the utterance grows (final=false), then once with final=true. */
interface Utterance extends Envelope {
  type: 'utterance'
  utt_id: string
  t_start: number
  t_end: number
  text: string                 // committed words
  pending: string              // provisional words, may change
  final: boolean
  bearing_deg: WorldBearing    // circular mean of speech directions so far
  bearing_spread_deg: number
  source_id: number
  overlap: boolean
  tone: Tone | null
  tone_confidence: number
  loudness: number             // 0..1
  engine: 'faster_whisper' | 'scribe_realtime'
  stage_ms?: Record<string, number>
}
```

### 12.7 Laptop → phone: sounds and alerts

```ts
interface SoundEvent extends Envelope {
  type: 'sound_event'
  event_id: string
  label: SoundLabel
  category: SoundCategory
  emoji: string                // "🔔"
  text: string                 // "Doorbell"
  confidence: number
  bearing_deg: WorldBearing | null
  t_start: number
  t_end: number
  count: number                // merged repeats
  source_id?: number           // the sources track it came from
  model?: string               // which classifier labelled it ("panns_cnn14", a Hugging Face model id...;
                               // "<model>+enrolled" when an enrolled sound such as our doorbell matched)
}

interface Alert extends Envelope {
  type: 'alert'
  alert_id: string
  kind: AlertKind
  label: string                // "Doorbell", "Name called"
  emoji: string
  priority: 1 | 2 | 3          // 3 = emergency
  bearing_deg: WorldBearing | null
  active: boolean              // false when it ends
  confidence: number
  ref?: { utt_id?: string; event_id?: string }
}

/** Optional, after the event it refers to. Never decides whether an event exists or where it is. */
interface PerceptionEnrichment extends Envelope {
  type: 'perception_enrichment'
  ref: { utt_id?: string; event_id?: string }
  attention: 'ambient' | 'notice' | 'important' | 'urgent'   // how prominent to make it
  attention_confidence: number
  attention_model: string      // Hugging Face repo id (zero-shot model), or "rules" = deterministic fallback
  directed_at_user?: number    // probability the speech is aimed at the wearer
  context?: string             // Gemini: one-line semantic note
  context_model?: string
}
```

The phone combines `attention` with its own relative bearing (only the phone knows "behind you"). Without
enrichment, or with `attention_model: "rules"`, it keeps its default rules.

### 12.8 Phone → relay (extras only)

```ts
interface Attribution extends Envelope {
  type: 'attribution'
  ref: { utt_id?: string; event_id?: string }
  person_label: string | null  // "Person 2"; null = off camera
  method: 'bearing' | 'off_camera'
  score: number
}

interface Scene extends Envelope {            // ~2–5 Hz
  type: 'scene'
  heading_deg: WorldBearing
  people: { label: string; bearing_deg: WorldBearing; speaking: boolean }[]
}
```

### 12.9 Extras

```ts
interface SearchRequest extends Envelope { type: 'search_request'; query_id: string; text: string; limit: number }
interface SearchResult extends Envelope {
  type: 'search_result'
  query_id: string
  results: { t: number; kind: 'utterance' | 'alert'; person_label?: string; text: string; tone?: Tone | null; bearing_deg?: WorldBearing; score: number }[]
}
```

### 12.10 Routing (default subscriptions)

| Role | Subscribes to |
|---|---|
| phone | `sources`, `speech_start`, `utterance`, `sound_event`, `alert`, `perception_enrichment`, `config`, `clock_pong`, `heartbeat` |
| laptop | `config`, `clock_ping` |
| dashboard (extra) | `*` |
| memory (extra) | `utterance`, `alert`, `sound_event`, `perception_enrichment`, `attribution`, `search_request` |
| tool | `*` |

### 12.11 Example

```json
{"type":"sources","v":3,"src":"laptop","t":1760000000.10,"sources":[{"id":0,"bearing_deg":38.5,"energy":0.7,"speech":true,"confidence":0.82}]}
{"type":"speech_start","v":3,"src":"laptop","t":1760000000.12,"utt_id":"u-0192","bearing_deg":38.5,"source_id":0,"confidence":0.82}
{"type":"utterance","v":3,"src":"laptop","t":1760000000.45,"utt_id":"u-0192","t_start":1760000000.08,"t_end":1760000000.45,"text":"did you see","pending":"the email","final":false,"bearing_deg":38.9,"bearing_spread_deg":4.1,"source_id":0,"overlap":false,"tone":"excited","tone_confidence":0.7,"loudness":0.55,"engine":"faster_whisper"}
```

Phone: heading is 0°, Person 1's face is at 41° → caption on Person 1, ring around them. Relative bearing ~39° → not in front (>30°) → belt pattern `speech` on motor 2.

---

## 13. Attribution

Runs **on the phone**. Thresholds *(tune)*.

### Speech → person
1. Each tracked face gets a world bearing from its horizontal position (section 8).
2. On `speech_start` / `utterance`: pick the face within **15°** of `bearing_deg`. If two are close, prefer whoever spoke most recently.
3. No face within 15° → **off-camera speaker**: caption at the screen edge with a chevron toward the bearing (or at its screen x if in view but no face found).
4. Keep the assignment for the whole utterance unless the bearing moves > 30° for > 1 s.
5. Person labels: **Person 1, 2, 3** in order of first appearance, each with a fixed color, kept while the track lives. A lost face that reappears within 5 s at a similar bearing keeps its label.

### Sound → source
- Rings on sources need object detection (extra). Core: doorbell and name called use the **top-right panel**, plus an **edge glow** on the matching side if off screen, plus a ring on the person who said the name if they're on camera.

### On the laptop
- Speech bearing: circular mean of `sources` with `speech = true`, `confidence ≥ 0.4` during the utterance. Spread > 25° → `overlap = true`.
- Fixed beams (the demo): those readings only name the beam (0.8 / -179.2), so speech bearings come from SRP-PHAT on the raw mics instead, restricted to that beam's half of the room, one median bearing per utterance; `bearing_spread_deg` = how much its looks agree (≥ 8°). No confident raw-mic bearing → the beam's bearing with the beam's width (`audio.fixed_beam_spread_deg`, 30°) as the spread, never 0.
- Name called: fuzzy match of `user_name` and `name_aliases` in transcripts (allow common misspellings).

---

## 14. The iPhone app

Native **Swift / SwiftUI, iOS 26, iPhone 17**. **No ARKit.**

### Pipeline

| Job | Tool | Rate |
|---|---|---|
| Camera + preview | AVFoundation; `AVCaptureVideoPreviewLayer` (video never passes through our code) | 30 fps, 1080p or 720p, ultrawide, **landscape** |
| Faces | Vision face detection every few frames + Vision tracking in between | detect 5–10 Hz, track every frame |
| Heading | CoreMotion quaternion (section 8) | 60 Hz |
| Network | `URLSessionWebSocketTask` to the relay (wss) | event-driven |
| Belt | CoreBluetooth central, write-without-response | on demand |
| Rendering | SwiftUI overlays; **Liquid Glass** (`.glassEffect()`) | every frame |
| (extra) objects | Core ML YOLO nano: person, dog, cat, tv | 10–15 Hz |

### Threading
- Capture queue for frames, `alwaysDiscardsLateVideoFrames = true`.
- One serial queue for Vision.
- Main thread only updates overlay positions/text. Positions smoothed (critically damped spring) so captions glide.

### Coordinates (where bugs live)
- Tell Vision the image orientation explicitly for the landscape mount.
- Convert boxes with `previewLayer.layerRectConverted(fromMetadataOutputRect:)`.
- **First milestone: plain rectangles on faces.** If they line up, everything on top will.

### What the screen shows
- **Captions:** above heads (below the chin only if no room, never over a mouth). Header: color dot + "Person N" + tone chip (emoji + word). Body: committed text, provisional words dimmer, max two lines, tail toward the head.
- **Typing indicator** on `speech_start`, before words arrive.
- **Rings:** thin ring around the active speaker in their color, pulsing gently with loudness.
- **Top-right panel:** icon + label ("Doorbell", "Name called") + **direction ring** (glowing arc toward the sound, relative to the wearer) + "behind you / to your left". Fades after a few seconds; repeats stack as ×N.
- **Off-camera:** captions and sounds at the matching screen edge with a chevron or edge glow.
- **Status pill** (small, top-left): relay connected, transcription engine, thermal.
- **Debug overlay** (toggle): face boxes, bearings, source markers, latency stages.

### Modes
- **Focus mode:** shows only speech, alerts and name calls; hides background sound rings and labels.
- **Belt-only mode** (for judges trying the belt without the phone): heading locked to 0 (judge faces the array's front), belt driven purely from directions, AR view keeps running on the mounted or table phone.

### Belt logic (on the phone)
- Speech: pattern `speech` when someone **starts** talking and |relative bearing| > 30°; not again for the same person within 5 s.
- Alerts: always buzz (`name_called`, `alert`) toward the bearing.
- Max one non-alert pattern per 1.5 s; higher priority interrupts lower.

### Settings screen
Relay URL + token + session, user name + aliases, HFOV, Calibrate button, focus mode, belt-only mode, belt strength, belt connect/test, feature toggles.

### Heat
Power bank in the harness, no case. `ProcessInfo.thermalState`: `serious` → halve detection rate; `critical` → simplify glass, drop extras. 20-minute test with AirPlay on.

### Accessibility rules (non-negotiable)
- Never cover a mouth.
- Identity never by color alone: always "Person N".
- Legible glass: darker tint + text shadow; test against the brightest background at the booth. Provisional words dimmer but ≥ 4.5:1 contrast.
- Two lines max; smooth motion; respect Reduce Motion.
- Tone describes how it **sounded**, emoji **and** word; low confidence fades the chip.
- Font: SF Pro Rounded or Atkinson Hyperlegible (bundled).

### Dev tips
- Build captions, rings and the panel in **SwiftUI previews with mock data** for instant visual iteration.
- `tools/fake_events.py` sends scripted `sources` / `speech_start` / `utterance` / `alert` through the relay so the app can be built without the audio side.

---

## 15. Alerts

**Core:** doorbell, name called. **Extras:** dog barking, baby crying, knock, alarm, loud bang, car horn, siren, door.

| Alert | Source | Priority | Belt | Notes |
|---|---|---|---|---|
| Name called | transcript fuzzy match | 2 | name_called | aliases + misspellings |
| Doorbell | classifier (YAMNet or PANNs on GPU) | 2 | alert | reliable class |
| Dog barking (extra) | classifier | 1 | alert | merge repeats |
| Baby crying (extra) | classifier | 3 | emergency | |
| Loud bang (extra) | classifier | 2 | alert | **never labelled "gunshot"**; confidence ≥ 0.8 |
| Car horn (extra) | classifier | 2 | alert | |
| Siren (extra) | classifier | 3 | emergency | police / ambulance / fire / civil defense |
| Door (extra) | classifier | 1 | alert | someone came in; AudioSet "Door" also fires on doorbells |

- Minimum confidence 0.6 *(tune)*. Same kind from a similar direction within 4 s updates the existing alert.
- Expire 10 s after the last detection.
- Demo doorbell plays from the Bluetooth speaker at a rehearsed spot.

---

## 16. The belt

### Hardware
- ESP32 + ULN2803 + 8 coin motors at 45° + WS2812B status ring + Anker power bank.
- ULN2803 **COM pin to the motor supply** (flyback diodes). **Shared ground.** Separate power bank ports for motors and ESP32. Confirm the bank doesn't auto-shut-off at low draw (30-minute test).
- Per-motor strength calibration (back motors often feel weaker).
- **Best-hardware presentation:** tidy wiring in a pouch/enclosure, quick-fit strap (velcro/buckle) over clothes, labelled.

### Patterns (ESP32 owns timing)

| ID | Pattern | Rhythm | Use |
|---|---|---|---|
| 0 | stop | off | cancel |
| 1 | speech | buzz-buzz (80 on / 80 off / 80 on ms) | someone started talking, not in front |
| 2 | name_called | buzz-buzz-buzz (100 / 80) | your name |
| 3 | alert | 3 quick pulses ×2 (60 / 60) | doorbell etc. |
| 4 | emergency | fast pulses (50 / 50), max 10 s | crying, alarm |
| 5 | tick | single 40 ms | low priority |
| 6 | test | each motor in turn | wiring check |

### BLE (phone = central, ESP32 = peripheral)
- One GATT service, one writable characteristic (write-without-response).
- **6-byte packet:** `[version 0x01, pattern_id, motor 1..8, motor2 (0=none), strength 0..255, priority]`.
- LED ring: green = connected, blue = speech cue, yellow = alert, red = emergency, dim white pulse = waiting.
- Test BLE in a crowded room early (Ranger had Bluetooth trouble).

---

## 17. Extras: memory, dashboard, cloud

The rule for extras: **nothing here is ever on the live reaction path.** The core demo works with
all of it switched off. If an extra threatens the core, cut the extra.

1. **Memory ("Ask HERE"):**
   - A service on Vultr joins the relay as role `memory` and stores canonical events in Neon. It falls back to local SQLite.
   - It answers questions by person, direction and time, with evidence events ("what did Person 2 say about the fair?").
   - A **seeded synthetic day**, in the same message shapes and labelled as seeded, demonstrates the long-term layer.
2. **Dashboard, "HERE World":**
   - A local page on the demo laptop: a LIVE world view with an event inspector (which provider produced which field), MEMORY, and a REPLAY LAB of what would surface under other attention settings.
   - Insights come from a Databricks job, with precomputed insights as the fallback.
3. **Sound AI:**
   - A Hugging Face classifier with honest `unknown`s; car horn, siren, door and vehicle labels.
   - Gemini context, and attention from a Hugging Face zero-shot model (`perception_enrichment`).
4. **Polish:** animations, nicer direction ring, entrance effects.


---

## 22. Conventions

- Every component runs alone with a mock or fixture (no hardware needed to develop), speaks only the contract,
  keeps its tunables in one config file and sends heartbeats with honest status.
- Python 3.12 (asyncio, `websockets`) on the laptop and relay; Swift/SwiftUI on iOS; Arduino C++ on the ESP32.
- Laptop entry: `python -m laptop --real | --mock | --replay fixtures/<name>`. JSON-lines logs in `logs/`.
- Secrets (relay token, ElevenLabs / Gemini / Neon / Vultr / Databricks keys) only in `.env` files, never committed.

---

## 25. Privacy

- Audio processed on the laptop; only text and directions leave it. Scribe used for transcription when available, whisper locally otherwise.
- No raw audio stored (test fixtures recorded with team consent are the exception).
- No facial recognition; people are "Person 1, 2, 3" only for the session.
- (Extra) memory auto-deletes after 7 days *(tune)*; the user can pause and delete.

---
