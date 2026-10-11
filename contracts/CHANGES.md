# Contract changes and known drift

Every change to the wire contract since v3 shipped, plus places in the repo that don't match the contract yet.
This is the input to the **accordance pass**, a whole-system check that every component, doc and plan matches
`contracts/`. Run that pass during the next big overhaul. Until then, `contracts/` (the schemas, mirrors and
examples) wins over any doc or plan that disagrees.

How to change the contract: contracts/CLAUDE.md. Additive changes (new enum values, new optional fields) keep
`v: 3`. Breaking changes bump `v`.

## Changes

### 2026-10-10: `perception_enrichment` (new type, additive, still v3)
- **Fields:**
  - `ref` (`utt_id` / `event_id`);
  - `attention` (`ambient / notice / important / urgent`), `attention_confidence`, `attention_model` (a Hugging Face repo id, or `rules`);
  - optional `directed_at_user` (0-1), `context` and `context_model` (Gemini).
- **Sent by the laptop** after the event it refers to. It never decides whether an event exists or where it is.
- **Default routing:** phone and memory added; dashboard and tool already get everything.
- **Updated:** `build_schemas.py`, the schemas, the Python/Swift/TS mirrors, a new example, `relay.py` `DEFAULT_SUBSCRIBE`, README §12.7 and §12.10.
- **Decision record:** `HERE_36hr_vibecode_plan/Shared/ATTENTION.md`. It was first planned for TypeSafe Jev, and switched to a Hugging Face zero-shot model the same day; the shape is unchanged.

### 2026-10-10: more sound labels; `sound_event.source_id` and `model` (additive, still v3)
- `SoundLabel` gained `car_horn`, `vehicle`, `siren`, `door` and `unknown`.
  - `other` = a recognised sound that isn't one of ours.
  - `unknown` = HERE heard something real but couldn't classify it. It keeps its direction: uncertainty lowers specificity and never hides the sound.
- `AlertKind` gained `car_horn` (priority 2), `siren` (3, emergency) and `door` (1).
- `sound_event` has two new optional fields, omitted when absent:
  - `source_id`: the `sources` track the sound came from;
  - `model`: which classifier labelled it, e.g. `panns_cnn14` or a Hugging Face model id.
- Updated in this commit: `build_schemas.py`, the schemas, the Python/Swift/TS mirrors, the `sound_event` example, README §12.3, §12.7 and §15, the laptop's label display and AudioSet map (`laptop/sounds/labels.py`, `config.yaml`), and the speech pipeline's non-speech list.
- **Not done yet:**
  - The laptop never emits `unknown`: the classifier only reports labels above their threshold.
  - The new labels aren't in `sounds.publish` yet. Add them after checking on real sounds.
  - iOS needs no code change: alert toggles come from `AlertKind.allCases`, and the emoji and text arrive in the message. Its `ContractTests` should be re-run on the Mac.

## Proposed (not shipped; Kori approves)

### `sources[].spread_deg` (optional, additive) — proposed by K1, 2026-10-10
- **Field:** `spread_deg: number` (>= 0), optional on each `sources` entry: the circular spread of the track's
  recent bearings (laptop/events/tracker.py already computes it).
- **Consumers:** the phone (ring width / how sure the direction is before buzzing), the dashboard (LIVE orb blur).
- **Why the current types can't do it:** `confidence` is speech energy, not direction certainty; in crowd noise a
  loud, confident track can still wander. Today only `utterance.bearing_spread_deg` carries spread, and only once
  words exist. Low priority: nothing breaks without it.

### Early directional cue before the sound is classified — proposed by K1, 2026-10-10
- **Problem:** the doorbell alert reaches a client ~0.9-1.1 s after the sound starts on the real array
  (`tools/doa_latency.py`, WORKLOG_K1 "Real-array latency"), against README's <= 250 ms sound -> phone. Direction
  alone is fast (`sources` p50 31 ms laptop -> client); classification is what's slow.
- **Shape (option A, the brief's idea):** new type `sound_cue` (additive, v3): `cue_id`, `t` (onset time, laptop
  clock), `bearing_deg` (world), `source_id`, `energy` (0-1), `rise_db`. Sent from K1's onset candidates
  (`laptop/events/onsets.py`). A later `sound_event` / `alert` may carry `ref.cue_id` to refine it; no
  `sound_event` within ~1.5 s = the cue was nothing (the phone lets it fade).
- **Measured (replays through `app.onsets`, laptop clock, `sounds` off):**
  - cue latency: candidates are published 12-52 ms after the sound's aligned start (doorbell in `array_tts`,
    `k1_room_live2`, `raw_speaker_180`); + relay ~31 ms = **~50-90 ms to a local client**, inside 250 ms.
  - but a level-only cue can't tell a doorbell from a laugh or a chair. On 3.7 min of real chatter
    (`fixtures/k1_positions`) and the three doorbell scenes:

    | cue rule | real chatter cues/min | doorbell cued (of 3 takes) |
    |---|---|---|
    | every onset, stream not in ambience (>= 4 onsets / 30 s) | 2.2 | - |
    | rise >= 20 dB, not in ambience (any onset counts) | **0.3** | 1 (35 ms) |
    | rise >= 20 dB, ambience counts non-speech onsets only | 4.4 | 2 (26-35 ms) |
    | rise >= 25 dB, no ambience gate | 4.6 | - |

    The doorbell's first onset is flagged as speech by the firmware (a chime reads as speech), and it often follows
    conversation, so a quiet rule misses it; a rule that catches it fires several times a minute on chatter.
- **Option B (recommended):** no new type. K1 proposal (b) on `kori/k1-proposals` (A2's code): match the first
  0.5 s of the enrolled doorbell 0.4 s after an onset -> the normal `sound_event` + `alert` at **~0.39-0.55 s**
  (was ~0.9-1.1 s), 0 false alerts on the same 3.7 min of chatter. Specific to the demo's sound, not
  label-less; misses the 250 ms target by ~150-300 ms, but every cue it sends is right.
- **How the phone / belt should treat option A if Kori picks it:** visual only, low emphasis (an edge glow at
  the bearing that fades within ~1.5 s unless a `sound_event` / `alert` with `ref.cue_id` lands); **never a belt
  buzz** at 0.3-4.4 false cues/min; the belt waits for the alert (priority, dedupe unchanged). Not on the
  phone's default subscription until measured on the hall floor. Dashboard: a faint pulse.
- **K1's recommendation:** B now (ships without a contract change); A only as a dashboard/visual experiment.
- **Reproduce:** `python tools/doa_cues.py fixtures/<k1_positions | array_tts | k1_room_live2 | raw_speaker_180>`.

## Known drift (fix in the accordance pass)

- README §14 (iPhone app) and §20 (five-day plan) still describe the original team and schedule. The iPhone app is
  now a separate team (`IOS_INTEGRATION.md`), and the live schedule is `HERE_36hr_vibecode_plan/`.
- The laptop doesn't emit `unknown` or `perception_enrichment` yet (A2's tasks), and the new labels aren't in `sounds.publish`.
