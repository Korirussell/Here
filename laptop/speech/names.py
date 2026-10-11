"""Name-called detection: fuzzy match of the user's name and aliases in transcripts (README §13, §15).

Two checks per word, either is enough:
- spelling: difflib ratio >= name_match_ratio ("Korri" ~ "Kori")
- sound: a small phonetic key, because the transcriber spells names however it likes
  ("Kori" is transcribed "Cory", "Corey", "Kory": all key to "kori")
"""

import re
from difflib import SequenceMatcher


def norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower()).removesuffix("'s").replace("'", "")


def sound_key(word: str) -> str:
    """Rough English sounds-alike key: c/k/q/ck -> k, ph -> f, soft c -> s, y/ey/ie -> i, no doubles, no silent h."""
    w = norm(word)
    w = re.sub(r"ph", "f", w)
    w = re.sub(r"c(?=[eiy])", "s", w)
    w = re.sub(r"ck|q|c", "k", w)
    w = re.sub(r"(ey|ie|y|ee)$", "i", w)
    w = re.sub(r"(?<=[a-z])h", "", w)
    w = re.sub(r"(.)\1+", r"\1", w)
    return w


class NameMatcher:
    def __init__(self, names=(), ratio=0.8):
        self.ratio = ratio
        self.set_names(names)

    def set_names(self, names):
        # Multi-word names ("Kori Russell") match on any part; very short parts are skipped (too many false hits).
        parts = {norm(p) for n in names if n for p in n.split()}
        self.names = sorted(p for p in parts if len(p) >= 3)
        self.keys = {n: sound_key(n) for n in self.names}

    def find(self, text: str):
        """(name, score) of the best match in `text`, or None."""
        best = None
        for raw in text.split():
            w = norm(raw)
            if len(w) < 3:
                continue
            wk = sound_key(w)
            for n in self.names:
                r = 1.0 if w == n else SequenceMatcher(None, w, n).ratio()
                if wk == self.keys[n]:
                    r = max(r, 0.9)
                if r >= self.ratio and (best is None or r > best[1]):
                    best = (n, round(r, 2))
        return best
