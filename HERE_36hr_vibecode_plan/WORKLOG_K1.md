# WORKLOG K1: ReSpeaker + event engine

Branch `kori/k1-respeaker`, worktree `../shh-k1`. Python: `..\shh\.venv\Scripts\python.exe` (no venv in the worktree,
so `.\tasks.ps1` falls back to `py`: run the commands below with the shared venv instead).

## A. Baseline (2026-10-10, before any change)

| Check | Result |
|---|---|
| `python -m pytest contracts relay laptop tests -q` | 207 passed |
| `python tools/stress_test.py --golden` | 7/7 (mock_scene clean/+10, array_tts clean/+10, babble_only, two_baby_beams, two_talkers_beams) |
| `python -m laptop --real --offline --dashboard`, 2 min (killed by `timeout`, then `xvf3800.py --reset`) | ran clean: 106 s of `sources` at 20 Hz, 54 heartbeats `ok` (`faster_whisper`, `builtin`), 35 speech_start / live captions from people talking in the room on both beams |

Observations from the smoke run (for F, not fixed yet): the same sentence sometimes captioned on both beams
("Well, I'm sure some of them know" on 0 and 1); several empty finals (retractions); `stage_ms.lag` 760-1290 ms.

### Frozen known-good config (`laptop/config.yaml`)
- `audio.capture: beams`, `audio.fixed_beams: [0.0, 180.0]`, `audio.doa_source: builtin`
- `array_offset_deg: 356.4`, `array_clockwise: true`, `aec_to_device_offset_deg: 359.2`, `aec_to_device_sign: 1`
- `raw_mic_category: 3`, `raw_mic_order: [0, 1, 2, 3]`, `doa_rate_hz: 20`, `ring_main: auto`
- Firmware 2.1.1, 6 channels. Real `sources` in this mode: always two entries, id 0 at +0.8, id 1 at -179.2.

## E. Scripted scenes (first version)

- `tools/fake_events.py`: the script runs on a clock (real, or virtual for building); timers and idle keepalives
  live in `sleep`, so a built scene is exactly what a live play sends. New: `--scene <name>|all [--loop]`,
  `--list`, `--build-scenes`, `--from-log`. `--moment` unchanged (smoke test still passes).
- `fixtures/events/*.jsonl`: the 7 scenes, scripted (hand-written), beams-mode shape. Record from real runs in F.
- Playback rebases `t`/`t_start`/`t_end` to now and suffixes ids `-<run>-<loop>-<scene>`.
- Test: `laptop/tests/test_scenes.py`. Checked live: local relay + `relay_tap --validate` + `--scene all --speed 4`:
  0 contract violations.
- Fixed: a bearing rounding to -180.0 (schema rejects it) in the moments' jitter.

## B. Source state

- `laptop/events/tracker.py` `SourceTracker` (config `audio.tracker`), wired in `app.on_sources` between the raw
  readings and the throttle. Raw readings still feed `DirectionHistory` (A1/A2 unaffected) and the recorder
  (so replays re-run the tracker and send the same wire `sources`).
- Active = speech flag, or energy >= own noise floor + 0.12 (floor = slow min tracker): the quiet room's ~0.4
  level no longer keeps beams listed. Hold 1.5 s, then expiry; empty list sent on change + keepalive.
- `SourcesThrottle.should_send(..., active=)`, compares by id, counts `sent` / `suppressed` (for D).
- `builtin_doa.py`: beams mode `energy` per beam (each stream's loudest block since the last poll).
- Scenes now run their raw readings through the same tracker + throttle (rebuilt).
- Spread: internal only; proposed `sources[].spread_deg` in `contracts/CHANGES.md`. Overlap: `tracker.overlap`.
- Tests: `laptop/tests/test_tracker.py`.

## C. Onset candidates

- `laptop/events/onsets.py` `OnsetDetector` (config `audio.onsets`), fed per stream from `app._publish`
  (`app.feed_onsets`), bearing from the stream's track (`app.onset_where`). Hook: `app.onsets.subscribe(fn)`,
  documented in `laptop/CLAUDE.md`. Not wired to A2's pipeline (their file): A2 adds the subscribe line.
- Tests `laptop/tests/test_onsets.py`: one doorbell press (clean, and through the array over a fan) = 1
  candidate; steady brown/white noise = 0; two presses = 2; bearing + subscriber + app wiring.
- On fixtures: array_tts 7 candidates in 39 s (speech onsets + doorbell), mock_scene 9.
- Speech onsets are candidates too (`speech=True`), A2 may skip them.

## D. Instrumentation

- `laptop/events/stats.py` `STATS` (rolling p50/p95): `capture` (ADC -> callback, builtin_doa), `doa` (USB poll),
  `serialize` + `socket_send` (relay client). Every log line in `logs/laptop.jsonl` gets `_stage`; the client
  now writes a line after the websocket write (with `socket_send`), or with `_dropped: true` (disconnected, or
  evicted from a full outbox). `utterance.stage_ms` gets `capture`/`doa` without overwriting A1's keys.
  `sources` carry theirs on `msg._stage` (dataclass fields only go on the wire: checked).
- `--dashboard`: latency line, dropped (relay / audio fanout), sources suppressed/total, queue depth (audio
  queues, relay outbox, max), onsets count + last, live track ids + OVERLAP.
- Live (array, `--offline --no-speech --no-sounds`, 40 s): capture p50 16 / p95 20 ms, doa p50 27 / p95 35 ms,
  serialize 0.03 ms, `sources` 27 ms old at send. `socket_send` needs a relay: measure in F.
- Gate: 232 tests, golden 7/7.

## E (cont.). Recording scenes from the real pipeline: blocked by room noise

- Tried (03:3x): `python -m laptop --real --offline --user Kori` + the rear line, one doorbell press and "Hey
  Kori, the pizza is here!" from the laptop speaker (Realtek, default volume, behind the array). The room was
  loud (people talking; both beams 0.3-0.8 nonstop): no rear caption, no doorbell, no name called; only room
  chatter got captioned. Scenes stay scripted. Not retried.
- Found while doing it: level-based non-speech tracks list both beams ~all the time in that noise (raising
  the margin to 0.3 still 177/247 frames). Fixed: `audio.tracker.nonspeech: onsets` (default): a non-speech
  track appears only after an onset candidate on its stream, for `nonspeech_hold_s` (1 s) + hold. Speech tracks
  unchanged. `energy` / `off` remain as options.

### Human steps to record real scenes (Kori, when the room is quiet)
1. Quiet room, array on the desk, a Bluetooth/laptop speaker ~1 m **behind** the array (world 180), volume up
   so a line reads ~-25 dBFS on beam 1 (the dashboard's input line).
2. Terminal 1: `..\shh\.venv\Scripts\python.exe -m laptop --real --offline --user Kori --dashboard`.
3. Wait for `[laptop] running`, then play (or say from behind): "Why is nobody looking at the person holding
   all the pizza?", pause 5 s, one doorbell press, pause 5 s, "Hey Kori, the pizza is here!".
4. For front / two speakers: a person in front (world 0) says a line; then front + behind talk over each other.
5. Ctrl+C, then `python laptop/audio/xvf3800.py --reset`. Note the clock times (or find them in
   `logs/laptop.jsonl`) and cut each scene:
   `python tools/fake_events.py --from-log logs/laptop.jsonl --start <t0> --end <t1> --out fixtures/events/<name>.jsonl`,
   add the name to `RECORDED` in `tools/fake_events.py`, run `python -m pytest laptop/tests/test_scenes.py`.

## Assumptions
- Scenes include `perception_enrichment` with `attention_model: "rules"` so K2 / iOS can build the prominence UI
  before A2 ships the model; A2's real ones replace the model id.
- `sound_event.model`: `enrolled` for the doorbell (template match), `panns_cnn14` otherwise.

## Risks / TODO
- iOS must handle `sources: []` and missing ids (IOS_INTEGRATION row). If they can't in time, set
  `audio.tracker.hold_s` very high to get the old "always listed" behaviour back.
- Per-beam energy on the live array is untested until the next `--real` run (F): check the floor/margin there.

## F. Noisy-room rear line / doorbell / "Hey Kori" (supervisor priority 2, 2026-10-10 morning)

Merged origin/main (A1 Scribe + fallback) first: 252 passed, golden 7/7, pushed 743b373.

### What the failed 03:29 run did (from `logs/laptop.jsonl`; no audio was recorded)
- Rear beam: utterances segmented, most finals empty; the played lines never captioned.
- Two **false** `name_called` alerts at the *front* bearing: "Kori. Thank you." = whisper writing the
  `name_hints: prompt` hint back on noise. (The 03:42 run, `fixtures/k1_positions`, is room chatter only:
  people + Mario Party music around the desk, near-equal on both beams.)

### Offline repro on real acoustics: `tools/stress_test.py --room`
New: `--room <beams fixture>` mixes a real two-beam room recording into a two-beam scene per beam at `--snr`
(SNR on the beam carrying the scene's speech), merging the array readings too (speech flags OR'd, energies
summed), so DoA gating sees the chatter. `--no-empty` skips babble-only. Scene: `raw_speaker_180` (whole scene
from behind, quiet room); room: `k1_positions` (from 19 s).

    python tools/stress_test.py --audio-only --scene raw_speaker_180 --room k1_positions --snr 10 5 0 -5 --no-empty

| condition (current config) | rear "pizza" line | doorbell | name called |
|---|---|---|---|
| clean | ok | hit | hit |
| +10 / +5 / 0 dB | ok | hit | hit |
| -5 dB | ok | hit | **MISS** |

The -5 dB miss, two causes in A1's `laptop/speech/pipeline.py` / `speech:` config:
1. `not captioned: music for 80% of it ('Kori.')`: the non-speech veto (`NONSPEECH` includes `music`, `tv`)
   drops captions whenever background music outscores speech; music raises no alert, so the wearer gets
   nothing. With music/tv out of the veto (monkeypatched, not committed), cause 2 shows.
2. Silero fired on "the pizza" (speech_start 28.99 s) though the array flagged the talker from 27.72 s;
   `array_lookback_s: 0.8` reaches back to 28.19 only, so "Hey Kori," is cut off, the lone "Kori." chunk fails
   the hint-echo check (plain decode hears "Thank you.") and is dropped. `array_lookback_s: 1.5` ->
   "Hey Kori, the pizza is here." + `name_called` at -179.2.

### Live take on the real array (08:1x, `fixtures/k1_room_live1`, not in git)
`python tools/speaker_scene.py --out k1_room_live1`: room floor -36 dBFS on both beams, the lines at -31..-39
dBFS (0-5 dB SNR; "Hey Kori" at -38.7, *below* the floor). The laptop was **in front** of the array (beam 0
louder), not behind. The firmware flagged speech in 6 % / 11 % of frames (vs ~100 % of a line in the quiet
`raw_speaker_180` take).
- Current config: 3 utterances in 36 s, nothing useful. `endpoint_use_doa` blocks every Silero start because
  the array's speech flag (firmware per-beam speech energy) doesn't fire at this SNR. Lookback 1.5 doesn't help.
- `speech.endpoint_use_doa: false`: every line captioned right on beam 0, beam 1's leak copies all dropped as
  noise, "Hey Kori, the pizza is here." -> `name_called`, but with `bearing: None` (no confident speech reading
  -> spread 180 -> no bearing). Fix in K1's `DirectionHistory` (below).

### Before / after (name called; doorbell hit at every level in every row; 0 false name alerts)

| config | golden | room 0 dB | room -5 dB | room -10 dB | live take `k1_room_live1` |
|---|---|---|---|---|---|
| current | 7/7 | hit | **MISS** | hit | nothing captioned (gate shut) |
| proposed: `array_lookback_s` 1.5 + no music/tv veto | 7/7 | hit | hit | hit | nothing captioned (gate shut) |
| `endpoint_use_doa: false` (rejected) | 3/7 | | | | all lines captioned, name hit |

Golden notes: lookback 1.5 alone went 5/7 once (array_tts +10 dB: the name alert landed but 0.03 s past the
final_lag limit and outside the scorer's window), then 7/7. A1's `test_network_drop_mid_utterance...` flaked once
in the full gate (passes 3/3 alone).

**Recommendation (A1's area, sent to the supervisor for Kori -> Adil; not applied):** `speech.array_lookback_s:
1.5`, and drop `music`, `tv` from `NONSPEECH` in `laptop/speech/pipeline.py` (better: a `speech.nonspeech_labels`
list). Reproduce: `python tools/stress_test.py --audio-only --scene raw_speaker_180 --room k1_positions --snr 0 -5
-10 --no-empty --set speech.array_lookback_s=1.5` (+ the NONSPEECH change).

**K1 shipped:** fixed-beam bearing fallback (`audio.fixed_beam_spread_deg`). Live take with the gate off:
`name_called` at +0.8 (laptop was in front), was `None`.

**Open (the live gap):** at 0-5 dB SNR the firmware's speech flag misses the talker, so the gate stays shut.
Next K1 step: a directional-level speech flag in beams mode (beam level over its floor and over the other beam),
measured on `k1_room_live1` + golden. Physical fix in parallel: talker/speaker ~10 dB over the room.

### Human steps (Kori): rear take in the real room
1. Laptop (or a BT speaker) ~1 m **behind** the array (world 180), volume up until the dashboard's beam-1 input
   reads about -25 dBFS while a line plays.
2. Tell K1; K1 runs `python tools/speaker_scene.py --out k1_room_rear --position "behind, 1 m" --true-bearing 180`
   (plays the scene: rear line, doorbell, "Hey Kori"), then `xvf3800.py --reset`.
3. For Adil's step 5 with a person: stand ~1 m behind the array and say "Hey Kori, the pizza is here!" while K1
   runs `python -m laptop --real --offline --user Kori --dashboard` (expect one `name_called` near 180).

## Kori's 10-minute array checklist (one sitting, human only)

Needs: floor tape at 0 / 90 / 180 / -90 (= 270) around the array at 1 m (0 = the array's marked front, 90 = its
right), a quiet-ish moment, one person (Kori) to walk and talk. Close everything else that uses the array
(`python tools/demo.py down`, no `python -m laptop`). Terminal in `shh-k1`, `$py = ..\shh\.venv\Scripts\python.exe`.

1. **Reset (10 s).** `& $py laptop/audio/xvf3800.py --reset`, then `& $py laptop/audio/xvf3800.py` shows readings
   (Ctrl+C after a second).
2. **Direction with a person (5 min).** `& $py tools/doa_accuracy.py --angles 0 90 180 -90 --distances 1.0
   --seconds 8 --note "Kori, room <quiet/noisy>"`. At each prompt stand on the tape mark facing the array, press
   Enter, talk normally (count, read anything) until it moves on. Paste the printed markdown table into
   `laptop/audio/SPIKE.md` (K1 does it if you leave it in `logs/doa_accuracy/`). Pass: median error <= 15 deg at
   every mark.
3. **LED ring (3 min).** `& $py -m laptop.audio.ring --sweep`: LED 0 should light first at the array's front, then
   go clockwise. Note the mark LED 0 lights at and the direction it walks. Then `& $py -m laptop.audio.ring
   --follow` and talk from 0, 90, 180, -90: the lit LED should face you. If it is off by a constant, set
   `audio.ring_led0_deg` to that offset (if it walks the wrong way, `ring_led_step: -1`) in `laptop/config.yaml`,
   re-run `--follow`. Ctrl+C, then `& $py -m laptop.audio.ring --firmware`.
4. **Rear name call (1 min).** `& $py -m laptop --real --offline --user Kori --dashboard`, stand on the 180 mark,
   say "Hey Kori, the pizza is here!" at normal volume. Pass: one `name_called` near 180 in the dashboard line.
   Ctrl+C.
5. **Reset again.** `& $py laptop/audio/xvf3800.py --reset`. Tell K1 "checklist done": K1 records the numbers.

### `audio.level_speech` (K1, laptop/events/level_speech.py): measured, stays OFF by default

A beam counts as speech while its level is >= its own floor + `margin` (0.25 = ~11 dB) and >= the other beam +
`separation` (0.08 = ~3.6 dB), held 0.4 s, confidence 0.5; only adds flags; applied in `app.on_sources` (the
recorder keeps the firmware's readings, so replays re-run it). Parameter sweep on recorded readings: live take
8/8 lines flagged (89 % of line frames), real chatter 6.5 % of frames newly flagged (the firmware already flags
66 % of beam-0 chatter frames: it does hear real people; it's the laptop's TTS it misses).

| check | off | on |
|---|---|---|
| golden | 7/7 | 7/7 |
| live take `k1_room_live1` | nothing captioned | all 8 lines on beam 0, `name_called` at +0.8; 1 extra leak duplicate on beam 1 |
| `--room` repro 0 / -5 / -10 dB name | hit / MISS / hit | hit / MISS / hit (that miss is the music veto + lookback) |
| `k1_positions` real chatter only, 220 s | 22 captions, 0 alerts | 26 captions, **1 false `name_called`** |

The false alert: "Kori. Thank you." = whisper writing the name hint back on noise; the same pattern made both
false alerts of the 03:29 run with the switch off. It isn't caught because the hint-echo guard only re-checks
a *looped* or *lone* name. **For A1:** treat "<name>." followed only by `hallucinations` phrases as name-only
(so the no-hint decode decides). With that fixed, `level_speech` can be turned on; until then it stays off.
Turn on for a test: `--set audio.level_speech.enabled=true` (stress_test) or edit `laptop/config.yaml`.

## Gate 2: real array -> local relay -> K2 dashboard (`tools/demo.py up --source real`, ports 18082/18782, session k1gate)

Works end to end on main's code: dashboard credits ReSpeaker / whisper / PANNs / rules / local relay, direction
latency p50 28-30 ms, captions with bearings (front +1, behind -179), the doorbell (enrolled match) as a
`sound_event` + `alert` (one alert id, updates, then `active: false`) and enrichment. `relay_tap --validate`: 0
violations over 708 messages. Scene was played from the laptop speaker (`speaker_scene.play_scene`, gain 2.5)
while the demo laptop held the array.

`--real` vs `--scene` differences (fields): real `sound_event` has no `source_id` (scenes do: A2), real
`perception_enrichment` no `directed_at_user` (optional). Behaviour:
- **Fixed (K1):** both beams listed in 93 % of `sources` frames: A2 merges most onsets as ambience (125 in 5 min)
  but the tracker took every onset as a sound starting. Now `audio.tracker.ambience_max` (4 in 30 s per stream):
  replaying `k1_positions` both-beams 80 % -> 42 % (the rest = the firmware flagging real talkers). Golden 7/7.
- "Hey Kori," cut off again ("the pizza is here." only): A1 diff (lookback / music veto) still pending.
- One doorbell press: 9 `sound_event` updates with the bearing jumping 24.8 / 27.8 / 28.8 / 0.8 (A2: the 0.8 ones
  are the beam bearing fallback; the raw-mic SRP says ~26).
- Dashboard (K2): retracted utterances (empty finals) show as empty caption cards; the doorbell alert was gone
  from the Alerts panel ~20 s later (alert `expire_s` 10: maybe intended).

## Doorbell enrollment through the array (K1 recording, A2's file to review)

`fixtures/sounds/doorbell_k1_array.wav`: the demo doorbell (`fixtures/sounds/doorbell.wav`, the synthetic
ding-dong: there is no real doorbell recording in the repo) played once from the laptop speaker at gain 1.5 and
recorded by `python -m laptop.sounds.enroll doorbell --seconds 9 --name doorbell_k1_array --no-config`: sound at
3.79-7.94 s, peak -5 dBFS, self-match 0.46, suggested threshold 0.12. **Config untouched** (`sounds:` is A2's).
Caveat: `enroll` records the array's default processed output, not the fixed beams HERE runs with.
On the live beams take `k1_room_live2`: current template and new template both hit (0.93-0.95 on 11 windows). On 220 s of real chatter (`k1_positions`) the new template raised 0 alerts.
For the real demo: A2 decides whether to point `sounds.enrolled` at it; re-enroll from the actual demo
Bluetooth speaker on its tape mark at demo volume (human: place the speaker; K1 runs the command).

## Two talkers at once

Real acoustics without people: `raw_speaker_180` (the scene from behind) mixed with `k1_room_live2` (the scene
from the laptop in front) at 0 dB per beam, array readings merged (`stress_test.make_room_condition`, offset 2 s;
`logs/stress/two_real_front_rear_0dB`). Result (default config):
- overlapping lines both captioned on their own beams (front on b0, rear on b1 at the same seconds);
- the rear talker reads clean; the front one is fragmented while the rear one talks ("with my resume.",
  "Why is nobody looking at you?", "All the pizza.");
- the front doorbell alerts at +0.8, the rear one at -179.2; `name_called` (rear "Hey Kori") at -179.2;
  baby at -179.2.
Synthetic two-talker / talker+baby (golden) pass as before.

### Human steps (Kori + one more person, 3 min)
1. `& $py laptop/audio/xvf3800.py --reset`, then `& $py -m laptop --real --offline --user Kori --dashboard`.
2. Person A on the 0 mark, person B on the 180 mark, 1 m away. A reads "Did you see the email about the
   internship fair?" while B, at the same time, says "Hey Kori, the pizza is here!".
3. Pass: two captions at once (one +1, one -179 in the dashboard), one `name_called` near 180.
4. Ctrl+C, `& $py laptop/audio/xvf3800.py --reset`. Add `--record two_people` to step 1 to keep it as a fixture.

## Booth robustness: array unplugged / replugged, laptop sleep (K1)

`laptop/audio/builtin_doa.py`: setup moved into `ReSpeaker.connect()`; `run()` loops. A watchdog in `poll_doa`
returns "lost" after `audio.loss_doa_fails` (10) USB failures in a row, no audio for `audio_stale_s` (1.5 s), a
pause > `sleep_gap_s` (5 s, laptop slept), or `audio.drill_file` appearing. Then: heartbeat `degraded`, detail
"ReSpeaker lost (<why>); reconnecting" (+ "try N: <error>" while it fails), release (ring, routing, USB handle,
no exceptions), and every `reconnect_s` (2 s) PortAudio re-enumerates (a replug can change the device index) and
`connect()` sets everything up again (routing, fixed beams, raw-mic category, ring). Ctrl+C still restores routing.
Tests: `laptop/tests/test_array_reconnect.py`.

Drill (`tools/doa_usb_drill.py`, `run_drill()` for K3's tools/drills.py): `--inject` (no hands) or `--unplug`
(human). Real array through a local relay: **PASS: degraded after 2.0 s, ok + sources again at 4.0 s**
(reconnect 2.1 s). Not yet tried: a real cable pull and a real sleep/resume (human: `python tools/doa_usb_drill.py
--unplug` with a `--real` laptop running; close the lid ~10 s for sleep). K3 measured light judge resets with
`--source real` at 17.7-18.6 s.

## Doorbell bearing jitter on the wire (K1's, fixed)

Live, one press (event s47475-19 / alert a47449-2) read 28.8 / 27.8 / 0.8 / 24.8. Cause: K1's `locate()`
(ReSpeaker and, separately, FixturePlayer) returned None when the same event was asked again within 0.5 s or
SRP coherence was low; A2's `publish_event` then fell back to `history.any_bearing` = the fixed beam's bearing
(+0.8). Fix: `laptop/audio/localize.EventBearings`, shared by both: one bearing per event (circular median of
its SRP results, best confidence), returned on every update, never None once found. Replay of `k1_room_live2`:
before 24.8 / 23.8 / 0.8 / 22.8 / 0.8 / 22.8 / 0.8 ..., after 24.8 / 24.8 / 24.8 / 23.8 / 23.8 / 22.8 x6. Test
`test_one_sound_event_keeps_one_bearing`. Gate 548 passed, golden 7/7. No change for A2. (Seen in the same
replay, A2's side: an `unknown` event's update moved -179.2 -> 0.8, from the onset's beam bearing.)

## Real-array latency (for the pitch), 2026-10-10 ~13:xx

`tools/doa_latency.py` (new): a relay client on the laptop (same clock) stamps every laptop message on arrival.
Run: `tools/demo.py up --source real` (local relay :18082, session k1lat, branch code), the scene played twice
from the laptop speaker (gain 2.5), 95 s.

| metric (laptop -> local relay -> client) | n | p50 | p95 | vs <= 250 ms sound -> phone |
|---|---|---|---|---|
| direction (`sources`, from the DoA sample) | 292 | **31 ms** | 39 ms | yes, with ~200 ms to spare for Wi-Fi |
| speech_start (from the utterance's first audio) | 10 | 269 ms | 569 ms | ~ (VAD/array onset) |
| first caption words (from first audio) | 6 | 841 ms | 968 ms | words, not a buzz |
| final caption after speech ends | 6 | 868 ms | 960 ms | includes end_silence 0.6 s |
| doorbell alert (from the sound's onset) | 2 | 1063 ms | 1075 ms | **no** |

Stages from the same run (`stage_ms`): capture p50 16 ms, DoA poll 26 ms, whisper decode (`asr`) p50 149 / p95
325 ms. Not included: the firmware's acoustic -> DoA lag, the phone's Wi-Fi hop (`tools/latency_probe.py`).

**First word:** = VAD/array onset (~270 ms, measured from t_start which includes the 0.25 s pre-roll) + the first
partial fires when the utterance holds `speech.partial_every_s` (0.4 s) of audio + whisper ~150 ms. All A1's;
nothing cheap on K1's side (capture 16 + DoA 26 ms). Ideas for A1: a first partial at ~0.25 s of voiced audio,
then every 0.4 s; Scribe's first partial once the key is in.
**Doorbell alert ~1.06 s:** the onset path classifies a 1 s window that starts 0.1 s before the onset, so it waits
~0.9 s of audio. For A2: classify a 0.5 s window first (the enrolled template match is quick and robust), then
confirm with the full window.

## Proposals for A1 / A2: branch `kori/k1-proposals` (pushed, NOT for merging by K1; Adil cherry-picks)

Based on `kori/k1-respeaker` (main + K1: (d) needs K1's `level_speech`). Each commit touches only its owner's
files + its config lines + a test; (a) to (c) should cherry-pick onto main. Gate on the branch: 558 passed.

| commit | change | measured (real-array recordings unless noted) | golden | false alerts |
|---|---|---|---|---|
| 192b967 (a) A1 | partials every 0.2 s until the first words, then 0.4 s (`speech.first_words_every_s`) | first words from the line's start, `array_tts`: p50 605 -> **423 ms**, p95 694 -> 563 | 7/7 | none added |
| 188ff51 (b) A2 | enrolled doorbell's first 0.5 s matched 0.4 s after an onset (`sounds.early_after_s`, `head_s`, `head_threshold` 0.25), speech-flagged onsets included | `--replay`, alert from onset: `array_tts` 920 -> **389 ms**, `k1_room_live2` 1128 -> **555 ms**; head score doorbell 0.32-0.63 vs <= 0.17 elsewhere | 7/7 | 0 doorbell alerts on 220 s real chatter |
| 612c76d (c) A1 | `array_lookback_s` 1.5; `speech.nonspeech_labels` without music/tv; "<name>. <invention>" re-checked without the hint | `--room` repro (rear scene + real chatter), name called at 0/-5/-10 dB: hit/MISS/hit -> **hit/hit/hit** | 7/7 | 0 |
| be400c1 (d) K1 | `audio.level_speech` on (needs (c)) | live takes in a loud room `k1_room_live1`/`live2`: WER 0.97 -> **0.26**, 0.87 -> **0.11**; name miss -> **hit** on both | 7/7 | real chatter 0 alerts (1 false name_called without (c)) |

**Live, all four, real array (demo.py up --source real from the branch, scene x2, `tools/doa_latency.py`), in a
busy room:** direction p50 32 / p95 48 ms; speech_start 269 / 766; first words 1118 / 2178; final after end
1030 / 1502; doorbell alert 2278 / 2446 ms. Worse than this morning's baseline (first words 841, doorbell
1063), for two reasons:
1. With `level_speech` on, a busy room sends many more utterances to whisper on both beams (55 speech starts
   in 95 s): `asr` p50 149 -> 240 ms, and decodes queue. The words are right (WER above), but they come later in a
   crowd. Before turning (d) on for the demo: cap concurrent decodes or prefer the beam being looked at (A1), or
   keep (d) off and rely on a louder talker.
2. Live, the doorbell's onset was detected and recognised ("doorbell (recognised, not published)") but not
   published on the onset path; the first alert came from the regular windows 2.3 s later. In single-stream /
   offline replays (b) fires. Not yet confirmed: A2's per-beam arbiter (`loudest_here`) may block onset-path
   publishes in two-beam live runs. For A2 to check before taking (b).
Numbers from the offline recordings are repeatable; the live numbers depend on the room (it was busier than
this morning).

## Which proposals to ship for the demo (live A/B, real array, busy room, 2026-10-10 afternoon)

Four live runs back to back (same room, same hour): `demo.py up --source real` from each config, the scene
played twice from the laptop speaker (gain 2.5), `tools/doa_latency.py` 95 s, then logs. Configs toggled in the
proposals worktree (`sounds.early_after_s`, `audio.level_speech.enabled`).

| live run | speech starts / 95 s | asr p50 / p95 | first words p50 / p95 | doorbell alert | name called | false alerts |
|---|---|---|---|---|---|---|
| baseline (no proposals) | 58 | 240 / 536 | 1061 / 1920 | ~1.65 s (regular windows) | miss x2 | 0 |
| (a)+(c) | 59 | 206 / 416 | **757** / 1847 | ~1.6 s | miss x2 | 0 |
| (a)+(b)+(b2)+(c) | 52 | 234 / 517 | 986 / 2128 | ~1.6 s (early never fired at 0.4) | miss x2 | 0 |
| all four (+d) | 53 | 249 / 534 | 1192 / 2066 | 2.5 / 3.8 s | miss x2 | 0 |

- **"(d) queues whisper": refuted.** Every config, the baseline included, saw 52-59 speech starts and asr p50 206-249 ms:
  the busy room is the load (this morning's 149 ms was a quieter room). So no (d2) rate-limit.
- **Nobody caught "Hey Kori" live from the laptop speaker in this room**, baseline included: the line is below the
  room. (c)/(d) catch it in the recorded loud-room takes; the live check needs a person (checklist above).
- **(b) live:** instrumented. Two causes: (1) A2's per-beam arbiter dropped onset/early publishes (fixed by (b2),
  test fails before / passes after; two-beam replays then alert at ~600 ms); (2) at `early_after_s` 0.4 only 0.4 s
  of the 0.5 s head had been heard, too little at live SNR: 1 of 3 presses fired. (b3) 0.6: 2 of 4 presses fired
  early, the rest fell back to the regular windows, 0 false.
- `doa_latency.py` fixed: an alert on an upgraded `unknown` used that event's older t_start (the "35 s" row); now
  skipped.

**Recommendation: ship (a), (b)+(b2)+(b3), (c); keep (d) off for the demo.**
- (a): the clearest live win (first words 1061 -> 757 ms p50), no cost.
- (b)+(b2)+(b3): harmless (0 false alerts, falls back to today's path) and faster on about half the presses live, every
  press in replays. Doorbell alert at ~0.6-0.7 s when it fires. A2 may want a more robust early match.
- (c): no live regression, fixes the -5 dB name miss and the "Kori. Thank you." false alerts offline.
- (d): no live gain measured in this room (first words worse, 1192 vs 986, probably noise), and (c) is a prerequisite.
  Turn on only after the person-talker checklist shows it catches a rear "Hey Kori" that (c) alone misses.
