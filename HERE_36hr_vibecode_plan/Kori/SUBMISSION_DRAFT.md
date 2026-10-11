# HERE: submission write-up (first draft, K3, 2026-10-10)

> Draft for Kori. Every claim below is checked against `main` on 2026-10-10 (8d78be3). Where something isn't live
> yet it says so; update the "Status" column the morning of judging, and delete this note.
> Architecture picture: `extras/dashboard/architecture.svg` / `.png` (K2). The live version is the dashboard's "How it works" view (key H).

## The problem
Deaf and hard-of-hearing people miss more than words. They miss **where** a sound came from, **who** in a group is
speaking, **how** something was said, and that something happened at all while they were looking away: a doorbell,
their own name. Caption apps turn speech into text but don't say who said it or where they are. Sound alerts say
"doorbell" but not which way to turn.

## What HERE does
HERE fuses a microphone array with a camera into one spatial picture of the room. Every sound and every visible person
becomes something with a direction.

- **Captions on the person who said them:** on the iPhone, which stands in for AR glasses. Each caption shows who spoke and the tone.
- **Rings** around whoever or whatever is making sound.
- **A sound panel** that names the sound (doorbell, name called) and shows which way it is.
- **A haptic belt** that buzzes in the direction to turn: direction only, never continuous.

HERE commits fast, then refines. The direction and "someone is talking" reach the phone before any words. Captions
stream in and settle. Slower models (attention, context) add to an event afterwards; they never decide whether it exists.

## The demo (about 3 minutes)
1. **Captions over the right person.** Two people talk; each line appears above whoever said it, with a ring and a tone chip.
2. **The turn-around.** Someone speaks behind the wearer. The belt buzzes toward them, the wearer turns, and the caption slides into view.
3. **Doorbell / name called from behind.** The panel shows it with a ring pointing back, and the belt pulses that way.
4. **A judge tries the belt.** "Close your eyes. Turn when you feel the buzz."
5. **HERE World** (dashboard), if time allows: the live room, the memory of the day ("Ask HERE"), and the Replay Lab.

Controlled conditions: scripted lines, marked positions, our own doorbell sound, calibration before judges arrive. If
something breaks we switch to the labelled replay mode and say so. We never present a faked output as live.

## How it works
- **Laptop (the hearing module):**
  - The ReSpeaker XVF3800 array gives directions, tracked as stable sources (K1's tracker).
  - The speech fast path: voice onset gives `speech_start` with a bearing; streaming transcription gives captions; name called is detected.
  - Sound events with honest `unknown`s.
  - Optional enrichment afterwards: attention, plus Gemini context.
  - **No audio or video leaves the laptop.** Only text and world bearings.
- **Relay:** a WebSocket relay; local on the laptop for the demo, Vultr in the cloud. One versioned contract (v3) for every message.
- **iPhone:** camera, faces, heading. The phone turns world bearings into "behind you", attributes captions to faces, and drives the belt over BLE.
- **Belt:** ESP32 with 8 motors; the patterns are timed on the device.
- **Memory and dashboard (extras, never on the live reaction path):** a memory service stores the canonical events and answers
  questions with evidence. The dashboard shows the live room, the day and a replay lab.

## Sponsor tech: what is live and what is the fallback
| Sponsor | Role in HERE | Status on main (2026-10-10) | Fallback (tested) |
|---|---|---|---|
| **ElevenLabs** | Scribe v2 Realtime for captions, behind one transcriber interface | Code done, failover tested against a local fake server. **Not yet run on the live API** (no key on the demo laptop yet) | faster-whisper on the GPU, mid-utterance, no lost words (drills: `--unplug`, network drop) |
| **Hugging Face** | Sound classifier (`sound_event.model`) and the attention model (`perception_enrichment.attention_model`) | Sound model is **PANNs (`panns_cnn14`) plus an enrolled doorbell**; the HF bakeoff (AST, CED, CLAP) is running on the demo GPU. Attention: **`rules` on the demo laptop today** (transformers isn't installed in its venv; `demo-check` now warns) | PANNs when an HF model can't load; `rules` when attention is slow, unsure or down (drills) |
| **Gemini** | One-line semantic context on meaningful events | Code done; **off without `GEMINI_API_KEY`** | Context is left out; events unchanged (drill) |
| **Vultr** | The cloud relay; memory API and status behind the same Caddy; the laptop mirrors to it | Deploy prepared; **not deployed yet** (A3) | The local relay on the laptop; the phone switches URL (`demo-up` prints it) |
| **Neon** | Persistent memory: sessions, events, transcripts, insights | Memory service works on Neon (A3, HTTPS transport when 5432 is blocked) | Local SQLite; the dashboard falls back to the seeded day (drill) |
| **Databricks** | Historical insights over the event export | 12 insights from a Free Edition job (`source: "databricks"`) | Precomputed insights |

## Numbers
- **Relay latency, local:** p50 1.0 ms, p95 1.5 ms (laptop `t` to client receive, 271 messages); the probe gives p95 1.0 ms, 0/200 lost.
  Vultr from campus Wi-Fi and a hotspot: to measure once it's deployed (`.\tasks.ps1 latency --relay wss://<host>/ws`).
- **Captions on the GPU (whisper, `array_tts`):** first words a median of **0.33 s** after a line starts; final **0.84 s** after it ends;
  mean WER 0.04.
- **Golden gate** (simulated loud hall, 7 conditions, run before every merge): **7/7**. WER 0.00 to 0.02, 0 phantom captions,
  0 alerts on pure babble, doorbell and name called hit.
- **Reset between judge groups:** 6.2 s light (the laptop stays warm), 16 s full with the scripted source. Real source: to measure.
- **Failure drills** (`tools/drills.py`): Hugging Face down, a sound model that can't load, Gemini down, slow attention. Events are
  identical to the clean run every time.

## Privacy
No audio or video leaves the laptop. The relay only carries text, bearings and labels. Memory keeps live data 7 days, and
the seeded demo day is labelled as seeded. Proxied memory requests need the relay token.

## Team
- **Kori:** integrator; ReSpeaker and event engine, dashboard, demo laptop, pitch.
- **Adil:** speech (ElevenLabs + whisper), sound AI (Hugging Face, attention, Gemini), cloud (Vultr, Neon, Databricks).
- **iOS team:** the iPhone app (vision, AR, heading, attribution, belt BLE).
- **Belt hardware:** wiring, enclosure, ESP32 firmware, motor calibration.
- **Testing and demo:** recorded sessions, crowd-noise tests, floor plan, the demo script, the submission video.

HERE was started before the event; the git history shows what was built during it.
