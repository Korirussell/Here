"""Zero-shot relevance scores for the Replay Lab (docs/ATTENTION.md tier 2), on CPU.

score(text) = 1 - P("casual small talk") among RELEVANCE_LABELS, from MoritzLaurer/deberta-v3-base-zeroshot-v2.0's
entailment logits (one premise/hypothesis pair per label, softmax across labels). Utterances only: on one-word sound
labels ("Speech", "Vehicle") the model's scores were meaningless, and the rules handle sounds.

Labels chosen 2026-10-10 on the seeded day's 51 utterances against its hand-set attention (weak ground truth):
AUC 0.73 with mean 0.79 important vs 0.58 casual. "hazard / talking to me / important / background noise" ranked
about as well (0.75) but squeezed everything into 0.82-0.94; plain "important / not important" was worst (0.68). Called on the model directly, with
a few threads: the transformers pipeline with its default thread count took 10-20 s per text on a laptop
i7-1250U (2 performance + 8 efficiency cores); this takes ~0.3 s.

Replay never waits for the model: `cached(text)` returns a score or None, and `request(texts)` queues the missing
ones for a background thread. Scores persist in extras/memory/data/relevance_cache.json, keyed by model id.
"""

import json
import threading
from collections import OrderedDict

from . import config

RELEVANCE_LABELS = ["a warning or emergency", "a question or request for the listener",
                    "a date, deadline or plan to remember", "casual small talk"]
NOT_RELEVANT = "casual small talk"
TEMPLATE = "This example is {}."
CACHE_PATH = config.DATA_DIR / "relevance_cache.json"
BATCH = 8                                   # texts per forward pass (x4 hypotheses)
QUEUE_MAX = 2000


class RelevanceScorer:
    def __init__(self, model_id=config.ZERO_SHOT_MODEL, cache_path=CACHE_PATH, log=print):
        self.model_id = model_id
        self.cache_path = cache_path
        self.log = log
        self.key = f"{model_id}|{'|'.join(RELEVANCE_LABELS)}"     # new labels = new cache
        self.scores = {}
        self.pending = OrderedDict()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.model = self.tokenizer = None
        self.entail = None
        self.error = None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            self.scores = data.get(self.key, {})
        except (OSError, ValueError):
            pass

    # ---- never blocks ----------------------------------------------------------------------------------

    def cached(self, text):
        return self.scores.get(text) if text else None

    def request(self, texts):
        """Queue texts that have no score yet; starts the worker on first use."""
        added = 0
        with self.lock:
            for t in texts:
                if t and t not in self.scores and t not in self.pending and len(self.pending) < QUEUE_MAX:
                    self.pending[t] = None
                    added += 1
        if added and self.error is None:
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="relevance", daemon=True)
                self.thread.start()
            self.wake.set()
        return added

    def status(self):
        return {"model": self.model_id, "scored": len(self.scores), "pending": len(self.pending),
                "error": self.error}

    # ---- the worker ------------------------------------------------------------------------------------

    def _load(self):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        torch.set_num_threads(int(config.env("HERE_REPLAY_MODEL_THREADS", config.REPLAY_MODEL_THREADS)))
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id)
        self.model = AutoModelForSequenceClassification.from_pretrained(self.model_id).eval()
        label2id = {k.lower(): v for k, v in self.model.config.label2id.items()}
        self.entail = label2id.get("entailment", 0)

    def score_batch(self, texts):
        import torch
        if self.model is None:
            self._load()
        n = len(RELEVANCE_LABELS)
        premises = [t for t in texts for _ in range(n)]
        hyps = [TEMPLATE.format(lbl) for _ in texts for lbl in RELEVANCE_LABELS]
        enc = self.tokenizer(premises, hyps, return_tensors="pt", padding=True, truncation=True, max_length=128)
        with torch.inference_mode():
            logits = self.model(**enc).logits[:, self.entail].reshape(len(texts), n)
        probs = torch.softmax(logits, dim=1)
        noise = RELEVANCE_LABELS.index(NOT_RELEVANT)
        return {t: round(1.0 - float(p[noise]), 3) for t, p in zip(texts, probs)}

    def _run(self):
        while True:
            self.wake.wait()
            with self.lock:
                batch = list(self.pending)[:BATCH]
            if not batch:
                self.wake.clear()
                self._save()
                continue
            try:
                got = self.score_batch(batch)
            except Exception as e:                          # model missing / no torch: replay stays rules-only
                with self.lock:
                    self.pending.clear()                    # before `error`, which wait_idle() watches
                self.error = f"{type(e).__name__}: {e}"[:200]
                self.log(f"[memory] relevance model unavailable ({self.error}); replay stays deterministic")
                return
            with self.lock:
                self.scores.update(got)
                for t in batch:
                    self.pending.pop(t, None)

    def _save(self):
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            data[self.key] = dict(sorted(self.scores.items()))
            self.cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
        except OSError:
            pass

    def wait_idle(self, timeout=None):
        """For tools and tests: block until the queue is empty."""
        import time
        t0 = time.time()
        while self.pending and self.error is None and (timeout is None or time.time() - t0 < timeout):
            time.sleep(0.1)
        return not self.pending


_scorer = None


def scorer():
    """The process-wide scorer when HERE_REPLAY_MODEL=1 (or config.REPLAY_MODEL_SCORES), else None."""
    global _scorer
    if config.env("HERE_REPLAY_MODEL", "1" if config.REPLAY_MODEL_SCORES else "0") != "1":
        return None
    if _scorer is None:
        _scorer = RelevanceScorer()
    return _scorer
