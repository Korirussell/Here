# WORKLOG A2: sound AI (Adil's machine: no ReSpeaker, no NVIDIA GPU, CPU only)

## 2026-10-10

### Setup
- Branch `adil/a2-sound-ai`; Python = `C:\Users\User\projects\shh\shh\.venv` (shared venv). Added `transformers 4.57`
  and `psutil` to it (additive). Baseline `pytest contracts relay laptop tests`: 206 passed, 1 skipped.
- PANNs checkpoint: the first resumed download was corrupt (md5 mismatch, constant outputs for every input).
  Re-downloaded and checked against Zenodo's md5 `541141fa2ee191a88f24a3219fff024e`. **If PANNs ever scores
  ~0.66 "Music" on everything, the checkpoint is corrupt.**
- K1's onset candidates exist on `origin/kori/k1-respeaker` (not on main yet). The sounds factory subscribes with
  `app.onsets.subscribe(pipeline.on_onset)` only when `app.onsets` exists, so this branch works before and after K1
  merges. Tests drive `on_onset` / `handle_window` with fake candidates.

### A2 B: wrapper (done, model still PANNs until the bakeoff picks)
- `laptop/sounds/models.py`: one backend call for PANNs, Hugging Face AudioSet taggers (AST, CED: mapped by
  AudioSet index with the same `label_map`) and CLAP zero-shot (prompts per SoundLabel in `sounds.clap_prompts`).
- `Classifier`: `sounds.model`, PANNs fallback at load and mid-session (`problem` says why); per-model thresholds
  and alert minimums (`sounds.models.<id>.thresholds / default_threshold / alert_min`), because score scales
  differ between models. `sound_event.model` = whoever answered (`+enrolled` when the doorbell template matched).
- `SoundPipeline`: sliding window (unchanged) + onset windows (0.1 s before the onset, queue of 4, drop oldest;
  speech-flagged and other-beam candidates skipped).
- Unknowns (C): see the classifier docstring and `sounds.unknown`. Upgrade keeps `event_id` and the onset bearing.
- Tests: `laptop/tests/test_sound_ai.py` (16, fakes only).

### A2 A: bakeoff (`python tools/sound_bench.py`)
In progress: see below.

#### Status at 04:00
- Partial CPU results (Adil's machine), 254 clips: **ced-mini** top-1 80%, detect@2%FPR 64%, +babble 41%, 10/82 false
  alerts on negatives, 24 ms/window, 300 MB. The PANNs row from that run is **invalid** (corrupt checkpoint, fixed since).
- Run of ced-base / AST / CLAP was **killed by the system for low memory** (three large models in a row on this
  machine). Not rerun here: the bakeoff, attention tier 2 / JEV-9B, golden and the replay smoke are handed to Kori's
  laptop (`Adil/A2_ON_KORI_LAPTOP.md`, jobs 1-7).
- Observed: AudioSet taggers hear the synthetic/real demo doorbell as "Music" (ced-mini doorbell median 0.04); the
  enrolled template matcher stays essential for the demo doorbell.

### A2 E/D: attention + Gemini (code done, tested with fakes)
- `laptop/enrich/` (see its docstrings). Tier 2 measured below ("Attention tier 2 on CPU").
- JEV-9B: needs transformers >= 5 (Qwen3.5 architecture; the shared venv has 4.57) + peft + bitsandbytes on Kori's
  laptop. K3: `python -m laptop.enrich.attention --bench`.
- No GEMINI_API_KEY on this machine: Gemini is tested with a fake transport only.

### Attention tier 2 on CPU (2026-10-10, overnight)
- `6d66b4c`: DeBERTa (`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`, MIT) is called directly (batched NLI, 4 torch
  threads) instead of through the `transformers` pipeline, which took minutes per decision under memory pressure here.
  A model answer never talks an `alert` or name-called event below the rules.
- `8141666`: wording tuned on the fixture states (7 attention + 5 directed-at-user wordings compared, `enrich` in
  `laptop/config.yaml`); name called keeps `directed_at_user >= 0.9`.
- `python -m laptop.enrich.attention --states` (30 labelled states, i7-1250U, warm): load 8.6 s; **median 447 ms,
  worst 797 ms per decision** (inside the 1.5 s CPU budget). Agreement with the wanted label **15/30 (obvious cases
  10/13)**. Most top probabilities are under 0.5, so in the live pipeline those fall through to the rules table with
  `attention_model: "rules"`, as ATTENTION.md intends. Misses lean one way: siren/baby/knock/bang are judged
  `ambient`/`important` instead of `urgent`/`important`; that is safe because a model never lowers an alert.
  Directed-at-user is close to the wanted values (0.90 on every name call, 0.13-0.21 on overheard chatter).

### Demo set G on CPU (2026-10-10, overnight; default `panns_cnn14` + enrolled doorbell)
| Input | Result (full pipeline, `--replay ... --offline --no-speech`) |
|---|---|
| `mock_scene` doorbell | `sound_event doorbell`, `model panns_cnn14+enrolled`, conf 1.0, **bearing 172.6** (behind), `alert Doorbell` same bearing; `perception_enrichment` from DeBERTa: `important` |
| same, `--no-enrich` | identical `sound_event` + `alert` (no enrichment); also `test_everything_down_changes_nothing_else` |
| `rot_talker_baby` | one `unknown` (conf 0.95, `bearing_deg: null`), enrichment by rules: `notice`. The `rot_*` fixtures have **empty `sources.jsonl`** (raw mics only, directions need `--odas` / WSL), so no bearing exists to keep: honest, not a dropped bearing |
| `fixtures/sounds/baby_cry.wav` (classifier CLI) | `baby_crying` 0.71 |
| `fixtures/sounds/doorbell.wav` (classifier CLI, no template) | `music` 0.48 then `doorbell` 0.50: the template matcher is what makes the doorbell reliable |
| `fixtures/sounds/doorbell_array.wav` (classifier CLI, no template) | `music` 0.81: PANNs alone hears the real played doorbell as music (same finding as the bakeoff) |

- Offline on `rot_talker_baby/audio.wav` PANNs hears vehicle 0.35 / music 0.78 / baby 0.40 in the synthetic rotated mix;
  the pipeline's per-model thresholds and speech gating reject all of it except one honest `unknown`. Not tuned: the
  rotated scenes are synthetic beam audio, and golden on the array is the reference (README rule: don't tune to CPU
  fixtures).
- Not covered by a clip here (none recorded yet): knock / bang, door close, car horn, siren, a deliberately weird sound.
  Record them on Kori's laptop (`python -m laptop.sounds.enroll ... --no-config` or `--record`) for the bakeoff.

### Still open, for Kori's laptop
- The bakeoff (`Adil/A2_ON_KORI_LAPTOP.md` jobs 1-3) and the model pick; publishing `car_horn`/`vehicle`/`siren`/`door`
  needs thresholds from real clips + golden 7/7.
- JEV-9B gates: `python -m laptop.enrich.attention --bench` (needs transformers >= 5, peft, bitsandbytes, the GPU).
- Gemini with a real key: no `GEMINI_API_KEY` on this machine yet (`ADIL_TODO.md` §1). Code tested with a fake transport.

## Results from Kori's laptop
(K3 pastes the outputs of `Adil/A2_ON_KORI_LAPTOP.md` jobs here.)

## Note from K3 (2026-10-10): transformers on Kori's laptop
- `transformers` is not in `laptop/requirements.txt`, and the shared `.venv` on the demo laptop doesn't have it. So both
  attention tiers log "unavailable" at startup and the demo has always run on `rules`. Please add it to requirements.
- Pick a version that fits the venv: `"transformers>=4.45,<5"` would downgrade `huggingface_hub` (1.33.0 -> 0.36.2) and
  `tokenizers` (0.23.2 -> 0.22.2), which faster-whisper uses. **`transformers==5.18.0` installs with nothing shifted** and
  imports with torch 2.5.1. Avoid 5.19+ (needs torch >= 2.6) and 5.0-5.14 (needs tokenizers <= 0.23.0). Full table:
  WORKLOG_K3 "Round 7". Is A2's code fine on 5.x? K3 is comparing DeBERTa decisions and latency on 4.57.6 vs 5.18.0.
- `check_gpu --demo` now prints which attention tier actually runs.

