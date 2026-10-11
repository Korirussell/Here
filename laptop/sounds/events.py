"""AudioSet scores -> contract SoundLabels -> merged sound_events (HERE_PLAN.md §8.6). No model or I/O here."""

import itertools
import time

from . import config as C


class Labeler:
    """Maps the classifier's 527 AudioSet probabilities onto contract SoundLabels."""

    def __init__(self, class_names, label_map=C.LABEL_MAP):
        index = {n: i for i, n in enumerate(class_names)}
        self.class_names = list(class_names)
        self.missing = [n for names in label_map.values() for n in names if n not in index]
        self.groups = {label: [index[n] for n in names if n in index] for label, names in label_map.items()}
        # Music genres, for detail ("Christmas music"); "Music" itself and instruments are excluded.
        self.genres = [i for i, n in enumerate(class_names) if n.endswith(" music") or n.startswith("Music of ")]

    def scores(self, probs):
        """{label: (score, raw_label)} where score is the best of that label's AudioSet classes."""
        out = {}
        for label, idx in self.groups.items():
            if idx:
                best = max(idx, key=lambda i: probs[i])
                out[label] = (float(probs[best]), self.class_names[best])
        return out

    def genre(self, probs):
        if not self.genres:
            return None
        best = max(self.genres, key=lambda i: probs[i])
        return self.class_names[best] if probs[best] >= C.GENRE_MIN else None


class Events:
    """Turns per-window detections into sound_events: one event per label, repeats merged into `count`."""

    def __init__(self, thresholds=None, default=None, merge_gap_s=C.MERGE_GAP_S,
                 publish_speech=C.PUBLISH_SPEECH):
        self.thresholds = C.THRESHOLDS if thresholds is None else thresholds
        self.default = C.DEFAULT_THRESHOLD if default is None else default
        self.merge_gap_s = merge_gap_s
        self.publish_speech = publish_speech
        self.open = {}                              # label -> event dict (contract fields + hit_last)
        self.ids = itertools.count(1)
        self.prefix = f"s{int(time.time()) % 100000}"

    def update(self, w0, w1, scores, detail=None):
        """Feed one classified window [w0, w1]. Returns the sound_event fields to publish now."""
        for label in [k for k, e in self.open.items() if w1 - e["t_end"] > self.merge_gap_s]:
            del self.open[label]
        out = []
        for label, (score, raw) in scores.items():
            if label == "speech" and not self.publish_speech:
                continue
            ev = self.open.get(label)
            if score < self.thresholds.get(label, self.default):
                if ev is not None:
                    ev["hit_last"] = False
                continue
            if ev is None:
                ev = self.open[label] = {"event_id": f"{self.prefix}-{next(self.ids)}", "label": label,
                                         "raw_label": raw, "confidence": score, "t_start": w0, "t_end": w1,
                                         "count": 1, "hit_last": True}
            else:
                if not ev["hit_last"]:
                    ev["count"] += 1                # a fresh onset after a quiet window: bark x2, x3...
                ev["t_end"] = max(ev["t_end"], w1)
                ev["hit_last"] = True
                if score > ev["confidence"]:
                    ev["confidence"], ev["raw_label"] = score, raw
            msg = {k: v for k, v in ev.items() if k != "hit_last"}
            msg["confidence"] = round(msg["confidence"], 3)
            if label == "music" and detail:
                msg["detail"] = detail
            out.append(msg)
        return out
