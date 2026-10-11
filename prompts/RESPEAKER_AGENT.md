# Kickoff: laptop / ReSpeaker agent

Paste everything below the line into Claude Code on the **Windows demo laptop** (RTX 5000 Ada, ReSpeaker
XVF3800 on USB), started at the repo root. The human will check in rarely: work autonomously, verify your
own work, and leave a clear trail.

---

You own the **hearing module** of HERE: `laptop/` (ReSpeaker XVF3800 → directions, streaming transcription,
doorbell, name called → contract v3 messages to the relay). Another agent owns the iPhone app in `ios/`;
don't touch `ios/`, `belt/` or the contract shapes.

## Read first (in this order)
1. `README.md` (source of truth; §4 core moments, §8 calibration, §10 speed, §11 DoA vs ODAS, §12 contract,
   §13 attribution, §15 alerts, §21 spikes, §24 risks).
2. `CLAUDE.md`, `laptop/CLAUDE.md`, `laptop/audio/CLAUDE.md`, `laptop/speech/CLAUDE.md`, `laptop/sounds/CLAUDE.md`.
3. `laptop/README.md` (setup, the GPU/DLL fixes, measured numbers, loud-hall section) and `CHANGELOG.md`.

## Setup
- `git fetch && git checkout overhaul/v3 && git pull`, then `git checkout -b laptop/respeaker`. Commit small and
  often; push your branch at the end of each task. Never force-push, never push to `main`.
- Python env: `.venv` at the repo root (Python 3.12, torch 2.5.1+cu121; see laptop/README.md if it's missing).
- Baseline: `.\.venv\Scripts\python -m pytest contracts relay laptop tests -q` must pass before you change anything.
- Hardware check: `.\.venv\Scripts\python laptop\audio\xvf3800.py --dump` (if "not found": WinUSB/Zadig note in
  laptop/README.md). Mic capture from a sandboxed process returns silence: run audio commands unsandboxed.

## Ground rules
- **Contract first:** never change message shapes. If you believe one must change, write the proposal in
  `prompts/CONTRACT_PROPOSALS.md` and carry on without it.
- Keep Adil's device code working: `python laptop/audio/xvf3800.py` must keep running standalone.
- All tunables in `laptop/config.yaml`; secrets only in `laptop/.env`.
- After every change: the full test suite, plus `python tools/stress_test.py --audio-only` whenever you touch
  speech, sounds or segmentation. Never let the stress table get worse without writing down why.
- Never copy code from Ranger (`../Treehacks2025`); it has no license.
- Write measurements into files (SPIKE.md, CHANGELOG.md), not only into chat.

## Tasks, in order (each ends with: tests pass, CHANGELOG entry, commit, push)

1. **Real-array smoke run.** `python -m laptop --real --offline` with someone (or a phone playing speech) at a
   few positions. Confirm `sources`, `speech_start`, `utterance` look sane. If no one is around, play speech
   from the laptop's own speakers and note the limitation.
2. **Wearer's own voice.** The wearer stands within 0.5 m of the array; their speech must not be captioned as
   someone else or buzz the belt. Add `audio.wearer_bearing_deg` + `wearer_ignore_deg` (and a
   `python -m laptop.audio.calibrate --wearer` step: talk 5 s from the wearer's mark, store the bearing).
   Utterances whose bearing is within the window are dropped (retract the speech_start). Unit-test it.
3. **Names for Whisper.** Pass `user_name` + aliases (from `config`, or the `names:` / `mock:` defaults) to
   faster-whisper as `hotwords` / `initial_prompt`. Measure with the stress test: the 0 dB name-called miss
   should improve; WER must not get worse.
4. **Hallucination filter.** Drop finals that are classic Whisper inventions on noise ("Thank you.",
   "Thanks for watching!", single filler words) unless the array heard real speech for > 1 s, and use the
   segment-level `avg_logprob` / `compression_ratio`. Add a stress condition with pure babble and no talker:
   0 captions expected.
5. **Laughter tone from PANNs.** The sounds classifier already scores "laughter" every 0.5 s; feed recent
   laughter into the speech tone (`laughing`). Test with a mixed clip.
6. **Enroll from the array.** `python -m laptop.sounds.enroll doorbell --seconds 5`: record from the array,
   trim to the sound, save to `fixtures/sounds/`, update config. Check it with `stress_test.py`.
7. **DoA accuracy spike tool + report.** `tools/doa_accuracy.py`: guided prompts ("stand at 45°, talk 10 s"),
   positions every 45° at 1 m and 1.5 m, optionally with crowd noise playing; prints the error table, settle
   time, and how often the second beam sees a second talker. Write results to `laptop/audio/SPIKE.md`
   (README §21). If nobody can walk around, do what you can with the laptop speaker at fixed spots and say so.
8. **Run report.** `tools/run_report.py logs/laptop.jsonl`: latency histograms (onset → speech_start, speech end
   → final), counts of captions/alerts/retractions per session, engine/heartbeat problems. Numbers for the pitch.
9. **Operator dashboard (terminal).** A small live view (direction compass, input level, VAD state, current
   caption, alerts, relay status) behind `python -m laptop --real --dashboard`. Keep it optional; no new
   heavy dependencies (`rich` is fine).
10. **Record real fixtures** listed in `fixtures/README.md` if people are available, then add them to
    `stress_test.py` as golden conditions with pass thresholds.
11. **ODAS gate 1 only** (README §11): can the XVF3800 expose raw channels on Windows? Answer in SPIKE.md.
    Don't go further unless it passes in under 3 hours.

## When you're blocked
Hardware missing, a person needed, or a decision that's the team's: write it under "Blocked / needs a human"
at the top of `laptop/audio/SPIKE.md` with exactly what to do, then move to the next task. Don't stop.

## Done means
Each finished task: code + tests + CHANGELOG line + pushed commit. At the end, a short summary at the top of
`laptop/audio/SPIKE.md`: what works on the real array, measured numbers, what's still unverified.
