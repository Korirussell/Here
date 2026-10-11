"""Attribution and geometry tests (HERE_PLAN.md §6, §9.1). Run: python -m pytest tests"""

import pytest

from here_common.geometry import bearing_from_pixel, circular_mean, motor_for, pixel_from_bearing, wrap
from world_here import config as C
from world_here.fusion import World

T = 1000.0


def test_wrap_range():
    assert wrap(180) == 180
    assert wrap(-180) == 180
    assert wrap(190) == -170
    assert wrap(-190) == 170
    assert wrap(720) == 0


def test_circular_mean_across_seam():
    mean, std = circular_mean([170, -170])
    assert abs(abs(mean) - 180) < 1e-6
    assert std < 15


def test_pixel_bearing_round_trip():
    for b in (-30, 0, 12.5, 30):
        x = pixel_from_bearing(b, 1280, 70, 0)
        assert bearing_from_pixel(x, 1280, 70, 0) == pytest.approx(b)
    assert pixel_from_bearing(100, 1280, 70, 0) is None


def test_motor_for():
    assert [motor_for(b) for b in (0, 45, 90, 135, 180, -135, -90, -45, 20, -20)] == [1, 2, 3, 4, 5, 6, 7, 8, 1, 1]


def detections(w, t, people):
    w.on_detections({"t": t, "frame_id": 1, "objects": [
        {"track_id": tid, "kind": "person", "raw_class": "person", "confidence": 0.9,
         "bbox": [0, 0, 100, 300], "bearing_deg": b} for tid, b in people]})


def doa(w, t0, t1, bearing, speech=True, hz=20):
    n = int((t1 - t0) * hz)
    for i in range(n + 1):
        w.on_doa({"t": t0 + i / hz, "bearing_deg": bearing, "confidence": 0.9, "speech": speech})


def utt(w, uid, t0, t1, final=False, text="hello"):
    return w.on_utterance({"utt_id": uid, "t_start": t0, "t_end": t1, "text": text, "pending": "",
                           "final": final, "tone": None, "tone_confidence": 0, "loudness": 0.5})


def test_attributes_to_nearest_visible_person():
    w = World()
    detections(w, T, [(1, -30), (2, 32)])
    doa(w, T, T + 1, 38)
    assert utt(w, "u1", T, T + 1).speaker_id == 2


def test_beyond_15_degrees_becomes_off_camera_speaker():
    w = World()
    detections(w, T, [(1, 0)])
    doa(w, T, T + 1, 90)
    s = utt(w, "u1", T, T + 1)
    assert s.speaker_id >= C.OFFCAM_ID_START
    assert w.entities[s.speaker_id].bearing == pytest.approx(90)


def test_off_camera_speaker_is_reused_nearby():
    w = World()
    doa(w, T, T + 1, 120)
    first = utt(w, "u1", T, T + 1, final=True).speaker_id
    doa(w, T + 5, T + 6, 132)
    assert utt(w, "u2", T + 5, T + 6).speaker_id == first
    doa(w, T + 10, T + 11, -120)
    assert utt(w, "u3", T + 10, T + 11).speaker_id != first


def test_speaker_sticks_unless_doa_clearly_moves():
    w = World()
    detections(w, T, [(1, 0), (2, 60)])
    doa(w, T, T + 1, 2)
    assert utt(w, "u1", T, T + 1).speaker_id == 1
    doa(w, T + 1.05, T + 1.3, 60)                         # brief blip: keep speaker
    detections(w, T + 1.3, [(1, 0), (2, 60)])
    assert utt(w, "u1", T, T + 1.3).speaker_id == 1
    doa(w, T + 1.35, T + 2.6, 60)                         # sustained move > 1 s: switch
    detections(w, T + 2.6, [(1, 0), (2, 60)])
    assert utt(w, "u1", T, T + 2.6).speaker_id == 2


def test_overlap_flag_on_wide_spread():
    w = World()
    doa(w, T, T + 1, -60)
    doa(w, T + 0.025, T + 1.025, 60)
    assert utt(w, "u1", T, T + 1).overlap


def test_no_doa_single_visible_person_fallback():
    w = World()
    detections(w, T, [(7, 10)])
    assert utt(w, "u1", T, T + 1).speaker_id == 7


def test_frame_meta_caption_shapes():
    w = World()
    detections(w, T, [(1, 0)])
    doa(w, T, T + 1, 0)
    utt(w, "u1", T, T + 1)
    doa(w, T + 1.5, T + 2.5, 100)
    utt(w, "u2", T + 1.5, T + 2.5)
    meta = w.frame_meta({"t": T + 2, "frame_id": 5, "width": 1280, "height": 720, "hfov_deg": 70}, T + 2.5)
    caps = {c["speaker_id"]: c for c in meta["captions"]}
    assert "bearing" not in caps[1]                       # on camera: anchored to the face
    off = [c for sid, c in caps.items() if sid >= C.OFFCAM_ID_START]
    assert off and off[0]["bearing"] == pytest.approx(100)
    assert meta["faces"][0]["id"] == 1


def test_final_caption_expires():
    w = World()
    doa(w, T, T + 1, 0)
    utt(w, "u1", T, T + 1, final=True)
    vf = {"t": 0, "frame_id": 1, "width": 10, "height": 10, "hfov_deg": 70}
    assert w.frame_meta(vf, T + 2)["captions"]
    assert not w.frame_meta(vf, T + 1 + C.CAPTION_HOLD_S + 0.1)["captions"]


def lip_detections(w, t, people):
    """people: (track_id, bearing, speaking score or None)."""
    objs = []
    for tid, b, sp in people:
        o = {"track_id": tid, "kind": "person", "raw_class": "person", "confidence": 0.9,
             "bbox": [0, 0, 100, 300], "bearing_deg": b}
        if sp is not None:
            o["speaking"] = sp
        objs.append(o)
    w.on_detections({"t": t, "frame_id": 1, "objects": objs})


def test_lips_break_tie_between_close_people():
    w = World()
    lip_detections(w, T, [(1, 10.0, 0.05), (2, 18.0, 0.8)])   # DoA is nearer person 1, person 2 is talking
    doa(w, T, T + 1, 11.0)
    assert utt(w, "u1", T, T + 1).speaker_id == 2


def test_lips_ignored_when_stale_or_single_candidate():
    w = World()
    lip_detections(w, T - 5, [(1, 10.0, 0.05), (2, 18.0, 0.8)])   # lip scores 5 s old
    lip_detections(w, T, [(1, 10.0, None), (2, 18.0, None)])
    doa(w, T, T + 1, 11.0)
    assert utt(w, "u1", T, T + 1).speaker_id == 1


def test_lips_pick_speaker_without_doa():
    w = World()
    lip_detections(w, T, [(1, -30.0, 0.0), (2, 30.0, 0.7)])
    assert utt(w, "u1", T, T + 0.5).speaker_id == 2
