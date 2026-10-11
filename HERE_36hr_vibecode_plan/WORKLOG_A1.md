# WORKLOG A1: speech (Scribe Realtime + whisper)

Branch `adil/a1-speech`. Adil's machine: no ReSpeaker, no NVIDIA GPU (whisper = CPU `base.en`). The real
`ELEVENLABS_API_KEY` is in the main checkout's `laptop/.env` since 2026-10-10 (Scribe numbers below). Everything ran on fixtures.

## Status

| Task (TASKS.md A1) | State |
|---|---|
| A. Baseline | `.\tasks.ps1 test` green on CPU (206 passed before, 225 now). Whisper CPU numbers below. Scribe numbers measured with the real key (below). |
| B. Provider interface | Done: `decode(audio, final, use_hint, utt_id)`, `end`, `warm`, `close`; Scribe, whisper, `FakeTranscriber`. |
| C. Scribe Realtime | Implemented from the official API reference (manual commit, `xi-api-key` header, `input_audio_chunk`, `partial_transcript` / `committed_transcript`, error types). Tested against a local fake server and **the live API** (bench + unplug, below). |
| D. Whisper fallback | Unchanged decoder, warmed at startup. Mid-session fallback + background probe that switches back. |
| E. Utterance assembly | Stable ids/bearing (pipeline unchanged), `final` exactly once, punctuation-only partials dropped, provider timings in `stage_ms`. |
| F. Name called | Rear name-call shown on `mock_scene` (below). Aliases tested. |
| G. Tests | `laptop/tests/test_transcribers.py` (19 tests). |

## Numbers (Adil's laptop, CPU)

`python -m laptop.speech.bench fixtures/array_tts --engine faster_whisper` (whisper `base.en` int8 on CPU):

| | first words after line start | final after line end | WER |
|---|---|---|---|
| median | 1.18 s | 2.40 s | mean 0.04 (7 lines) |

CPU only: one line waited 6 s for its first words behind queued decodes. Not representative of the demo GPU (Kori's laptop).

### Scribe Realtime, live API (Adil's machine, real key, 2026-10-10, `fixtures/array_tts`)

| Run | first words after line start (median) | final after line end (median) | WER | notes |
|---|---|---|---|---|
| `--engine scribe_realtime` | 1.24 s | 0.82 s | mean 0.04 | 10 utterances, 0 duplicate finals |
| `--engine scribe_realtime --unplug 12:22 --retry-s 5` | 1.80 s | 0.95 s | mean 0.04 | -> `faster_whisper` at 12.4 s, back to `scribe_realtime` at 22.5 s, 0 duplicate finals |
| whisper GPU (K3, Kori's laptop) | 0.33 s | 0.84 s | mean 0.04 | for comparison |
| whisper CPU (above) | 1.18 s | 2.40 s | mean 0.04 | for comparison |

- First partial of the **first** line: 2.07 s. The "first 2 seconds of audio" note holds for session start only; later
  lines 1.19-1.35 s.
- Scribe spells the name "Corey". `NameMatcher(["Kori"])` matches Corey / Cory / Kory (0.9) and rejects Rory / Kari /
  Gori (`laptop/tests/test_speech_logic.py` already covers Corey).
- **Recommendation:** Scribe clearly beats CPU whisper. Against GPU whisper the finals are equal and first words are
  slower. Kori picks the demo engine after the live array check (step 5 of the runbook below).

## Failure injection

Unit tests (local fake Scribe server, `laptop/tests/test_transcribers.py`):

| Injection | Result |
|---|---|
| no key | Scribe never constructed; whisper only; heartbeat detail `scribe unavailable (ELEVENLABS_API_KEY not set) -> whisper` |
| bad key (`auth_error`) | whisper from startup, detail `scribe_realtime unavailable: auth_error: ... -> faster_whisper`; the key never appears in logs/detail |
| network drop mid-utterance | noticed on the next decode (connection closed, < 2 s total incl. the final), whisper decodes **the whole utterance** from sample 0 |
| slow (no commit answer) | `final_timeout_s` (1.2 s; 0.3 in the test) -> whisper decodes the final |
| outage then recovery | background probe every `fallback_retry_s`; switches back without any speech needed |
| silence (`insufficient_audio_activity`) | empty final, **not** treated as an outage |
| blip retracted mid-stream | session discarded (`end`), its audio never reaches the next utterance |

Real pipeline, real time, `python -m laptop.speech.bench fixtures/array_tts --primary fake --unplug 12:22 --retry-s 3`
(a scripted stand-in for Scribe, real CPU whisper as fallback):
- switch to `faster_whisper` at fixture 12.2 s (unplugged at 12.0), every line still captioned, 0 duplicate finals;
- back on the primary at 27.8 s. That run predates the background probe; it now recovers within `fallback_retry_s` of
  the outage ending, speech or not.
- the stand-in's captions are scripted per utterance, so its WER column means nothing; whisper's lines were correct.

Name called, `python -m laptop --replay fixtures/mock_scene --offline --no-sounds` (CPU whisper): one `name_called`
alert, `bearing_deg -179.9` (behind), on "Hey Kori, the pizza is here.", expired after 10 s. No repeats.

## For Kori (A1 is on `main`: `git pull`, then run these on the demo laptop)

Adil has no GPU, no array and no ElevenLabs key, so everything that needs one of those is yours.

1. **Golden, first:** `.	asks.ps1 golden` must stay 7/7. The default engine is still `faster_whisper`, so this
   exercises whisper as before. What changed in the pipeline: `utt_id` reaches streaming engines,
   punctuation-only text counts as empty, `end()` on a retract. If golden drops, revert the A1 merge and tell Adil.
2. **Whisper on the GPU, for comparison:** `python -m laptop.speech.bench fixtures/array_tts --engine faster_whisper`.
   Paste the table under "Numbers" (Adil's CPU numbers are there).
3. **Scribe (needs the key):** put `ELEVENLABS_API_KEY=...` in `laptop/.env` (gitignored, never committed), then
   `python -m laptop.speech.bench fixtures/array_tts --engine scribe_realtime`. Paste the table. Check
   `stage_ms.first_partial` against the "2 seconds" note below.
4. **Pull the plug:** `python -m laptop.speech.bench fixtures/array_tts --engine scribe_realtime --unplug 12:22 --retry-s 5`,
   then once for real: turn Wi-Fi off mid-sentence during
   `python -m laptop --replay fixtures/array_tts --offline --engine scribe_realtime`. Expect
   `[speech] scribe_realtime failed: ... -> faster_whisper`, captions continuing, then `... back: captions use it again`.
5. **On the array:** `python -m laptop --real --engine scribe_realtime`, say "Hey Kori" from behind: one
   `name_called` alert with a rear bearing.
6. **Demo on Scribe** only if 3-4 look good: `speech.engine: scribe_realtime` in `laptop/config.yaml`
   (or `--engine scribe_realtime`). Whisper stays the automatic fallback either way.

## Results from Kori's laptop (K3, 2026-10-10): see WORKLOG_K3.md "Round 4"

- Golden on clean `main` (A1 merged): **7/7**, same numbers as before A1. No revert needed.
- Whisper on the GPU (array_tts): median first words **0.33 s**, final **0.84 s**, mean WER 0.04 (CPU: 1.18 / 2.40 s).
- Scribe bench and `--unplug`: run on Adil's machine with the real key instead (see "Scribe Realtime, live API" above). The
  real Wi-Fi-off drill and the array check (steps 4-5) are still Kori's.
- `--primary fake --unplug 12:22 --retry-s 5` on the GPU: whisper at 12.2 s, back at 22.2 s, 0 duplicate finals.

## Unverified until there's a key (check on first live run)

- ~~"Transcript processing starts after the first 2 seconds of audio"~~ measured: 2.07 s on the session's first line
  only, 1.19-1.35 s after that.
- Whether `committed_transcript` arrives for a very short commit or `insufficient_audio_activity` is sent instead
  (both handled).
- `keyterms` encoding (repeated query param); off by default (`speech.scribe.keyterms: false`).
- Commit frequency: one manual commit per utterance (every few seconds). The guide says rapid commits "can degrade
  model performance"; watch for `commit_throttled` (handled as an outage -> whisper).

## Wiring outside `laptop/speech/` (K1 owns these files)

- `laptop/__main__.py`: `--engine faster_whisper|scribe_realtime|fake` (4 lines).
- `laptop/app.py`: unchanged (heartbeat already reads `transcriber.engine` / `.reason`).

## Log

- 2026-10-10: flaky drop test fixed (K3's root cause below): `ScribeSession.send()` turns a send on a socket closed
  under it into `ConnectionError(<the reader's reason> or "connection closed (...)")`, so the heartbeat detail is
  always honest. Loop of the test: 21/300 failed before, 300/300 passed after; full gate 3x green. K3: golden not
  rerun, change is scribe-only (whisper path untouched). Live Scribe numbers added (real key on Adil's machine).

- 2026-10-10: merged to `main` at Adil's request before golden; Kori runs golden + the key/GPU checks from main.
- 2026-10-10: baseline green; provider interface, FakeTranscriber, Scribe Realtime adapter, fallback with background
  probe, bench, 19 tests. Whisper CPU numbers. Waiting on the key for Scribe numbers.

## Flaky test, found by K3 (2026-10-10)
`laptop/tests/test_transcribers.py::test_network_drop_mid_utterance_hands_the_whole_utterance_to_whisper` fails about 1 in 5
full `pytest contracts relay laptop tests` runs on Kori's laptop (on `main` 23c83f9, and with A2 merged); alone it passes
30/30. The log at the failure: `scribe_realtime failed: received 1000 (OK); then sent 1000 (OK) -> faster_whisper`, so
the fake server's "drop" can surface as a clean close, and then `"connection" in f.reason` fails (or the < 2.0 s timing
check misses under load). Please make the test (or the reason text for a close mid-utterance) deterministic. Details:
WORKLOG_K3 "Round 6".

### Root cause and the minimal fix (K3, verified on a throwaway copy of main 43f1f02; A1 to apply)
**Cause:** a race in `ScribeSession` (`laptop/speech/transcriber.py`).
- When the server closes mid-utterance, the reader thread usually sees it first and sets `self.error = "connection closed"`.
- Sometimes the main thread's next `self.ws.send(...)` hits the closed socket first. Then websockets' raw
  `ConnectionClosedOK` escapes, its text is `received 1000 (OK); then sent 1000 (OK)`, and that becomes
  `FallbackTranscriber.reason`.
- The fallback itself works either way (whisper gets the whole utterance). Only the reason text (heartbeat `detail`)
  changes, and the test's `"connection" in f.reason` fails.

**Failing first:** the test in a 150x loop (one process, the same `server` fixture):
```python
# loop file, outside the repo
import pytest
from laptop.tests.test_transcribers import server, test_network_drop_mid_utterance_hands_the_whole_utterance_to_whisper as t
@pytest.mark.parametrize("i", range(150))
def test_loop(server, i):
    t(server)
```
On main: **13 failed / 137 passed** (all on line 173, the reason check).

**The fix:** `send()` raises the same honest reason the reader would have set:
```diff
--- a/laptop/speech/transcriber.py
+++ b/laptop/speech/transcriber.py
@@ -225,9 +225,12 @@ class ScribeSession:
             if commit:
                 self.t_commit = now
         for k, piece in enumerate(pieces):
-            self.ws.send(json.dumps({"message_type": "input_audio_chunk", "sample_rate": SR,
-                                     "audio_base_64": base64.b64encode(piece.astype("<i2").tobytes()).decode(),
-                                     "commit": bool(commit and k == len(pieces) - 1)}))
+            try:
+                self.ws.send(json.dumps({"message_type": "input_audio_chunk", "sample_rate": SR,
+                                         "audio_base_64": base64.b64encode(piece.astype("<i2").tobytes()).decode(),
+                                         "commit": bool(commit and k == len(pieces) - 1)}))
+            except Exception as e:  # noqa: BLE001 - closed under us before the reader noticed: same honest reason
+                raise ConnectionError(self.error or f"connection closed ({type(e).__name__})") from e
 
     def close(self):
         try:
```
With it: **150/150 passed, twice** (300/300), and `laptop/tests/test_transcribers.py` 19 passed. The heartbeat detail
on a real drop now always reads `scribe_realtime failed: connection closed (...) -> faster_whisper`.

