# HERE: internal interfaces

> **The wire contract is `contracts/`**: the JSON Schemas, the Python/Swift/TS mirrors and one example per type.
> This file adds the internal interfaces between components (onsets, speech, the memory API, failure behaviour).
> If anything here disagrees with `contracts/`, `contracts/` wins.

---

## 1. Critical path

```text
ReSpeaker -> laptop (laptop directions + speech speech + sound AI sounds) -> relay (cloud deploys) -> iPhone (iOS team) -> belt
                                                            \-> dashboard (dashboard, role "dashboard": sees everything)
                                                            \-> memory service (cloud, role "memory") -> Neon -> Ask HERE / insights / replay
```

**The cloud must never be on the path for any of these:** the first direction cue, `speech_start`, live caption
updates, sound cards appearing, belt direction, or attribution on the phone. If the cloud is unavailable, the core
demo still works through the local relay (`python -m relay` on the laptop).

The memory service is just another relay client: role `memory` already subscribes to `utterance`, `alert`,
`sound_event`, `attribution` and `search_request`. **Mirroring events to the cloud needs no laptop change.**

## 2. Events get richer over time

Never wait for a "complete answer" before publishing something useful. A typical sequence:

```text
t=0 ms     sources: a track appears at 124 deg
t=50 ms    speech_start (utt_id u-0192, source_id 1, bearing 123)
t=180 ms   utterance partial "Hey..." (same utt_id)
t=700 ms   utterance final + tone
t=900 ms   perception_enrichment (ref = utt_id / event_id): attention (Hugging Face), Gemini context
```

Related messages are linked by `source_id`, `utt_id`, `event_id` and `alert_id`, plus the per-sender `seq`. The
laptop client already sets `seq`.

## 3. Coordinates (non-negotiable)

- **World frame:** the ReSpeaker's calibrated front is 0°, clockwise is positive, and bearings are in **(-180, 180]**: 90 = right, 180 = behind, -90 = left. The schema rejects -180.
- **The laptop sends world bearings only.** It never compensates for the phone's heading, the wearer's body, or the camera or display orientation. Those belong to the phone.
- **"Behind the wearer" is a phone-side fact.** Anything that needs it (for example, whether a sound is behind the user) runs on the phone, or on the server using the phone's `scene`, never on the laptop.

## 4. Live messages (shapes in `contracts/examples/`)

| Type | Rate / when | Notes |
|---|---|---|
| `sources` | 10-30 Hz while anything is active | Integer `id`, stable while tracked. Up to two entries in beams mode. The newest message replaces older ones; never queued. |
| `speech_start` | Once per speech onset | Never waits for transcription. Fields: `utt_id`, `bearing_deg`, `source_id`, `confidence`. |
| `utterance` | Streaming until `final: true` | `text` (committed), `pending` (provisional), `final`, `bearing_deg`, `bearing_spread_deg`, `source_id`, `overlap`, `tone`, `loudness`, `engine`, optional `stage_ms`. |
| `sound_event` | On onset, label change and end; repeats merge into `count` | `label` includes `car_horn`, `vehicle`, `siren`, `door`, `unknown`. Optional `source_id` and `model`. |
| `alert` | Doorbell, name called, hazards | Same `alert_id` for updates; `active: false` when it ends. |
| `heartbeat` | Every 2 s | Laptop: `engine`, `doa_source`, and `detail` for honest degraded states. |

**Unknown sounds are valid events.** `unknown` = HERE heard something real but couldn't classify it. It keeps its
bearing; uncertainty lowers how specific the label is and never hides the sound. A later `sound_event` with the same
`event_id` can upgrade the label.

**`perception_enrichment`** (in the contract since 2026-10-10) is sent after an event, keyed by `ref`. It carries:
- `attention` (`ambient / notice / important / urgent`) from a local Hugging Face zero-shot model, or from deterministic rules (`attention_model: "rules"`) when the model is slow or unsure;
- optionally `directed_at_user` (same model) and `context` (Gemini).

Phone, memory and dashboard receive it. It never decides whether an event exists or where it is.
Decision record: `docs/ATTENTION.md`.

## 5. Internal interfaces (not on the wire)

These seams let each component be built and tested alone.

### 5.1 Laptop: onset candidates (laptop → sound AI)
the laptop's event engine publishes non-speech **onset candidates**: `(t, bearing_deg, energy, source_id)` whenever a track
appears or its energy jumps. sound AI subscribes, classifies that window, and emits a `sound_event`: a real label above
threshold, or `unknown` when the sound has enough energy and persistence but no confident label. sound AI never drops a
candidate silently; at most it merges repeats or applies a cooldown.

### 5.2 Laptop: speech (speech → laptop)
`laptop/speech/` owns `speech_start`, `utterance` and the name-called alert. It reads bearings from the laptop's
`DirectionHistory` (`laptop/audio/base.py`) and doesn't change it. Transcription providers implement the
`Transcriber` protocol in `laptop/speech/transcriber.py`.

### 5.3 Memory API (cloud → dashboard)
cloud serves this over HTTP: locally on `:8790`, and on Vultr behind Caddy at `/api`. the dashboard consumes it and
falls back to the seeded JSON in `extras/memory/seed/` when the API is down. The endpoints:

| Endpoint | Returns |
|---|---|
| `GET /api/health` | `{ok, db: "neon" \| "local", events, sessions}` |
| `GET /api/day?date=` | `{segments:[{id, label, start, end, location, environment}], events:[<canonical event + person_label, location_id>]}` |
| `POST /api/ask {q}` | `{answer, evidence:[{event_id \| utt_id, t, text, person_label, bearing_deg}], cached: bool}` |
| `GET /api/insights` | `{insights:[{id, category, title, text, score, evidence_ids, source: "databricks" \| "precomputed"}]}` |
| `POST /api/replay {segment_id, policy:{sensitivity, crowd_filter, focus_mode, hazard_priority, speech_priority}}` | `{surfaced, suppressed, kept_important, by_category:{...}, decisions:[{event_id, surfaced, reason, model_score?}]}` |

Stored events use the **canonical message shapes unchanged**, with ingest metadata added (session, ingest time).
The synthetic day uses the same shapes as live data.

## 6. Rate rules

- Raw streams can be fast: `sources` at 10-30 Hz.
- Expensive AI runs on events only:
  - the Hugging Face classifier on onset candidates or windows;
  - the attention model at 1-5 decisions per second at most (local, bounded queue);
  - Gemini only on meaningful candidates (finals, label upgrades).
- Never send the 30 Hz stream to a cloud model. Never wait on a cloud answer before publishing a direction. No unbounded queues: drop the oldest.

## 7. Latency

**Targets:**
- sound activity to the phone reacting: ≤ 250 ms;
- the laptop-to-phone network part: ≤ 100 ms;
- direction always arrives before meaning.

**Measuring it:**
- Stage timings: `capture`, `doa`, `vad`, `classify`, `transcribe_first_partial`, `transcribe_final`, `serialize`, `socket_send`.
- `utterance.stage_ms` carries the speech stages.
- `clock_ping` / `clock_pong` give the phone the clock offset.
- The dashboard has a demo latency panel; don't instrument every UI element.

## 8. Failure behaviour (each one tested in `tests/`)

| If this fails | Then |
|---|---|
| ElevenLabs | faster-whisper, automatically (heartbeat `engine` changes) |
| Hugging Face model | PANNs, or `unknown` with a bearing. Direction is never suppressed. |
| Gemini | Enrichment is omitted. |
| Attention model | Deterministic rules decide prominence (`attention_model: "rules"`). Events are never lost. |
| Vultr | Local relay on the laptop; the phone switches its URL. |
| Neon | Local SQLite / seeded JSON (`/api/health` says `db: "local"`). |
| Databricks | Precomputed insight JSON, labelled `source: "precomputed"`. |
| Belt | On-screen direction indicator (iOS). |
| No live sound | Scripted fixtures (`fixtures/events/`, `tools/fake_events.py`). |

