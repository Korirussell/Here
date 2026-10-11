# laptop (hearing module)

Part of HERE. Read ../README.md §6, §8-§13, §15 first, and laptop/README.md for setup.

## Job
ReSpeaker XVF3800 in, contract v3 messages out to the relay: directions (`sources`), the speech fast path
(`speech_start`), streaming transcription (`utterance` with bearing, spread, overlap, tone, loudness),
doorbell (`sound_event` + `alert`) and name called (`alert`). No audio or video leaves the laptop.

## Inputs / outputs
In: the ReSpeaker (or a fixture / the laptop mic), and from the relay `config` (user name, aliases, alert
switches) and `clock_ping`. Out: `sources`, `speech_start`, `utterance`, `sound_event`, `alert`,
`heartbeat` (engine, doa_source), `clock_pong`.

## Layout
- `audio/` (Kori, K1): `xvf3800.py` USB control + standalone readout, `builtin_doa.py` the ReSpeaker as a
  DirectionSource + AudioSource, `calibrate.py`, `ring.py`, `localize.py` (SRP-PHAT, mic order, SrpTracker), `odas.py` (ODAS in WSL2, opt-in),
  `mock.py` (fixture player, mic, direction slider), `base.py` (interfaces, DirectionHistory, throttle).
- `speech/`: `pipeline.py` (VAD, partials, speech_start, bearing), `transcriber.py` (whisper, Scribe Realtime,
  the fallback between them, fake), `tone.py`, `names.py`.
- `sounds/`: `classifier.py` (configured model with PANNs as the fallback + pipeline), `models.py` (PANNs / Hugging Face
  backends), `enrolled.py` + `enroll.py` (enrolled sounds such as our doorbell), `events.py` (merging), `labels.py`.
- `enrich/` (A2): `perception_enrichment` after the fact: attention tiers (JEV-9B -> DeBERTa zero-shot -> rules) and Gemini
  context. Needs `transformers` for the model tiers; without it everything is `rules` (`check_gpu --demo` says which).
- `events/` (K1): `tracker.py` `SourceTracker`: raw direction readings -> the wire `sources` (stable ids, snap
  or smooth, hold then expiry, `overlap`, per-track `spread`). `app.tracker` is live; speech and sounds still read
  the raw readings from `DirectionHistory`. `onsets.py` `OnsetDetector`: onset candidates per audio stream.
- `alerts.py` (dedupe / expiry), `client/relay.py` + `client/fanout.py` (local relay + mirrors), `app.py` (wiring + supervisor + recorder), `settings.py`.
- `dashboard.py` (`--dashboard`, terminal), `viz.py` + `viz.html` (`--viz`: 3D sound field in the browser on
  127.0.0.1:8770, heat map + ODAS + LEDs; local only, not the relay dashboard of extras/).

## Run
`python -m laptop --real | --mock | --replay fixtures/<name>` (`--help` for options). Tests:
`python -m pytest laptop`.

## Onset candidates (K1 -> A2, Shared/INTEGRATION_CONTRACT.md §5.1)
`app.onsets` (`laptop/events/onsets.py`) watches every audio stream's level against its own noise floor and
publishes an `OnsetCandidate(t, bearing_deg, energy, source_id, level_db, rise_db, speech)` when a sound starts
(debounced, one per ding-dong, steady fan noise never fires; tunables `audio.onsets`). Subscribe from the sounds
factory in `__main__.py`:

```python
app.onsets.subscribe(lambda c: pipeline.on_onset(c))   # called on the event loop; don't block
```

`t` is the laptop-clock time the level first crossed (classify a window around it), `source_id` = the stream /
`sources` id (beams: 0 front, 1 rear), `bearing_deg` = that track's bearing (refine with `audio.locate()`),
`speech` = the array flagged a talker then (the speech pipeline owns those; skip or merge). Never drop a candidate
silently: emit a label, `unknown`, or merge it into an open event. Fixture replays produce the same candidates.

## Rules
- Contract shapes come from `contracts/python/here_contract.py`; never build message dicts by hand.
- All tunables in `config.yaml` (subpackage `config.py` files only expose a section as UPPER_CASE names).
- `import torch` before faster_whisper (Windows DLLs). Pass numpy audio to whisper, never paths.
- Timestamps are observation times on the laptop clock; bearings world frame (-180, 180].
- Keep `python laptop/audio/xvf3800.py` working standalone: it's the go-to hardware check.

## Current milestone
Runs on the real XVF3800 (laptop/audio/SPIKE.md): captions, doorbell and name called measured on the array's
audio, golden gate `python tools/stress_test.py --golden` 7/7 (main 8d78be3, 2026-10-10). Next: the "Blocked" list in SPIKE.md (real
doorbell enrollment, wearer calibration, DoA accuracy with people and crowd noise, people-recorded fixtures).
