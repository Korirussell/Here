# Audio spike: the XVF3800 on the demo laptop (README §11, §21)

## Morning summary: ODAS night 2026-10-09 (branch laptop/odas, prompts/ODAS_AGENT.md)

**Verdict: keep Track A (fixed beams + built-in DoA) for the demo.** ODAS works end to end, live and offline, but
fails 4 of the 5 decision-rule gates. Tables and the gate-by-gate verdict: laptop/audio/DOA_VS_ODAS.md "Results".
Default behaviour unchanged (`audio.doa_source: builtin`); 187 tests pass; golden 7/7.

What works now:
- **Raw mics at a usable level:** routing USB ch 3-6 to category 3 (`AUDIO_MGR_MIC_GAIN`, +39 dB) instead of the
  default unamplified mics (~4 LSB of ambient). `audio.raw_mic_category: 3`, restored on exit.
- **6-channel recorder:** `--record` writes raw.wav + device meta; replays `locate()` on it. `fixtures/raw_speaker_180`.
- **Mic order fitted:** identity (AEC_MIC_ARRAY_GEO order), 177.8 for truth 177.2; one mirror candidate left (below).
- **Rotation scenes:** `tools/make_raw_scene.py`, 7 scenes with truth from one real recording (`fixtures/rot_*`).
- **ODAS in WSL2:** `tools/odas/setup_wsl.sh` (pinned commit + a build-time patch for a glibc/sprintf bug that made
  every tracked frame a bare `}`). **Live bridge without socat:** ODAS connects straight to Windows' 127.0.0.1
  (mirrored networking); odaslive is started, restarted and killed by HERE. `python -m laptop --real --odas`,
  `--replay rot_talker_baby --odas`, `--odas-audio beams|separated|postfiltered`.
- **Comparison harness:** `tools/doa_compare.py --scenes | --live | --captions | --replay`.

Numbers (simulated = rotation scenes; live = real array, laptop speaker at 180, one source):
- Two sources at once (simulated): ODAS 62-68% of the overlap time, stable ids, 0 wrong-direction ghosts; but tracks
  linger ~2 s after people stop (5-30 s/min stale) and the error is 8-15 deg (our SRP 4.5-7).
- Live single source: device DoA 2.4 deg error, first correct source 0.13 s after onset; ODAS 13.9 deg, a new track
  after 0.1-1.3 s. The bridge itself adds ~0 ms (frames arrive as soon as the audio block is in).
- Captions: fixed beams WER 0.03 live; hybrid (beams steered by ODAS) 0.09; ODAS's own separated audio 0.21-0.52 on
  simulated two-source scenes, and the baby-crying alert was missed with it.
- Found and fixed live: hybrid steering put both beams on one (split) source, leaving the front talker without a beam.

Unverified: everything with two real sources or people (the gate that matters), moving talkers, crowds, the mirror
check of the mic order. Only one loudspeaker position (180) was available all night.

**Kori, < 10 minutes to confirm** (from the repo root, `git checkout laptop/odas`, array on USB):
1. `.\.venv\Scripts\python -m pytest contracts relay laptop tests -q` -> 187 pass.
2. Mic-order mirror check (2 min): `python -m laptop.audio.localize --check --seconds 40`: talk 15 s from the
   array's front, then 15 s from its right. Expect `-> keep [0, 1, 2, 3]`. If it says "the MIRROR fits better", set
   `audio.raw_mic_order: [1, 0, 3, 2]` in laptop/config.yaml (SRP + ODAS both read it).
3. ODAS live (3 min): `python -m laptop --real --odas --dashboard --offline`; talk from the front, then walk around
   to the right. The marker should follow you (ODAS starts ~8 s after launch). Heartbeat says `doa_source odas`.
4. The real test that decides it (3 min): you in front + the baby cry from the laptop at 180, once with
   `python -m laptop --real --odas --dashboard --offline --record two_real_odas` and once with the default
   (`python -m laptop --real --dashboard --offline`). Captions for you only, "Baby crying" from behind, in both?
   Then `python tools/doa_compare.py --replay two_real_odas` for the source stats.
5. After any killed run: `python laptop/audio/xvf3800.py --reset` (beams, routing, raw-mic category).
ODAS rebuild if WSL was reset: `wsl -d Ubuntu -- bash tools/odas/setup_wsl.sh`.

## 2026-10-09 (day): LED ring

The ring was "slow or wrong" because DOA_VALUE is the active focused beam's direction: with the demo's fixed
beams [0, 180] it can only say front or back (beams at +-90, speaker at 177 -> +92 / -88). With auto beams it
held the previous talker ~0.15 s and then swept ~0.3 s. The main marker now comes from our SRP on the raw mics,
checked against the firmware's free-running beam (right 87-95% of the time it is lit, live); the second marker
from ODAS tracks (`--odas`) or a second SRP track that has itself been the loudest (echoes never are).
Numbers: DOA_VS_ODAS.md "LED ring". **Check by eye** (nobody has looked at the LEDs yet):
`python -m laptop.audio.ring --sweep` (LED 0 should face the array's front; else `audio.ring_led0_deg`), then
`python -m laptop --real` and walk round it talking; with a second person or the baby clip, `--odas`.

## Blocked / needs a human

Each item: what to do, exactly, and what it unblocks. Everything else below was measured without people.

0c. **ODAS track (2026-10-09 night):** two real sources (talker + baby, two talkers) through `--odas` and the
   mirror check of the raw mic order: steps 2-4 of the morning summary above. Nothing else is blocked.

0a. **Calibrate the array (now also fits the beam directions).** `python -m laptop.audio.calibrate`: talk from
   the front, then the right. Besides the array offset it now solves `aec_to_device_offset_deg/sign`, which
   beams mode uses for both beams' bearings. Until then the sign is a guess: beam 0 matched the device DoA
   within 3 deg at the one spot measured, but a mirrored sign would put left-hand talkers on the right.
   Check with `python -m laptop --real --dashboard`: a person on the left must show on the left.
0b. **Two real sources** (a person + a Bluetooth speaker playing fixtures/sounds/baby_cry.wav from behind):
   `python -m laptop --real --dashboard --record two_real`. Expect two beam markers, captions only for the
   person, "Baby crying" from behind.
0. **Enroll the real demo doorbell** from the Bluetooth speaker on its tape mark, at demo volume:
   `python -m laptop.sounds.enroll doorbell` (play it once when asked), then `.\tasks.ps1 golden`.
1. **DoA accuracy at 45° steps, 1 m and 1.5 m, with crowd noise (README §21).** Needs a person (or a
   Bluetooth speaker carried to taped spots) and a second loudspeaker for the crowd noise. Tape the array's
   front, then: `python tools/doa_accuracy.py --noise logs/stress/babble.wav --noise-device <speaker>`
   (talk 10 s per prompt; `--speaker --play-device <bt speaker>` if a speaker stands in for the person).
   Paste the printed table under "DoA accuracy" below. Only one fixed source was available here.
2. **Wearer calibration on the real rig.** Wearer on their tape mark: `python -m laptop.audio.calibrate --wearer`
   (talk 5 s). Until then `audio.wearer_bearing_deg` is `null` and the wearer's own voice IS captioned.
3. **Array forward check.** The saved offset (355.3°) was not re-verified: nobody could stand at the
   marked front. `python -m laptop.audio.calibrate` once the array is on its tape mark.
4. **ODAS gate 1 decision (raw channels).** Firmware 2.0.6 exposes 2 capture channels only (below). Raw mics
   need Seeed's 6-channel USB firmware flashed over DFU on the demo unit. That changes shared demo hardware,
   so it's the team's call; the measurements below suggest it isn't needed for the core moments.
5. **Real fixtures with people** (fixtures/README.md: two_people, turn_around, name_called, crowd_noise,
   overlap, angles). `python -m laptop --real --record <name> --offline`, Ctrl+C to stop. Then add them to
   `tools/stress_test.py --scene <name>` once their meta.json has `lines` (tools/speaker_scene.py shows the shape).

## ODAS night 2026-10-09: raw mics, mic order (laptop/odas)

**Raw mic level.** USB ch 3-6 default to category 1 (unamplified raw mics): ambient ~-78 dBFS, ~4 LSB of int16,
and int32/float capture gives no extra bits. Category 3 (`AUDIO_MGR_OP_CH3..6` = (3, n)) is the same mics after
`AUDIO_MGR_MIC_GAIN` (90x, +39 dB): ambient -35..-40 dBFS. `audio.raw_mic_category: 3`, restored to 1 on exit.
With the laptop speakers at 1.5x TTS the in-band (300-6000 Hz) SNR is still only ~5 dB (speech -44, room/fan
-49 dBFS): the laptop's own fan sits next to its speakers.

**Mic order** (`python -m laptop.audio.localize --fit fixtures/raw_speaker_180`, 9 lines, 0.5 s SRP windows;
truth world 180 = AEC 177.2 deg). All 24 permutations; the top of the table and the rotations/mirrors:

| raw_mic_order | rotation/mirror | median error | within 10 deg | mean azimuth | mean coherence |
|---|---|---|---|---|---|
| [1, 0, 2, 3] | no | 4.2 | 81% | 172.9 | 0.785 |
| **[0, 1, 2, 3]** | yes (identity) | 5.8 | 81% | 177.8 | 0.794 |
| [0, 1, 3, 2] | no | 9.8 | 56% | 187.1 | 0.785 |
| [1, 0, 3, 2] | yes (x-mirror) | 10.8 | 46% | 182.2 | 0.794 |
| other rotations | yes | 80-174 | 0% | 2-272 | 0.794 |
| other scrambles | no | 47-176 | 0% | | 0.516-0.785 |

A source at 180 sits on the array's x axis, so orders that only swap the two mics mirrored across that axis
can't be told apart (scrambles [1,0,2,3] / [0,1,3,2] are not valid geometries: lower coherence than the dihedral
pair). The two candidates are **identity** (= AEC_MIC_ARRAY_GEO order on USB ch 3-6, kept) and the x-mirror
[1, 0, 3, 2]. `localize --check` live with the speaker at 180 agreed: identity 5 deg median error vs the
device's DoA, mirror 13 deg; the beam fit reproduced AEC_TO_DEVICE 359.5 / +1 (config 359.2 / +1).
The old "-98 deg for 180" didn't reproduce: an 8 s baby cry gave 173 deg on category 1 and 172 on category 3
(coherence 0.75 vs 0.79).

## Update 2026-10-09: 6-channel firmware flashed; next = our own tracking on the raw mics

- Flashed Seeed's `respeaker_xvf3800_usb_dfu_firmware_v2.1.1_16k6ch.bin` (dfu-util 0.11, `-d 2886:001a -a 1`;
  the laptop also exposes a webcam DFU device 04f2:b7a8, so always pass `-d`). Rollback: the same command with
  the v2.0.6 image (Seeed repo `xmos_firmwares/usb/old_firmwares/`).
- Verified: VERSION 2.1.1; 6 capture channels: ch 1-2 = the configured outputs (our two beams, fixed beams work,
  the 180 deg beam louder than the 0 deg beam for a source at 180), ch 3-6 raw mics at -75..-80 dBFS (quiet, as
  Seeed notes). `ReSpeaker` picks the raw mics up by itself: "raw-mic sound direction on".
- **SRP-PHAT frame is NOT calibrated:** laptop speaker at 180 -> `locate()` says -98 deg (coherence 0.63). Likely
  the raw channel order differs from AEC_MIC_ARRAY_GEO's order (rotation by 90 deg steps) and/or a mirror; one
  position can't tell. First task next session: fit it with `python -m laptop.audio.localize --check` while
  someone talks from 2-3 spots (or extend calibrate.py like the beam fit), then add the mic order to config.
- Next (the "ODAS-like" plan): SRP-PHAT on the raw mics several times a second -> the two strongest sources ->
  steer `AEC_FIXEDBEAMSAZIMUTH_VALUES` to them (beams follow people, no taped positions) and per-direction
  speech-vs-cry energy from the raw mics to kill the 2 phantom captions + 1 front baby alert still seen when
  replaying fixtures/two_real2 (local recording of the live test). ODAS itself (Linux/WSL) only if that falls short.

## Update: two sources at once (beams mode, 2026-10-08 late night)

- **Works without a firmware flash:** the 2.0.6 audio manager routes the two focused beams to USB ch 0/1
  (`AUDIO_MGR_OP_L/R` = (6,0)/(6,1)); measured -19/-22 dBFS speech on both, defaults (8,0)/(7,3).
- Synthetic two-source scenes (beam leakage -9 / -6 dB): talker + baby crying behind -> captions WER 0.00, baby
  alert at 180 only (processed mode: first alert at the talker, 3 misdirected); two talkers overlapping -> both
  captioned, WER 0.00, no duplicates. Real array (one loudspeaker): captions once on beam 0, real baby cry
  alerted at the speaker's bearing and never captioned.
- **Unverified:** real beam separation (needs two sources at different spots: two people, or a person + a
  Bluetooth speaker playing the baby cry). Measure it with `python -m laptop --real --record two_real` +
  `--dashboard`: watch both beam markers. If separation is weak, flash Seeed's 6-ch firmware
  (`respeaker_xvf3800_usb_dfu_firmware_v2.1.1_16k6ch.bin`, `dfu-util -R -e -a 1 -D <bin>`; the DFU interface
  already has WinUSB here, no Zadig) to add raw mics for SRP direction of sounds. Blocked here: downloading /
  flashing firmware needs your OK.

## Summary (2026-10-08, laptop/respeaker)

**Works on the real array** (XVF3800 firmware 2.0.6, 2-ch USB, demo laptop), measured with speech and the
doorbell played from the laptop's speakers (nobody else was around):
- `python -m laptop --real` end to end: speech_start ~0.2 s after the voice, partials every 0.4 s, finals
  0.75-0.9 s after speech ends (0.6 s of that is the end-silence wait), bearings steady to ±0.5°.
- Captions on the real array's audio (`fixtures/array_tts`, babble mixed in): WER 0.00 clean, 0.02 at +10 dB,
  0.08 at +5 dB, 0.58 at 0 dB; 0 phantom captions; doorbell and name called hit at every level; 0 false alarms.
  Pure crowd babble with nobody talking: 0 captions, 0 alerts (with a modelled array track, see below).
- Doorbell direction works on the 2-ch firmware: the XVF3800 flags the chime as speech with the right bearing.
- Wearer filter, name hints with an echo guard, noise filters, laughter tone, sound enrollment, the
  dashboard, the run report and a golden regression gate (`.\tasks.ps1 golden`, 5/5 pass) are in
  (CHANGELOG 2026-10-08 night).
- ODAS gate 1: **fails** on this firmware (2 capture channels only) -> built-in DoA stays (details below).

**Still unverified** (each needs a person or the real rig; see "Blocked"):
- DoA absolute error, distance/angle dependence and behaviour in real crowd noise (only one fixed speaker).
- The array's real behaviour in a crowd: the `babble_only` stress condition uses a *modelled* DoA track
  (speech flagged 60% of the time from random directions, weak confidence). `diffuse_spread_deg` (60°) and
  the "nothing starts unless the array hears a talker" gate depend on it. Record `crowd_noise` and re-run.
- Real voices (TTS only so far), the wearer's own voice on the rig, the real demo doorbell from the
  Bluetooth speaker, laughter from real people (PANNs recognises acted laughs in only ~6% of 1 s windows).
- One check during the dashboard test saw speech at -85° (left) instead of the laptop speaker's +133°: the
  output device index in a scratch script may have pointed elsewhere, or someone spoke. Worth a glance with
  `python laptop/audio/xvf3800.py` while someone talks from the left.

## Hardware as found

- XVF3800 USB, firmware 2.0.6 (bcdDevice 0x206), WinUSB on "reSpeaker Control", 2-channel USB audio.
- Audio input: WASAPI "Echo Cancelling Speakerphone (reSpeaker XVF3800 4-Mic Array)", 16 kHz, 2 ch.
- `xvf3800.py --dump` crashed on `LED_RING_COLOR` (write-only on 2.0.6, status 65); now reported, not fatal.
- The array's 2-ch firmware means `locate()` (raw-mic SRP-PHAT) is off and the heartbeat says so. That turned
  out not to matter for the doorbell: the XVF3800 flags the chime as "speech" and reports its direction
  (81 confident readings at the loudspeaker's bearing during the 4 s doorbell, see below).

## Method (no people available)

Everything was played from the laptop's own speakers (Realtek), at one fixed spot ~0.4 m from the array
(world bearing ≈ +133° with the saved calibration). Lessons that matter for anyone repeating this:

- **Windows playback start-up latency is 0.6-2.4 s and varies** (longer after the output has been idle).
  Never use play-call times as ground truth: `tools/speaker_scene.py` finds each clip in the recording by
  envelope correlation instead (match 0.83-0.94).
- **Open one output stream for the whole scene.** Opening a stream per line clicks ~0.6 s before the audio,
  and the XVF3800 flags the click as speech; those blips bridged pauses between lines in babble and merged
  utterances. With one buffer the gaps are clean.
- The laptop's DAC and the array's ADC drift ~0.2% (70 ms over 31 s).

## Measurements

### Latency (laptop speaker -> array -> pipeline)

| What | Value |
|---|---|
| Audio callback timestamp lag (WASAPI reports) | 16 ms |
| Array audio vs. DoA beam energy at a voice onset | DoA energy ~80-100 ms *earlier* than the processed audio |
| DoA speech flag after the voice starts | ~60-100 ms after the audio onset |
| Confident speech (conf ≥ 0.3) after the voice stops | ends ~0.4 s later (0.5 s peak-hold), flag itself lingers ~1.4 s |
| speech_start after the voice onset (real array, quiet) | ~0.2 s |
| Final caption after the voice ends | 0.75-0.89 s (incl. 0.6 s end silence) |

### Captions on the real array (tools/speaker_scene.py -> fixtures/array_tts, scored by tools/stress_test.py)

TTS lines + the doorbell from the laptop speaker, recorded through the array; crowd babble mixed in afterwards
(so the array's DoA track is the quiet-room one). `python tools/stress_test.py --audio-only --scene array_tts`.

| condition | WER before -> now | phantom captions | final lag (median) | doorbell | name called | false alarms |
|---|---|---|---|---|---|---|
| clean | 0.02 -> 0.00 | 0 | 0.89 s | hit 1.5 s | hit 1.1 s | 0 |
| babble +10 dB | 0.04 -> 0.02 | 1 -> 0 | 1.18 s | hit 0.5 s | hit 1.5 s | 0 |
| babble +5 dB | 0.06 -> 0.08 | 1 -> 0 | 1.19 s | hit 0.5 s | hit 3.1 s | 0 |
| babble 0 dB | 1.15 -> 0.58 | 1 -> 0 | 1.20 s | hit 0.5 s | miss -> hit | 0 |

Finals come ~0.3 s later in babble than clean: the array's confident-speech hold (0.5 s) plus the end
silence. The doorbell is detected faster on the array recording (0.5 s) than on the mock (1.5 s) because
the XVF3800's processing makes the chime stand out (PANNs scored it 0.81).

Per-beam directions (README §27): the control interface exposes both focused beams (`AEC_AZIMUTH_VALUES`
+ `AEC_SPENERGY_VALUES`); with one talker the second beam reports a ghost in 0-1% of readings. Two-talker
detection rate not measured (needs two people).

### DoA consistency (one fixed loudspeaker, quiet room)

`python tools/doa_accuracy.py --speaker --angles 132 --distances 0.4 --seconds 8 --trials 6 --auto --no-truth`

| trial | readings | median | spread | within 15° | settle | 2nd beam |
|---|---|---|---|---|---|---|
| 1-6 | 146-153 per 8 s | +132.7° to +133.7° | 0.0-0.5° | 100% | 0.00-0.15 s | 0-1% |

The built-in DoA is rock steady on a single talker in a quiet room: readings sit on 1° steps, the first
confident reading is already right (settle ≈ 0), and the second focused beam almost never invents a second
talker (0-1% of readings). **Not measured: absolute error** (the speaker's true angle is unknown) **and any
noise, distance or angle dependence**: see "Blocked" item 1.

### ODAS gate 1: raw channels on Windows (README §11)

USB descriptors of the unit (firmware 2.0.6):

```
intf 0  AudioControl  INPUT_TERMINAL 0x0101 (USB streaming) 2 ch, INPUT_TERMINAL 0x0405 (echo-cancelling speakerphone) 2 ch
intf 1  AudioStreaming alt 1: 2 ch, 16 bit       intf 2  AudioStreaming alt 1: 2 ch, 16 bit
intf 3  reSpeaker Control (vendor)                intf 4  DFU Factory / DFU Upgrade      intf 5  HID
```

**Result: fail on the current firmware** (no alt setting offers more than 2 channels; Windows lists 2 inputs).
Raw mics exist only with Seeed's 6-channel firmware (ch 0/1 processed, 2-5 raw), flashable over the DFU
interface. Not flashed here (shared demo hardware; see Blocked item 4). Per README §11 the gate fails ->
**built-in DoA stays**. Nothing measured so far argues for ODAS: one talker is tracked to ±0.5°, the doorbell
gets a direction from the built-in DoA, and the second focused beam is available for a second talker.
