# Attention model (Hugging Face): decisions for HERE

Decided 2026-10-10. TypeSafe isn't a sponsor, so we don't use the hosted Jev API. Instead we use **open Hugging Face
models** for the same job: bounded, typed judgments that return a label from a fixed list, with probabilities.
Hugging Face is then our provider for both sound classification and attention.

## Three tiers (best first; each falls back to the next)

| Tier | Model | Where | Why |
|---|---|---|---|
| **1. Demo** | [`autotrust/JEV-9B`](https://huggingface.co/autotrust/JEV-9B), **quantized to 4-bit or 8-bit** | the demo laptop's GPU | An open "System One" model (Apache-2.0, AutoTrust AI Lab, built on Qwen3.5-9B plus a 40M decision head) distilled to behave like TypeSafe's Jev: typed choice / yes-no / rating answers with calibrated probabilities. About 90 ms per decision on a B200; on its benchmark it's close to hosted Jev 1.13 (0.868 vs 0.890 at 2 options). |
| **2. Development and fallback** | [`MoritzLaurer/deberta-v3-base-zeroshot-v2.0`](https://huggingface.co/MoritzLaurer/deberta-v3-base-zeroshot-v2.0) | CPU anywhere (Adil's machine), or GPU | A small (184M) zero-shot classifier with the same label-plus-probabilities output. About 1 s per 4-label decision on CPU. |
| **3. Always available** | Deterministic rules | Anywhere | `alert` priority 3 → urgent; priority 2 → important; known label → notice; `unknown` with low energy → ambient. |

**Why not `autotrust/JEV-27B-VL`:**
- It's 52 GB in BF16 and its server wants an 80 GB GPU. Even at 4-bit it would fill the whole 16 GB laptop GPU, leaving nothing for whisper.
- Its image input is useless to us: no video crosses the network, and the phone owns the camera.
- JEV-27B is only slightly better than 9B on short option lists like ours: 4 attention labels and yes/no.

**Why JEV-9B has to be quantized:**
- The demo laptop has an **RTX 5000 Ada Laptop GPU with 16 GB**, and JEV-9B is 18 GB in BF16.
- In 8-bit it's about 10 GB, and in 4-bit NF4 about 6 GB. Whisper `large-v3-turbo` and PANNs need roughly 3-4 GB.
- So **4-bit is the safe target; 8-bit only if it measurably fits.**

**How to run it on Windows:** use the model card's documented **`transformers` + `peft` path** (merge the adapter,
apply the decision head to the last hidden state), with `bitsandbytes` for the quantization. That avoids vLLM, which
needs Linux or WSL. The card doesn't say whether the published quantized variants keep the decision head. Load the
BF16 repo and quantize at load time instead.

## Gates for tier 1 (measured on the demo laptop before the demo relies on it)

1. **VRAM:** JEV-9B (quantized), whisper and PANNs all loaded, with headroom: `nvidia-smi` under 14.5 GB of 16.
2. **Whisper unaffected:** golden still 7/7; first-partial and final latency within 10% of baseline with JEV-9B loaded and answering.
3. **Speed:** median decision ≤ 250 ms and worst case ≤ 600 ms on our state sentences.
4. **Calibration after quantization:** on about 30 labelled states (doorbell during conversation, steady fan, siren, name called...), 4-bit agrees with tier 2 / the rules on the obvious cases and isn't worse than tier 2 on the ambiguous ones. If time allows, run the same states once in BF16 on CPU offline to see how far quantization moved the probabilities.

If any gate fails, the demo uses tier 2 on the GPU, which is still fast and still Hugging Face. Don't fight it.

## What it decides

Each question works the same way: build one short **state** from the event, and ask a typed question with fixed options.

| Question | JEV-9B kind | Options | Output |
|---|---|---|---|
| How much attention does this sound or caption deserve for a deaf wearer right now? | `choice` | `ambient`, `notice`, `important`, `urgent` (with one-line descriptions) | `perception_enrichment.attention` = top option; `attention_confidence` = its probability |
| Is this speech addressed to the wearer? | yes/no | | `perception_enrichment.directed_at_user` = P(yes) |
| (Cloud, cloud) Is this event relevant under these replay settings? | rating or yes/no | | `/api/replay` `decisions[].model_score`. On Vultr this uses tier 2 on CPU, since there's no GPU there. |

**Example state:** *"Sound: doorbell (confidence 0.71, energy 0.6). The wearer was in a conversation. No repeats in the last minute. Focus mode off."*

For tier 2, the same options become zero-shot labels.

**Never decided by these models:** whether an event exists, what it is, or where it is. The canonical `sound_event`,
`utterance` and `alert` go out first, unchanged. Attribution (which face spoke) is the phone's job.

## On the wire: `perception_enrichment` (already in the contract)

- **`attention_model`:**
  - the repo id of whichever tier decided: `autotrust/JEV-9B`, `MoritzLaurer/deberta-v3-base-zeroshot-v2.0`;
  - or `rules`.
- **`context` / `context_model`:** Gemini's one-line note, if Gemini is used.
- **Routing:** phone, memory and dashboard receive it. The phone combines `attention` with its own relative bearing.

## Rules

- **Only on events:** new `sound_event`s (onset or label upgrade) and final `utterance`s. Never the 30 Hz `sources` stream.
- **One worker thread:** a queue of at most 4 that drops the oldest, and a 5 s cache for identical states. Load and warm the model at startup; loading JEV-9B takes a while, so do it before the demo.
- **Timeouts:** tier 1 at 600 ms, tier 2 at 1.5 s on CPU (300 ms on GPU). If a tier times out, errors, or its top probability is under 0.5, the next tier decides.
- **Pitch it honestly:** "JEV-9B, an open Jev-style decision model from AutoTrust on Hugging Face". Not "TypeSafe Jev".
- **Tests** use a fake decider; `pytest` never downloads a model.
- **Dashboard inspector:** show the option probabilities, which tier decided, and "rules would have said X".
