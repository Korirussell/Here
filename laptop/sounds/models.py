"""Sound classifier backends: 16 kHz int16 audio in, contract SoundLabel scores out (README §12.7, §18).

One `Backend` per model family, all behind the same call so the live pipeline (classifier.py) and the bakeoff
(tools/sound_bench.py) run exactly the same code:

- `panns_cnn14`: PANNs CNN14 (AudioSet, 32 kHz), the known-good fallback.
- a Hugging Face AudioSet tagger (`MIT/ast-finetuned-audioset-10-10-0.4593`, `mispeech/ced-base`, ...): 527
  AudioSet probabilities, mapped onto SoundLabels by AudioSet index with the same `label_map` as PANNs.
- a Hugging Face CLAP model (`laion/larger_clap_general`): zero-shot against text prompts per SoundLabel.

    backend = load_backend("mispeech/ced-base", device="cpu")
    scores, genre = backend(pcm16k)      # {label: (score, raw class)}, music genre or None

Model ids, prompts and per-model thresholds live in laptop/config.yaml (`sounds:`).
"""

import csv
import hashlib
import time
import urllib.request
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from . import config as C
from .events import Labeler

SR = 16000
PANNS_ID = "panns_cnn14"


def ensure_panns_files(checkpoint=True):
    """panns_inference shells out to wget for these on import, which Windows lacks; fetch them first."""
    d = Path(C.MODEL_DIR).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    files = [("class_labels_indices.csv", C.LABELS_URL)]
    if checkpoint:
        files.append(("Cnn14_mAP=0.431.pth", C.CHECKPOINT_URL))
    for name, url in files:
        path = d / name
        if not path.exists() or path.stat().st_size < 1000:
            print(f"[sounds] downloading {name} ...")
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            want = getattr(C, "CHECKPOINT_MD5", None) if name.endswith(".pth") else None
            if want:     # a resumed download once gave a same-size, corrupt file: "Music 0.66" for everything
                h = hashlib.md5()
                with open(tmp, "rb") as f:
                    for block in iter(lambda: f.read(1 << 20), b""):
                        h.update(block)
                if h.hexdigest() != want:
                    tmp.unlink()
                    raise OSError(f"{name}: md5 {h.hexdigest()} != {want} (corrupt download, deleted)")
            tmp.replace(path)
    return d / "Cnn14_mAP=0.431.pth"


def audioset_names():
    """The 527 AudioSet display names in index order (the names `label_map` uses), from PANNs' csv."""
    ensure_panns_files(checkpoint=False)
    path = Path(C.MODEL_DIR).expanduser() / "class_labels_indices.csv"
    with open(path, encoding="utf-8") as f:
        return [row["display_name"] for row in csv.DictReader(f)]


def pick_device(want):
    import torch
    device = want if (want != "cpu" and torch.cuda.is_available()) else "cpu"
    try:
        torch.zeros(1, device=device)
    except Exception as e:  # e.g. CUDA runtime newer than the driver
        print(f"[sounds] CUDA unusable ({e}); using CPU")
        device = "cpu"
    return device


def to_float(pcm16k, sr):
    x = pcm16k.astype(np.float32) / 32768.0
    return x if sr == SR else resample_poly(x, sr, SR).astype(np.float32)


class Backend:
    """A loaded model. `model_id` is what `sound_event.model` says; `__call__` is thread-safe enough for the one
    executor thread the pipeline uses."""

    model_id = "?"
    device = "cpu"
    load_s = 0.0

    def __call__(self, pcm16k):
        raise NotImplementedError

    def warm(self, seconds=1.0):
        """The first inference allocates buffers (and on CUDA initialises cuDNN): do it before the live audio."""
        self(np.zeros(int(seconds * SR), np.int16))


class AudioSetBackend(Backend):
    """Any model with 527 AudioSet outputs: probs -> SoundLabels with the shared label_map."""

    def __init__(self, label_map=None):
        self.labeler = Labeler(audioset_names(), C.LABEL_MAP if label_map is None else label_map)
        if self.labeler.missing:
            print(f"[sounds] warning: unknown AudioSet classes in label_map: {self.labeler.missing}")

    def probs(self, pcm16k):
        raise NotImplementedError

    def __call__(self, pcm16k):
        p = self.probs(pcm16k)
        return self.labeler.scores(p), self.labeler.genre(p)


class PannsBackend(AudioSetBackend):
    model_id = PANNS_ID

    def __init__(self, device="cuda", label_map=None):
        t0 = time.perf_counter()
        checkpoint = ensure_panns_files()
        super().__init__(label_map)
        from panns_inference import AudioTagging
        self.device = pick_device(device)
        self.model = AudioTagging(checkpoint_path=str(checkpoint), device=self.device)
        self.load_s = time.perf_counter() - t0

    def probs(self, pcm16k):
        probs, _ = self.model.inference(to_float(pcm16k, C.MODEL_SR)[None, :])
        return probs[0]


class HFAudioSetBackend(AudioSetBackend):
    """transformers AutoModelForAudioClassification with an AudioSet head (AST, CED).

    min_s: inputs shorter than this are zero-padded (CED's patch embedding needs ~1 s); AST always pads to its
    fixed 10.24 s itself. sigmoid: whether the model returns logits (AST) or already probabilities (CED)."""

    def __init__(self, repo, device="cuda", revision=None, trust_remote_code=False, sigmoid=True, min_s=0.0,
                 label_map=None):
        t0 = time.perf_counter()
        super().__init__(label_map)
        import torch
        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification
        self.torch = torch
        self.model_id = repo
        self.device = pick_device(device)
        kw = {"revision": revision, "trust_remote_code": trust_remote_code}
        self.extractor = AutoFeatureExtractor.from_pretrained(repo, **kw)
        self.model = AutoModelForAudioClassification.from_pretrained(repo, **kw).to(self.device).eval()
        n = self.model.config.num_labels
        if n != 527:
            raise ValueError(f"{repo}: {n} outputs, expected the 527 AudioSet classes")
        self.sr = int(getattr(self.extractor, "sampling_rate", SR))
        self.sigmoid, self.min_s = sigmoid, min_s
        self.load_s = time.perf_counter() - t0

    def probs(self, pcm16k):
        x = to_float(pcm16k, self.sr)
        if len(x) < self.min_s * self.sr:
            x = np.pad(x, (0, int(self.min_s * self.sr) - len(x)))
        inputs = self.extractor(x, sampling_rate=self.sr, return_tensors="pt")
        with self.torch.inference_mode():
            out = self.model(**{k: v.to(self.device) for k, v in inputs.items()}).logits[0]
            if self.sigmoid:
                out = out.sigmoid()
        return out.float().cpu().numpy()


class ClapBackend(Backend):
    """CLAP zero-shot: cosine similarity of the audio to text prompts, softmax over every prompt (SoundLabels plus
    `background` prompts that map to nothing), score per SoundLabel = the sum of its prompts' probabilities."""

    def __init__(self, repo, prompts, device="cuda", revision=None):
        t0 = time.perf_counter()
        import torch
        from transformers import ClapModel, ClapProcessor
        self.torch = torch
        self.model_id = repo
        self.device = pick_device(device)
        self.processor = ClapProcessor.from_pretrained(repo, revision=revision)
        self.model = ClapModel.from_pretrained(repo, revision=revision).to(self.device).eval()
        self.owner, texts = [], []
        for label, ps in prompts.items():
            for p in ps:
                self.owner.append(label)
                texts.append(p)
        self.texts = texts
        with torch.inference_mode():
            t = self.processor(text=texts, return_tensors="pt", padding=True)
            emb = self.model.get_text_features(**{k: v.to(self.device) for k, v in t.items()})
            self.text_emb = torch.nn.functional.normalize(emb, dim=-1)
        self.sr = int(self.processor.feature_extractor.sampling_rate)
        self.load_s = time.perf_counter() - t0

    def __call__(self, pcm16k):
        torch = self.torch
        x = to_float(pcm16k, self.sr)
        with torch.inference_mode():
            a = self.processor(audios=x, sampling_rate=self.sr, return_tensors="pt")
            emb = self.model.get_audio_features(**{k: v.to(self.device) for k, v in a.items()})
            emb = torch.nn.functional.normalize(emb, dim=-1)
            p = ((emb @ self.text_emb.T) * self.model.logit_scale_a.exp()).softmax(-1)[0].float().cpu().numpy()
        out = {}
        for i, label in enumerate(self.owner):
            if label == "background":
                continue
            s, raw = out.get(label, (0.0, ""))
            out[label] = (s + float(p[i]), raw if raw and s >= p[i] else self.texts[i])
        return out, None


def load_backend(model_id, device=None, spec=None):
    """model_id: "panns_cnn14" or a Hugging Face repo id; spec: its `sounds.models` entry (kind, revision...)."""
    spec = dict(spec or getattr(C, "MODELS", {}).get(model_id) or {})
    device = device or C.DEVICE
    kind = spec.get("kind", "panns" if model_id == PANNS_ID else "audioset")
    if kind == "panns":
        b = PannsBackend(device)
    elif kind == "audioset":
        b = HFAudioSetBackend(model_id, device, revision=spec.get("revision"),
                              trust_remote_code=bool(spec.get("trust_remote_code", False)),
                              sigmoid=bool(spec.get("sigmoid", True)), min_s=float(spec.get("min_s", 0.0)))
    elif kind == "clap":
        b = ClapBackend(model_id, spec.get("prompts") or C.CLAP_PROMPTS, device, revision=spec.get("revision"))
    else:
        raise ValueError(f"unknown sounds model kind {kind!r} for {model_id}")
    t0 = time.perf_counter()
    b.warm(C.WINDOW_S)
    b.warm_s = time.perf_counter() - t0
    return b
