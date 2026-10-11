# Built-in DoA vs ODAS: two complete approaches, one fair comparison (README §11)

Status 2026-10-09: the array runs Seeed firmware **2.1.1, 6 channels** (ch 1-2 = two routable outputs, ch 3-6 =
the four raw mics). Beams mode with fixed beams captions a talker in front through a baby crying behind (live
test); auto-tracking beams failed that test; SRP-PHAT on the raw mics runs (frame fitted overnight: identity mic order,
177.8 for a source at 177.2; the old -98 didn't reproduce). Details: laptop/audio/SPIKE.md, CHANGELOG 2026-10-09.

## Results (overnight 2026-10-09, branch laptop/odas) and recommendation

**Recommendation: keep Track A for the demo** (fixed beams + the built-in DoA, today's default). ODAS fails four of
the five decision-rule gates below. It stays opt-in (`--odas`) and works: worth showing in Q&A, not worth the booth
risk. The most promising next step for "beams follow people" is our own SRP tracker (Track A step 2): it's more
accurate than ODAS on the same audio, but it needs better ghost gating first.

What was measured how (be careful with these numbers):
- **Rotation scenes** (`tools/make_raw_scene.py`): real raw-mic audio of ONE recording (laptop speakers at world 180,
  `fixtures/raw_speaker_180`), rotated by mic permutation; two-source scenes are sums of two rotated pieces. Real
  acoustics and noise, but one room position and TTS voices. Truth = nominal 180 (+90 k); the speaker really sits at
  ~174-178 (SRP 173-175, device DoA 178), so every method carries a few degrees of offset.
- **Live**: the real array, the same laptop speakers at 180, one source only (nobody else was there). The "speaker"
  is the laptop's two stereo speakers ~30 cm apart (not a point source: ODAS sometimes splits it into two tracks).
- Builtin and hybrid can't run on rotation scenes (the XVF3800 beams can't be simulated); `srp` = our SRP-PHAT top-2
  peaks + `SrpTracker` on the raw mics, as Track A's planned tracker.

### Rotation scenes (`python tools/doa_compare.py --scenes`, ODAS tuned: probMin 0.3)

| scene | method | detected (per source) | median error | two-source | ghosts/min | stale s/min | id switches/min | ids | fresh-onset latency (median, n) |
|---|---|---|---|---|---|---|---|---|---|
| single_0 | srp | 73% | 6.5 | - | 59.2 | 0.0 | 0.0 | 3 | 0.06 s (9) |
| single_0 | odas | 82% | 14.4 | - | 3.0 | 8.4 | 0.0 | 1 | 0.62 s (2) |
| single_90 | srp | 71% | 7.0 | - | 28.9 | 0.0 | 7.6 | 5 | 0.06 s (9) |
| single_90 | odas | 83% | 11.6 | - | 1.5 | 5.3 | 0.0 | 1 | 0.05 s (4) |
| single_180 | srp | 74% | 5.0 | - | 34.9 | 0.2 | 1.5 | 4 | 0.06 s (8) |
| single_180 | odas | 75% | 11.6 | - | 3.0 | 8.1 | 0.0 | 1 | 1.32 s (2) |
| single_m90 | srp | 72% | 6.6 | - | 34.9 | 0.0 | 0.0 | 4 | 0.06 s (9) |
| single_m90 | odas | 69% | 9.4 | - | 0.0 | 4.9 | 0.0 | 2 | 0.64 s (2) |
| talker_baby | srp | 69% / 58% | 5.1 | 34% | 11.2 | 1.8 | 0.0 | 4 | 0.04 s (7) |
| talker_baby | odas | 90% / 89% | 10.2 | **68%** | 0.0 | 6.7 | 0.0 | 2 | 0.1 s (4) |
| two_talkers_90 | srp | 57% / 56% | 5.7 | 16% | 38.3 | 0.0 | 3.2 | 8 | 0.3 s (7) |
| two_talkers_90 | odas | 94% / 80% | 15.2 | **68%** | 0.0 | 27.8 | 0.0 | 2 | 0.15 s (3) |
| two_talkers_180 | srp | 71% / 82% | 4.5 | 72% | 12.8 | 3.5 | 0.0 | 5 | 0.2 s (6) |
| two_talkers_180 | odas | 95% / 76% | 8.3 | 62% | 0.0 | 29.7 | 0.0 | 3 | 0.15 s (3) |

Detected = share of a source's active time with a report within 25 deg. Ghosts = confident reports > 35 deg from
every real source (episodes/min). Stale = confident reports at a real source's bearing while it is silent (> 0.6 s
after it stopped), seconds per minute. Fresh-onset latency counts only onsets with nothing at that bearing in the
0.5 s before (ODAS keeps tracks alive ~2 s, so most onsets aren't fresh: small n).

- ODAS: the only method holding two sources at once most of the time, stable ids (0 switches), no wrong-direction
  ghosts. But: 10-15 deg error, tracks linger 2 s after people stop (5-30 s/min of stale direction), and a new
  source takes 0.1-1.3 s to get a track.
- SRP + tracker: 5-7 deg error and ~50 ms onsets, but ghosts from the room's noise (the laptop fan) and noisier ids.
- Tuning sweep (5 scenes): probMin 0.5 -> 0.3 cut the error 14.8 -> 11.6 deg at the same detection (85%) and two-source
  rate (66%). Shorter track life (N_inactive 100) cut stale time 16 -> 3 s/min but two-source fell to 5%; frame 512,
  particle filter and lower activity thresholds were worse overall.

### Live, real array, laptop speaker at 180 (`python tools/doa_compare.py --live`)

| method | detected | median error | ghosts/min | stale s/min | measured bearing | onset -> first correct source on Windows | captions WER | phantoms |
|---|---|---|---|---|---|---|---|---|
| builtin (processed, DOA_VALUE + 2nd beam) | 100% | 2.4 | 10.0 | 0.3 | 178.3 | 0.13 s (7/7 onsets) | 0.07 | 0 |
| fixed beams [0, 180] (today's default) | 100% | (fixed) | 2.9 | 0.0 | -179.2 | 0.09 s (7/7) | **0.03** | 0 |
| odas / hybrid (probMin 0.5) | 68-74% | 13.0-15.2 | 2.5-3.3 | 7.3-8.5 | 159-160 | 1.26 s (2 fresh onsets) | 0.09-0.16 | 0-2 |
| odas / hybrid (probMin 0.3, beams >= 45 deg apart) | 72% | 13.9 | 1.3 | 4.8 | 161.7 | 1.04 s (2 fresh onsets) | 0.09 | 0 |

- **ODAS transport latency is negligible**: a frame reaches Windows within the timestamp precision of its newest
  sample (median -1..-2 ms: WASAPI block times are a few ms late). `sources` latency is ODAS's track birth, not the
  bridge. WSL2 mirrored networking: ODAS connects straight to Windows' 127.0.0.1 (no socat, no FIFO). odaslive needs
  ~6-8 s to start inside WSL.
- Live ODAS reads the speaker at ~160 where offline ODAS on the original recording gives ~180 (tracks at -171 and
  +168) and SRP on the same live audio 170: content-dependent, consistent with two speakers at ~5 dB in-band SNR.
- Hybrid steering, first try: ODAS split the speaker into two drifting tracks and steered **both** beams behind the
  array (front talker unserved, 60 ghost episodes/min). `BeamSteer` now keeps the beams >= 45 deg apart; the rerun
  kept beam 0 in front while beam 1 followed the track between 140 and 180.

### Captions from ODAS's own audio (`python tools/doa_compare.py --captions`, rotation scenes, probMin 0.5)

| scene | unseparated mix (1 stream) | ODAS separated (per track) | ODAS post-filtered (per track) |
|---|---|---|---|
| talker_baby | WER 0.23, 2 phantoms | 0.32, 1 phantom | 0.43, 0 |
| two_talkers_90 | 0.61 | **0.21** | 0.37 |
| two_talkers_180 | 0.50 | 0.52, 1 phantom | 0.36 |

Name called was hit every time. **No baby-crying alert in any talker_baby run** (with ODAS audio). Separation helps
only for two talkers 90 deg apart. For comparison, the beams on the synthetic two-source golden scenes reach WER 0.00
(golden gate), and live fixed beams captioned a real talker through the baby at 180 word for word (fixtures/two_real2).

### Decision rule (DOA_VS_ODAS.md) against the numbers

| gate | ODAS / hybrid | verdict |
|---|---|---|
| two-source detection >= 90% (vs Track A) | 62-68% on rotation scenes (srp 16-72%; Track A's beams: fixed by config, not detected) | fail |
| localization error <= 10 deg median | 8.3-15.2 scenes, 13.9 live (device DoA 2.4, srp 4.5-7.0) | fail |
| ghost sources <= 1/min | 0-3 wrong-direction/min, plus 5-30 s/min stale tracks; live 1.3 (60 before the beam fix) | borderline |
| `sources` latency < 250 ms | transport ~0, but a new source's first track 0.1-1.3 s (small n) | fail for new sources |
| live talker + baby captions >= fixed beams | not testable live (one speaker); hybrid 0.09 vs fixed 0.03 on one source; ODAS audio 0.21-0.52 | fail |

### LED ring (2026-10-09 day, laptop speaker at 177, `ring.RingPlanner`)

DOA_VALUE is the active focused beam's direction: with fixed beams it names a beam, not the sound (beams at +-90
-> +92 / -88 for a speaker at 177). The "builtin" row above ran with processed capture (auto-tracking beams), so
its 2.4 deg holds there; in the beams-mode demo config DOA_VALUE is front or back only.

| main marker | lit (share of sound) | right when lit (within 20) |
|---|---|---|
| DOA_VALUE, fixed beams at +-90 | ~100% | 0% |
| free-running beam, single reading | 63-69% | 67-77% |
| free-running beam, 0.5 s cluster | 82-88% | 74-84% |
| SRP tracks (raw mics) | 75-77% | 77-93% |
| **SRP, vetoed by the cluster (> 30 deg apart)** (offline on the same runs) | 61-74% | 88-95% |
| the same, live through the ring (3 runs incl. one with ODAS running) | 66-82% | 87-95% |

Second marker, one real source (every lit sample is a phantom): SRP second track 17-21% of the sound before
the "has been the loudest" rule, 0% after; ODAS tracks 46% + in silence before, 3% / 0 after. Two sources
(rotation scenes): both markers right 11-46% (SRP) vs 60-69% (ODAS tracks): the two-LED demo wants `--odas`.

### Not verified (needs people or a second loudspeaker)

- Two real sources live (talker + baby, two talkers) with ODAS / hybrid: the gate that matters most.
- The mic order's mirror check (front + right): `python -m laptop.audio.localize --check`.
- Moving talkers (ODAS's strength), more than two sources, a real crowd.

The question for the demo: **who decides where the sources are**, the XVF3800's own beam steering (+ our help),
or ODAS tracking on the raw mics? Build both to the same contract, compare on the same audio, pick with numbers.

---

## Track A: "DoA+" (the XVF3800 does the audio, we do the tracking)

What exists: `audio.capture: beams`, fixed beams (`audio.fixed_beams`), per-beam speech/sound pipelines, masking,
cross-talk, sound arbiter, non-speech guard (all in `laptop/`).

To finish:
1. **Fit the SRP frame** (raw channel order / mirror) with `localize --check` or a calibrate.py step.
2. **Tracker:** SRP-PHAT on the raw mics every ~100 ms, top-2 peaks, a small tracker (gating 20 deg, birth after
   3 hits, death after 1.5 s, IDs). Steer `AEC_FIXEDBEAMSAZIMUTH_VALUES` to the two tracks (hysteresis so beams
   don't jump mid-sentence). `sources` id = track id.
3. **Per-direction labels from the raw mics:** delay-and-sum toward each track, PANNs on that, so "this direction
   is crying, that one is talking" no longer comes from the leaky beam audio.
4. Keep `fixed_beams` from config as the safe fallback (known demo positions).

Critique of Track A:
- **+** Best audio we can get: the XVF3800's AEC / noise suppression / beamformer run on-chip, no extra process,
  no Linux. Already demo-proven in the fixed configuration.
- **-** Steering on-chip beams from the host adds a control loop over USB (~20 Hz, shared with the LED ring and DoA
  poll); beam re-steering latency is unmeasured. A beam moving mid-utterance could chop a caption.
- **-** Our SRP tracker is home-made; ODAS's tracker (Kalman/particle) is more mature.
- **-** Only two beams, ever.

## Track B: ODAS (introlab/odas) on the raw mics

ODAS = localization (SRP-PHAT), tracking (Kalman, several sources with IDs), separation + post-filtering (one
audio stream per tracked source). Linux only -> WSL2 Ubuntu (installed on this laptop).

Pipeline:
```
Windows: python captures 6 ch (laptop/audio) -> raw ch 3-6 -> TCP -> WSL: socat -> FIFO -> odaslive (raw file source)
odaslive sinks: tracked sources (JSON, socket) -> laptop/audio/odas.py OdasSource -> contract `sources` (ids)
               separated/post-filtered audio per source (socket) -> one speech + sound pipeline per track
```
Every downstream piece already exists (`OdasSource` stub, per-stream pipelines, masking, guards).

Critique of Track B:
- **+** Real multi-source tracking with IDs (more than 2 sources, moving people), the README §11 "head-to-head"
  finally done properly; a ODAS Studio view is a nice Q&A screen.
- **-** **Demo risk:** WSL2 VM + socat + FIFO + odaslive + network hops between Windows and WSL. More moving parts
  at the booth; WSL2 networking (localhost forwarding) and clock alignment between the two OSes need care.
- **-** **Audio quality:** ODAS separation on a 4-mic, 6.6 cm array is weak (a few dB), with musical noise; the raw
  mics skip the XVF3800's AEC/NS. Captions from ODAS's separated audio may be *worse* than from the XVF beams.
  A hybrid is likely best: **ODAS for directions/tracks, XVF fixed beams steered to ODAS's tracks for audio**
  (Track A step 2 with ODAS as the tracker). Plan for it.
- **-** Raw mics are quiet (-75..-80 dBFS in the test): ODAS's thresholds assume normal levels; add gain.
- **-** Mic geometry / channel order: same unfitted-frame problem as Track A; ODAS needs the exact positions.
- **-** Latency: ODAS frames (hop 128-256) + TCP + FIFO; must stay well under README §10's 250 ms for `sources`.

## Shared infrastructure (build once, both tracks use it)

1. **6-channel recorder:** `--record` also writes `raw.wav` (4 raw mics) next to `audio.wav` (the beams), plus
   meta (firmware, routing, fixed beams, calibration). Both tracks replay the same fixture.
2. **Real multi-source scenes without people (the night trick):** with a square array, permuting the 4 raw mic
   channels = rotating the source by 90 deg (exact far field; the room isn't rotated, acceptable). So one real
   recording of the laptop speaker at 180 also gives real sources at 90, 0 and -90; summing two such recordings
   (different content, different rotation) gives a real-acoustics two-source raw-mic scene with known truth.
   (Only usable for raw-mic consumers: ODAS and our SRP. The XVF beams can't be simulated this way.)
3. **Comparison harness** `tools/doa_compare.py`: one scorer for any `sources` stream: localization error, track
   ID stability, ghost sources, latency, two-source detection rate; plus the caption/alert metrics of
   `stress_test.py` when audio per source is available. Output: a table in laptop/audio/SPIKE.md.

## Decision rule (README §11, made concrete)

Switch the demo to ODAS (or the hybrid) only if, on the same scenes: two-source detection >= 90% (vs Track A),
localization error <= 10 deg median, ghost sources <= 1/min, `sources` latency < 250 ms, and the live talker +
crying-baby test gives captions at least as good as fixed beams. Otherwise Track A, no regrets.

## What can't happen unattended (be honest in the morning summary)

- Anything needing a person at a second position: SRP/ODAS frame fit sign check, real two-talker tests.
  The rotation trick gives geometry; a 2-minute morning check confirms it (talk from front and right).
- Firmware flashing, admin installs, reboots: blocked or unsafe unattended.
- The overnight agent plays sounds through the laptop speakers (quiet room needed; the laptop sits at 180).
