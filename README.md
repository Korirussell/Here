# HERE
### Hearing Environmental Reality Engine

> HERE shows deaf and hard-of-hearing people **who** is talking, **what** they said, **how** they said it, and
> **where** it came from. Captions sit on the person who spoke, important sounds show up with their direction,
> and a haptic belt turns you toward the ones you'd otherwise miss.

---

## Inspiration

Caption apps turn speech into text. They don't tell you who said it, or where that person is standing. Sound alerts
say "doorbell" and leave you to figure out which way to turn.

Deaf and hard-of-hearing people miss more than words. They miss where a sound came from, who in a group is talking,
how it was said, and that anything happened at all while they were looking the other way. A car behind you is a
direction problem before it's a vocabulary problem.

Two earlier projects showed the pieces were possible: **Ranger** (a ReSpeaker sound radar on a headset) and
**Beyond Hearing** (direction cues and haptics on AR glasses, built with deaf participants). Their research gave us
our design rules: people picture sound as a simple circle, cues must never block the view, and users want a quiet
mode. But neither put the caption on the person who spoke. That's what we set out to build.

## What it does

HERE fuses a microphone array, a camera, and a haptic belt into one spatial picture of the room.

- 🗣️ **Captions on the right person.** Each line floats above whoever said it (Person 1, 2, 3) with a tone chip:
  whispering, laughing, loud.
- 🔔 **Sounds with a direction.** Doorbell, your name, a car, footsteps, an alarm. A card names the sound and a ring
  points to it.
- 🧭 **A belt that turns you.** Someone speaks behind you, the belt buzzes on that side of your waist, you turn, and
  their caption slides into view. Works with the phone locked in your pocket.
- 🧠 **Attention, not noise.** An AI layer decides whether a sound is ambient, notice, important, or urgent. A fan
  doesn't buzz you. A car behind you always does.
- 🗺️ **Memory.** *HERE Now* shows the room live. *Hear & Now* replays your days on a 3D map of campus, answers
  questions like "when did the doorbell ring?" with evidence, and surfaces patterns across days.
- 🔒 **Private by design.** No audio or video ever leaves a device. Only text, labels, and bearings travel.

## How it works

```
ReSpeaker XVF3800 ──USB──▶ Laptop (RTX GPU)                       Vultr relay (wss, Caddy)
  DoA + 2 beams            speech · sounds · attention ──JSON──▶  fans out text/labels/bearings
                                                                      │
                     ┌────────────────────────────────────────────────┼──────────────────────┐
                     ▼                                                ▼                      ▼
              iPhone (the glasses)                             Memory service          Dashboard
              camera + faces + heading                         Neon · Gemini           HERE Now / Hear & Now
              attribution, AR captions                         Databricks insights     (3D campus map)
                     │ BLE
                     ▼
              ESP32 haptic belt
```

| Part | Role | Code |
|---|---|---|
| ReSpeaker XVF3800 mic array | The ears: direction of arrival + two focused beams | `laptop/audio/` |
| Laptop with RTX GPU | The hearing module: speech, sound, and attention models | `laptop/` |
| iPhone on a chest mount | Stands in for AR glasses: its camera is the glasses' camera, its screen is the display | `ios/` |
| ESP32 haptic belt | The output: tells your body where to turn | `belt/` |
| Relay on Vultr | Connects everything, on any network | `relay/` |

**The phone decides where to buzz, not the belt.** The array reports where the sound is in the world, the phone
knows which way you're facing, and the belt gets the direction relative to your body. Turn around and the buzz stays
pinned to the sound.

### The belt (built from parts)

- **Controller:** 30-pin ESP32 in a screw-terminal breakout. It only switches; it never powers a motor.
- **Driver:** ULN2803 Darlington array, COM tied to +5 V so its built-in flyback diodes absorb the motors' kick.
- **Motors:** 3 V coin motors, each bonded to a 2.5 cm plate cut from a plastic card, so the buzz spreads over skin
  you can't miss through a shirt.
- **Power:** Anker power bank through a cut USB cable into Wago lever hubs, with a 1000 µF cap so the ESP32 doesn't
  brown out on spin-up. Every motor gets its own home-run wire pair with strain relief.
- **Motor safety:** a firmware duty cap on the PWM so 5 V never cooks a 3 V motor.
- **Bluetooth LE:** one write-without-response characteristic. A 3-byte command fires one motor; a 4-byte bitmask
  fires several at once; `0xFF` stops everything.
- **Fail-safe:** the firmware owns the off-timers, so BLE jitter can't smear a pattern, and a disconnect stops every
  motor.

Firmware: `belt/firmware/here_belt_6motor/`. Wiring and a browser BLE test page: `belt/README.md`, `belt/controller/`.

### The AI stack

HERE is a stack of models, each doing the one job it's good at, on a strict latency budget.

- 🎯 **Attention with JEV-9B.** Each event becomes one short state sentence (*"Sound: doorbell (confidence 0.71). The
  wearer was in a conversation. No repeats in the last minute. Focus mode off."*). JEV-9B answers a typed question
  with fixed options (ambient / notice / important / urgent, plus "was this said to the wearer?") with calibrated
  probabilities. 4-bit quantized beside the speech model on one laptop GPU: **208 ms median per decision**.
- 🛡️ **A safety rule around the model.** The model can raise an alert, never lower one. Under 0.5 confidence or over
  budget, the next tier decides: DeBERTa zero-shot, then deterministic rules. A vehicle behind you is urgent no
  matter what. Every event records which tier made the call. (`docs/ATTENTION.md`)
- **Sound classification:** audio-event models label what the array hears; the phone's on-device sound analysis is a
  second sensor that has to agree.
- **Speech:** streaming transcription, a tone estimate per line, and filters that stop the model inventing text on
  noise (a segment is trusted only if the array heard a talker long enough).
- **Fusion:** the phone matches each line's bearing to a tracked face, so the caption lands on the right person.
- **Context:** Gemini writes a one-line context for a caption and phrases Ask HERE answers from evidence rows.

## Technologies and endpoints

Every technology in this writeup has a home in the repo. Each one is **one config line from its fallback**, and the
system runs with all of them switched off.

| Technology | What it does in HERE | Code | Endpoint / interface | Fallback |
|---|---|---|---|---|
| 🎙️ **ElevenLabs** Scribe v2 Realtime | Streaming speech-to-text (WER 0.04, first words ~1.2 s) | `laptop/speech/transcriber.py` | `speech.engine: scribe_realtime` → `utterance` messages | Local Whisper, mid-sentence failover |
| ☁️ **Vultr** | WebSocket relay behind Caddy TLS; one-command deploy; status page; session export | `relay/`, `tools/deploy_vultr.py`, `extras/cloud/s3.py` | `wss://<host>/ws`, `GET /health`, `GET /api/status.html`, `POST /api/export` | Local relay `python -m relay` |
| 🤗 **Hugging Face** | JEV-9B attention (4-bit: transformers + peft + bitsandbytes), DeBERTa zero-shot, Whisper large-v3-turbo, CED-mini / AST / CLAP / PANNs bakeoff | `laptop/enrich/attention.py`, `laptop/sounds/models.py`, `tools/sound_bench.py` | `perception_enrichment` and `sound_event.model`; `POST /api/replay` (`use_model`) | DeBERTa → rules; PANNs CNN14 |
| 🧱 **Databricks** | Serverless Spark job: event log in a Unity Catalog volume → Delta tables → ranked insights (~4,500 events → 13 insights in 83 s) | `extras/cloud/databricks/`, `extras/cloud/databricks_sync.py` | `GET /api/insights`, `POST /api/insights/refresh` | Plain-Python insights (`insights_local.py`) |
| 🐘 **Neon** | Postgres memory: sessions, events, full-text utterances, insights; SQL-over-HTTPS shim for networks that block 5432 | `extras/memory/store.py`, `extras/memory/neon_http.py` | `GET /api/day`, `POST /api/ask`, `GET /api/segments` | Local SQLite |
| ✨ **Gemini** | One-line caption context; phrases Ask HERE answers from evidence | `laptop/enrich/gemini.py`, `extras/memory/ask.py` | `perception_enrichment.context`, `POST /api/ask` | Template answers; context omitted |
| 🍎 **Apple SoundAnalysis** | Phone-side second sensor that confirms the laptop's sound labels | `ios/HERE/Audio/OnDeviceSoundSensor.swift` | `OnDeviceSoundSensor.agrees(with:)` | Laptop label alone |

`GET /api/integrations` on the memory service reports which of these are configured right now.

### Memory API (`python -m extras.memory`, `:8790` locally, `/api` on Vultr)

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{ok, db: "neon" \| "local", events, sessions}` |
| GET | `/api/day?date=YYYY-MM-DD` | A day's segments and events, with person and place labels |
| GET | `/api/segments` | Every known segment (seeded + live) |
| POST | `/api/ask {q}` | Answer + evidence rows (full-text search, time and bearing filters, Gemini phrasing) |
| GET | `/api/insights` | Databricks insights, or the precomputed ones |
| POST | `/api/insights/refresh` | Re-run insights in the background (Databricks when configured) |
| POST | `/api/replay {segment_id, policy}` | What would surface under other attention settings |
| POST | `/api/export {session}` | Session bundle (text + metadata, never audio) → Vultr Object Storage |
| GET | `/api/status`, `/api/status.html` | Relay health and message rates, memory, insights |
| GET | `/api/integrations` | Which sponsor technologies are live vs on their fallback |

Behind a proxy, every route except `/api/health` needs the relay token (`?token=` or `Authorization: Bearer`).

### Relay (`python -m relay`)

`wss://<host>/ws?session=&role=laptop|phone|dashboard|memory|tool&token=` and `GET /health`. It forwards contract
messages by type; the message contract is `contracts/` (JSON Schema + Python / Swift / TypeScript mirrors), described
in [`docs/SPEC.md` §12](docs/SPEC.md#12-the-contract).

## Run it

Everything runs without hardware, from mocks and recorded fixtures.

```bash
py -3.12 -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -r laptop/requirements.txt -r extras/memory/requirements.txt
```

| Task | Windows | make |
|---|---|---|
| Tests (contracts, relay, laptop, end to end, extras) | `.\tasks.ps1 test` | `make test` |
| Local relay on :8080 | `.\tasks.ps1 relay` | `make relay` |
| Scripted events to the phone, no audio | `.\tasks.ps1 fake-events` | `make fake-events` |
| Synthetic scene through the real models | `.\tasks.ps1 laptop-mock` | `make laptop-mock` |
| Real ReSpeaker | `.\tasks.ps1 laptop-real` | `make laptop-real` |
| Watch a session | `.\tasks.ps1 tap` | `make tap` |
| Whole demo stack (relay + source + dashboard + memory) | `.\tasks.ps1 demo-up` | `make demo-up` |
| GPU / model / array check | `.\tasks.ps1 check` | `make check` |

The iPhone app: `cd ios && xcodegen generate && open HERE.xcodeproj`. Deploying the relay to Vultr:
`relay/DEPLOY.md`. Keys go in the `.env` files next to each `.env.example`; never commit them.

## Repo layout

| Folder | What's in it |
|---|---|
| `contracts/` | Message schemas + Python / Swift / TS types + one example per message |
| `relay/` | WebSocket relay, Caddyfile, systemd unit, deploy guide |
| `laptop/` | Hearing module: ReSpeaker input, speech, sounds, attention, alerts |
| `ios/` | The glasses: camera, faces, heading, attribution, AR captions, belt |
| `belt/` | ESP32 firmware (6-motor build + original 8-motor) and a Web Bluetooth test page |
| `extras/memory/` | Memory service and API (Neon / SQLite, Ask HERE, replay) |
| `extras/cloud/` | Databricks insights job, Vultr Object Storage export |
| `extras/dashboard/` | Dashboard: live view (HERE Now), memory and replay (Hear & Now) |
| `tools/` | Fake events, relay tap, replay, benchmarks, deploy, demo harness |
| `fixtures/` | Recorded and synthetic sessions for running without hardware |
| `tests/` | End-to-end, consistency and resilience tests |
| `docs/` | `SPEC.md` (frames, timing, contract, belt protocol), `ATTENTION.md`, `INTEGRATION.md` |

## Challenges we ran into

- **Two motors didn't survive.** We designed for eight; coin motor leads are hair-thin. We ended with six channels,
  remapped the firmware, added the bitmask command so two neighbours can fire together to point between them, and
  re-tuned the direction mapping.
- **Three models, one GPU.** JEV-9B is 18 GB at full precision; the laptop has 16 GB shared with speech and sound.
  4-bit got it to ~6 GB. Cold it missed our latency gate (496 ms); warm it passed (208 ms).
- **Speech models hallucinate on noise.** We only trust a segment if the array heard a talker long enough, and drop
  segments the model itself doubts.
- **Campus Wi-Fi** blocks Postgres and isolates devices, which is exactly why the relay and HTTPS database driver exist.
- **Four ideas of "forward."** The array, the compass, the camera's field of view, and the belt all disagreed. Nothing
  worked until everything became a bearing in one shared frame (`docs/SPEC.md` §8).
- **Overloading the user.** The first version buzzed for everything. Most of the attention work is deciding what to
  leave out.

## Accomplishments we're proud of

- The turn-around: someone speaks behind you, the belt buzzes, you turn, and their caption is waiting on them.
- A hand-built belt a stranger can put on and understand in seconds.
- An AI layer with a real safety rule: models can escalate, never suppress.
- A full system that runs without any of its hardware, with one message contract shared across Python, Swift, and
  TypeScript.

## What we learned

- Direction is the missing half of accessibility for sound. A label without a bearing leaves the user searching.
- Haptics should carry direction and nothing else. Short patterns, never continuous buzzing.
- Commit fast, then refine. Show something immediately and let slower models fill in.
- Measure before you claim. Our benchmarks changed our minds more than once.
- Soldering: put the heat shrink on before you make the joint.

## What's next

- Real AR glasses in place of the chest-mounted phone, with the mic array on the wearer
- All eight motors, then a slimmer belt on a custom PCB
- Testing with deaf and hard-of-hearing users, and tuning attention to each person
- A phone-only mode for walking outside, where a car behind you matters most
