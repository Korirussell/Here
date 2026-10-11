

def test_head_template_matches_the_start_of_the_sound_early():
    """Array tuning proposal (b): the first 0.5 s of the enrolled doorbell, matched ~0.4 s after its onset, alerts at ~0.4-0.55 s
    from onset on the real array (fixtures/array_tts 920 -> 389 ms, k1_room_live2 1128 -> 555 ms), never on
    220 s of real chatter (fixtures/k1_positions: head score <= 0.17, threshold 0.25)."""
    import numpy as np
    from laptop.audio.mock import read_wav
    from laptop.sounds.enrolled import EnrolledMatcher, SR
    from laptop.settings import ROOT
    m = EnrolledMatcher([{"label": "doorbell", "file": "fixtures/sounds/doorbell.wav", "threshold": 0.12,
                          "head_s": 0.5, "head_threshold": 0.25}], ROOT)
    bell = read_wav(ROOT / "fixtures" / "sounds" / "doorbell.wav").astype(np.float32)
    rng = np.random.default_rng(1)
    room = rng.standard_normal(SR).astype(np.float32) * 200           # 1 s of room before the bell
    m.add(np.concatenate([room, bell[: int(0.4 * SR)]]).astype(np.int16))   # 0.4 s into the ding
    assert "doorbell" in m.head_scores(1.0)
    m2 = EnrolledMatcher([{"label": "doorbell", "file": "fixtures/sounds/doorbell.wav", "head_s": 0.5}], ROOT)
    m2.add((rng.standard_normal(2 * SR) * 2000).astype(np.int16))        # noise only
    assert m2.head_scores(1.0) == {}
    m3 = EnrolledMatcher([{"label": "doorbell", "file": "fixtures/sounds/doorbell.wav"}], ROOT)
    m3.add(np.concatenate([room, bell[: int(0.4 * SR)]]).astype(np.int16))
    assert m3.head_scores(1.0) == {}                                      # no head_s configured: off
