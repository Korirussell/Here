# HERE
## Hearing Environmental Reality Engine

> HERE builds a live spatial model of the sound around you, then tells you **who** is talking, **what** they said, **how** they said it, and **where** it came from, through captions and a haptic belt.

This is the master document for the project: the idea, the scope, the hardware rig, the architecture, the message contracts every service must follow, the work split, and the demo plan. Every sub-project's `CLAUDE.md` should point here and to `contracts/`.

**Status:** planning. Numbers marked *(tune)* are starting values to adjust after testing. Anything marked *(spike)* must be confirmed by the investigation in that workstream before other teams rely on it.

---

## Table of contents

1. [The idea](#1-the-idea)
2. [Who it's for and what we're not doing](#2-who-its-for-and-what-were-not-doing)
3. [What the demo shows](#3-what-the-demo-shows)
4. [The prototype rig](#4-the-prototype-rig)
5. [System architecture](#5-system-architecture)
6. [Coordinate system and calibration](#6-coordinate-system-and-calibration)
7. [Time and clocks](#7-time-and-clocks)
8. [The contract](#8-the-contract)
9. [Fusion and attribution rules](#9-fusion-and-attribution-rules)
10. [Alerts](#10-alerts)
11. [Haptic language and belt protocol](#11-haptic-language-and-belt-protocol)
12. [The monitor display](#12-the-monitor-display)
13. [Memory and search](#13-memory-and-search)
14. [Sponsor integrations](#14-sponsor-integrations)
15. [Workstreams and ownership](#15-workstreams-and-ownership)
16. [Investigation spikes](#16-investigation-spikes)
17. [Repo layout and conventions](#17-repo-layout-and-conventions)
18. [Build order and milestones](#18-build-order-and-milestones)
19. [Demo script](#19-demo-script)
20. [Risks and mitigations](#20-risks-and-mitigations)
21. [Privacy stance](#21-privacy-stance)
22. [Decisions log](#22-decisions-log)
23. [Open questions](#23-open-questions)

---

## 1. The idea

Deaf and hard-of-hearing people miss more than words. They miss *where* a sound came from, *who* is speaking in a group, *how* something was said, and the fact that something happened at all while they were looking elsewhere: a doorbell, a baby crying, someone calling their name.

Existing tools solve pieces of this. Live caption apps turn speech into text, but they don't say who said it or where they are. Sound-alert apps notify you of a doorbell, but not which direction to turn.

**HERE's core idea is a spatial world model.** Every sound and every visible person or object becomes an entity with a direction and a history:

```json
{ "id": 7, "kind": "person", "label": "Maya", "bearing_deg": 42, "speaking": true }
{ "id": 12, "kind": "tv", "bearing_deg": 95, "playing_audio": true }
{ "id": 19, "kind": "sound", "label": "doorbell", "bearing_deg": 180 }
```

Everything the user experiences is an **output adapter** reading that model:

| Output | What it delivers |
|---|---|
| Captions display | Who said what, how they said it, attached to where they are |
| Haptic belt | Which direction to turn |
| Alerts | Important sounds, with direction |
| Memory search | What happened, later |

The world model is the product. The belt and display are the first interfaces. The intended long-term form factor is AR glasses plus a wearable belt; the hackathon rig stands in for that (section 4).

### Principles

1. **The world persists.** Entities don't vanish when they leave the camera. The model remembers where they were.
2. **Everything becomes a direction.** Audio, people, objects and the user share one coordinate system (section 6).
3. **The belt says direction, not information.** "Front right, buzz buzz" — never a paragraph.
4. **Commit fast, then refine.** Show something instantly from fast signals; let slower models correct it.
5. **Confidence is visible.** Low-confidence guesses look tentative. Confidently wrong is worse than uncertain.

### One-sentence pitch

> HERE is a spatial awareness system for deaf and hard-of-hearing people that fuses a microphone array and a camera into a live model of who is speaking and what is happening around you, delivered through directional captions and a haptic belt.

---

## 2. Who it's for and what we're not doing

**Primary user:** deaf and hard-of-hearing people, especially in group conversations and at home (doorbells, alarms, babies, pets, being called).

**In scope (core):**
- Sound direction from a microphone array
- Live transcription with speaker attribution
- Tone in captions (how it sounded: shouting, whispering, laughing, asking)
- Sound event recognition with direction (bark, doorbell, crying, music, TV)
- Alerts with direction on screen and on the belt
- Name-called detection
- Haptic belt pointing toward sounds
- Event history and search ("what did Maya say about the fair?")

**Stretch (only if the core passes its gates, section 18):**
- "While you were looking away" summaries
- Type-to-speak replies (ElevenLabs TTS)
- Polished AR caption styling (glass, motion)
- Own-voice level meter
- "Is this directed at me?" detection
- Blind-mode object finding as a 30–60 s "the platform extends" demo

**Not doing (say so if asked):**
- Separating two people talking at the exact same time. We detect overlap and show it; we don't untangle it.
- Navigation or obstacle avoidance for blind users. Safety-critical, needs a different stack.
- Facial recognition of strangers. Names come from opt-in enrollment only.
- Running on real AR glasses. The monitor shows what glasses would show.

---

## 3. What the demo shows

The judges watch a **big monitor** showing what an AR-glasses wearer would see: the live camera view from the wearer's chest, with

- captions attached to each speaker, with name and tone
- labels on sound sources ("📺 TV", "🐕 barking", "🎵 Christmas music")
- off-screen sounds shown at the screen edge with a chevron toward them
- an **alerts panel** in the top right: what happened (doorbell, baby crying, name called…) and a **direction ring** (like a video-game damage indicator) showing where
- a small event feed

At the same time, the **wearer feels the belt** buzz toward each sound and alert.

The wearer cannot see the monitor. That's expected and we say it out loud: the monitor is the glasses view; the wearer's channel today is the belt.

The finale is **memory search**: "what did Maya say about the internship fair?" brings back the caption, who said it, when, the tone, and where she was standing.

---

## 4. The prototype rig

| Part | Role | Notes |
|---|---|---|
| **Windows laptop** (RTX 5000 Ada, 16 GB VRAM) | All processing; renders the monitor view; drives the belt over BLE | Sits at the demo desk. Outputs to the monitor via HDMI. |
| **iPhone**, chest-mounted | Camera + heading sensor only | Portrait, upside down in the harness. Wired to the laptop over USB-C. Ultrawide lens recommended *(tune)*. |
| **ReSpeaker XVF3800** | Sound direction + clean audio | On the desk, USB to the laptop. Its forward direction defines the world frame. |
| **Haptic belt** | 8 motors around the waist | ESP32 + ULN2803 + 8 coin motors + WS2812B ring, Anker power bank. |
| **Monitor** | The judges' view | HDMI from the laptop. |
| **Bluetooth speaker** | Plays doorbell / bark / crying on cue from a known direction | Makes alert demos repeatable. |

### Phone ↔ laptop link

USB-C cable using **Personal Hotspot over USB**: the phone appears to Windows as a network adapter and the app talks to the laptop's IP on that link. Traffic between the two never touches cellular data.

- Requires Apple's device driver (Apple Devices app or iTunes) on Windows.
- **Keep the laptop's internet on campus Wi-Fi**: set the Apple adapter's IPv4 interface metric high (e.g. 100) and Wi-Fi low (e.g. 10), and mark the Apple connection as metered. Verify with `route print` that the default route uses Wi-Fi.
- **Fallback** if the hotspot toggle is unavailable: `iproxy` (libimobiledevice) port-forwarding over USB, with the phone app listening on a port.

### Why these shortcuts are OK

The rig is a stand-in for the real product (glasses + phone compute + belt). Laptop compute, a desk microphone array and a chest phone are prototype conveniences. We state that plainly in the pitch.

### Known physical constraints

- **Stand close to the array.** The array measures direction from its own position. The wearer should stay within about 0.5 m of it; speakers should be 1.5 m+ away. Direction error is roughly `atan(wearer_offset / speaker_distance)`.
- **Portrait narrows the view.** Two people side by side may not fit at conversation distance. The ultrawide lens helps. Measure HFOV in the exact orientation and lens used.
- **Heat.** Test a full-length run with the phone in the harness.

---

## 5. System architecture

```
                         ┌──────────────── Laptop ───────────────────────────────┐
 iPhone (chest)          │                                                      │
 ┌──────────────┐  USB   │  ┌────────────┐  frames   ┌─────────────┐             │
 │ camera       │───────────►            │──────────►│ vision_here │──detections─┐
 │ heading      │───────────►            │           └─────────────┘             │
 │ settings UI  │◄──────────│            │                                       │
 └──────────────┘        │  │            │  audio    ┌─────────────┐             │
                         │  │ world_here │──────────►│ speech_here │──utterances─┤
 ReSpeaker ── USB ──► audio_here ──doa───►  (HUB)   │  └─────────────┘             │
                         │  │            │──────────►┌─────────────┐             │
                         │  │            │  audio    │ sound_here  │──sound_ev───┤
                         │  │            │◄──────────└─────────────┘             │
                         │  │            │◄─────────────────────────────────────┘
                         │  │            │──frame + display state──► ar_here ──HDMI──► Monitor
                         │  │            │──belt_command──► belt_here ──BLE──► Belt
                         │  │            │──events──► memory (local + Vultr)
                         │  └────────────┘
                         └──────────────────────────────────────────────────────┘
```

**world_here is the hub.** Every service connects to it over a local WebSocket and sends and receives typed messages (section 8). It owns fusion, the world model, alert decisions, history, and the display state.

**What runs where:**

| Device | Runs |
|---|---|
| iPhone | Camera capture, frame rotation, heading from quaternion, clock sync, settings UI, (later) type-to-speak input |
| Laptop | audio_here, speech_here, sound_here, vision_here, world_here, ar_here (browser), belt_here bridge |
| Cloud | ElevenLabs Scribe Realtime (transcription, with local fallback), ElevenLabs TTS (stretch), Vultr (memory/search backend, replay page) |
| ESP32 | BLE receive, haptic pattern playback, LED ring. Nothing else. |

**Rule:** every core feature must keep working if the internet drops. Cloud services are upgrades with local fallbacks, never single points of failure.

---

## 6. Coordinate system and calibration

### Frames

**World frame.** Bearings in degrees, measured from the **ReSpeaker array's calibrated forward direction**, **clockwise positive** when viewed from above, normalized to **(-180, 180]**. The array sits still on the desk, so this frame is fixed.

**Body frame.** Bearings relative to the direction the wearer's torso faces. 0 = straight ahead, clockwise positive, (-180, 180]. The chest camera and the belt both live in this frame.

**Heading.** The world bearing the wearer's torso (and camera) faces. Comes from the phone.

```
relative_bearing = wrap(world_bearing - heading)
world_bearing    = wrap(relative_bearing + heading)
wrap(a)          = ((a + 180) mod 360) - 180, mapped into (-180, 180]
```

### Camera pixels ↔ bearings

The camera's optical axis points at `heading`. For a frame of width `W` with horizontal field of view `HFOV`:

```
f              = (W / 2) / tan(HFOV / 2)
bearing(x)     = heading + atan((x - W/2) / f)           (degrees)
pixel_x(b)     = W/2 + f * tan(wrap(b - heading))        (only meaningful if |wrap(b - heading)| < HFOV/2)
```

`x` is the horizontal pixel of the object's centre in the **upright, rotated** frame.

### Belt motor from a bearing

Motors are numbered clockwise from the front: 1 Front, 2 Front-right, 3 Right, 4 Back-right, 5 Back, 6 Back-left, 7 Left, 8 Front-left.

```
rel   = relative_bearing mod 360          (0..360)
motor = (round(rel / 45) mod 8) + 1
```

Optional refinement (stretch): drive the two nearest motors with strengths weighted by distance to get in-between directions.

### Heading from the phone (important)

Do **not** use CoreMotion's Euler `attitude.yaw`. With the phone upright in the harness, Euler angles hit gimbal lock and heading jumps or freezes.

Instead:
1. Read `CMAttitude` as a rotation matrix or quaternion, reference frame `.xArbitraryZVertical` (no magnetometer; indoor compass readings are unreliable).
2. The back camera looks along the device's **−Z** axis. Rotate `(0, 0, −1)` into the reference frame.
3. Drop the vertical component and take `atan2` to get `heading_raw_deg`.

This works for any mounting, including portrait and upside down. Send `heading_raw_deg`; world_here applies the calibration offset.

### Calibration procedures (do these at the start of every demo session)

| Calibration | How | Stored where |
|---|---|---|
| **Array forward** | Someone speaks from directly in front of the array's marked "front". Read the reported direction; that value is the array offset. Also confirm clockwise vs counter-clockwise by speaking from the right. | audio_here config (`array_offset_deg`, `array_clockwise`) |
| **Heading zero** | Wearer stands at the desk facing the array's front, taps **Calibrate** in the iPhone app. world_here stores `heading_offset = heading_raw` at that moment. | world_here state |
| **HFOV** | With the phone in the harness (same lens and orientation), place two objects at the left and right edges of the frame and measure their angle apart from the camera. | iPhone app sends it in `video_frame`; default *(tune)* |

---

## 7. Time and clocks

- **All laptop services timestamp with the laptop's system clock**, Unix seconds as a float (`time.time()` in Python, `Date.now()/1000` in JS).
- **Every message carries `t`**: the time the underlying observation happened (capture time for frames, audio sample time for DoA and audio), not when the message was sent.
- **The phone syncs its clock offset** to the laptop on connect and every 10 s using `clock_ping` / `clock_pong` (section 8). It converts its capture timestamps to laptop time before sending. Use the round-trip midpoint method; keep the sample with the smallest round trip.
- **Interpolation:** when a service needs a value at time `t` (e.g. vision needs heading at a frame's capture time), interpolate between the two nearest samples.

Without shared time, fusion breaks in ways that look like random bugs. Treat timestamps as part of correctness.

---

## 8. The contract

The source of truth lives in `contracts/` as JSON Schema files generated from the TypeScript definitions below. **Change it only through the integrator (world_here owner).** When the contract changes, bump `v`, update `contracts/`, this section, and every mock.

### 8.1 Transport

- **Hub:** world_here runs a WebSocket server at `ws://0.0.0.0:8765`.
- Every client connects, sends `hello` first, then sends and receives messages.
- **Text messages are JSON objects** with the common envelope below.
- **Binary messages** only ever follow a JSON header that announces them (`video_frame`, `audio_chunk`, and hub→display `frame`). The binary message is sent **immediately after** its header on the same socket. Receivers pair them by order.
- **Drop, don't queue.** Real-time consumers keep only the newest frame or state. Never build up a backlog.
- Clients **reconnect automatically** with backoff (500 ms doubling to 5 s).

### 8.2 Envelope (every JSON message)

```ts
interface Envelope {
  type: string        // message type, see below
  v: 1                // contract version
  src: ServiceName    // who sent it
  t: number           // observation time, laptop clock, unix seconds (float)
  seq?: number        // per-sender increasing counter, for spotting drops
}

type ServiceName =
  | 'world_here' | 'audio_here' | 'speech_here' | 'sound_here'
  | 'vision_here' | 'ios_here' | 'ar_here' | 'belt_here' | 'memory_here'
```

### 8.3 Shared types

```ts
/** Degrees, world frame, clockwise positive, (-180, 180]. */
type WorldBearing = number
/** Degrees, body frame (0 = wearer's front), clockwise positive, (-180, 180]. */
type RelBearing = number
/** Pixel box [x, y, width, height] in the upright frame, origin top-left. */
type BBox = [number, number, number, number]

type Tone =
  | 'neutral' | 'excited' | 'laughing' | 'shouting'
  | 'whispering' | 'questioning' | 'sad'

type SoundLabel =
  | 'speech' | 'dog_barking' | 'cat_meowing' | 'doorbell' | 'knock'
  | 'baby_crying' | 'alarm' | 'smoke_alarm' | 'phone_ringing'
  | 'music' | 'tv' | 'loud_bang' | 'footsteps' | 'laughter'
  | 'applause' | 'vehicle' | 'siren' | 'other'

type AlertKind =
  | 'doorbell' | 'knock' | 'baby_crying' | 'dog_barking' | 'name_called'
  | 'smoke_alarm' | 'alarm' | 'siren' | 'loud_bang' | 'footsteps' | 'phone_ringing'

type EntityKind =
  | 'person' | 'dog' | 'cat' | 'tv' | 'laptop' | 'phone'
  | 'speaker' | 'vehicle' | 'unknown_source'
```

### 8.4 Session and control messages

```ts
/** Client → hub. First message after connecting. */
interface Hello extends Envelope {
  type: 'hello'
  role: ServiceName
  subscribe: string[]        // message types this client wants; ['*'] for all
}

/** Any → hub → all. Liveness and health. Send every 2 s. */
interface Heartbeat extends Envelope {
  type: 'heartbeat'
  status: 'ok' | 'degraded' | 'error'
  detail?: string            // e.g. "scribe offline, using local whisper"
  rate_hz?: number           // main output rate, for the debug overlay
}

/** Phone ↔ hub clock sync. */
interface ClockPing extends Envelope { type: 'clock_ping'; t_phone: number }
interface ClockPong extends Envelope { type: 'clock_pong'; t_phone: number; t_hub: number }

/** Phone → hub to request a change; hub → all to announce the active config. */
interface Config extends Envelope {
  type: 'config'
  profile: 'deaf' | 'custom'
  user_name: string                       // for name-called detection
  name_aliases: string[]                  // nicknames, common misspellings
  known_people: { id: number; name: string }[]   // opt-in enrollment
  features: {
    captions: boolean
    tone: boolean
    sound_labels: boolean
    alerts: boolean
    belt: boolean
    memory: boolean
    looking_away_summary: boolean          // stretch
    type_to_speak: boolean                 // stretch
  }
  alerts_enabled: Partial<Record<AlertKind, boolean>>
  belt_strength: number                   // 0..255
  caption_scale: number                   // 0.75..1.5
}

/** Any → hub. Calibration actions (section 6). */
interface Calibrate extends Envelope {
  type: 'calibrate'
  kind: 'heading_zero' | 'array_forward'
  value?: number                          // for array_forward: measured offset
}
```

### 8.5 Sensor messages

```ts
/** ios_here → hub. JSON header, then one binary message: JPEG bytes. ~24–30 fps. */
interface VideoFrame extends Envelope {
  type: 'video_frame'
  frame_id: number
  width: number              // after rotation (upright)
  height: number
  hfov_deg: number           // horizontal FOV of this upright frame
  lens: 'wide' | 'ultrawide'
  encoding: 'jpeg'
}

/** ios_here → hub. ~60 Hz, small. Not tied to video frames. */
interface Heading extends Envelope {
  type: 'heading'
  heading_raw_deg: number    // from quaternion method (section 6), arbitrary zero
  quality: 'good' | 'settling' | 'bad'
}

/** audio_here → hub. ~10–30 Hz (spike: confirm achievable rate). */
interface Doa extends Envelope {
  type: 'doa'
  bearing_deg: WorldBearing  // calibrated
  confidence: number         // 0..1 (spike: derive from what the device provides)
  speech: boolean            // device or local VAD says speech is present
  energy_db: number          // level of the processed signal
  /** The array's two focused beams, each steered at its own talker (two-talker tracking). Optional. */
  beams?: {
    bearing_deg: WorldBearing
    speech: boolean          // this beam hears speech
    energy_db: number        // device speech energy, uncalibrated dB; 0 when silent
  }[]
}

/**
 * audio_here → hub. Direction of a sound_event, from SRP-PHAT on the array's raw mics over the
 * event's window (needs the 6-channel USB firmware). Sent after each sound_event update; join on
 * event_id. t = t_end.
 */
interface SoundDoa extends Envelope {
  type: 'sound_doa'
  event_id: string           // the sound_event this locates
  label: SoundLabel
  bearing_deg: WorldBearing
  confidence: number         // 0..1, peak coherence across mic pairs
  t_start: number            // audio window used
  t_end: number
}

/**
 * audio_here → hub → speech_here, sound_here, memory. JSON header, then one
 * binary message of raw PCM. Default: 16 kHz, mono, signed 16-bit little-endian,
 * 100 ms per chunk (1600 samples). t = time of the first sample.
 */
interface AudioChunk extends Envelope {
  type: 'audio_chunk'
  sample_rate: 16000
  channels: 1
  format: 's16le'
  samples: number
  stream: 'processed'        // processed (beamformed/denoised) output of the array
}
```

### 8.6 Perception messages

```ts
/**
 * speech_here → hub. One utterance is sent many times while it grows
 * (final=false), then once more with final=true. Same utt_id throughout.
 */
interface Utterance extends Envelope {
  type: 'utterance'
  utt_id: string
  t_start: number
  t_end: number              // end so far (or final end)
  text: string               // final words, will not change
  pending: string            // provisional words, may change
  final: boolean
  tone: Tone | null
  tone_confidence: number    // 0..1
  loudness: number           // 0..1 relative to the room
  engine: 'scribe_realtime' | 'faster_whisper'
  name_mentions: { name: string; t: number; confidence: number }[]
}

/** sound_here → hub. One per detected event; repeats are merged (see `count`). */
interface SoundEvent extends Envelope {
  type: 'sound_event'
  event_id: string
  label: SoundLabel
  raw_label: string          // classifier's own class name
  confidence: number         // 0..1
  t_start: number
  t_end: number
  count: number              // repeats merged into this event (bark x3)
  detail?: string            // e.g. music genre "christmas"
}

/** vision_here → hub. One per processed frame. */
interface Detections extends Envelope {
  type: 'detections'
  frame_id: number           // matches the VideoFrame it came from
  objects: {
    track_id: number         // stable across frames
    kind: EntityKind         // mapped from detector class
    raw_class: string
    confidence: number
    bbox: BBox
    bearing_deg: WorldBearing   // using heading at the frame's t
    face_bbox?: BBox            // for people, when a face is found
    speaking?: number           // people with a face: 0..1 lip-motion score (§9.1)
  }[]
}
```

### 8.7 Output messages (from world_here)

```ts
/** world_here → belt_here. */
interface BeltCommand extends Envelope {
  type: 'belt_command'
  pattern: BeltPattern       // section 11
  motor: number              // 1..8
  motor2?: number            // optional second motor for in-between directions
  strength: number           // 0..255
  priority: number           // higher interrupts lower
}

type BeltPattern =
  | 'speech' | 'name_called' | 'alert' | 'emergency'
  | 'tick' | 'stop' | 'test'

/** world_here → ar_here, memory, ios_here. */
interface Alert extends Envelope {
  type: 'alert'
  alert_id: string
  kind: AlertKind
  label: string              // display text, e.g. "Doorbell"
  priority: 1 | 2 | 3        // 3 = emergency
  bearing_deg: WorldBearing | null
  rel_bearing_deg: RelBearing | null
  entity_id?: number
  active: boolean            // false when it ends or expires
  confidence: number
}
```

### 8.8 Display frame (world_here → ar_here)

For every video frame it forwards, the hub sends one JSON `frame` message followed by the JPEG of that same frame. This is **backward-compatible with ar_here's current `FrameMeta`**: `faces` and `captions` keep their shape; the other fields are new and optional.

```ts
interface FrameMeta {
  type: 'frame'
  frame_id: number
  t: number
  width: number
  height: number
  camera: {
    hfov_deg: number
    yaw_deg: WorldBearing     // = current heading (world bearing the camera faces)
  }

  /** People on camera. id = world entity id. */
  faces: {
    id: number
    bbox: BBox                // face box if found, else top of person box
    bearing?: WorldBearing
  }[]

  /** At most one per speaker. speaker_id = entity id (on camera or not). */
  captions: {
    speaker_id: number
    speaker_label?: string    // "Maya" if enrolled, else omitted
    text: string
    pending: string
    final: boolean
    tone?: Tone | null
    tone_confidence?: number
    loudness?: number
    bearing?: WorldBearing     // set when the speaker has no visible face
    overlap?: boolean          // overlapping speech detected
  }[]

  /** Non-person sound sources on camera (NEW). */
  objects?: {
    id: number
    kind: EntityKind
    bbox: BBox
    bearing: WorldBearing
    active_sound?: { label: SoundLabel; emoji: string; text: string }  // "🐕", "barking"
  }[]

  /** Sounds with no visible source, placed by direction (NEW). */
  sounds?: {
    id: string
    label: SoundLabel
    emoji: string
    text: string               // "Christmas music"
    bearing: WorldBearing
    count?: number
  }[]

  /** Currently active alerts for the top-right panel (NEW). */
  alerts?: {
    alert_id: string
    kind: AlertKind
    label: string
    priority: 1 | 2 | 3
    rel_bearing_deg: RelBearing | null
    age_s: number
  }[]

  /** Recent events for the feed (NEW). Newest first, max 6. */
  feed?: { t: number; text: string; icon: string }[]

  /** Health for the debug overlay (NEW). */
  health?: Record<ServiceName, 'ok' | 'degraded' | 'error' | 'offline'>
}
```

### 8.9 Memory messages

```ts
/** ios_here or ar_here → hub → memory_here. */
interface SearchRequest extends Envelope {
  type: 'search_request'
  query_id: string
  text: string               // "what did Maya say about the fair?"
  limit: number
}

/** memory_here → hub → requester. */
interface SearchResult extends Envelope {
  type: 'search_result'
  query_id: string
  results: {
    t: number
    kind: 'utterance' | 'sound' | 'alert'
    speaker_label?: string
    text: string
    tone?: Tone | null
    bearing_deg?: WorldBearing
    snapshot_url?: string    // frame thumbnail at that moment
    score: number
  }[]
}
```

### 8.10 Stretch messages

```ts
/** ios_here → hub → speech_here. Type-to-speak via ElevenLabs TTS. */
interface SpeakRequest extends Envelope {
  type: 'speak_request'
  text: string
  voice_id?: string
}

/** world_here → ar_here. "While you were looking away." */
interface MissedSummary extends Envelope {
  type: 'missed_summary'
  t_from: number
  t_to: number
  items: { t: number; text: string; speaker_label?: string; bearing_deg?: WorldBearing }[]
}
```

### 8.11 Example: one second in the life of the system

```json
{"type":"doa","v":1,"src":"audio_here","t":1760000000.10,"bearing_deg":38.5,"confidence":0.82,"speech":true,"energy_db":-28.1}
{"type":"heading","v":1,"src":"ios_here","t":1760000000.11,"heading_raw_deg":112.4,"quality":"good"}
{"type":"detections","v":1,"src":"vision_here","t":1760000000.12,"frame_id":881,"objects":[{"track_id":3,"kind":"person","raw_class":"person","confidence":0.91,"bbox":[402,180,210,520],"bearing_deg":41.0,"face_bbox":[455,190,96,118]}]}
{"type":"utterance","v":1,"src":"speech_here","t":1760000000.40,"utt_id":"u-0192","t_start":1760000000.05,"t_end":1760000000.40,"text":"did you see","pending":"the email","final":false,"tone":"excited","tone_confidence":0.7,"loudness":0.55,"engine":"scribe_realtime","name_mentions":[]}
{"type":"belt_command","v":1,"src":"world_here","t":1760000000.41,"pattern":"speech","motor":2,"strength":160,"priority":1}
```

world_here concludes: speech at 38.5°, person track 3 at 41.0° → track 3 is speaking → caption on track 3; relative bearing ≈ 41° → motor 2 (front-right).

---

## 9. Fusion and attribution rules

All thresholds are *(tune)* starting values.

### 9.1 Speech attribution

For each utterance segment `[t_start, t_end]`:

1. Collect `doa` samples in the window with `speech = true` and `confidence ≥ 0.4`.
2. Compute the **circular mean** bearing (average unit vectors, not raw angles). If the spread is large (circular std > 25°), mark the segment as possibly **overlapping speech**.
3. Find people (from recent `detections`) whose bearing is within **15°** of the speech bearing. Choose the closest. Prefer the person who was speaking most recently if two are close.
4. If no visible person matches, attach to an **off-camera speaker entity** at that bearing. Reuse an existing off-camera entity if one is within 20° and was active in the last 30 s; otherwise create one.
5. Speaker identity sticks: once an utterance is attributed, partial updates keep the same speaker unless the DoA clearly moves (> 30° for > 1 s).

**Mouth-movement scoring:** when two or more visible people are within 15° of the speech bearing, the one whose `detections.speaking` ≥ 0.35 (highest wins) is chosen; with no DoA at all, a person with moving lips is chosen. Scores older than 1 s are ignored.

### 9.2 Sound source attribution

A `sound_event` attaches to a visible object when **both** hold:

- bearing within **20°**, and
- the types are compatible:

| Sound label | Compatible entity kinds |
|---|---|
| speech | person, tv, laptop, phone, speaker |
| dog_barking | dog |
| cat_meowing | cat |
| music | tv, laptop, phone, speaker |
| tv | tv |
| phone_ringing | phone |
| vehicle, siren | vehicle |
| laughter | person, tv |
| others | none (place by bearing) |

Otherwise, it becomes a direction-only `sounds[]` entry, or an off-screen edge indicator if outside the camera's view.

### 9.3 Entity memory

- Entities persist after leaving the camera. Their last known world bearing is kept.
- Visible entities are re-matched to `track_id`s by bearing continuity when tracks are lost and re-found.
- Entities not seen or heard for **5 minutes** are archived (kept in memory/search, removed from the live model).

### 9.4 Belt output rules

- **Speech:** pulse toward a speaker when they **start** talking and are **not in front** (|rel| > 30°). Don't re-pulse for the same speaker within 5 s.
- **Alerts:** always pulse, using the alert pattern, until acknowledged or 10 s pass.
- **Silence is a feature:** no belt output for things the wearer is already facing, except alerts.
- Rate limit: no more than one non-alert pattern per 1.5 s.

---

## 10. Alerts

| Alert | Source | Priority | Belt pattern | Notes |
|---|---|---|---|---|
| Name called | speech_here `name_mentions` | 2 | name_called | Fuzzy match on user_name + aliases |
| Doorbell | sound_here | 2 | alert | Reliable class |
| Knock | sound_here | 2 | alert | |
| Baby crying | sound_here | 3 | emergency | Long-lived until it stops |
| Smoke alarm / alarm | sound_here | 3 | emergency | |
| Siren | sound_here | 2 | alert | |
| Dog barking | sound_here | 1 | alert | Merge repeats (×3) |
| Phone ringing | sound_here | 1 | alert | |
| Loud bang | sound_here | 2 | alert | **Shown as "Loud bang", not "gunshot."** Claps, slams and drops cause false positives; a false "GUNSHOT" at a demo is a disaster. Require confidence ≥ 0.8. |
| Footsteps | sound_here | 1 | tick | Lowest reliability; off by default |

**Rules**
- Minimum confidence 0.6 *(tune)*, 0.8 for loud bang.
- Debounce: the same alert kind from a similar bearing within 4 s updates the existing alert instead of creating a new one.
- Alerts expire 10 s after the last detection (emergencies: when the sound stops + 5 s).
- Every alert is written to memory.

**Demo practice:** trigger alerts by playing recordings from the Bluetooth speaker placed at a known direction. Rehearse the exact positions.

---

## 11. Haptic language and belt protocol

### 11.1 Patterns

The ESP32 **owns pattern timing**. The laptop sends *which* pattern on *which* motor; BLE latency jitter can't smear the rhythm.

| ID | Pattern | Rhythm | Use |
|---|---|---|---|
| 0 | stop | all off | Cancel |
| 1 | speech | buzz-buzz (80 ms on, 80 off, 80 on) | Someone started talking |
| 2 | name_called | buzz-buzz-buzz (100/80) | Your name was said |
| 3 | alert | 3 rapid pulses (60/60) ×2 | Doorbell, knock, bark, bang |
| 4 | emergency | fast pulses (50/50) until stopped, max 10 s | Alarm, baby crying |
| 5 | tick | single 40 ms tap | Low-priority cues |
| 6 | test | each motor in turn | Calibration / wiring check |

Rules: a higher-priority pattern interrupts a lower one. Each pattern is short; continuous vibration fades from attention within seconds and tires the wearer.

### 11.2 BLE protocol

- ESP32 exposes one GATT service with one writable characteristic (no response) for commands.
- **Command packet: 6 bytes**

| Byte | Field | Notes |
|---|---|---|
| 0 | version | `0x01` |
| 1 | pattern_id | table above |
| 2 | motor | 1..8 (0 = none) |
| 3 | motor2 | 0 = none |
| 4 | strength | 0..255 (PWM duty via LEDC) |
| 5 | priority | 0..255 |

- belt_here is a small Python process using `bleak`: subscribes to `belt_command` on the hub, writes packets, reconnects automatically, reports health.
- ESP32 status LED ring: green = connected, blue = speech cue, yellow = alert, red = emergency, dim white pulse = waiting for connection.

### 11.3 Belt hardware checklist

- ULN2803 **COM pin to the motor supply** (enables its flyback diodes).
- **Shared ground** between ESP32 and motor supply.
- Separate power bank ports for motors and ESP32 so motor spikes don't reset the ESP32.
- Some power banks auto-shutoff at low draw; verify the ESP32 port stays on for 30+ minutes.
- Per-motor strength calibration (back motors often feel weaker).

---

## 12. The monitor display

Rendered by **ar_here** (React Three Fiber, browser, fullscreen) from `frame` messages. Layout for a landscape monitor with a portrait video:

```
┌──────────────────────────────────────────────────────────────────────┐
│  HERE  ● live            ┌──────────────────┐        ┌─────────────┐ │
│                          │                  │        │ ALERTS      │ │
│  EVENT FEED              │   live camera    │        │  ◜  ◝       │ │
│  14:02 Maya: "…"         │   (portrait)     │        │ ◟ ⬤ ◞  ←   │ │
│  14:02 🐕 barking ×3      │  [caption box]   │        │ Doorbell    │ │
│  14:01 🔔 doorbell        │      ▼           │        │ behind you  │ │
│                          │   person         │        └─────────────┘ │
│                          │                  │                        │
│  ‹ voice (off camera)    │                  │      BELT  ○ ● ○ ...   │
│                          └──────────────────┘                        │
└──────────────────────────────────────────────────────────────────────┘
```

- **Center:** camera view with captions above speakers, labels on sound sources, edge chevrons for off-camera sounds.
- **Top right:** alerts panel with a **direction ring** (damage-indicator style: glowing arc toward the sound, relative to the wearer) plus label and short description ("Doorbell, behind you").
- **Left:** event feed (newest first).
- **Bottom right (optional):** belt ring showing which motor is firing, so the audience sees what the wearer feels.

### Build stages

1. **Plain overlay first (early, required):** boxes/name tags, plain caption text under speakers, plain alert panel. This is the debugging tool for attribution and the fallback demo visual.
2. **Polish later (gated, timeboxed):** glass captions, tone chips, smooth follow, animated ring. Only after the core passes its gate (section 18).

### Accessibility rules (non-negotiable)

- Never cover a mouth; captions go above heads, or below the chin if no room.
- Speaker identity is never color alone; always a name or "Person N".
- High contrast: white text on dark plates. Provisional words dimmer but still ≥ 4.5:1.
- Max two lines per caption; older words scroll off.
- Smooth positions; no jitter. Respect `prefers-reduced-motion`.
- Tone describes how it **sounded** (whispering, shouting, laughing, asking), shown as emoji **and** word. Low confidence fades the chip.
- Font: Atkinson Hyperlegible.

---

## 13. Memory and search

**memory_here** stores every final utterance, sound event and alert with time, speaker, bearing, tone and an optional frame thumbnail.

- **Local first:** SQLite on the laptop, written live.
- **Search:** embedding-based semantic search over utterance text and sound labels, plus filters (speaker, time range, kind). "who mentioned internships" should match "Disney is hiring interns".
- **Vultr:** sync the event store to a small Vultr-hosted service that provides the search API and a **shareable replay page** (timeline + search) judges can open on their own phones after the demo.
- **Batch cleanup (optional):** re-transcribe stored audio with ElevenLabs Scribe v2 batch for a cleaner record with diarization and non-speech tags.

UI: a search box on the monitor (and on the phone), results as cards with who, what, when, tone and "where" (a small direction indicator).

---

## 14. Sponsor integrations

**ElevenLabs**
- **Scribe v2 Realtime** as the primary live transcription engine (~150 ms model latency per ElevenLabs, excluding network). Audio upload is light, so campus Wi-Fi should be fine. **Local faster-whisper (large-v3-turbo, CUDA) is the automatic fallback** if the connection drops or latency exceeds 1.5 s. The `engine` field records which one produced each utterance.
- **TTS for type-to-speak (stretch, strong demo):** the wearer types a reply on the phone; HERE speaks it aloud. Turns HERE from "understand the room" into "take part in it".
- **Scribe v2 batch (optional):** cleaned-up transcripts for memory.

**Vultr**
- Hosts the memory/search backend and the replay page. Nothing latency-critical runs in the cloud.

Check each sponsor's prize criteria at the event.

---

## 15. Workstreams and ownership

| Project | Owns | Consumes | Publishes | Owner |
|---|---|---|---|---|
| **world_here** | Hub, fusion, entities, alerts logic, belt decisions, display state, calibration state | everything | `frame`, `alert`, `belt_command`, `config` | _(integrator)_ |
| **audio_here** | ReSpeaker: DoA (+ focused beams), VAD flag, processed audio, array calibration, sound-event direction (raw mics), LED ring, **fixture recording** | config, `sound_event` | `doa`, `audio_chunk`, `sound_doa` | |
| **speech_here** | Live transcription (Scribe + fallback), utterance segmentation, tone, loudness, name detection, TTS (stretch) | `audio_chunk`, config | `utterance` | |
| **sound_here** | Sound classification, label mapping, repeat merging | `audio_chunk` | `sound_event` | |
| **ios_here** | iPhone app: camera, rotation, quaternion heading, clock sync, USB transport, settings UI, calibrate button | `config`, `search_result` | `video_frame`, `heading`, `config`, `calibrate`, `search_request` | |
| **vision_here** | YOLO detection + ByteTrack tracking, face boxes, pixel → bearing | `video_frame`, `heading` | `detections` | |
| **ar_here** | Monitor display (exists: glass captions, layout, mock server) | `frame` (+ JPEG) | `search_request` | |
| **belt_here** | ESP32 firmware, BLE bridge (`bleak`), patterns, hardware | `belt_command` | `heartbeat` | |
| **memory_here** | SQLite store, embeddings, search, Vultr sync, replay page | utterances, sound events, alerts | `search_result` | |

**Suggested splits**
- **3 people:** (A) audio_here + sound_here · (B) ios_here + vision_here + belt_here · (C) world_here + speech_here + ar_here; memory_here goes to whoever hits their first milestone first.
- **4 people:** (A) audio_here + sound_here · (B) speech_here + memory_here · (C) ios_here + vision_here · (D) world_here + ar_here; belt_here to the first person free.

---

## 16. Investigation spikes

Each spike ends with a **short written report** (`<project>/SPIKE.md`): measurements, what works, what doesn't, and recommended settings for other teams. Spikes come before polish.

### audio_here: ReSpeaker XVF3800 (highest priority)

Everything spatial depends on this. Answer with measurements:

- **Setup:** Which firmware is installed? Do we need the USB firmware variant? What audio channels are exposed over USB (processed output, raw mics, both)? Sample rates?
- **Direction readout:** How is DoA read from the host (control interface, commands, library)? Update rate? Is there a speech flag or confidence?
- **Accuracy:** Play speech from a speaker at 0°, 45°, 90°, … 315°, then in between, at 1 m and 3 m. Table of error per angle. Worse behind or to the sides?
- **Noise:** Repeat with background chatter or music. How much worse?
- **Speed:** When a new person starts talking, how long until the bearing settles?
- **Two speakers:** Quick alternation: does it follow? Overlap: what does it report?
- **Non-speech:** Does it give directions for a doorbell, clap, bark, or only speech?
- **Calibration:** Where is 0°? Clockwise or counter-clockwise? Matches section 6?
- **Fixtures:** Record the scenarios in section 18 (M2).

Deliverable: `respeaker_probe.py` (live direction printout + logging), `SPIKE.md` with results, recommended smoothing window and confidence threshold.

### speech_here

- Scribe Realtime latency from campus Wi-Fi: time from end of a word to it appearing, partial vs final.
- faster-whisper fallback latency on the RTX at 0.5 s update intervals.
- Accuracy in a noisy room using the array's processed audio vs a raw mic.
- How to segment turns (Scribe's segmentation vs Silero VAD vs DoA changes).
- Tone: what signals are usable (loudness, pitch variance, laughter detection, transcript punctuation).

### sound_here

- YAMNet vs PANNs: which labels map to our `SoundLabel` list, confidence distributions, false-positive rate in a noisy hall (record an hour of hackathon noise and count false alerts).
- Music genre detection: is "Christmas music" reliably distinguishable?
- Latency per window on the GPU.

### ios_here

- Quaternion heading stability in the chest mount: drift over 10 minutes, behavior when walking, turning.
- Personal Hotspot over USB on Windows: setup steps, throughput, latency; `iproxy` fallback.
- Thermal behavior streaming 720p @ 30 fps for 20 minutes.
- Portrait + ultrawide: HFOV measurement, distortion near edges.

### vision_here

- YOLO model size vs FPS on the RTX at the stream's resolution.
- Face box availability for caption anchoring (YOLO face model vs MediaPipe).
- Track ID stability when people turn or cross.

### belt_here

- Motor strength felt through a shirt; per-motor calibration values.
- Can wearers tell 8 directions apart? Are in-between directions (two motors) useful?
- BLE latency from laptop write to vibration.

---

## 17. Repo layout and conventions

```
here/
  HERE_PLAN.md               ← this document
  contracts/                 ← JSON Schemas generated from section 8 (source of truth)
    envelope.schema.json
    doa.schema.json
    ...
  fixtures/                  ← recorded scenarios (audio, doa log, video, heading)
  tools/
    hub_tap.py               ← print/log every message on the hub
    replay.py                ← replay a fixture into the hub at real time
  world_here/   CLAUDE.md …
  audio_here/   CLAUDE.md …
  speech_here/  CLAUDE.md …
  sound_here/   CLAUDE.md …
  vision_here/  CLAUDE.md …
  ios_here/     CLAUDE.md …  (Xcode project; built on the Mac)
  ar_here/      CLAUDE.md …  (exists)
  belt_here/    CLAUDE.md …  (firmware/ + bridge/)
  memory_here/  CLAUDE.md …
```

### Every project must

1. **Run on its own** with a mock producer and a mock consumer (like `ar_here/server/mock_server.py`).
2. **Speak only the contract.** No private side channels between projects.
3. **Support fixture replay** so it can be developed without hardware.
4. **Send heartbeats** with honest status.
5. **Keep tunable numbers in one config file**, not scattered in code.
6. **Have a `CLAUDE.md`** using the template below.

### CLAUDE.md template for each project

```markdown
# <project>

Part of HERE. Read ../HERE_PLAN.md (sections 6–9 at minimum) and ../contracts/ first.

## Job
<one paragraph: what this project does and does not do>

## Inputs / outputs
Consumes: <message types>. Publishes: <message types>. Connects to the hub at ws://localhost:8765.

## Run
<commands: real mode, mock mode, fixture replay>

## Rules
- Follow the contract exactly. Do not change message shapes; ask the integrator.
- Timestamps are observation times on the laptop clock (HERE_PLAN.md §7).
- Bearings: world frame, clockwise positive, (-180, 180] (HERE_PLAN.md §6).
- Drop stale data instead of queueing.
- Tunables live in config.<ext>.

## Current milestone
<what "done" means right now>
```

### Code conventions
- Python 3.11+ for laptop services (asyncio + `websockets`), TypeScript for ar_here, Swift for ios_here, Arduino C++ for the ESP32.
- Python services: `python -m <project> --mock`, `--replay fixtures/<name>`, `--real`.
- Log in JSON lines to `logs/<project>.jsonl` so `tools/` can analyse any run.

---

## 18. Build order and milestones

Each milestone is something that **works end to end**, even if parts are mocked.

| # | Milestone | Done when |
|---|---|---|
| **M0** | Contracts + walking skeleton | `contracts/` exists; world_here hub runs; a fake `doa` → hub → fake caption shows in ar_here → fake `belt_command` printed by belt_here. All with mocks. |
| **M1** | Sensors live | ReSpeaker probe prints bearings; phone streams frames + heading over USB; YOLO detects people; Scribe or whisper prints transcripts; sound classifier prints labels; belt buzzes a chosen motor over BLE. Each alone. |
| **M2** | Fixtures recorded | 5-min scenarios saved with synced audio, DoA, video and heading: (1) two people taking turns, (2) three people with interruptions, (3) name called from behind, (4) doorbell / bark / crying from the speaker at known angles, (5) noisy background, (6) TV playing + person talking. Everyone develops against these. |
| **M3** | Attribution | On fixture 1, captions land on the correct speaker ≥ 90% of utterances. Plain overlay shows it. |
| **M4** | Belt end to end | Live: speech and alerts buzz the correct motor ≥ 90% of the time for sources ~2 m away, front/sides/back. |
| **M5** | Alerts | Doorbell, crying, bark, name called raise alerts with the correct direction ring; false alerts in fixture 5 < 1 per 5 minutes. |
| **M6** | Memory search | Search finds utterances and alerts from the last session by meaning; replay page live on Vultr. |
| **GATE** | **Core passes M3–M5 live, three runs in a row** | Only then start polish. |
| **M7** | Display polish (timeboxed) | Glass captions, tone chips, animated ring, smooth follow. |
| **M8** | Stretch features (timeboxed) | Type-to-speak; looking-away summary; own-voice level. |
| **M9** | Demo rehearsal | Full script (section 19) run 5 times, with calibration, in the actual room. |

**Integration cadence:** merge to main and run the full system on a fixture at fixed checkpoints (e.g. every 4–6 hours during build time). Anything that breaks the skeleton gets fixed before new features.

---

## 19. Demo script

Target: 3 minutes, rehearsed. Roles: **wearer**, **two speakers** (Maya, Jordan), **operator** (laptop + Bluetooth speaker), **presenter**.

0. **Setup (before judges arrive):** calibrate array forward, heading zero and HFOV; test belt motors; confirm Scribe connected (fallback ready).
1. **Hook (15 s):** "Imagine a group conversation where you can't tell who's talking or where. Here's what HERE gives you." Point at the monitor: "This is what AR glasses would show; the wearer feels the belt."
2. **Conversation (60 s):** Maya and Jordan talk naturally on either side. Captions appear over each with tone ("excited", "asking"). Jordan speaks while the wearer faces Maya → belt buzzes toward Jordan → wearer turns.
3. **Sound sources (30 s):** TV clip plays → captions attach to the TV. A bark from the speaker → "🐕 barking" on the source or at its direction.
4. **Alerts (30 s):** Doorbell from behind → alert panel, direction ring points back, belt pulses back. Someone says the wearer's name off camera → "Name called" + triple buzz toward them.
5. **Memory finale (30 s):** "What did Maya say about the internship fair?" → result card with who, when, tone and direction. Optionally show the replay page on a judge's phone via QR code.
6. **Close (15 s):** "One spatial world model; captions, haptics, alerts and memory are just outputs. Glasses are where this goes next."

**Backup plans:** if Scribe fails → fallback engine is automatic (say nothing). If ReSpeaker misbehaves → replay fixture 1 through the full pipeline and narrate. If the belt disconnects → the belt ring on screen still shows what it would do.

---

## 20. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| DoA unreliable in a noisy hall | Wrong captions, wrong belt | Smooth over a window; confidence threshold; test in the real room early; demo with speakers 1.5–2 m away; fixture-replay backup |
| Overlapping speech | Garbled captions | Detect and mark `overlap`; don't try to separate; speakers take turns in demo |
| Wearer far from the desk array | Directions skewed | Wearer stays within ~0.5 m of the array |
| Campus Wi-Fi drops | Cloud transcription stops | Local whisper fallback; all core features local |
| Phone link problems on Windows | No video | Test hotspot-over-USB early; `iproxy` fallback |
| Phone overheats | Throttled camera | 720p, dim screen, test full-length runs, ventilated mount |
| Heading drift / gimbal lock | Belt points wrong after turning | Quaternion method; recalibrate between demos |
| False alerts (esp. "gunshot") | Embarrassing demo moment | "Loud bang" label, high threshold, recordings on cue |
| Belt too weak / indistinct | Wearer can't feel direction | Per-motor calibration; short distinct patterns; test through clothing |
| Clock mismatch | Attribution randomly wrong | Clock sync, observation timestamps, fixture tests |
| Polish eats the core | Pretty but wrong | Gate before M7; timebox polish |
| Integration chaos with several agents | Broken builds | Contract changes only via integrator; mocks; skeleton must always run |

---

## 21. Privacy stance

A system that hears and remembers everyone nearby needs a clear answer, and judges will ask.

- **Local first.** Processing and storage happen on the user's devices. Cloud transcription is an option, with a local engine available.
- **Transcripts, not recordings.** Raw audio is not kept after processing (fixtures recorded with team consent are the exception).
- **Opt-in names.** No facial recognition of strangers. People are named only if enrolled with consent.
- **Auto-expiry.** History deletes after a set period (default 7 days *(tune)*), user-adjustable.
- **The user controls it.** Pause, delete, and export from the phone.

---

## 22. Decisions log

| Decision | Why |
|---|---|
| **Focus on deaf and hard-of-hearing users** | The audio–vision fusion (our novelty) serves them most; depth beats breadth. Blind mode is a stretch extension demo. |
| **World model is the product; AR is a frontend** | The world model is the novel part; AR is one output adapter. |
| **Plain overlay early, polish gated** | Needed for debugging attribution; polish only after the core works. |
| **ReSpeaker on the desk; world frame = array frame** | Stable reference; wearer stays near it. Backpack mast is the fallback if yaw handling gets fiddly. |
| **iPhone chest-mounted, portrait, upside down, wired USB-C** | Port position; wired avoids campus Wi-Fi problems. |
| **Personal Hotspot over USB, `iproxy` fallback** | Simplest wired networking on Windows; no cellular data used for local traffic. |
| **Laptop renders the monitor view (ar_here, R3F)** | Wired link makes full-quality video available; the laptop drives HDMI. |
| **No ARKit** | Not needed (heading from CoreMotion quaternion, faces/objects from laptop vision); AI-generated ARKit code has been unreliable. |
| **Heading from quaternion, not Euler yaw** | Upright phone causes gimbal lock with Euler angles. |
| **ESP32 owns haptic timing** | BLE jitter would smear rhythms. |
| **ElevenLabs Scribe Realtime primary, local whisper fallback** | Low latency + sponsor; never a single point of failure. |
| **Vultr for memory/search + replay page** | Latency-tolerant workload; shareable with judges. |
| **"Loud bang" instead of "gunshot"** | False positives are likely; the label must not overclaim. |
| **Tone describes sound, not emotion** | More accurate; matches captioning convention. |

---

## 23. Open questions

- Team members and owners for each workstream (section 15).
- ReSpeaker DoA details: readout method, rate, confidence, non-speech behavior *(spike)*.
- Is Personal Hotspot available on the phone's carrier plan?
- Which iPhone model (affects USB-C vs Lightning, ultrawide availability)?
- Final HFOV in the harness (measure).
- Hackathon date, room conditions (noise, monitor, power outlets), sponsor prize criteria.
- Does Scribe Realtime's segmentation give us usable turn boundaries, or do we segment ourselves?
- Do we demo blind-mode object finding at all, or keep it as a pitch slide?