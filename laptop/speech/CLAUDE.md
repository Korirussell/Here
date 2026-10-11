# laptop/speech

Part of HERE. Read ../../README.md §10 (fast path), §12.6 (`speech_start`, `utterance`), §13 first.

## Job
Silero VAD segments the processed audio. At voice onset: `speech_start` with the newest speech direction
(before any words). While the utterance grows it's re-decoded every `partial_every_s` (0.4 s): words that
two consecutive decodes agree on move from `pending` to `text`; a beam-5 final follows `end_silence_s`
after speech stops. Each update carries the circular-mean bearing of the speech `sources` during the
utterance (`bearing_spread_deg`, `overlap` when spread > 25 deg or two tracked sources talk; fixed beams: the
talker's raw-mic bearing in that beam's half of the room via `locate_speech`, else the beam's bearing with
`audio.fixed_beam_spread_deg`), loudness relative to this room's normal talking level, and a tone (loudness + punctuation rules: shouting,
whispering, questioning, excited, laughing, neutral). Name called: fuzzy + sounds-alike match of the
phone's `user_name` / aliases on committed words, once per utterance -> `alert`.

What it refuses to show (each measured in tools/stress_test.py, see CHANGELOG 2026-10-08 night):
- the wearer's own voice (`audio.wearer_bearing_deg` ± `wearer_ignore_deg`): no speech_start, no caption;
- noise: with an array attached, nothing starts unless the array hears a talker (from the first second);
  diffuse babble (confident readings spread > `diffuse_spread_deg`) is retracted; whisper's inventions
  ("Thank you.", lone fillers) and low-avg_logprob segments are dropped unless the array heard a talker > 1 s;
- the name hint written back on noise: `name_hints: prompt` gives whisper the user's name, which helps at
  0 dB but makes it loop "Kori. Kori." on noise (a doorbell in babble did). Loops collapse to one name, and a
  looped or lone name only raises name called if a decode *without* the hint hears it too.
Laughter: the sounds pipeline hands every window's scores to `note_sounds`; PANNs laughter >= `tone_laugh_min`
during the utterance makes the tone `laughing` (scores are low on real laughs: see CHANGELOG Task 5).

## Inputs / outputs
In: `(t, pcm)` from the audio fan-out; the DirectionHistory; per-window sound scores. Out: `SpeechStart`,
`Utterance`, name-called via `AlertManager`. A blip that never becomes words gets an empty final, so the
phone drops its typing dots.

## Run
Part of `python -m laptop` (`--engine faster_whisper | scribe_realtime | fake` overrides `speech.engine`).
Tests (fake VAD + fake engines, a local fake Scribe server): `python -m pytest laptop/tests/test_speech_logic.py
laptop/tests/test_transcribers.py`. Latency + failure injection on a fixture, in real time:
`python -m laptop.speech.bench fixtures/array_tts [--engine scribe_realtime] [--unplug 12:22] [--primary fake]`.

## Engines (`transcriber.py`)
- One interface: `decode(audio, final, use_hint, utt_id)` (the whole utterance so far; streaming engines send only
  what's new), `end(utt_id)` (a dropped blip), `warm()`, `close()`; any exception = engine down.
- `ScribeRealtimeTranscriber`: ElevenLabs Scribe v2 Realtime WebSocket, manual commit per utterance. Partials ->
  `pending`/`text` through the same local agreement, `committed_transcript` -> the final. A small pool of warm
  connections (one per talking beam); a session is reused after its commit and discarded after anything odd, so a
  late answer can't reach the next utterance. Only utterance audio is sent. Timings in `stage_ms` (`first_partial`,
  `commit`). Tunables `speech.scribe`.
- `WhisperTranscriber`: faster-whisper, the mandatory fallback. `FakeTranscriber`: deterministic, for tests.
- `FallbackTranscriber`: Scribe first; on an error, a dropped connection, a missing commit (`final_timeout_s`) or
  an answer > `fallback_max_latency_s` the same decode goes to whisper with the whole utterance (no lost words),
  `heartbeat.engine` turns `faster_whisper` and `detail` says why. A background probe retries Scribe every
  `fallback_retry_s` and switches back on success. No key at startup: whisper only, detail says so.

## Rules
- `import torch` before faster_whisper; numpy audio only.
- Tunables in `laptop/config.yaml` (`speech:`).

## Current milestone
Scribe Realtime wired behind the fallback (tested against a local fake server and failure injection; not yet
against the live API: no key on Adil's machine). Next: measure Scribe latency/WER on `array_tts` with the key,
golden on Kori's laptop (see `HERE_36hr_vibecode_plan/WORKLOG_A1.md`).
