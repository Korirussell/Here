# HERE (Hearing Environmental Reality Engine)

Read **README.md** first: it is the source of truth (if code and README disagree, the README wins or gets
updated on purpose). The contract is README §12, implemented in `contracts/`.

## Layout (README §22)

| Folder | Job | Run | Brief |
|---|---|---|---|
| `contracts/` | v3 message schemas + Python / Swift / TS types + examples | `python -m pytest contracts` | contracts/CLAUDE.md |
| `relay/` | Vultr WebSocket relay (wss via Caddy) | `python -m relay` | relay/CLAUDE.md |
| `laptop/` | hearing module: ReSpeaker, speech, sounds, alerts | `python -m laptop --mock` | laptop/CLAUDE.md |
| `ios/` | the glasses: camera, faces, heading, attribution, AR, belt | Xcode via XcodeGen | ios/CLAUDE.md |
| `belt/` | ESP32 haptic belt firmware + wiring | Arduino IDE | belt/CLAUDE.md |
| `tools/` | fake_events, relay_tap, replay, check_gpu, make_mock_scene | see tools/CLAUDE.md | tools/CLAUDE.md |
| `fixtures/` | recorded sessions (`mock_scene` is synthetic) | `python -m laptop --replay` | fixtures/README.md |
| `extras/` | memory + dashboard, only after the gate (README §17) | | extras/*/README.md |
| `legacy/` | v1 code kept for reference (laptop vision, hub, ar_here web) | not maintained | legacy/README.md |
| `reference/` | external projects, gitignored, **never imported or copied** (Ranger has no license) | | reference/README.md |

## Starting the 36-hour push

If the user says **"I'm Kori, I'm ready to go"** or **"I'm Adil, let's go"** (or close variants), follow that person's
section of `HERE_36hr_vibecode_plan/START.md` from top to bottom: worktrees, checks, then three worker tabs. That
sentence is the go-ahead for every step in the section.

## Everyday commands

```powershell
.\tasks.ps1 test          # (or make test) contracts, relay, laptop, end-to-end smoke, consistency, extras
.\tasks.ps1 relay         # local relay on :8080
.\tasks.ps1 fake-events   # scripted core moments -> the phone, no audio
.\tasks.ps1 laptop-mock   # synthetic scene through the real models
.\tasks.ps1 tap           # watch a session
```

## Rules

- **Contract first.** Message shapes change only through the integrator (Kori): edit
  `contracts/build_schemas.py`, regenerate, update the Python/Swift/TS mirrors and examples, bump `v` on
  breaking changes, update README §12, all in one commit. `python -m pytest contracts tests` must pass.
- **No audio or video on the network** (README §6). Laptop sends world bearings; the phone converts.
- **Every component runs without hardware** (mocks / fixtures) and keeps tunables in one file.
- **Secrets** only in `.env` files (see the `.env.example`s); never commit them.
- **Nothing from `reference/`** is imported or copied (tests/test_consistency.py checks for references).
- Python 3.12 on the laptop (`.venv`), Swift/SwiftUI iOS 26 on the phone, Arduino C++ on the ESP32.
- **`IOS_INTEGRATION.md`**: a separate team builds `ios/`. Anything that changes what the phone receives or how it
  connects (contract, relay URL, new behaviour) gets a row there in the same commit.
- `CHANGELOG.md` gets an entry per meaningful change; `MIGRATION_PLAN.md` explains the v1 -> v3 move.
