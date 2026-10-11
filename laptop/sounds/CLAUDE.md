# laptop/sounds

Part of HERE. Read ../../README.md §10, §12.7, §15 first, then HERE_36hr_vibecode_plan/WORKLOG_A2.md (the bakeoff).

## Job
The classifier (`sounds.model`: a Hugging Face AudioSet tagger picked by `tools/sound_bench.py`, PANNs CNN14 as the
fallback at load and mid-session) labels 1 s windows from two triggers: the sliding window (newest `window_s` every
`hop_s`, drop, don't queue) and K1's onset candidates (`app.onsets`, a window from 0.1 s before the onset, queue of
4 that drops the oldest). AudioSet classes map onto contract SoundLabels (`label_map`, by AudioSet index for every
model; score = best class); repeats merge into one event (`count`). Labels in `sounds.publish` become `sound_event`s
with a bearing (raw-mic SRP via `ReSpeaker.locate` when available, else recent `sources`, else the onset's bearing);
alerting labels also raise an `alert` (AlertManager: dedupe 4 s, expire 10 s, never "gunshot"; the model's own
`alert_min` per kind, since score scales differ between models). `sound_event.model` names the model that answered.

`unknown`: an onset with real energy that lasts and that no label (published or not) explains. Keeps the onset's
bearing; a known label within `unknown.upgrade_s` re-sends the same `event_id`; cooldown and an ambience rule (3 in
30 s) merge repeats instead of sending more. Never a known label below its threshold.

## Files
`models.py` (backends: PANNs, HF AudioSet, CLAP zero-shot), `classifier.py` (Classifier with fallback,
SoundPipeline: triggers, unknowns, publishing), `events.py` (label mapping, merging), `labels.py` (display),
`enrolled.py` / `enroll.py` (the demo doorbell template).

## Run
Part of `python -m laptop`. Offline: `python -m laptop.sounds.classifier --wav f.wav [--model ID]`.
Bakeoff: `python tools/sound_bench.py [--models ...] [--device cuda]`. Enroll the demo doorbell from the array:
`python -m laptop.sounds.enroll doorbell`. PANNs downloads its checkpoint (~330 MB, md5-checked) to `~/panns_data`;
Hugging Face models go to the HF cache. Tests: `python -m pytest laptop/tests/test_sound_events.py
laptop/tests/test_sound_ai.py` (fakes; never downloads).

## Rules
- Thresholds, windows, label map, published labels, per-model settings: `laptop/config.yaml` (`sounds:`, `alerts:`).
- Never block on the model: windows are dropped, onsets queue at most 4.
- Never a "behind the wearer" judgment here (the laptop doesn't know the phone's heading).
