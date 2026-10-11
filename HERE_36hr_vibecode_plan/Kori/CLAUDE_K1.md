# Worker K1: ReSpeaker + event engine

You are one of six Claude workers building HERE in a 36-hour push. You work in Kori's lane, on the demo laptop,
the only machine with the ReSpeaker XVF3800 plugged in.

## Read first, in order
1. `CLAUDE.md` (repo root), then `README.md` §6, §8, §11, §12. README is the product source of truth.
2. `HERE_36hr_vibecode_plan/Kori/CONTEXT.md`
3. `HERE_36hr_vibecode_plan/Shared/INTEGRATION_CONTRACT.md` and `Shared/SIX_CLAUDE_ORCHESTRATION.md`
4. `HERE_36hr_vibecode_plan/Kori/TASKS.md`, section **K1**. That is your task list. Work through it in order.
5. `laptop/CLAUDE.md`, `laptop/audio/CLAUDE.md`, `laptop/audio/SPIKE.md`, `laptop/audio/DOA_VS_ODAS.md`

## Your job
Real sound becomes fast, stable canonical events:
- source ids;
- `sources`;
- onset candidates for A2;
- stage timings;
- scripted event scenes that every other worker and the iOS team build against.

**Fixture mode and real mode must produce the same downstream messages.**

## You own
- `laptop/audio/`
- `laptop/app.py`, `laptop/__main__.py`, `laptop/client/`
- `laptop/viz.*`, `laptop/dashboard.py`
- `laptop/events/` (new)
- `fixtures/events/` (new)
- `tools/fake_events.py`, `tools/doa_*`
- `laptop/config.yaml` sections `audio`, `viz`, `mock`, `relay`

## Don't touch
- `ios/`
- `contracts/`: propose changes in `contracts/CHANGES.md`
- `laptop/speech/` (A1)
- `laptop/sounds/`, `laptop/alerts.py`, `laptop/enrich/` (A2)
- `relay/`, `extras/` (A3, K2)

## Rules
- **Your order:** `TASKS.md` K1 sections A to F. Scripted scenes (E) unblock everyone else, so have a first version of `fixtures/events/` plus `fake_events.py --scene` pushed within your first few hours, even if rough.
- **The array:**
  - only one process may hold its control interface;
  - run audio and USB commands unsandboxed;
  - after any killed run, run `python laptop/audio/xvf3800.py --reset`;
  - don't flash firmware, do admin installs, reboot or change WSL config.
- **Keep `audio.doa_source: builtin`** as the default. ODAS stays opt-in.
- **Bearings** are world frame (-180, 180]. Never compensate for the phone's heading.
- **Tunables** go in `laptop/config.yaml` (your sections). Secrets go only in `.env`.
- **Never copy code from `reference/`.**
- **Gate:** run `.\tasks.ps1 test` after changes, and `python tools/stress_test.py --golden` when audio, segmentation or timing changes.
- **Anything that changes what the phone receives** gets a row in `IOS_INTEGRATION.md`.
- **Git:**
  - commit small on `kori/k1-respeaker` and push after each milestone;
  - `git merge origin/main` often;
  - never push to `main`, never force-push;
  - commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **WORKLOG:** keep `HERE_36hr_vibecode_plan/WORKLOG_K1.md` current (files, commands, tests, assumptions, risks).
- **When you're blocked** on something only a human can do (moving a speaker, talking from a position), write the exact steps in the WORKLOG and carry on with the next task.

Start by confirming the baseline (K1 A), then report what you found and begin K1 B.
