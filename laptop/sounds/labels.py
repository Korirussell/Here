"""How each contract SoundLabel / AlertKind is shown (SPEC §12.3, §15). Data, not tunables."""

# label -> (category, emoji, text)
SOUND_DISPLAY = {
    "speech": ("speech", "💬", "Speech"),
    "doorbell": ("alert", "🔔", "Doorbell"),
    "dog_barking": ("animal", "🐕", "Dog barking"),
    "baby_crying": ("alert", "👶", "Baby crying"),
    "knock": ("alert", "🚪", "Knock"),
    "alarm": ("alert", "🚨", "Alarm"),
    "music": ("media", "🎵", "Music"),
    "tv": ("media", "📺", "TV"),
    "laughter": ("speech", "😂", "Laughter"),
    "phone_ringing": ("alert", "📱", "Phone ringing"),
    "loud_bang": ("alert", "💥", "Loud bang"),          # never "gunshot" (SPEC §15)
    "car_horn": ("alert", "📯", "Car horn"),
    "vehicle": ("other", "🚗", "Vehicle"),
    "siren": ("alert", "🚑", "Siren"),
    "door": ("other", "🚪", "Door"),
    "other": ("other", "🔊", "Sound"),                 # recognised, not one of ours
    "unknown": ("other", "❔", "Unknown sound"),       # heard, not classifiable: keeps the direction
}

# alert kind -> (label, emoji, priority, belt pattern on the phone)
ALERT_DISPLAY = {
    "name_called": ("Name called", "🗣️", 2),
    "doorbell": ("Doorbell", "🔔", 2),
    "knock": ("Knock", "🚪", 2),
    "dog_barking": ("Dog barking", "🐕", 1),
    "baby_crying": ("Baby crying", "👶", 3),
    "alarm": ("Alarm", "🚨", 3),
    "loud_bang": ("Loud bang", "💥", 2),
    "car_horn": ("Car horn", "📯", 2),
    "siren": ("Siren", "🚑", 3),
    "door": ("Door", "🚪", 1),
}

# sound labels that raise an alert of the same name (when published: sounds.publish in config.yaml)
ALERTING_LABELS = {"doorbell", "dog_barking", "baby_crying", "knock", "alarm", "loud_bang", "car_horn", "siren", "door"}
# alert sounds that, heard clearly, overrule an enrolled-template match. Not "door": AudioSet's Door fires on doorbells.
OVERRULING_LABELS = ALERTING_LABELS - {"door"}
