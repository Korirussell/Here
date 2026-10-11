"""audio tunables: the `audio:` section of laptop/config.yaml as UPPER_CASE module attributes.

Kept as a module (not a dict) so the ReSpeaker code reads config exactly as before
(`C.ARRAY_OFFSET_DEG`), including when run as a plain script (`python laptop/audio/xvf3800.py`).
Edit laptop/config.yaml, not this file.
"""

from pathlib import Path

import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
SECTION = "audio"
_TUPLES = {"raw_mic_channels", "srp_band_hz"}


def _load():
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    out = {}
    for key, value in (data.get(SECTION) or {}).items():
        if key in _TUPLES and isinstance(value, list):
            value = tuple(value)
        out[key.upper()] = value
    return out


globals().update(_load())
