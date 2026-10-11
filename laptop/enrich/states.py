"""State sentences for the attention models (docs/ATTENTION.md "What it decides"), plus labelled demo states.

One short sentence per event, built only from what the laptop knows: label, confidence, energy, a transcript
snippet, repeats in the last minute, whether a conversation is going on, focus mode. Never a direction relative to
the wearer: the laptop doesn't know the phone's heading ("behind you" is the phone's call).
"""

ATTENTION_LABELS = ["ambient", "notice", "important", "urgent"]

# One-line descriptions: JEV-9B's choice options and the zero-shot hypotheses are built from these.
ATTENTION_OPTIONS = {
    "ambient": "ambient: background sound, safe to ignore",
    "notice": "notice: worth a glance when convenient",
    "important": "important: the wearer should look now",
    "urgent": "urgent: a possible emergency or danger, act immediately",
}

ATTENTION_QUESTION = "How much attention does this sound or caption deserve from a deaf wearer right now?"
DIRECTED_QUESTION = "Is this speech addressed to the wearer?"


def sound_state(label, confidence, energy=None, repeats=0, conversation=False, focus=False, text=None):
    what = text or label.replace("_", " ")
    if label == "unknown":
        head = "An unrecognised sound"
    else:
        head = f"Sound: {what}"
    bits = [f"confidence {confidence:.2f}"]
    if energy is not None:
        bits.append(f"loudness {energy:.1f}")
    s = f"{head} ({', '.join(bits)})."
    s += " The wearer was in a conversation." if conversation else " The room was otherwise quiet."
    s += " No repeats in the last minute." if repeats <= 0 else f" Heard {repeats + 1} times in the last minute."
    s += f" Focus mode {'on' if focus else 'off'}."
    return s


def speech_state(text, tone=None, name_called=False, overlap=False, focus=False, words_max=30):
    words = (text or "").split()
    snippet = " ".join(words[:words_max]) + (" ..." if len(words) > words_max else "")
    s = f'Someone nearby said: "{snippet}".'
    if tone and tone != "neutral":
        s += f" Tone: {tone}."
    if name_called:
        s += " It contains the wearer's name."
    if overlap:
        s += " Other people were talking at the same time."
    s += f" Focus mode {'on' if focus else 'off'}."
    return s


# (state, expected attention, expected directed-at-user or None): gate 4 in docs/ATTENTION.md and tuning on CPU.
# "obvious" = rules and every model should agree; the rest are judgment calls.
DEMO_STATES = [
    (sound_state("siren", 0.85, 0.8), "urgent", None, "obvious"),
    (sound_state("alarm", 0.78, 0.7, conversation=True), "urgent", None, "obvious"),
    (sound_state("baby_crying", 0.62, 0.6), "urgent", None, "obvious"),
    (sound_state("doorbell", 0.71, 0.6, conversation=True), "important", None, "obvious"),
    (sound_state("doorbell", 0.55, 0.5, repeats=2), "important", None, "obvious"),
    (sound_state("car_horn", 0.66, 0.8), "important", None, "obvious"),
    (sound_state("knock", 0.6, 0.5), "important", None, "ambiguous"),
    (sound_state("loud_bang", 0.82, 0.9), "important", None, "ambiguous"),
    (sound_state("door", 0.5, 0.4, conversation=True), "notice", None, "ambiguous"),
    (sound_state("dog_barking", 0.7, 0.5, repeats=4), "notice", None, "ambiguous"),
    (sound_state("vehicle", 0.6, 0.4), "notice", None, "ambiguous"),
    (sound_state("phone_ringing", 0.65, 0.5), "notice", None, "ambiguous"),
    (sound_state("music", 0.8, 0.4, conversation=True), "ambient", None, "obvious"),
    (sound_state("tv", 0.7, 0.3), "ambient", None, "obvious"),
    (sound_state("unknown", 0.3, 0.3, repeats=5), "ambient", None, "obvious"),
    (sound_state("unknown", 0.6, 0.85), "notice", None, "ambiguous"),
    (sound_state("unknown", 0.4, 0.4, focus=True), "ambient", None, "ambiguous"),
    (sound_state("laughter", 0.7, 0.5, conversation=True), "ambient", None, "ambiguous"),
    (sound_state("doorbell", 0.6, 0.5, focus=True), "important", None, "ambiguous"),
    (speech_state("Hey Kori, the pizza is here!", name_called=True), "important", 0.95, "obvious"),
    (speech_state("Kori, watch out!", tone="shouting", name_called=True), "urgent", 0.95, "obvious"),
    (speech_state("Excuse me, can you help me find room 204?", tone="questioning"), "important", 0.8, "ambiguous"),
    (speech_state("Did you see the email about the internship fair?", tone="questioning"), "notice", 0.7,
     "ambiguous"),
    (speech_state("So I told him the meeting was moved to Thursday afternoon.", overlap=True), "ambient", 0.15,
     "ambiguous"),
    (speech_state("The wifi in this building is honestly terrible today.", overlap=True), "ambient", 0.1,
     "ambiguous"),
    (speech_state("Fire! Everybody get out of the building!", tone="shouting"), "urgent", 0.6, "obvious"),
    (speech_state("Can you pass me the charger?", tone="questioning"), "notice", 0.75, "ambiguous"),
    (speech_state("We pivoted twice already and it is only Saturday.", focus=True), "ambient", 0.2, "ambiguous"),
    (speech_state("Kori, your turn to present.", name_called=True, focus=True), "important", 0.9, "obvious"),
    (speech_state("Yes! It's on Friday. Are you going?", tone="excited"), "notice", 0.6, "ambiguous"),
]
