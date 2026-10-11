"""LED ring drawing (audio_here/ring.py) against a fake device."""

import pytest

from laptop.audio import config as C
from laptop.audio import ring
from laptop.audio.ring import EFFECT_RING, N_LEDS, Ring, draw, led_position


class FakeDev:
    def __init__(self, effect=4):
        self.regs = {"LED_EFFECT": (effect,)}
        self.writes = []

    def read(self, name):
        return self.regs[name]

    def write(self, name, *values):
        self.regs[name] = values
        self.writes.append(name)


@pytest.fixture(autouse=True)
def mapping(monkeypatch):
    monkeypatch.setattr(C, "RING_LED0_DEG", 0.0)
    monkeypatch.setattr(C, "RING_LED_STEP", 1)


def test_led_position_wraps_and_respects_step(monkeypatch):
    assert led_position(0) == 0
    assert led_position(90) == 3
    assert led_position(345) == pytest.approx(11.5)
    monkeypatch.setattr(C, "RING_LED_STEP", -1)
    assert led_position(90) == 9
    monkeypatch.setattr(C, "RING_LED0_DEG", 30.0)
    assert led_position(30) == 0


def test_marker_on_an_led_lights_only_that_led():
    leds = draw([(90.0, 0xFF0000, 1.0)])
    assert leds[3] == 0xFF0000
    assert sum(1 for c in leds if c) == 1


def test_marker_between_leds_splits_brightness():
    leds = draw([(105.0, 0x0000FF, 1.0)])           # halfway between LED 3 and 4
    assert leds[3] == leds[4] == 0x000080


def test_wraparound_between_led_11_and_0():
    leds = draw([(355.0, 0x00FF00, 1.0)])
    assert leds[11] and leds[0] and not any(leds[1:11])


def test_colours_add_and_clip():
    leds = draw([(0.0, 0xFF0000, 1.0), (0.0, 0xFF8000, 1.0)])
    assert leds[0] == 0xFF8000


def test_ring_fades_and_restores_effect():
    dev = FakeDev(effect=4)
    r = Ring(dev)
    assert dev.regs["LED_EFFECT"] == (EFFECT_RING,)
    r.show("doa", 0.0, 0xFF0000, 1.0, hold_s=1.0)
    t = r.marks["doa"][3]
    assert r.frame(t + 0.5)[0] == 0x800000
    assert r.frame(t + 1.5) == [0] * N_LEDS and "doa" not in r.marks
    r.flush(t + 2.0)
    r.flush(t + 2.1)                                 # unchanged frame: no second write
    assert dev.writes.count("LED_RING_COLOR") == 1
    r.close()
    assert dev.regs["LED_EFFECT"] == (4,)


def test_set_mode():
    dev = FakeDev()
    ring.set_mode(dev, "off")
    assert dev.regs["LED_EFFECT"] == (0,)


# ---- what the markers show -----------------------------------------------------------------------------------

def run_lead(doa, fast, energy=1e5):
    lead = ring.LeadDoa()
    return [lead.update(1.0 + 0.05 * k, d, True, f, energy if f is not None else 0.0)
            for k, (d, f) in enumerate(zip(doa, fast))]


def test_lead_doa_uses_the_fast_beam_while_the_doa_is_stale_then_hands_back():
    # Recorded live (2026-10-09): a voice starts at 177; DOA_VALUE holds 11 for 0.15 s, then sweeps 41..177.
    doa = [11, 11, 11, 11, 11, 41, 101, 125, 142, 153, 161, 166, 171, 172, 174, 175, 176, 176, 177]
    fast = [None, 208, 175, 176, 176, 175, 178, 176, 177, 176, 177, 177, 178, 177, 176, 177, 176, 177, 177]
    shown = run_lead(doa, fast)
    assert shown[:4] == [11, 11, 11, 11]                    # the fast beam must be steady first (reflections)
    assert all(abs(x - 176) <= 6 for x in shown[4:])        # right from the 5th sample (~0.15 s), no sweep
    assert shown[-1] == 177                                 # settled: the DoA itself


def test_lead_doa_keeps_a_right_doa_when_the_fast_beam_chases_a_reflection():
    # Recorded live: a doorbell at 178 after a talker at 178; the free-running beam sits at 202-211 / 273.
    doa = [178] * 12
    fast = [273, 273, 202, 202, 206, 208, 209, 211, 211, 189, 298, 258]
    assert run_lead(doa, fast) == [178] * 12


def test_lead_doa_drops_a_steady_reflection_when_the_doa_never_follows():
    # Recorded live: doorbell at 178; the free-running beam holds 272-275 for 0.15 s, then wanders.
    doa = [178] * 20
    fast = [275, 272, 272, 238, 210, 276, 212, 212, 211, 212, 213, 186, 268, 297, 297, 181, 180, 180, 180, 181]
    shown = run_lead(doa, fast)
    assert sum(abs(x - 178) > 20 for x in shown) <= 6       # at most 0.3 s on the reflection
    assert all(x == 178 for x in shown[8:])                 # then the DoA for the rest of the sound


def test_lead_doa_ignores_a_fast_beam_reflection_once_settled():
    lead = ring.LeadDoa()
    for k in range(30):                                     # 1.5 s of one talker at 176
        lead.update(k * 0.05, 176, True, 176, 1e5)
    for k in range(5):                                      # the free-running beam visits a reflection
        assert lead.update(1.5 + 0.05 * k, 176, True, 244, 1e5) == 176


def test_lead_doa_sweep_without_a_fast_reading_is_dark_not_crawling():
    lead = ring.LeadDoa()
    for k in range(30):
        lead.update(k * 0.05, 90, True)
    assert lead.update(1.5, 120, True) is None              # 30 deg in one sample: sweeping
    assert lead.update(1.55, 150, True) is None
    assert lead.update(1.6, 152, True) == 152
    assert lead.update(1.65, 152, False) is None            # silence: nothing new to draw


def test_second_picker_keeps_its_source_and_respects_the_separation():
    p = ring.SecondPicker(min_sep_deg=45)
    assert p.pick(0.0, [(7, 20.0, 0.9)]) is None                         # too close to the main marker
    assert p.pick(0.0, [(7, 180.0, 0.5), (8, 90.0, 0.6)])[0] == 8        # strongest other
    assert p.pick(0.0, [(7, 180.0, 0.9), (8, 92.0, 0.4)])[0] == 8        # stays on it while it lasts
    assert p.pick(0.0, [(7, 180.0, 0.9)])[0] == 7
    assert p.pick(None, [(9, 10.0, 0.3)])[0] == 9                         # no main marker: any source


def test_fast_cluster_finds_the_dominant_direction_and_forgets_old_readings():
    c = ring.FastCluster(window_s=0.5)
    for k, d in enumerate([170, 180, 244, 176, 178, 245]):
        c.add(k * 0.05, d, 1e6)
    assert abs(c.value() - 176) <= 4
    c.add(2.0, None, 0)
    assert c.value() is None                                # all older than the window


def test_planner_srp_main_is_vetoed_by_a_disagreeing_fast_beam():
    p = ring.RingPlanner(main="srp")
    p.set_srp(1.0, [(1, 100.0, 0.5)])
    for k in range(4):
        main, hide, _ = p.plan(1.0 + 0.05 * k, 0, True, fast=(250.0, 1e6))
    assert main is None and hide
    p.set_srp(1.2, [(1, 100.0, 0.5)])
    for k in range(12):                                     # the fast beam moves to the sound
        main, hide, _ = p.plan(1.2 + 0.05 * k, 0, True, fast=(104.0, 1e6))
        p.set_srp(1.2 + 0.05 * k, [(1, 100.0, 0.5)])
    assert main == (100.0, pytest.approx(0.833, abs=0.01)) and not hide


def test_planner_second_srp_track_needs_strength_age_and_to_have_led():
    p = ring.RingPlanner(main="srp", second_rel=0.6, second_alive_s=0.3, second_leader_hops=2)
    p.set_srp(0.9, [(2, 180.0, 0.5)])                       # a second source heard alone first: it led
    p.set_srp(0.95, [(2, 180.0, 0.5)])
    p.set_srp(1.0, [(1, 0.0, 0.6), (2, 180.0, 0.5)])
    assert p.plan(1.0, 0, True)[2] is None                  # 0.1 s old: not yet
    p.set_srp(1.4, [(1, 0.0, 0.6), (2, 180.0, 0.5)])
    assert p.plan(1.4, 0, True)[2][:2] == (2, 180.0)
    p.set_srp(1.5, [(1, 0.0, 0.6), (2, 180.0, 0.2)])        # too weak against the main one
    assert p.plan(1.5, 0, True)[2] is None


def test_planner_never_lights_an_echo_that_was_never_the_loudest():
    p = ring.RingPlanner(main="srp")
    for k in range(40):                                     # a talker at 0 and its wall echo at 140, always weaker
        p.set_srp(k * 0.1, [(1, 0.0, 0.6), (3, 140.0, 0.45)])
        assert p.plan(k * 0.1, 0, True)[2] is None


def test_planner_prefers_odas_tracks_for_the_second_marker():
    p = ring.RingPlanner(main="srp")
    p.set_srp(1.0, [(1, 0.0, 0.6)])
    for t in (0.9, 0.95):                                   # track 7 alone (and loudest) while there is sound
        p.set_srp(t, [(2, 200.0, 0.6)])
        p.set_odas(t, [(7, 200.0, 0.6)])
        p.plan(t, 0, True)
    p.set_srp(1.0, [(1, 0.0, 0.6)])
    p.set_odas(1.0, [(4, 2.0, 0.9), (7, 200.0, 0.6)])
    main, _, second = p.plan(1.0, 0, True)
    assert main[0] == 0.0 and second[:2] == (7, 200.0)
    q = ring.RingPlanner(main="srp")                         # silence: lingering ODAS tracks light nothing
    for k in range(10):
        q.set_odas(k * 0.05, [(9, 220.0, 0.5)])
        assert q.plan(k * 0.05, 0, False) == (None, False, None)
    assert ring.RingPlanner(main="srp", second="off").plan(1.0, 0, True)[2] is None
