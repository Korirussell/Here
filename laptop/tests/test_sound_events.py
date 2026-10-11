"""sound_here label mapping and event merging (sound_here/events.py)."""

from laptop.sounds.events import Events, Labeler

NAMES = ["Speech", "Bark", "Dog", "Doorbell", "Music", "Christmas music", "Pop music", "Silence"]
MAP = {"speech": ["Speech"], "dog_barking": ["Bark", "Dog"], "doorbell": ["Doorbell", "Ding-dong"],
       "music": ["Music"]}


def probs(**kw):
    p = [0.0] * len(NAMES)
    for name, v in kw.items():
        p[NAMES.index(name.replace("_", " "))] = v
    return p


def test_labeler_takes_best_class_and_reports_missing():
    lab = Labeler(NAMES, MAP)
    assert lab.missing == ["Ding-dong"]
    s = lab.scores(probs(Bark=0.2, Dog=0.7))
    assert s["dog_barking"] == (0.7, "Dog")


def test_labeler_genre():
    lab = Labeler(NAMES, MAP)
    assert lab.genre(probs(Christmas_music=0.4, Pop_music=0.1)) == "Christmas music"
    assert lab.genre(probs(Pop_music=0.05)) is None


def ev(**scores):
    return {k: (v, k) for k, v in scores.items()}


def test_continuous_sound_is_one_event_with_growing_end():
    e = Events(thresholds={}, default=0.3)
    a = e.update(0.0, 2.0, ev(doorbell=0.8))
    b = e.update(0.5, 2.5, ev(doorbell=0.6))
    assert a[0]["event_id"] == b[0]["event_id"]
    assert b[0]["count"] == 1 and b[0]["t_start"] == 0.0 and b[0]["t_end"] == 2.5
    assert b[0]["confidence"] == 0.8


def test_repeats_after_a_quiet_window_increment_count():
    e = Events(thresholds={}, default=0.3)
    e.update(0.0, 2.0, ev(dog_barking=0.9))
    assert e.update(0.5, 2.5, ev(dog_barking=0.1)) == []
    out = e.update(1.0, 3.0, ev(dog_barking=0.7))
    assert out[0]["count"] == 2


def test_long_gap_starts_a_new_event():
    e = Events(thresholds={}, default=0.3, merge_gap_s=3.0)
    first = e.update(0.0, 2.0, ev(dog_barking=0.9))[0]["event_id"]
    second = e.update(10.0, 12.0, ev(dog_barking=0.9))[0]
    assert second["event_id"] != first and second["count"] == 1


def test_speech_suppressed_and_thresholds_per_label():
    e = Events(thresholds={"music": 0.5}, default=0.3, publish_speech=False)
    out = e.update(0.0, 2.0, ev(speech=0.9, music=0.4, doorbell=0.35))
    assert [m["label"] for m in out] == ["doorbell"]


def test_music_detail():
    e = Events(thresholds={}, default=0.3)
    out = e.update(0.0, 2.0, ev(music=0.9), detail="Christmas music")
    assert out[0]["detail"] == "Christmas music"
