# A2 jobs that run on Kori's laptop (K3 runs them)

Adil's machine has **no NVIDIA GPU and not enough memory for the larger models**: on 2026-10-10 the system killed the
bakeoff run of CED-base / AST / CLAP for low memory. So every heavy A2 job runs on Kori's laptop (RTX 5000 Ada, 16 GB),
and A2 on Adil's machine only writes code, runs the unit tests, and runs small models (CED-mini size) on CPU.

**Rule for A2 from now on:** anything that loads a model bigger than CED-mini, runs more than one model, needs CUDA,
or is golden goes in the table below instead of running on Adil's machine. Add a row, commit, push, tell Kori.

## How to run (K3, in `../shh-k3`)

```powershell
git fetch origin
git checkout adil/a2-sound-ai          # or merge it into a scratch branch; never push to it without asking Adil
pip install "transformers>=4.45,<5" psutil            # once, in the laptop .venv (A2's code needs both)
```

Paste each job's output into `HERE_36hr_vibecode_plan/WORKLOG_A2.md` (section "Results from Kori's laptop") on
`adil/a2-sound-ai`, or send it to Adil to paste. A2 picks the model and thresholds from these numbers.

## Jobs

| # | Job | Command | Report back | Status |
|---|---|---|---|---|
| 1 | **Sound bakeoff, GPU** (all five candidates: PANNs, CED-mini, CED-base, AST, CLAP). Downloads ~1.8 GB of models the first time; clips download to `logs/sound_bench/` | `python tools/sound_bench.py --device cuda` | The three printed tables (overall, per class, false alerts) | Open |
| 2 | **Sound bakeoff, CPU** (same, for the fallback-on-CPU numbers). Run after 1, with nothing else running | `python tools/sound_bench.py --device cpu` | The three tables | Open |
| 3 | **Attention tier 2 on the demo states** (DeBERTa zero-shot) | `python -m laptop.enrich.attention --states --device cuda` | The per-state lines and the "agree / median / worst" line | Open |
| 4 | **JEV-9B gates 1, 3, 4** (ATTENTION.md). Needs transformers >= 5 (Qwen3.5), so use a **separate venv** to keep the laptop's .venv intact: `py -3.12 -m venv .venv-jev; .venv-jev\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cu121; .venv-jev\Scripts\pip install "transformers>=5" peft bitsandbytes accelerate safetensors pyyaml numpy scipy`. Downloads 18 GB | `.venv-jev\Scripts\python -m laptop.enrich.attention --bench` | Load time, VRAM, gate 3 PASS/FAIL, the gate-4 comparison, peak VRAM. Gate 1: `nvidia-smi` with whisper + PANNs + JEV-9B loaded (< 14.5 GB) | Open |
| 5 | **Golden on this branch** (it must stay 7/7, no new false alerts in `golden_babble_only`). The live model is still `panns_cnn14`; the new parts are onset windows, `unknown` and enrichment | `python tools/stress_test.py --golden` | The golden table | Open |
| 6 | **Replay smoke with enrichment** (no relay needed) | `python -m laptop --replay fixtures/array_tts --offline` | Any `unknown`, `perception_enrichment` and errors in the console | Open |
| 7 | **Live demo set** at the end, on the array: doorbell, knock/bang, door close, car horn, siren, speech, an ambiguous sound, noisy ambience, a weird sound (TASKS.md A2 G) | `python -m laptop --real --verbose` | What each sound produced (label or `unknown`, bearing, `model`) | Later, together |

After 1-3, A2 sets `sounds.model`, per-model thresholds and `sounds.publish` (car_horn, vehicle, siren, door), adds the
`IOS_INTEGRATION.md` row, and asks for job 5 again before the merge.
