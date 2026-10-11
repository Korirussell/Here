You own the **ODAS track** of HERE's hearing module (laptop/). Kori is asleep: work through the night without
asking questions, and leave a clear morning summary. Another agent may work on ios/; don't touch `ios/`, `belt/`,
or the contract shapes.

## Read first (in this order)
1. `laptop/audio/DOA_VS_ODAS.md` (the plan you are executing: Track B + the shared infrastructure, with critiques).
2. `README.md` §6, §10, §11, §12.5; `CLAUDE.md`; `laptop/CLAUDE.md`; `laptop/audio/CLAUDE.md`.
3. `laptop/audio/SPIKE.md` (top sections: firmware 2.1.1 6-ch, beams, fixed beams, the unfitted SRP frame) and
   `CHANGELOG.md` 2026-10-08/09.
4. `laptop/audio/odas.py` (OdasSource stub), `laptop/audio/builtin_doa.py`, `laptop/audio/localize.py`, `laptop/app.py`.

## Setup
- Branch: `git checkout laptop/respeaker && git pull`, then `git checkout -b laptop/odas`. Commit small and often,
  push the branch after each milestone. Never force-push, never push to `main` or `overhaul/v3`.
- Baseline first: `.\.venv\Scripts\python -m pytest contracts relay laptop tests -q` (161 pass) and
  `python tools/stress_test.py --golden` (7/7). Both must still pass at the end.
- Hardware: ReSpeaker XVF3800 on USB, **firmware 2.1.1 16 kHz 6-channel** (ch 0-1 outputs, ch 2-5 raw mics).
  Laptop speakers sit at world **180 deg** from the array; the room is quiet; nobody will talk to you.
  Run audio/USB commands unsandboxed (sandboxed capture returns silence). Only one process may hold the array's
  control interface; after any killed run, reset with: `AEC_FIXEDBEAMSONOFF 0`, `AUDIO_MGR_OP_L 8 0`, `AUDIO_MGR_OP_R 7 3`
  (or just start `python -m laptop --real --no-beams` once).
- WSL2 **Ubuntu** is installed. Kori should have run the pre-sleep command below; if `sudo` asks for a password,
  don't wait: build what you can without root (e.g. deps from source into `~/.local`) or record it as blocked.

## Rules
- Contract first: never change message shapes (proposals go to `prompts/CONTRACT_PROPOSALS.md`).
- All tunables in `laptop/config.yaml` (`audio.odas:` section); secrets only in `.env`.
- Default behaviour must not change: `audio.doa_source: builtin` stays the default; ODAS is opt-in
  (`audio.doa_source: odas` / `--odas`). Every component still runs without hardware (fixtures).
- Don't flash firmware, don't install Windows software needing admin, don't reboot, don't change WSL config
  that needs `wsl --shutdown` unless nothing else is running and you note it.
- Code from `reference/` (Ranger) is never copied. ODAS is MIT: build and run it, don't vendor its source into
  our repo (a setup script that clones a pinned commit is fine).
- After every change: full tests; whenever speech/sounds/segmentation are touched also `tools/stress_test.py --golden`.
  Write measurements into files (SPIKE.md, CHANGELOG.md, DOA_VS_ODAS.md), not only into chat.
- Volume: you'll play test sounds through the laptop speakers; keep sessions short, gain ~1.5x TTS level.

## Tasks, in order (each ends with: tests pass, CHANGELOG line, commit, push)

1. **6-channel recorder + raw fixtures.** `--record` writes `raw.wav` (4 raw mics, int16, with a configurable
   digital gain noted in meta) next to `audio.wav`; FixturePlayer exposes `raw` for replay; meta records firmware,
   routing, fixed beams, calibration. Record `fixtures/raw_speaker_180` (TTS lines + baby_cry + doorbell from the
   laptop speaker, `tools/speaker_scene.py` extended for raw). Unit tests.
2. **Mic channel order / frame.** Using the raw recording at 180 and the calibrated beam frame, find which channel
   permutation of the 4 raw mics + rotation makes SRP-PHAT (localize.py) agree with 180 (test all 24 permutations;
   report peak coherence for each). A single position can't separate a mirror image: keep the two best candidates,
   pick the one consistent with AEC_MIC_ARRAY_GEO's order, and write a 2-minute morning check for Kori
   (talk from front and right: `python -m laptop.audio.localize --check`). Store the result in config
   (`audio.raw_mic_order`), used by SRP and ODAS alike.
3. **Rotation scenes (the night trick, DOA_VS_ODAS.md "shared infrastructure" 2).** `tools/make_raw_scene.py`:
   from real raw recordings at 180, build real-acoustics raw-mic scenes by channel rotation: single sources at
   0/90/180/-90, talker + baby (front + behind), two talkers (90 apart and 180 apart), with ground truth.
4. **ODAS in WSL.** `tools/odas/setup_wsl.sh` (clone introlab/odas at a pinned commit, build with cmake, check
   deps; WSL has CMake 4.2: if ODAS's CMakeLists declares an old minimum, configure with
   `-DCMAKE_POLICY_VERSION_MINIMUM=3.5`), `laptop/audio/odas/here.cfg` (XVF3800 mic geometry from AEC_MIC_ARRAY_GEO in the fitted order,
   16 kHz, 4 ch, hop 128 or 256, tracking for up to 4 sources, sinks: tracked JSON to a socket, separated +
   post-filtered audio to sockets). First run offline on the rotation scenes (raw file source) and check the
   tracked directions against truth.
5. **Live bridge.** Windows capture (the ReSpeaker class, 6 ch) -> raw ch -> TCP -> WSL socat -> FIFO -> odaslive.
   Measure the end-to-end latency (onset in the raw audio -> first tracked source on Windows). WSL2 networking:
   use the Windows host IP from WSL (or mirrored mode if already enabled); document what works.
6. **OdasSource for real.** Turn the stub in `laptop/audio/odas.py` into a working DirectionSource: tracked JSON
   -> contract `sources` (stable ids, world bearings via calibration, energy/confidence/speech from ODAS activity),
   heartbeat `doa_source: odas`; problems surfaced in heartbeat detail when the bridge is down.
7. **Audio per track: two variants.** (a) ODAS separated/post-filtered streams -> one speech + sound pipeline per
   track (app.py's multi-stream support); (b) **hybrid**: ODAS tracks steer the XVF fixed beams
   (`AEC_FIXEDBEAMSAZIMUTH_VALUES`, with hysteresis) and captions come from the beams as today. Config switch.
8. **Comparison harness.** `tools/doa_compare.py`: localization error, ID stability, ghosts/min, two-source
   detection, latency for builtin vs ODAS vs hybrid on the rotation scenes (raw consumers) and live laptop-speaker
   runs (all three); captions/alerts metrics where audio per source exists (reuse stress_test scorers).
   Replay `fixtures/two_real2` (local, Kori's live talker + baby test, 2-ch beams only: builtin baseline) for reference.
9. **Report.** Fill the results into `laptop/audio/DOA_VS_ODAS.md` (table + recommendation against the decision
   rule) and a morning summary at the top of `laptop/audio/SPIKE.md`: what works, numbers, what's unverified,
   exactly what Kori must do (with commands) to confirm in < 10 minutes.

## When you're blocked
Write it under "Blocked / needs a human" at the top of `laptop/audio/SPIKE.md` with exactly what to do, then move
to the next task. Don't stop, don't wait.

## Done means
Branch `laptop/odas` pushed; builtin path unchanged and golden 7/7; ODAS runs offline on the rotation scenes and,
if the bridge works, live; the comparison table exists with a recommendation; the morning summary is honest about
what was simulated (rotation scenes, laptop speaker at one spot) and what was measured live.

---

## Pre-sleep checklist for Kori (2 minutes, before starting the agent)

1. Laptop speakers at 180 deg from the array (behind it), volume ~50%, array on USB, room quiet.
2. (Done 2026-10-09: all deps installed.) Install ODAS's build deps once (asks for your Linux password):
   `wsl -d Ubuntu -- sudo apt-get update; wsl -d Ubuntu -- sudo apt-get install -y build-essential cmake git libfftw3-dev libconfig-dev libasound2-dev libpulse-dev socat`
3. Power settings: plugged in, sleep disabled (Settings > System > Power: "never").
4. Start Claude Code in `C:\Users\RUSSK087\Coding\shh` and paste this file's contents above the checklist (or:
   "Read prompts/ODAS_AGENT.md and do it"). Allow audio/USB commands to run unsandboxed when asked.
