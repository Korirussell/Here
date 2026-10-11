"""SourceTracker (laptop/events/tracker.py): stable ids, no lag, expiry, overlap, the wire rate."""

from contracts.python.here_contract import Source
from laptop.audio.base import SourcesThrottle
from laptop.events.tracker import SourceTracker

DT = 0.05


def src(i, b, speech=True, energy=0.4, conf=0.8):
    return Source(id=i, bearing_deg=b, energy=energy, speech=speech, confidence=conf if speech else 0.0)


def quiet_beams(e=0.4):
    """Beams mode in a quiet room: both beams reported every tick, no speech, ambient level ~0.4."""
    return [src(0, 0.8, False, e), src(1, -179.2, False, e)]


def run(tr, frames, t0=0.0):
    out, t = [], t0
    for f in frames:
        out.append((t, tr.update(t, f)))
        t += DT
    return out, t


def test_quiet_room_has_no_sources():
    tr = SourceTracker()
    out, _ = run(tr, [quiet_beams()] * 40)
    assert all(w == [] for _, w in out)
    assert not tr.active


def test_new_talker_shows_on_the_first_frame_and_keeps_its_id_through_a_pause():
    tr = SourceTracker(hold_s=1.5)
    run(tr, [quiet_beams()] * 10)
    talking = [src(0, 0.8, True, 0.6), src(1, -179.2, False, 0.4)]
    out, t = run(tr, [talking], t0=0.5)
    assert [s.id for s in out[0][1]] == [0] and out[0][1][0].speech         # first frame
    # 1 s pause: still listed (same id, not speech), then back to talking
    out, t = run(tr, [quiet_beams()] * 20, t0=t)
    assert all([s.id for s in w] == [0] and not w[0].speech for _, w in out)
    out, t = run(tr, [talking] * 3, t0=t)
    assert [s.id for s in out[-1][1]] == [0] and out[-1][1][0].speech


def test_track_expires_after_hold():
    tr = SourceTracker(hold_s=1.5)
    out, t = run(tr, [[src(1, 180.0, True, 0.6)]] * 5)
    out, t = run(tr, [quiet_beams()] * 40, t0=t)                             # 2 s of quiet
    assert out[0][1] and out[-1][1] == []
    expire = next(tt for tt, w in out if not w)
    assert 1.4 <= expire - (0.2) <= 1.7


def test_bearing_snaps_on_a_big_move_and_smooths_wobble():
    tr = SourceTracker(snap_deg=25.0, alpha=0.5)
    tr.update(0.0, [src(5, 10.0)])
    w = tr.update(0.05, [src(5, 14.0)])
    assert w[0].bearing_deg == 12.0                                          # smoothed
    w = tr.update(0.10, [src(5, 120.0)])
    assert w[0].bearing_deg == 120.0                                         # snapped: no lag
    w = tr.update(0.15, [src(5, 179.0)])
    w = tr.update(0.20, [src(5, -179.0)])                                    # wobble across +-180
    assert w[0].bearing_deg == 180.0                                         # 179 + half of +2, wrapped


def test_overlap_when_two_tracks_are_active():
    tr = SourceTracker()
    tr.update(0.0, [src(0, 0.0), src(1, 180.0, False, 0.4)])
    assert not tr.overlap
    tr.update(0.05, [src(0, 0.0), src(1, 180.0)])
    assert tr.overlap and len(tr.live(0.05)) == 2


def test_non_speech_energy_over_the_floor_is_a_track_but_steady_noise_is_not():
    tr = SourceTracker(active_margin=0.12, nonspeech="energy")
    run(tr, [quiet_beams(0.4)] * 20)
    w = tr.update(1.0, [src(0, 0.8, False, 0.4), src(1, -179.2, False, 0.7)])  # doorbell on the rear beam
    assert [s.id for s in w] == [1] and not w[0].speech
    tr2 = SourceTracker(nonspeech="energy")
    out, _ = run(tr2, [quiet_beams(0.75)] * 100)                              # loud fan from the start
    assert all(w == [] for _, w in out)


def test_spread_of_a_steady_talker_is_small():
    tr = SourceTracker()
    run(tr, [[src(0, b)] for b in (30.0, 31.0, 29.5, 30.5, 30.0)])
    assert tr.spread(0) < 2.0


def test_throttle_sends_every_frame_while_active_and_on_change_when_idle():
    tr, th = SourceTracker(hold_s=0.5), SourcesThrottle(keepalive_s=1.0)
    sent = []
    t = 0.0
    for f in [quiet_beams()] * 10 + [[src(0, 0.8, True, 0.6), src(1, -179.2, False, 0.4)]] * 10 + [quiet_beams()] * 60:
        w = tr.update(t, f)
        if th.should_send(t, w, active=tr.active):
            sent.append((round(t, 2), [s.id for s in w], any(s.speech for s in w)))
        t += DT
    talking = [x for x in sent if x[2]]
    assert len(talking) == 10                                               # 20 Hz while talking
    assert any(ids == [] and tt > 1.0 for tt, ids, _ in sent)                # expiry sent as an empty list
    idle_after = [x for x in sent if x[0] > 1.6]
    assert len(idle_after) <= 3                                             # then keepalives only
    assert th.suppressed > 0


def test_a_talker_leaking_into_the_other_beam_is_not_a_second_source():
    """Measured on the array: a talker at 180 reads 0.5 on beam 1 and 0.2-0.3 on beam 0 (speech only on 1)."""
    tr = SourceTracker(nonspeech="energy")
    run(tr, [quiet_beams(0.1)] * 20)
    out, _ = run(tr, [[src(0, 0.8, False, 0.27), src(1, -179.2, True, 0.55)]] * 20, t0=1.0)
    assert all([s.id for s in w] == [1] for _, w in out)
    assert not tr.overlap


def test_a_zero_level_before_the_first_audio_block_does_not_set_the_floor():
    tr = SourceTracker(nonspeech="energy")
    tr.update(0.0, quiet_beams(0.0))
    out, _ = run(tr, [quiet_beams(0.15)] * 20, t0=0.05)
    assert all(w == [] for _, w in out)


def test_onsets_mode_lists_a_non_speech_track_only_after_an_onset():
    """Default: a loud fluctuating room (as measured: 0.3-0.8 on both beams) lists nothing until an onset."""
    import random
    rng = random.Random(3)
    tr = SourceTracker(hold_s=1.5, nonspeech_hold_s=1.0)
    noisy = [[src(0, 0.8, False, rng.uniform(0.3, 0.8)), src(1, -179.2, False, rng.uniform(0.3, 0.8))]
             for _ in range(100)]
    out, t = run(tr, noisy)
    assert all(w == [] for _, w in out)
    tr.note_onset(1, t)                                                      # a doorbell on the rear beam
    out, t2 = run(tr, noisy[:60], t0=t)
    listed = [tt for tt, w in out if [s.id for s in w] == [1]]
    assert listed and listed[0] == t and not out[0][1][0].speech
    assert 2.3 <= max(listed) - t <= 2.6                                     # 1 s active + 1.5 s hold
    assert out[-1][1] == []


def test_ambience_onsets_stop_making_tracks_but_a_lone_sound_still_does():
    """Real array, busy room (2026-10-10): an onset every ~2 s on both beams kept both listed 93 % of the time."""
    from laptop.events.tracker import SourceTracker
    tr = SourceTracker(ambience_window_s=30.0, ambience_max=4)
    for k in range(10):
        tr.note_onset(1, 100.0 + 2.0 * k)
    assert tr.onset_t[1] == 106.0                 # the first four counted, the rest are ambience
    tr2 = SourceTracker()
    tr2.note_onset(1, 100.0)
    tr2.note_onset(1, 140.0)                       # a doorbell now and then: each counts
    assert tr2.onset_t[1] == 140.0
