"""laptop.sounds.enroll without hardware: trimming, the noise check, and the config rewrite."""

import numpy as np
import yaml

from laptop.audio.mock import read_wav
from laptop.settings import CONFIG_PATH, ROOT
from laptop.sounds import enroll

SR = 16000
BELL = read_wav(ROOT / "fixtures" / "sounds" / "doorbell.wav")


def room(seconds, seed=0):
    return (np.random.default_rng(seed).standard_normal(int(seconds * SR)) * 60).astype(np.int16)


def test_trim_finds_the_sound_in_a_quiet_recording():
    x = np.concatenate([room(3.0), BELL, room(3.0, 1)])
    pcm, t0, t1 = enroll.trim(x)
    bell_s = len(BELL) / SR
    assert abs(t0 - 3.0) < 0.15 and abs(t1 - (3.0 + bell_s)) < 0.4
    hit, miss, threshold = enroll.check(pcm, x, t0, t1, "doorbell")
    assert hit > 0.3 and miss is not None and miss < 0.1 and 0.12 <= threshold < hit


def test_trim_refuses_silence():
    assert enroll.trim(room(5.0)) is None


def test_update_config_replaces_the_label_and_adds_new_ones(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    enroll.update_config("doorbell", "fixtures/sounds/doorbell_hall.wav", 0.23, cfg)
    enroll.update_config("knock", "fixtures/sounds/knock.wav", 0.3, cfg)
    s = yaml.safe_load(cfg.read_text(encoding="utf-8"))["sounds"]
    by_label = {e["label"]: e for e in s["enrolled"]}
    assert by_label["doorbell"] == {"label": "doorbell", "file": "fixtures/sounds/doorbell_hall.wav", "threshold": 0.23}
    assert by_label["knock"]["file"] == "fixtures/sounds/knock.wav" and len(s["enrolled"]) == 2
    before = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    published = before["sounds"]["publish"]
    assert s["publish"] == published + ([] if "knock" in published else ["knock"])   # added once, never twice
    after = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert {k: v for k, v in after.items() if k != "sounds"} == {k: v for k, v in before.items() if k != "sounds"}
