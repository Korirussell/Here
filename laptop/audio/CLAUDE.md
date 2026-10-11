# laptop/audio (ReSpeaker XVF3800)

Part of HERE. Read ../../README.md §8 (calibration), §11 (built-in DoA vs ODAS), §12.5 (`sources`) first.
Owner: Kori (K1 in the 36-hour plan; the array is on Kori's laptop). Written by Adil. Formerly `audio_here/` (v1); the device logic is unchanged, only the plumbing moved in-process.

## Job
The array's processed 16 kHz output (USB capture channel 0) goes to speech and sounds in-process, and its
direction of arrival is polled over the USB control interface (vendor control transfers, WinUSB) at 20 Hz,
calibrated to the world frame, as contract `sources` (one source, id 0; the second focused beam as id 1 when
`builtin_beams_as_sources` is on). With the 6-channel firmware the raw mics are buffered so sounds can ask
`locate(t0, t1, label)` for the direction of a doorbell (SRP-PHAT). Draws directions on the LED ring.

Beams mode (`audio.capture: beams`, the default): the two focused beams are routed to USB ch 0/1
(`AUDIO_MGR_OP_L/R` = (6,0)/(6,1), volatile, set at start, defaults (8,0)/(7,3) restored on exit) and the
input becomes two streams; `sources` id 0/1 carry each beam's direction and speech energy (beam 1 only when
>= 30 deg from beam 0). If a run is killed, the next start sets the routing again (or power-cycle the array).

## Inputs / outputs
`ReSpeaker` implements both interfaces in `base.py`: `on_sources(t, [Source])` and `on_audio(t, int16 pcm)`.
`FixturePlayer`, `MicAudio` + `SliderDirections` (mock.py) and `OdasSource` (odas.py, needs the raw mics + WSL2) are drop-in.

## Files
- `xvf3800.py`: USB control read/write, register table, `sample()`, standalone readout (ex respeaker_probe.py)
- `builtin_doa.py`: the device as a DirectionSource + AudioSource (ex audio_here/__main__.py)
- `localize.py`: SRP-PHAT, raw-audio ring buffer + `RawLocator`, mic order (`--fit`, `--check`), `SrpTracker`,
  `EventBearings` (one bearing per sound), `SpeechBearings` (fixed beams: the talker's bearing per utterance in the
  beam's half of the room, `locate_speech`; `audio.speech_srp`)
- `ring.py`: LED ring drawing (effect 5, `LED_RING_COLOR`), `RingPlanner` (main marker = SRP checked against the
  free-running beam, second = ODAS / SRP leader track), `LeadDoa` (2-ch firmware), and its CLI
- `calibrate.py`: guided array calibration -> `laptop/config.yaml` (`--verify`: 4-point check, `--four-point`)
- `odas.py`: ODAS track B (off unless `audio.doa_source: odas` / `--odas`): `OdasBridge` (TCP to odaslive in WSL2,
  started by HERE), `OdasSource` (tracks -> `sources`, audio per track: hybrid beams / separated / postfiltered),
  `BeamSteer`. `odas_cfg.py` writes ODAS's config from `audio.odas` (`odas/here.cfg` = the generated live one);
  `odas_wsl.py --offline <raw fixture>` runs ODAS on a recording. Build: `tools/odas/setup_wsl.sh`. Results and
  the decision: `DOA_VS_ODAS.md`.
- `mock.py`, `base.py`: no-hardware inputs; interfaces, DirectionHistory, SourcesThrottle, AudioFanout
- `config.py`: exposes `config.yaml`'s `audio:` section as UPPER_CASE names (edit the YAML)

## Run
Calibrate once: `python -m laptop.audio.calibrate`. Live readout without anything else:
`python laptop/audio/xvf3800.py` (`--dump`, `--log f.jsonl`). The full module: `python -m laptop --real`.
Ring: `python -m laptop.audio.ring --follow|--sweep|--firmware`. Beam/SRP fit: `python -m laptop.audio.localize --check`.
Tests: `python -m pytest laptop/tests/test_builtin_doa.py laptop/tests/test_localize.py laptop/tests/test_ring.py`.

## Device notes (firmware 2.0.6)
- `DOA_VALUE` = [deg 0..359, speech flag]; it holds the last direction in silence, so check `speech`. It is the
  active focused beam's direction: with fixed beams it names a beam, not the sound (+-90 beams, speaker at 177
  -> +92 / -88). The ring uses our SRP instead (`ring.RingPlanner`); `AEC_AZIMUTH_VALUES[2]` (free-running
  beam) jumps to new sounds in ~0.05 s but visits reflections.
- Beam azimuths (`AEC_AZIMUTH_VALUES`, radians) and SRP angles are in the mic-geometry frame;
  `aec_to_device_offset_deg/sign` maps them (fit with `localize --check`).
- LED registers (Seeed xvf_host.py): `LED_EFFECT` 20/12, `LED_RING_COLOR` 20/19. Writes drop the 0x80 read bit.
- Control reads sometimes return status 64 (retry) or stall with a pipe error; both are retried; reads and
  writes share a lock.
- Per-beam speech energy arrives in bursts; `confidence` uses a 0.5 s peak-hold (tune). `sources.energy` maps
  the processed audio's dBFS (`energy_dbfs_low..high`) to 0..1.
- 6-channel firmware: ch 0/1 processed, ch 2-5 raw mics. With 2 channels `locate()` is off and the heartbeat says so.
  Raw mics default to category 1 (unamplified, ~4 LSB of ambient); HERE routes category 3 (after
  `AUDIO_MGR_MIC_GAIN`, +39 dB) while running (`audio.raw_mic_category`). Mic order: `audio.raw_mic_order`
  (`localize --fit <raw fixture>`, `localize --check`). Reset after a killed run: `python laptop/audio/xvf3800.py --reset`.
- SRP uses PHAT-beta 0.5: full PHAT lost tonal sounds (doorbells) in simulation.
- Mic capture from a sandboxed process returns digital silence. Run from a normal terminal.

## Rules
- Output world bearings, clockwise, (-180, 180]; the phone does every other conversion.
- Tunables in `laptop/config.yaml` (`audio:`). Keep `xvf3800.py` runnable as a plain script.

## Current milestone
On hardware (SPIKE.md): DoA steady to ±0.5° on one talker, settle ~0, second-beam ghosts 0-1%; doorbell
direction from the built-in DoA; ODAS gate 1 fails on firmware 2.0.6 (2 ch). `calibrate --wearer` added.
Next: accuracy at 45° steps with people and crowd noise (`tools/doa_accuracy.py`), wearer calibration on the rig.
