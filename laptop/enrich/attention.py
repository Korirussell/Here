"""Attention judgments in three tiers (HERE_36hr_vibecode_plan/Shared/ATTENTION.md), behind one `Decider` interface.

1. `JevDecider`: autotrust/JEV-9B (Apache-2.0), System 1 path from the model card: transformers + peft (adapter
   merged), the 24-slot decision head on the last token's hidden state, per-kind temperatures. Quantized to 4-bit
   NF4 at load with bitsandbytes (the demo GPU has 16 GB; BF16 is 18 GB). GPU only. Needs transformers >= 5 (Qwen3.5).
2. `ZeroShotDecider`: MoritzLaurer/deberta-v3-base-zeroshot-v2.0 (MIT), the transformers zero-shot pipeline. CPU or GPU.
3. `RulesDecider`: deterministic, always answers.

`TieredDecider` asks each tier in order, under its timeout; a timeout, an error or a top probability under
`min_confidence` hands the question to the next tier. `AttentionWorker` runs it on one thread with a queue of 4 that
drops the oldest, a 5 s cache for identical states and at most `max_rate_hz` decisions per second.

    python -m laptop.enrich.attention --states              # tier 2 + rules on the labelled demo states (CPU)
    python -m laptop.enrich.attention --bench               # K3, Kori's GPU: JEV-9B 4-bit, the four ATTENTION.md gates
"""

import argparse
import concurrent.futures as cf
import json
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Optional

from . import config as C
from .states import ATTENTION_LABELS, ATTENTION_OPTIONS, ATTENTION_QUESTION, DIRECTED_QUESTION

JEV_ID = "autotrust/JEV-9B"
ZEROSHOT_ID = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"


@dataclass
class Job:
    """What to judge. kind: "sound" or "speech". priority: the alert priority the event raised (0 = none)."""
    key: str                          # the ref id (event_id / utt_id)
    kind: str
    state: str
    label: str = ""                   # sound label ("unknown"...) for the rules
    energy: float = 0.0
    priority: int = 0
    name_called: bool = False
    ask_directed: bool = False
    t: float = field(default_factory=time.time)


@dataclass
class Decision:
    attention: str
    confidence: float
    model: str                        # repo id of the tier that decided, or "rules"
    probs: dict
    directed: Optional[float] = None
    rules_attention: str = ""         # what the rules would have said (dashboard inspector)
    ms: float = 0.0
    tried: list = field(default_factory=list)   # [(tier, outcome)] for the log


class Decider:
    """attention(job) -> {label: p} over ATTENTION_LABELS; directed(job) -> P(addressed to the wearer)."""
    name = "?"

    def attention(self, job):
        raise NotImplementedError

    def directed(self, job):
        return None


class RulesDecider(Decider):
    """ATTENTION.md tier 3: alert priority 3 -> urgent, 2 -> important; known label -> notice; quiet unknown -> ambient."""
    name = "rules"
    AMBIENT_LABELS = {"music", "tv", "laughter", "speech", "other"}

    def __init__(self, unknown_loud=0.6):
        self.unknown_loud = unknown_loud

    def pick(self, job):
        if job.priority >= 3:
            return "urgent"
        if job.priority == 2 or job.name_called:
            return "important"
        if job.kind == "speech":
            return "notice"
        if job.label == "unknown":
            return "notice" if job.energy >= self.unknown_loud else "ambient"
        if job.label in self.AMBIENT_LABELS:
            return "ambient"
        return "notice"

    def attention(self, job):
        top = self.pick(job)
        return {k: (1.0 if k == top else 0.0) for k in ATTENTION_LABELS}

    def directed(self, job):
        return 0.9 if job.name_called else None


def nli_scorer(model=ZEROSHOT_ID, device=None, threads=4):
    """score(premise, hypotheses) -> softmax of the entailment logits across the hypotheses (what the zero-shot
    pipeline computes with multi_label=False), in one batched forward pass. The model is called directly with a
    few torch threads: the pipeline with its default thread count took seconds per decision on a P+E-core CPU."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    gpu = device != "cpu" and torch.cuda.is_available()
    if not gpu and threads:
        torch.set_num_threads(int(threads))
    tok = AutoTokenizer.from_pretrained(model)
    net = AutoModelForSequenceClassification.from_pretrained(model).eval()
    if gpu:
        net = net.cuda()
    l2i = {k.lower(): v for k, v in net.config.label2id.items()}
    entail = l2i.get("entailment", 0)

    def score(premise, hypotheses):
        enc = tok([premise] * len(hypotheses), list(hypotheses), return_tensors="pt", padding=True, truncation=True,
                  max_length=160)
        if gpu:
            enc = enc.to("cuda")
        with torch.inference_mode():
            logits = net(**enc).logits[:, entail]
        return torch.softmax(logits.float(), dim=0).tolist()
    return score


class ZeroShotDecider(Decider):
    """Tier 2: NLI zero-shot. Hypotheses = the template filled with each option's description; directed = one
    yes/no hypothesis pair. `score(premise, hypotheses) -> probs` is injectable so tests run without the model."""
    name = ZEROSHOT_ID

    def __init__(self, model=ZEROSHOT_ID, device=None, score=None):
        self.name = model
        self.score = score or nli_scorer(model, device, getattr(C, "ZEROSHOT_THREADS", 4))
        self.template = getattr(C, "ZEROSHOT_TEMPLATE", "For a deaf person, this deserves {}.")
        self.options = dict(ATTENTION_OPTIONS, **(getattr(C, "ZEROSHOT_OPTIONS", None) or {}))
        self.directed_template = getattr(C, "ZEROSHOT_DIRECTED_TEMPLATE", "This was {}.")
        self.directed_labels = list(getattr(C, "ZEROSHOT_DIRECTED", None) or
                                    ["spoken directly to the listener", "spoken to someone else"])
        self.attention(Job("warm", "sound", "Sound: doorbell (confidence 0.70)."))   # load + warm now

    def attention(self, job):
        p = self.score(job.state, [self.template.format(self.options[k]) for k in ATTENTION_LABELS])
        return {k: float(v) for k, v in zip(ATTENTION_LABELS, p)}

    def directed(self, job):
        p = self.score(job.state, [self.directed_template.format(x) for x in self.directed_labels])
        return float(p[0])


def jev_prompt(kind, state, question, options):
    """The model card's System 1 template, verbatim."""
    lines = options if kind != "choice" else [f"{'ABCDEFGHIJKLMNOP'[i]}) {o}" for i, o in enumerate(options)]
    return f"[kind] {kind}\n[state] {state}\n[question] {question}\n[options]\n" + "\n".join(lines) + "\n[decision]:"


def jev_probs(logits, kind, n, ranges, temps):
    """Head logits (24 slots) -> the distribution over this kind's first n slots, temperature applied."""
    import math
    s, _ = ranges[kind]
    z = [float(x) / temps[kind] for x in logits[s:s + n]]
    m = max(z)
    e = [math.exp(x - m) for x in z]
    return [x / sum(e) for x in e]


class JevDecider(Decider):
    """Tier 1: JEV-9B System 1. `forward(text) -> 24 head logits` is injectable so tests run without the model."""
    name = JEV_ID

    def __init__(self, repo=JEV_ID, quant="nf4", forward=None, judge_cfg=None, temps=None):
        self.name = repo
        if forward is None:
            forward, judge_cfg, temps = self._load(repo, quant)
        self.forward = forward
        self.ranges = judge_cfg["slots"]["ranges"]
        self.temps = temps
        self.options = [ATTENTION_OPTIONS[k] for k in ATTENTION_LABELS]

    @staticmethod
    def _load(repo, quant):
        import torch
        from pathlib import Path
        from huggingface_hub import hf_hub_download, snapshot_download
        from peft import PeftModel
        from safetensors.torch import load_file
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if not torch.cuda.is_available():
            raise RuntimeError("JEV-9B needs the GPU (18 GB BF16, ~6 GB NF4)")
        kw = {"dtype": torch.bfloat16, "device_map": "cuda"}
        if quant in ("nf4", "int8"):
            from transformers import BitsAndBytesConfig
            kw["quantization_config"] = (
                BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.bfloat16)
                if quant == "nf4" else BitsAndBytesConfig(load_in_8bit=True))
        tok = AutoTokenizer.from_pretrained(repo)
        base = AutoModelForCausalLM.from_pretrained(repo, **kw)
        # The adapter from its local folder: peft's hub lookup joins "adapter" + file name with os.path.join, a
        # backslash on Windows, and then can't find adapter_model.safetensors even when it's cached (peft 0.21).
        adapter = Path(snapshot_download(repo, allow_patterns=["adapter/*"])) / "adapter"
        model = PeftModel.from_pretrained(base, str(adapter))
        if quant not in ("nf4", "int8"):
            model = model.merge_and_unload()   # merging into 4-bit weights isn't supported: keep the LoRA attached
        model.eval()
        head = load_file(hf_hub_download(repo, "head.safetensors"))
        judge_cfg = json.load(open(hf_hub_download(repo, "judge_config.json"), encoding="utf-8"))
        temps = json.load(open(hf_hub_download(repo, "calibration.json"), encoding="utf-8"))["per_kind"]
        W, b = head["proj.weight"].cuda().float(), head["proj.bias"].cuda().float()
        backbone = model.get_base_model().model if hasattr(model, "get_base_model") else model.model

        def forward(text):
            ids = tok(text, return_tensors="pt", add_special_tokens=False).to("cuda")
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
                h = backbone(**ids).last_hidden_state[0, -1].float()
            return (W @ h + b).tolist()
        return forward, judge_cfg, temps

    def attention(self, job):
        p = jev_probs(self.forward(jev_prompt("choice", job.state, ATTENTION_QUESTION, self.options)), "choice",
                      len(self.options), self.ranges, self.temps)
        return dict(zip(ATTENTION_LABELS, p))

    def directed(self, job):
        p = jev_probs(self.forward(jev_prompt("noul", job.state, DIRECTED_QUESTION, ["false", "true"])), "noul", 2,
                      self.ranges, self.temps)
        return p[1]


class TieredDecider:
    """Each tier under its timeout; timeout / error / top probability under min_confidence -> the next tier."""

    def __init__(self, tiers, timeouts, min_confidence=0.5, rules=None):
        self.tiers = list(tiers)                        # best first, rules NOT included
        self.timeouts = dict(timeouts)                  # name -> seconds
        self.min_confidence = min_confidence
        self.rules = rules or RulesDecider()
        self.pools = {d.name: cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"attn-{i}")
                      for i, d in enumerate(self.tiers)}

    def _ask(self, d, fn, job):
        fut = self.pools[d.name].submit(fn, job)
        return fut.result(timeout=self.timeouts.get(d.name, 1.5))

    def decide(self, job):
        t0 = time.perf_counter()
        rules_probs = self.rules.attention(job)
        rules_top = max(rules_probs, key=rules_probs.get)
        tried = []
        for d in self.tiers:
            try:
                probs = self._ask(d, d.attention, job)
            except cf.TimeoutError:
                tried.append((d.name, "timeout"))
                continue
            except Exception as e:  # noqa: BLE001
                tried.append((d.name, f"error {type(e).__name__}: {e}"[:120]))
                continue
            top = max(probs, key=probs.get)
            if probs[top] < self.min_confidence:
                tried.append((d.name, f"unsure {top} {probs[top]:.2f}"))
                continue
            if (job.priority >= 2 or job.name_called) and \
                    ATTENTION_LABELS.index(top) < ATTENTION_LABELS.index(rules_top):
                tried.append((d.name, f"below floor {top} < {rules_top}"))   # an alert is never talked down
                continue
            directed = None
            if job.ask_directed:
                try:
                    directed = self._ask(d, d.directed, job)
                except Exception as e:  # noqa: BLE001  (attention stands; directed just goes missing)
                    tried.append((d.name, f"directed {type(e).__name__}"))
                if directed is not None and job.name_called:     # the wearer's name is the strongest evidence
                    directed = max(directed, self.rules.directed(job))
            tried.append((d.name, "ok"))
            return Decision(top, round(float(probs[top]), 3), d.name, {k: round(float(v), 3) for k, v in probs.items()},
                            directed=None if directed is None else round(float(directed), 3), rules_attention=rules_top,
                            ms=(time.perf_counter() - t0) * 1000, tried=tried)
        tried.append(("rules", "ok"))
        return Decision(rules_top, 1.0, "rules", rules_probs, directed=self.rules.directed(job) if job.ask_directed
                        else None, rules_attention=rules_top, ms=(time.perf_counter() - t0) * 1000, tried=tried)


class AttentionWorker:
    """One thread. submit(job) never blocks: queue of `queue_max` drops the oldest; identical states within
    `cache_s` reuse the decision; at most `max_rate_hz` decisions per second. on_decision(job, decision) runs on
    the worker thread (hand it to the event loop yourself)."""

    def __init__(self, decider, on_decision, queue_max=4, cache_s=5.0, max_rate_hz=5.0, clock=time.monotonic):
        self.decider = decider
        self.on_decision = on_decision
        self.queue = deque(maxlen=queue_max)
        self.cache = OrderedDict()                      # (state, ask_directed) -> (t, Decision)
        self.cache_s = cache_s
        self.min_gap = 1.0 / max_rate_hz if max_rate_hz else 0.0
        self.clock = clock
        self.cv = threading.Condition()
        self.dropped = 0
        self.stopped = False
        self.thread = threading.Thread(target=self._run, name="attention", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        with self.cv:
            self.stopped = True
            self.cv.notify()

    def submit(self, job):
        with self.cv:
            if len(self.queue) == self.queue.maxlen:
                self.dropped += 1
            self.queue.append(job)
            self.cv.notify()

    def process(self, job):
        """Decide one job now (the thread calls this; tests may too)."""
        key = (job.state, job.ask_directed, job.priority, job.name_called)
        now = self.clock()
        hit = self.cache.get(key)
        if hit is not None and now - hit[0] <= self.cache_s:
            d = hit[1]
            d = Decision(**{**d.__dict__, "ms": 0.0, "tried": [("cache", "hit")]})
        else:
            d = self.decider.decide(job)
            self.cache[key] = (now, d)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
        self.on_decision(job, d)
        return d

    def _run(self):
        last = 0.0
        while True:
            with self.cv:
                while not self.queue and not self.stopped:
                    self.cv.wait()
                if self.stopped:
                    return
                job = self.queue.popleft()
            gap = self.min_gap - (self.clock() - last)
            if gap > 0:
                time.sleep(gap)
            last = self.clock()
            try:
                self.process(job)
            except Exception as e:  # noqa: BLE001  (never kill the thread)
                print(f"[attention] {job.key}: {type(e).__name__}: {e}", flush=True)


def build_decider(tiers=None, device=None, log=print):
    """The configured tiers, best first, each loaded if it can be; rules always last."""
    names = list(tiers or getattr(C, "TIERS", [JEV_ID, ZEROSHOT_ID]))
    device = device or getattr(C, "DEVICE", "cuda")
    loaded, timeouts = [], {}
    gpu = False
    try:
        import torch
        gpu = device != "cpu" and torch.cuda.is_available()
    except Exception:  # noqa: BLE001
        pass
    t = dict(getattr(C, "TIMEOUTS_S", None) or {})
    for name in names:
        t0 = time.perf_counter()
        try:
            if name == JEV_ID or name.startswith("autotrust/"):
                if not gpu:
                    log(f"[attention] {name} skipped: needs the GPU")
                    continue
                d = JevDecider(name, quant=getattr(C, "JEV_QUANT", "nf4"))
                timeouts[name] = float(t.get("jev", 0.6))
            else:
                d = ZeroShotDecider(name, device=device)
                timeouts[name] = float(t.get("zeroshot_gpu" if gpu else "zeroshot_cpu", 0.3 if gpu else 1.5))
            loaded.append(d)
            log(f"[attention] {name} ready ({time.perf_counter() - t0:.1f} s)")
        except Exception as e:  # noqa: BLE001
            log(f"[attention] {name} unavailable ({type(e).__name__}: {e}); next tier")
    return TieredDecider(loaded, timeouts, float(getattr(C, "MIN_CONFIDENCE", 0.5)))


# ---- CLI: tuning on the demo states (CPU) and the tier-1 gates (GPU) -----------------------------------

def run_states(decider, rows):
    from .states import DEMO_STATES  # noqa: F401
    ok = {"all": 0, "obvious": 0}
    n = {"all": 0, "obvious": 0}
    lat = []
    out = []
    for state, want, want_dir, kind in rows:
        speech = state.startswith("Someone nearby said")
        job = Job("bench", "speech" if speech else "sound", state, priority=0, ask_directed=speech,
                  name_called="wearer's name" in state)
        t0 = time.perf_counter()
        d = decider.decide(job)
        lat.append((time.perf_counter() - t0) * 1000)
        good = d.attention == want
        for k in ("all", kind):
            if k in n:
                n[k] += 1
                ok[k] += good
        out.append((good, d, want, want_dir, state))
        dd = "" if d.directed is None else f" directed {d.directed:.2f} (want {want_dir})"
        print(f"{'OK ' if good else 'xx '} {d.attention:9s} {d.confidence:.2f} [{d.model.split('/')[-1]}] "
              f"want {want:9s} rules {d.rules_attention:9s}{dd} {d.ms:5.0f} ms | {state[:80]}")
    lat.sort()
    print(f"\nagree {ok['all']}/{n['all']} (obvious {ok['obvious']}/{n['obvious']}); "
          f"median {lat[len(lat) // 2]:.0f} ms, worst {lat[-1]:.0f} ms")
    return out, lat


def main():
    p = argparse.ArgumentParser(prog="python -m laptop.enrich.attention", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--states", action="store_true", help="tier 2 (+ rules) on the labelled demo states")
    p.add_argument("--bench", action="store_true", help="GPU: JEV-9B quantized, the ATTENTION.md gates")
    p.add_argument("--quant", default=None, help="nf4 (default) | int8 | bf16")
    p.add_argument("--device", default=None)
    a = p.parse_args()
    from .states import DEMO_STATES
    if a.bench:
        bench_jev(a.quant or getattr(C, "JEV_QUANT", "nf4"), DEMO_STATES)
        return
    names = [ZEROSHOT_ID] if a.states else None
    d = build_decider(names, a.device or ("cpu" if a.states else None))
    d.min_confidence = 0.0 if a.states else d.min_confidence     # show the model's own answer
    run_states(d, DEMO_STATES)


def bench_jev(quant, rows):
    """Gates 1, 3, 4 of ATTENTION.md on Kori's laptop (gate 2 = golden + whisper latency with this loaded)."""
    import torch
    print(f"=== JEV-9B ({quant}) on {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU'}")
    t0 = time.perf_counter()
    jev = JevDecider(JEV_ID, quant=quant)
    print(f"load {time.perf_counter() - t0:.0f} s; VRAM allocated {torch.cuda.memory_allocated() / 2 ** 30:.1f} GB "
          f"(gate 1: whisper + PANNs + this under 14.5 GB in nvidia-smi)")
    jev.attention(Job("warm", "sound", rows[0][0]))
    tier = TieredDecider([jev], {jev.name: 5.0}, min_confidence=0.0)
    out, lat = run_states(tier, rows)
    med, worst = lat[len(lat) // 2], lat[-1]
    print(f"gate 3 (median <= 250 ms, worst <= 600 ms): {'PASS' if med <= 250 and worst <= 600 else 'FAIL'}")
    print("gate 4: compare with tier 2 on the same states:")
    try:
        z = TieredDecider([ZeroShotDecider(device="cuda")], {ZEROSHOT_ID: 5.0}, min_confidence=0.0)
        out2, _ = run_states(z, rows)
        obvious = [(a[0], b[0]) for a, b, r in zip(out, out2, rows) if r[3] == "obvious"]
        amb = [(a[0], b[0]) for a, b, r in zip(out, out2, rows) if r[3] != "obvious"]
        print(f"obvious: JEV {sum(x for x, _ in obvious)}/{len(obvious)}, tier 2 {sum(y for _, y in obvious)}/"
              f"{len(obvious)}; ambiguous: JEV {sum(x for x, _ in amb)}/{len(amb)}, tier 2 {sum(y for _, y in amb)}/"
              f"{len(amb)}")
    except Exception as e:  # noqa: BLE001
        print(f"tier 2 unavailable: {e}")
    print(f"peak VRAM {torch.cuda.max_memory_allocated() / 2 ** 30:.1f} GB")


if __name__ == "__main__":
    main()
