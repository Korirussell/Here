# tools

Part of HERE. Read ../README.md §14 (dev tips) and §20-§22 first.

| Tool | Job |
|---|---|
| `fake_events.py` | scripted laptop events through the relay (README §4 core moments: Person 1 +35, Person 2 -35, someone behind at 180, doorbell at 165, name called), so the phone can be built with no audio hardware. `--loop`, `--speed`, `--moment N`. Scenes: `--scene <name>|all` plays `fixtures/events/<name>.jsonl` (canonical messages, beams mode), `--build-scenes` rewrites the scripted ones, `--from-log logs/laptop.jsonl --start --end --out` cuts a real run into a scene |
| `relay_tap.py` | print every message in a session, one line each. `--validate` flags contract violations; `--types`, `--raw` |
| `replay.py` | replay a recorded fixture through the laptop pipeline into the relay (`python -m laptop --replay`) |
| `make_mock_scene.py` | rebuild `fixtures/mock_scene` (Windows text-to-speech + a synthesized doorbell) |
| `stress_test.py` | the pipeline in a simulated loud hall (TTS crowd babble at falling SNR) scored against ground truth, plus DoA-noise attribution tolerance. Run after changing speech/sounds/segmentation settings |
| `speaker_scene.py` | record the README §4 scene through the REAL array from a loudspeaker (no people), ground truth aligned from the recording -> `fixtures/<name>` scoreable by `stress_test.py --scene` |
| `make_raw_scene.py` | real-acoustics raw-mic scenes without people: rotates `fixtures/raw_speaker_180` by permuting the square array's mics (single sources at 0/90/180/-90, talker + baby, two talkers 90/180 apart) -> `fixtures/rot_*` with ground truth. Raw-mic consumers only (SRP, ODAS) |
| `doa_compare.py` | built-in DoA vs ODAS vs hybrid vs our SRP on the same audio: one scorer (detection, error, two-source, ghosts/stale, ids, latency); `--scenes` (rotation scenes, offline), `--live` (real array + laptop speaker), `--captions`, `--replay`. Live/caption parts in `doa_live.py`. Results: laptop/audio/DOA_VS_ODAS.md |
| `odas/setup_wsl.sh` | build ODAS (pinned commit + a local glibc fix) in WSL2 Ubuntu: `wsl -d Ubuntu -- bash tools/odas/setup_wsl.sh` |
| `doa_accuracy.py` | DoA spike: guided positions (or `--speaker` playback), error / spread / settle time / second-beam rate per position, markdown table for laptop/audio/SPIKE.md |
| `run_report.py` | numbers for the pitch from `logs/laptop.jsonl`: latency histograms, captions / alerts / retractions per session, heartbeat and engine problems |
| `check_gpu.py` | pre-demo check: CUDA, whisper + PANNs speed, the ReSpeaker, relay reachable + token set. `--demo`: the running stack instead (token accepted, laptop heartbeat, dashboard, memory API + whether it is on Neon), no model loads |
| `latency_probe.py` | relay latency from this machine (local or Vultr): two clients in a private `latency-probe` session, 20 Hz `sources`, p50/p95/max + lost frames vs the 100 ms network budget. `.\tasks.ps1 latency --relay wss://<host>/ws` |
| `drills.py` | scripted failure drills: the real laptop on a fixture replay into a private relay with one dependency broken (network off in-process, a sound model that can't load, Gemini, slow / unloaded attention, relay restart, airplane), compared with a clean run. `--list`; logs in `logs/drills/` |
| `demo.py` | the demo stack in one command: `up` (local relay + source `fake`/`replay`/`real` + dashboard + memory API when they exist), `reset` (< 30 s between judges), `down`, `status`. Mirrors to the cloud relay when `laptop/.env` names a `wss://` one (`--no-mirror`, `--cloud`). `.\tasks.ps1 demo-up` etc. Logs and pids in `logs/demo/` |
| `deploy_vultr.py` | the Vultr relay (+ `--memory` API) in one re-runnable command over ssh: packages, firewall, code via `git archive`, token (never printed), systemd, Caddy on `<ip>.sslip.io`, health checks. `update`, `status`, `--dry-run`, `--save-token` (laptop/.env token + `HERE_RELAY_MIRRORS`). relay/DEPLOY.md |

Rules: tools speak only the contract (`contracts/python/here_contract.py`), connect with role `tool`, and
read relay settings from `laptop/config.yaml` / `laptop/.env`.
