"""How it sounded (SPEC §14 accessibility rules: tone describes sound, not emotion).

Simple, explainable rules on loudness, the transcript, and the sound classifier: PANNs scores
"laughter" every 0.5 s on the same audio, and recent laughter during an utterance makes it "laughing".
Returns (tone, confidence); the phone fades the chip when confidence is low.
"""

import re

LAUGH = re.compile(r"\b(?:(?:ha|he|hah)[\s,.!]*){2,}|\blol\b|\((?:laugh|laughs|laughing|laughter)\)|"
                   r"\[(?:laugh|laughs|laughing|laughter)\]", re.I)   # whisper writes laughs as "Ha, ha, ha."


def classify(text: str, loudness: float, final: bool, shout=0.8, whisper=0.12, laugh=0.0, laugh_min=0.3):
    """laugh: the highest PANNs laughter score heard during the utterance (0 when sounds are off)."""
    t = text.strip()
    if not t:
        return None, 0.0
    if LAUGH.search(t):
        return "laughing", 0.7
    if laugh >= laugh_min:
        return "laughing", round(min(1.0, 0.5 + laugh / 2), 2)
    if loudness >= shout:
        return "shouting", round(min(1.0, 0.6 + (loudness - shout) * 2), 2)
    if loudness <= whisper:
        return "whispering", round(min(1.0, 0.6 + (whisper - loudness) * 3), 2)
    if t.endswith("?"):
        return "questioning", 0.8 if final else 0.5
    if t.endswith("!"):
        return "excited", 0.6 if final else 0.4
    return "neutral", 0.5 if final else 0.3
