# Migration plan: HERE v1 (laptop hub) → v3 (relay + phone does AR)

Phase 0 audit, 2026-10-08. Source of truth: `README.md` (was `README NEW.md`).

**Status: executed the same day (branch `overhaul/v3`, see CHANGELOG.md).** How the blockers were resolved:
1. The in-tree WIP was committed as-is first (`e001d3c`), then moved with `git mv`.
2. `ar_here` copied into `legacy/ar_here/` unchanged (no `node_modules`).
3. Ranger left at `../Treehacks2025/`; `reference/` is gitignored with a pointer README. Nothing read, imported or copied (tests/test_consistency.py guards it).
4. `HERE_PLAN.md` -> `legacy/HERE_PLAN_v1.md`; `README NEW.md` -> `README.md`.
5. Hardware unknowns are config TODOs: relay host (`laptop/.env`, app Settings), token (`.env` only), XVF3800 firmware (auto-detected: 6 channels enable raw-mic doorbell direction). The belt BLE UUIDs had no prior owner, so they were defined once in `belt_config.h` and mirrored in the app (tested to match).
6. C11 decided as proposed: `doa.beams` -> optional `sources[1]` (off by default); `sound_doa` -> internal to the laptop (fills `sound_event`/`alert` bearings). No contract shape changed.

The audit as written before execution follows.

---

## Blockers (answer before Phase 1)

1. **Uncommitted work in the tree.** 18 tracked files are modified and 12 are untracked, mostly Adil's audio and sound work: `audio_here/localize.py`, `audio_here/ring.py`, all of `sound_here/`, four new test files, `vision_here/lips.py`, plus changes to `audio_here/__main__.py`, `respeaker_probe.py` and `config.py`. `git mv` only moves tracked files, and moving uncommitted work risks losing it. **Ask:** may I commit this as-is ("WIP: audio/sound work before overhaul", authored as-is) on a branch first, or will Adil commit it?
2. **The ar_here prototype is outside the repo**, at `../Here/ar_here/ar_here/` (no git). **Plan:** copy it into `legacy/ar_here/` untouched as a new commit (there's no history to keep). OK?
3. **Ranger is outside the repo**, at `../Treehacks2025/` (its own git clone of `YuanSamuel/Treehacks2025`; no LICENSE file found). Nothing in `shh/` imports from it or mentions it (checked). **Ask:** move it to `shh/reference/ranger/` (gitignored), or leave it where it is and just document it? Either way, nothing gets copied.
4. **Old plan doc.** `HERE_PLAN.md` (v1, with Adil's uncommitted v1 additions: `doa.beams`, `sound_doa`, `detections.speaking`) is superseded. **Plan:** `git mv HERE_PLAN.md legacy/HERE_PLAN_v1.md`, and `git mv "README NEW.md" README.md` (the current README is just `# shh`). OK?
5. **Hardware unknowns** (they'll go in config as TODOs, not guesses): BLE service/characteristic UUIDs, the Vultr hostname/domain, the relay token, and whether the XVF3800 has the 2-channel or 6-channel USB firmware on the demo unit (decides whether SRP-PHAT sound direction works, see C11).

---

## 1. Inventory: what exists today

Python 3.12 venv at `.venv` (torch 2.5.1+cu121, required by NVIDIA driver 538.18). Everything is multi-process: each service connects to `world_here` at `ws://localhost:8765`.

| Directory | What it does | How it runs | State | Author |
|---|---|---|---|---|
| `world_here/` | v1 hub: routes by `hello.subscribe`, pairs binary frames, clock sync, calibration, JSONL log. `fusion.py`: entity model, speech→person attribution (§9.1 v1), lip-score tiebreak, builds the `frame` message for ar_here | `python -m world_here` | Working; 12+ tests | Russell, Adil (lips) |
| `audio_here/` | **XVF3800 (must keep working).** `respeaker_probe.py`: USB control reads/writes, register table, standalone readout, `--dump`. `__main__.py`: 20 Hz DoA poll → `doa` (+ two focused `beams`), processed audio → `audio_chunk`, SRP-PHAT direction for `sound_event`s → `sound_doa`, LED ring. `calibrate.py`: guided offset/CW calibration that writes `config.py`. `localize.py`: SRP-PHAT, raw buffer, `--check` fit. `ring.py`: LED ring CLI | `python -m audio_here`; `python audio_here/respeaker_probe.py`; `python -m audio_here.calibrate` | Working on hardware (offset 355.3°, CW); ring/SRP not hardware-verified | Adil (much uncommitted) |
| `speech_here/` | Silero VAD + faster-whisper large-v3-turbo; streaming partials (`pending`→`text`), finals, loudness, name mentions. CPU fallback (`base.en`) | `python -m speech_here` | Working; 0.09 s partial latency measured | Russell, Adil (CPU fallback) |
| `sound_here/` | PANNs CNN14 (GPU), 2 s windows every 0.5 s, AudioSet→SoundLabel map, repeat merging → `sound_event` | `python -m sound_here`, `--wav` offline | Working with stand-ins; thresholds untuned | Adil (untracked) |
| `vision_here/` | YOLO11m + ByteTrack on JPEG frames from the phone → `detections` with world bearings; `lips.py`: MediaPipe face landmarker → `face_bbox` + lip-motion `speaking` score | `python -m vision_here` | Working on webcam | Russell, Adil |
| `here_common/` | `client.py` (hub client: hello, heartbeat, reconnect, binary pairing), `geometry.py` (wrap, angdiff, circular mean, pixel↔bearing, motor) | imported | Working, tested | Russell |
| `ios_here/` | SwiftUI app, xcodegen `project.yml`: quaternion heading (`HeadingProvider`), camera → **JPEG frames streamed to the laptop**, clock sync, `ws://` hub client, v1 settings, status screen. iOS 16 target, portrait-upside-down | Xcode on the Mac (`xcodegen generate`) | Builds; on-device spike not done; `SPIKE.md` is a template | Kori |
| `ios_here/mock_hub.py` | Stand-in hub for phone testing | `python ios_here/mock_hub.py` | Working | Russell |
| `tools/` | `fake_phone.py` (webcam→`video_frame`), `fake_audio.py` (mic/WAV→`audio_chunk`), `fake_doa.py` (slider→`doa`), `debug_view.py` (OpenCV overlay of `frame`), `record.py`/`replay.py` (fixtures via hub tap), `check_gpu.py` (pre-demo GPU/model/ReSpeaker check), `dev.ps1` (launch all) | scripts | Working | Russell, Adil |
| `tests/` | `test_fusion.py` (geometry + attribution) + untracked `test_localize`, `test_ring`, `test_sound_events`, `test_audio_here_events` | `python -m pytest tests` | Passing (last run) | both |
| root | `SETUP.md` (Windows GPU setup), `requirements.txt`, `HERE_PLAN.md` (v1 plan), `README.md` (`# shh`), `OVERHAUL_PROMPT.md`, `README NEW.md` (untracked), `yolo11{s,m}.pt` (gitignored), `logs/` | | | |
| outside: `../Here/ar_here/ar_here/` | R3F web display + `server/mock_server.py` streaming JPEG + v1/v2 `FrameMeta` | `npm run dev` + mock server | Prototype | Kori? |
| outside: `../Treehacks2025/` | Ranger clone (python-backend + unity). **No license; reference only** | n/a | n/a | other team |

Not present anywhere: belt firmware, relay, `contracts/`, `fixtures/` (no recordings yet), Vision face tracking on iOS, BLE.

---

## 2. Mapping: old → new (README §22)

`mv` = `git mv` (history kept). `port` = rewrite into the new shape using our own code as the source. `legacy` = moved untouched to `legacy/`.

### Laptop (`python -m laptop --real | --mock | --replay`)

| Old | New | How |
|---|---|---|
| `audio_here/respeaker_probe.py` | `laptop/audio/xvf3800.py` (+ keep the standalone CLI working: `python laptop/audio/xvf3800.py` / `--dump`) | mv; imports adjusted only |
| `audio_here/__main__.py` | `laptop/audio/builtin_doa.py`: `BuiltinDoaSource(DirectionSource)` | mv + wrap: DoA poll → `Sources` (one source, id 0); audio stream → in-process queue for speech/sounds instead of `audio_chunk` |
| `audio_here/calibrate.py`, `ring.py`, `localize.py` | `laptop/audio/` (same names) | mv; `calibrate.py` writes `laptop/config.yaml` instead of `config.py` |
| `audio_here/config.py` | `laptop/config.yaml` → `audio:` section | port values (offset 355.3, CW, rates, ring, SRP) |
| (new) | `laptop/audio/odas.py`: `OdasSource` stub, off by default | new |
| `speech_here/__main__.py` | `laptop/speech/whisper.py` (`WhisperTranscriber`), `laptop/speech/pipeline.py` (VAD, segmentation, `speech_start`, bearing/spread/overlap, tone, loudness) | mv + split; engine behind `Transcriber` |
| `world_here/fusion.py` circular-mean bearing + overlap | `laptop/speech/pipeline.py` | port (README §13 "On the laptop") |
| speech name mentions | `laptop/speech/names.py` → `alert{kind:name_called}` | port |
| (new) | `laptop/speech/scribe.py`: `ScribeRealtimeTranscriber` stub, key from `.env` | new |
| `sound_here/*` | `laptop/sounds/` (`classifier.py` = PANNs, `events.py` as-is) | mv; doorbell on, other labels off by config; emits `sound_event` + `alert` with bearing from recent sources / SRP |
| `here_common/client.py` | `laptop/client/relay.py` | port: `wss://…?session&role&token`, `hello` as `laptop`, heartbeat with `engine`/`doa_source`, answers `clock_ping`; drops binary pairing |
| `here_common/geometry.py` | `laptop/geometry.py` | mv |
| `SETUP.md` | `laptop/README.md` | mv + add XVF3800 driver steps |
| `requirements.txt` | `laptop/requirements.txt` | mv |
| `tools/check_gpu.py` | `tools/check_gpu.py` | keep; repoint config imports |
| `tools/fake_audio.py`, `tools/fake_doa.py` | `laptop/audio/mock.py` (`--mock`: WAV/mic audio + scripted or slider directions, in-process) | port (slider kept as an option) |
| `tools/record.py`, `tools/replay.py` | `tools/record.py` (laptop-side: `audio.wav` + `sources.jsonl`), `tools/replay.py` → `--replay` input | port; hub tap no longer exists |
| tests (localize, ring, sound_events, audio_here_events, geometry part of test_fusion) | `laptop/tests/` | mv; fix imports |

### Relay, contracts, phone, belt, tools

| Old | New | How |
|---|---|---|
| `world_here/hub.py` routing, logging, latest-config replay | `relay/relay.py` | port as the basis (subscribe routing, JSONL log, drop-if-busy), plus auth, sessions, `/health`, TLS via Caddy |
| `world_here/fusion.py` speech→face attribution + its tests | `ios/HERE/Attribution/` (Swift) + XCTest | port (our own code; the logic and test cases carry over) |
| `ios_here/App/HeadingProvider.swift` | `ios/HERE/Motion/` | mv; offset applied on the phone now (§8 step 4) |
| `ios_here/App/CameraCapture.swift` | `ios/HERE/Camera/` | mv + rework: landscape, preview layer, no JPEG encoding |
| `ios_here/App/Config.swift`, `SettingsView.swift` | `ios/HERE/Settings/` | mv + rework to v3 `config` + relay URL/token/session |
| `ios_here/App/HubConnection.swift`, `ClockSync.swift`, `PhoneClock.swift` | `ios/HERE/Network/` | mv + rework to wss relay, decode `HereContract` |
| `ios_here/App/ContractMessages.swift` | replaced by `contracts/swift/HereContract.swift` | replace |
| `ios_here/App/AppState.swift`, `HEREApp.swift`, `ContentView.swift` | `ios/HERE/` | mv + rework |
| `ios_here/project.yml`, `.gitignore`, `CLAUDE.md`, `SPIKE.md` | `ios/` | mv; target iOS 26, landscape, bundle/Info.plist strings updated |
| (new) | `ios/HERE/{Vision,Geometry,Belt,UI,Previews}/` | new |
| (new) | `belt/firmware/`, `belt/README.md` | new |
| (new) | `contracts/` (schemas, Python, Swift, TS, examples, tests) | new |
| (new) | `tools/fake_events.py`, `tools/relay_tap.py`, `Makefile` | new |

### To `legacy/` (kept, not maintained)

| Old | Why |
|---|---|
| `../Here/ar_here/ar_here/` → `legacy/ar_here/` | Web display of JPEG video; replaced by native AR on the phone. Styling can inform `extras/dashboard/` |
| `vision_here/` (incl. `lips.py`) | Laptop-side vision; v3 does faces on the phone. Lip-motion idea could return as an iOS extra (Vision face landmarks) |
| `world_here/` (whatever isn't ported) | Laptop hub replaced by the relay |
| `tools/fake_phone.py`, `tools/debug_view.py`, `tools/dev.ps1` | Exist only to feed/show laptop-side video |
| `ios_here/mock_hub.py`, `ios_here/SPIKE.md` (v1 USB-hotspot spike) | v1 transport (USB hotspot, JPEG streaming) |
| `HERE_PLAN.md` → `legacy/HERE_PLAN_v1.md` | Superseded by README v3 |
| `OVERHAUL_PROMPT.md` | One-off; delete or keep under `legacy/` (your call) |

---

## 3. Conflicts with README v3

### Architecture / network
- **C1. Video streams to the laptop.** `ios_here` encodes JPEGs and sends `video_frame` + binary; `vision_here` and `world_here` consume them. v3: no audio or video on the network; the phone does vision and AR (§6, §26).
- **C2. Laptop-side vision** (`vision_here`, YOLO + MediaPipe lips) and **laptop-side attribution** (`world_here/fusion.py`). v3: both on the phone (§13, §14).
- **C3. Laptop hub at `ws://0.0.0.0:8765`**, with the phone connecting to `ws://<laptop IP>` over a USB hotspot (`Config.swift` hardcodes `172.20.10.1:8765`; `here_common` defaults to `ws://localhost:8765`). v3: every device connects outbound to a Vultr relay over `wss` with session/role/token (§7).
- **C4. Audio travels as binary `audio_chunk` messages** between laptop processes. v3: one laptop process, audio passed in-process (§12.1: no binary on the network).
- **C5. Heading flows phone→laptop** (`heading`, `calibrate` rebroadcast to vision). v3: the laptop never needs heading; the phone applies the offset itself (§6, §8).
- **C6. Belt from the laptop** (v1 `belt_command`, never implemented). v3: phone drives the belt over CoreBluetooth (§14, §16). No belt code exists either way.
- **C7. No ARKit:** already satisfied (none used).

### Contract (v1 → v3)
- **C8. Envelope:** `v: 1`, `src` = service names (`audio_here`, `ios_here`…). v3: `v: 3`, `src` ∈ `laptop|phone|relay|dashboard|memory|tool`, `session` set by the relay.
- **C9. Removed message types:** `video_frame`, `heading`, `audio_chunk`, `detections`, `frame`, `belt_command`, `calibrate`, `sound_doa`.
- **C10. Renamed or reshaped:** `doa` → `sources` (array of sources; `energy` 0..1 instead of `energy_db`); `clock_pong.t_hub` → `t_laptop` (answered by the laptop, not the hub); `config` loses `profile`, `known_people`, `caption_scale` and adds `focus_mode`, `belt_only_mode`, `features.rings`; `utterance` gains `bearing_deg`, `bearing_spread_deg`, `source_id`, `overlap`, `stage_ms` and loses `name_mentions` (name-called becomes an `alert`); `sound_event` gains `category`, `emoji`, `text`, `bearing_deg` and loses `raw_label`, `detail`; `alert` gains `emoji`, `ref` and loses `rel_bearing_deg`, `entity_id`; `heartbeat` gains `thermal`, `engine`, `doa_source` and loses `rate_hz`.
- **C11. Adil's v1 additions have no direct v3 slot.** This needs your call:
  - `doa.beams` (the XVF3800's two focused beams). v3 `sources` can hold several sources, but §12.5 says built-in DoA sends one. **Proposal:** a `audio.builtin_beams_as_sources` config flag, default **off**. When on, the second beam goes out as `sources[1]` (id 1) only when it hears speech and is ≥ 30° from the main DoA. No shape change.
  - `sound_doa` (SRP-PHAT on raw mics). **Proposal:** use it inside the laptop to fill `sound_event.bearing_deg` / `alert.bearing_deg`, falling back to recent `sources` (speech-gated DoA often has no reading for a doorbell). No contract change; the `sound_doa` message disappears.
  - `detections.speaking` (lips). Gone with laptop vision (→ legacy).
- **C12. New types with no implementation yet:** `sources`, `speech_start`, `attribution`, `scene`, `search_request`/`search_result`.
- **C13. Tone:** v1 sends `tone: null`. v3 wants simple rules (loudness + punctuation).

### iOS app
- **C14.** Portrait-upside-down mount and `UISupportedInterfaceOrientations: Portrait`; v3 is **landscape** ultrawide.
- **C15.** Deployment target iOS 16 / Swift 5.0; v3 needs **iOS 26** (Liquid Glass `.glassEffect()`).
- **C16.** `ContractMessages.swift` is untyped `[String: Any]`; v3 wants `Codable` structs mirrored from `contracts/`.
- **C17.** Info.plist strings say video is "streamed to the laptop" / "USB Personal Hotspot"; both wrong under v3.
- **C18.** Heading offset is applied by the hub in v1; it moves into the app.

### Conventions
- **C19.** Tunables are `config.py` modules per service; v3 Phase 3 wants a single `laptop/config.yaml`. `audio_here/calibrate.py` rewrites `config.py` with a regex, so it must be ported to write YAML.
- **C20.** Entry points: `python -m audio_here`, `-m speech_here`… → `python -m laptop --real | --mock | --replay`.
- **C21.** No `.env` handling yet (nothing secret exists yet); the relay token and ElevenLabs key will need it.
- **C22.** Secrets check: no secrets found in tracked files.

---

## 4. Phase 1 move list (after the blockers are answered)

On a branch `overhaul/v3`, one commit per step so it's reviewable:

1. Commit the current uncommitted work as-is (blocker 1).
2. `git mv "README NEW.md" README.md` (replaces `# shh`); `git mv HERE_PLAN.md legacy/HERE_PLAN_v1.md`.
3. `git mv` the `legacy/` set: `vision_here/`, `world_here/`, `tools/{fake_phone,debug_view}.py`, `tools/dev.ps1`, `ios_here/mock_hub.py`, `ios_here/SPIKE.md` → `legacy/…`. Copy `../Here/ar_here/ar_here/` → `legacy/ar_here/` (new files, `node_modules` excluded).
4. `git mv` laptop pieces: `audio_here/*` → `laptop/audio/`, `speech_here/*` → `laptop/speech/`, `sound_here/*` → `laptop/sounds/`, `here_common/client.py` → `laptop/client/relay.py`, `here_common/geometry.py` → `laptop/geometry.py`, `SETUP.md` → `laptop/README.md`, `requirements.txt` → `laptop/requirements.txt`, tests → `laptop/tests/`. Fix imports only. **Check after this step: `python laptop/audio/xvf3800.py --dump` and the friend's live readout still work on the ReSpeaker.**
5. `git mv ios_here ios` with `App/` split into the Phase 4 folders (moves only).
6. Create the empty skeleton: `contracts/`, `relay/`, `belt/firmware/`, `fixtures/`, `extras/{memory,dashboard}/`, `reference/` (gitignored). Add `.env.example`s.
7. Write `contracts/` (schemas, Python, Swift, TS, examples, cross-language tests).
8. Root `CLAUDE.md` + one per component; `CHANGELOG.md`.

Between steps 4 and Phase 3 the laptop code won't run end to end (the hub is gone and the relay doesn't exist yet). The audio direction readout keeps working standalone throughout, since it has its own CLI.

---

## 5. Smaller notes

- `.gitignore` will need `reference/`, `.env`, `fixtures/**/*.wav` (decide whether fixtures are committed), `node_modules/`, `*.pth`/`*.task` (already there).
- `yolo11s.pt` / `yolo11m.pt` (60 MB, gitignored) in the repo root can be deleted once vision is in legacy.
- `tools/dev.ps1` uses `$ReSpeaker` without declaring it in `param()` (bug, moot once it's in legacy).
- README §22 says Python 3.11+; the laptop venv is 3.12 (fine).
- README §19 lists Kori as integrator and iPhone/relay owner; Adil (audio) has the most code that moves. Worth a heads-up to Adil before step 4 since his paths change.
