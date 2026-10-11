# HERE: project brief (as of 2026-10-09)

Context for a planning chat about the **server side**: how we serve the iOS app quickly and how we use our sponsors.
Everything below describes the repo as it is today. Proposals are marked as open questions.

## 1. What HERE is

HERE (Hearing Environmental Reality Engine) is a hackathon project for deaf and hard-of-hearing people. It shows
**who is talking, what they said, how they said it (tone), and where the sound came from.**

- **AR captions:** float above the person who spoke. An iPhone stands in for AR glasses, mirrored to a monitor.
- **Rings:** drawn around whoever is making sound.
- **Top-right sound panel:** "Doorbell" or "Name called", with a direction ring.
- **Haptic belt:** 8 motors that buzz in the direction to turn.

The three demo moments:
1. Captions appear over the right person in a group.
2. The turn-around: someone speaks behind the wearer, the belt buzzes, the wearer turns, and the caption slides in.
3. A doorbell or the wearer's name from behind: the panel shows it, and the belt pulses that way.

**Format:** hackathon table expo. 2 min setup, 6 min judging, hard stop. Prize targets: grand prize, best hardware, and sponsor prizes. Pre-built work is allowed; we disclose it.

## 2. The rig and the team

| Device | Role |
|---|---|
| iPhone 17 (chest mount, landscape, ultrawide) | "The glasses": camera, face tracking, heading, attribution (sound to face), AR overlay, belt over BLE |
| Windows laptop (RTX 5000 Ada) | "The hearing module": mic array, transcription, tone, sound classification, alerts |
| ReSpeaker XVF3800 (4-mic USB array on the desk) | Sound direction and clean audio. Its front defines the world frame. |
| Vultr server (Atlanta) | WebSocket relay between laptop and phone; later memory/search and a dashboard |
| ESP32 haptic belt | 8 coin motors, BLE peripheral; the phone is the central |
| Mac + monitor | AirPlay mirror of the phone for judges |

| Person | Owns |
|---|---|
| Kori (integrator) | iPhone app, relay on Vultr, message contract, integration, pitch |
| Friend (Adil) | Audio / laptop: directions, ODAS, transcription, tone, name called, doorbell |
| Teammate 3 | Belt hardware and firmware |
| Teammate 4 | Testing, demo script, recordings, outreach |

## 3. Architecture

```
ReSpeaker --USB--> Windows laptop (Python 3.12)
                     audio: directions (built-in DoA / SRP-PHAT / optional ODAS)
                     speech: VAD + streaming faster-whisper (ElevenLabs Scribe stub), tone, name called
                     sounds: PANNs classifier on GPU (doorbell, ...), alerts
                     client: builds contract messages and sends them to the relay
                          |  wss, outbound
                          v
                   Vultr relay (Python + websockets, Caddy for TLS)   <- the "server side" today
                          |  wss, outbound
                          v
                   iPhone 17 (Swift/SwiftUI, iOS 26, no ARKit)
                     camera + Vision faces -> attribution -> Liquid Glass overlay
                     CoreMotion heading; CoreBluetooth -> ESP32 belt
                          |  AirPlay
                          v
                   Mac -> monitor
```

**Hard rules that shape any server design:**
- **No audio or video ever goes over the network.** Only small JSON leaves the laptop: text, bearings, labels. This is a privacy promise and a speed choice. Audio is processed on the laptop. The one planned exception is cloud transcription (ElevenLabs Scribe), which would send audio from the laptop to ElevenLabs, not through our server.
- **Every device connects outbound** to the relay. This avoids campus Wi-Fi device-to-device problems.
- **iOS requires TLS** (`wss://`) for internet hosts, which is why Caddy is there.
- **Latency budget:** at most 250 ms from sound to the phone reacting. Laptop to relay to phone should be about 50-100 ms total. That means a persistent socket, no batching, `TCP_NODELAY`, and the Atlanta region.
- **The laptop sends world bearings.** The phone converts them to body bearings using its own heading, so the laptop never needs the phone's heading.
- **Contract first:** message shapes are versioned (`v: 3`) JSON Schemas in `contracts/`, mirrored as Python, Swift and TypeScript types. Kori approves every change.

## 4. The relay (the server side today)

Status: **written and tested locally, not deployed yet.** It has 7 tests plus an end-to-end smoke test, and the deploy guide (`relay/DEPLOY.md`, about 15 minutes) is ready.

- **What it is:** a single file (`relay/relay.py`), stdlib plus `websockets`, run as a systemd service on 127.0.0.1:8080. Caddy sits in front for automatic HTTPS, either on a domain or a free `<ip>.sslip.io` hostname. Firewall: only SSH, 80 and 443. The cheapest Vultr plan (1 vCPU / 1 GB) is enough.
- **URL:** `wss://<host>/ws?session=<s>&role=<laptop|phone|dashboard|memory|tool>&token=<shared secret>`. A wrong token gets HTTP 401 before the socket opens.
- **Routing:** each message goes to every other client in the same session that subscribed to its `type`. Clients subscribe in their `hello` message; each role has default subscriptions.
  - phone: `sources`, `speech_start`, `utterance`, `sound_event`, `alert`, `config`, `clock_pong`, `heartbeat`
  - laptop: `config`, `clock_ping`
  - dashboard and tool: everything
  - memory: `utterance`, `alert`, `sound_event`, `attribution`, `search_request`
- **State:** it keeps the latest `config` per session and replays it to new clients.
- **No queues:**
  - for position streams (`sources`, `scene`, `heartbeat`), it keeps only the newest message per sender for a slow client;
  - events go in a bounded outbox (256) that drops the oldest.
- **`GET /health`:** reports sessions and connected clients. Every message is logged as JSON lines.
- **Local fallback:** the same code runs on the laptop (`python -m relay`) if the internet dies. The phone just switches its URL.
- **Not a place for logic** by design. It is a dumb, fast router.

## 5. The message contract (v3, JSON text frames)

Every message has an envelope: `{type, v: 3, src, t (laptop clock, unix s), seq?, session?}`.

**Laptop to phone:**
- `sources`: 10-30 Hz while active. Each entry has `{id, bearing_deg, energy, speech, confidence}`.
- `speech_start`: the fast path, sent the moment speech starts at a direction, before any words exist.
- `utterance`: streaming. Fields: `text` (committed), `pending` (provisional), `final`, `bearing_deg`, `bearing_spread_deg`, `source_id`, `overlap`, `tone`, `loudness`, `engine`.
- `sound_event`: label, category, emoji, text, confidence, bearing.
- `alert`: kind (doorbell, name_called, baby_crying, ...), priority 1-3, bearing, `active` (on/off).
- `heartbeat` (every 2 s) and `clock_pong`.

**Phone to everyone:**
- `config`: user name and aliases, focus mode, belt-only mode, feature toggles, alert switches, belt strength.
- `heartbeat` (with thermal state) and `clock_ping`.

**Phone to relay, extras only:**
- `attribution`: which "Person N" said which utterance.
- `scene`: heading plus people and their bearings, 2-5 Hz.

**Extras, already in the contract:** `search_request` and `search_result`, for memory search: "what did Person 2 say about the fair?"

## 6. Status of each part

| Part | Status |
|---|---|
| Contract (`contracts/`) | Done. Schemas, Python/Swift/TS types and 14 examples, all tested. |
| Relay | Done locally. **Next: deploy on Vultr, choose a hostname, measure the round trip from campus Wi-Fi.** The laptop config still points at `ws://localhost:8080/ws`. |
| Laptop / hearing module | **Runs on the real array.** Captions, doorbell and name called have been measured on the array's audio, and the golden regression gate passes. Two simultaneous sources work via the array's two beams. Transcription is local faster-whisper on the GPU. The ElevenLabs Scribe Realtime engine is a stub behind the same interface, with automatic fallback to whisper. |
| Sound direction | Built-in DoA with fixed beams is the demo default. ODAS (running in WSL2) is opt-in: it tracks two sources better but failed 4 of 5 decision gates, so it is a Q&A extra. New this week: a 3D browser sound-field view (`--viz`, local only) and the array's LED ring tracking one or two sources. |
| iPhone app | **Builds clean; 43/43 tests pass** (merged into main 2026-10-10). All three core moments were verified in the Simulator against scripted fake events. Includes Liquid Glass, Settings, a Demo Setup walkthrough, VoiceOver and Dynamic Type. **Not yet verified on the device:** face-box alignment, heading sign, field of view, BLE in a crowded room, 20-minute thermals with AirPlay. |
| Belt firmware | Written (6-byte BLE packet, patterns timed on the ESP32). Hardware build is in progress by teammate 3. |
| Memory search (extra) | Not started. The plan is a relay-side service (role `memory`) that stores final utterances and alerts in SQLite, adds embeddings, answers `search_request`, and auto-deletes after 7 days. |
| Dashboard (extra) | Not started. The plan is a plain web page on the relay (role `dashboard`) with a top-down radar, alert list and caption feed. |

## 7. Sponsors (what the README says today)

- **Vultr:** hosts the relay that connects the whole system, and memory search if we build it. Current plan: the cheapest Cloud Compute instance in Atlanta.
- **ElevenLabs:** Scribe Realtime is meant to power every caption once we get access at the event. Whisper stays as the fallback. It is a one-file swap behind the transcription interface (the stub exists).
- **Hugging Face:** the open sound classifier, plus a local zero-shot model for attention (`perception_enrichment`). Jev (TypeSafe) was dropped: TypeSafe isn't a sponsor.
- The full sponsor list and prize criteria are still an open question; confirm at the event.

## 8. Open questions for the server-side planning

These are the things the planning chat could help decide. None of them is decided in the repo.

1. **Deploy:** domain versus sslip.io, and who deploys when. The guide exists; it takes about 15 minutes.
2. **"Serve the iOS app swiftly":**
   - keep the relay as a dumb router, or add server logic;
   - how to measure and protect the ≤ 250 ms budget (`clock_ping`/`clock_pong` and `stage_ms` exist for this);
   - reconnect behaviour;
   - campus Wi-Fi versus a hotspot;
   - whether a local fallback relay is needed.
3. **What runs on Vultr beyond the relay**, to earn the sponsor prize:
   - memory search: SQLite plus embeddings; where the embeddings come from (local model or an API);
   - the dashboard page;
   - session logs and replay;
   - a GPU instance for heavier models. Today the GPU work runs on the laptop, and audio may not leave it, except to ElevenLabs.
4. **ElevenLabs beyond Scribe:** for example, text-to-speech for something like a "type to speak" mode (currently out of scope), or anything else that fits their prize criteria without breaking the "no audio on our network" rule.
5. ~~Jev / TypeSafe~~: dropped; a Hugging Face model does the attention judgments (`HERE_36hr_vibecode_plan/Shared/ATTENTION.md`).
6. **Contract impact:** any new server feature needs new message types. Kori approves them, they are versioned, and they need Python, Swift and TS mirrors.
7. **Security and privacy story for judges:**
   - a shared token per session;
   - no audio or video stored or sent;
   - "Person 1/2/3" labels, no face recognition;
   - memory auto-deletes after 7 days.
