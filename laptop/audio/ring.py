"""The XVF3800's 12-LED ring as a direction display, drawn by HERE (LED effect 5, LED_RING_COLOR).

Markers are placed by device angle (DOA_VALUE units), so the lit LED faces the sound
whatever the world calibration is. Each marker fades out after its last update (RING_HOLD_S);
a marker between two LEDs lights both in proportion.

Two markers: the main one (RING_SPEECH_COLOR) is the device's DoA, except at a speech onset or while the DoA
swings, when the firmware's free-running beam is used instead (`LeadDoa`: DOA_VALUE keeps the previous talker's
direction for ~0.15 s after a new voice starts, then sweeps over ~0.3 s; the free-running beam is on the new
voice within ~0.05 s). The second (RING_BEAM2_COLOR) is another source at least RING_SECOND_MIN_SEP_DEG away
(`SecondPicker`): ODAS tracks with --odas, the other focused beam when the beams auto-track, or our SRP tracker
(`audio.ring_second`).

Stop HERE first (only one program can use the control interface), then:

    python -m laptop.audio.ring --follow      # light the LED at the device's DoA while you talk: it should face
                                              # you. If not, adjust ring_led0_deg / ring_led_step in config.yaml
    python -m laptop.audio.ring --sweep       # light LEDs 0..11 in turn
    python -m laptop.audio.ring --firmware    # hand the ring back to the device's own DoA effect
"""

import math
import time
from collections import deque

try:
    from . import config as C
except ImportError:
    import config as C

N_LEDS = 12
EFFECT_OFF, EFFECT_DOA, EFFECT_RING = 0, 4, 5


def led_position(device_deg):
    """Fractional LED index (0..12) that faces a device angle."""
    return ((device_deg - C.RING_LED0_DEG) * C.RING_LED_STEP / (360.0 / N_LEDS)) % N_LEDS


def draw(marks):
    """12 colours (0xRRGGBB) for markers [(device_deg, color, level 0..1)], blended additively."""
    rgb = [[0.0, 0.0, 0.0] for _ in range(N_LEDS)]
    for deg, color, level in marks:
        pos = led_position(deg)
        i = int(math.floor(pos)) % N_LEDS
        frac = pos - math.floor(pos)
        for led, w in ((i, 1.0 - frac), ((i + 1) % N_LEDS, frac)):
            for c in range(3):
                rgb[led][c] += ((color >> (16 - 8 * c)) & 0xFF) * level * w
    return [(min(255, round(r)) << 16) | (min(255, round(g)) << 8) | min(255, round(b)) for r, g, b in rgb]


def _diff(a, b):
    return (a - b + 180.0) % 360.0 - 180.0


class LeadDoa:
    """Where the main marker goes for one DoA sample (device degrees), or None (dark).

    DOA_VALUE is accurate once settled but lags a new talker: it holds the old direction ~0.15 s, then sweeps
    over ~0.3 s. The firmware's free-running beam is on a new voice within ~0.05 s but also visits reflections
    (25-35 deg off for a doorbell, measured), so it takes over only when it is steady (steady_n readings within
    steady_deg), the DoA may be wrong (within onset_s of a speech onset before the DoA has moved, or sweeping),
    and the two disagree by more than jump_deg: a new talker somewhere else. It then holds until the DoA comes
    within near_deg of it (or onset_s passes). If the DoA hasn't started moving confirm_s after the jump, the fast
    beam was on a reflection (a doorbell, measured: steady 94 deg off while the DoA was right): back to the DoA
    for the rest of that sound. A sweep with no steady fast reading draws nothing (no LED crawling round the ring).
    """

    def __init__(self, onset_s=0.8, quiet_s=0.5, slew_deg=5.0, steady_n=3, steady_deg=15.0, jump_deg=45.0,
                 near_deg=8.0, confirm_s=0.25):
        self.onset_s, self.quiet_s, self.slew_deg = onset_s, quiet_s, slew_deg
        self.steady_n, self.steady_deg, self.jump_deg, self.near_deg = steady_n, steady_deg, jump_deg, near_deg
        self.confirm_s = confirm_s
        self.trust_doa = False              # the fast beam was refuted during this sound
        self.lock_doa = None
        self.prev_doa = None
        self.fast_hist = deque(maxlen=steady_n)
        self.last_speech = -1e9
        self.onset_t, self.onset_doa = -1e9, None
        self.lock, self.lock_t = None, -1e9

    def steady_fast(self, fast_deg, fast_energy):
        if fast_deg is None or fast_energy <= 0:
            self.fast_hist.clear()
            return None
        self.fast_hist.append(fast_deg)
        h = list(self.fast_hist)
        if len(h) < self.steady_n or max(abs(_diff(x, y)) for x in h for y in h) > self.steady_deg:
            return None
        return (h[0] + sum(_diff(x, h[0]) for x in h) / len(h)) % 360

    def update(self, t, doa_deg, speech, fast_deg=None, fast_energy=0.0):
        fast = self.steady_fast(fast_deg, fast_energy)
        sweeping = self.prev_doa is not None and abs(_diff(doa_deg, self.prev_doa)) > self.slew_deg
        self.prev_doa = doa_deg
        if not speech:
            self.lock = None
            return None
        if t - self.last_speech > self.quiet_s:
            self.onset_t, self.onset_doa, self.trust_doa = t, doa_deg, False
        self.last_speech = t
        if self.lock is not None:
            if fast is not None and abs(_diff(fast, self.lock)) <= self.jump_deg:
                self.lock = fast
            refuted = t - self.lock_t > self.confirm_s and abs(_diff(doa_deg, self.lock_doa)) <= self.slew_deg
            if refuted:
                self.trust_doa = True
            if refuted or abs(_diff(doa_deg, self.lock)) <= self.near_deg or t - self.lock_t > self.onset_s:
                self.lock = None                    # the DoA has caught up (or never followed)
            else:
                return self.lock
        stale = t - self.onset_t <= self.onset_s and abs(_diff(doa_deg, self.onset_doa)) < 10.0
        if ((stale and not self.trust_doa) or sweeping) and fast is not None and                 abs(_diff(fast, doa_deg)) > self.jump_deg:
            self.lock, self.lock_t, self.lock_doa = fast, t, doa_deg
            return fast
        if sweeping:
            return None
        return doa_deg


class SecondPicker:
    """The second marker: the strongest other source >= min_sep_deg from the main one, keeping the same
    source while it lasts (no hopping between two candidates). tracks: [(id, device_deg, level 0..1)]."""

    def __init__(self, min_sep_deg=45.0):
        self.min_sep = min_sep_deg
        self.current = None

    def pick(self, main_deg, tracks):
        ok = [tr for tr in tracks if main_deg is None or abs(_diff(tr[1], main_deg)) >= self.min_sep]
        if not ok:
            self.current = None
            return None
        same = [tr for tr in ok if tr[0] == self.current]
        best = same[0] if same else max(ok, key=lambda tr: tr[2])
        self.current = best[0]
        return best


class FastCluster:
    """The free-running beam's dominant direction over the last window_s: the reading whose +-width_deg holds the
    most (log-energy) weight, if that is at least `share` of it all. Single readings often sit on a reflection;
    the cluster is right 74-84% of the time live (single readings 67-77%)."""

    def __init__(self, window_s=0.5, width_deg=20.0, share=0.5):
        self.window_s, self.width, self.share = window_s, width_deg, share
        self.hist = deque()

    def add(self, t, deg, energy):
        if deg is not None and energy > 0:
            self.hist.append((t, deg, math.log10(max(energy, 10.0))))
        while self.hist and self.hist[0][0] < t - self.window_s:
            self.hist.popleft()

    def value(self):
        if not self.hist:
            return None
        total = sum(w for _, _, w in self.hist)
        best, best_w = None, 0.0
        for _, v, _ in self.hist:
            near = [(d, w) for _, d, w in self.hist if abs(_diff(d, v)) <= self.width]
            weight = sum(w for _, w in near)
            if weight > best_w:
                best, best_w = near, weight
        if best_w < self.share * total:
            return None
        ref = best[0][0]
        return (ref + sum(_diff(d, ref) * w for d, w in best) / best_w) % 360


class RingPlanner:
    """Decides the ring's two markers each DoA sample (device degrees); the Ring only draws them.

    Main marker (audio.ring_main): `srp` = the strongest of our raw-mic SRP tracks, dark while the free-running
    beam's cluster disagrees by more than veto_deg (live, one talker: right 88-95% of the time it is lit, lit for
    61-74% of the sound). `doa` = DOA_VALUE through LeadDoa. auto = srp when the raw mics are there. DOA_VALUE is
    not used with raw mics: it reports the active focused beam, so with fixed beams it only ever names one of
    them (measured: beams at +-90, a speaker at 177 -> DOA_VALUE +92 / -88).
    Second marker (audio.ring_second): ODAS tracks (speech-active, same leader rule) while OdasSource pushes them;
    else (auto/srp)
    another SRP track at least second_rel x as strong as the main one, alive second_alive_s, that has itself been
    the strongest for second_leader_hops (a wall echo of the main sound never is: live, an echo 40 deg from the
    speaker lit a second marker for ~20% of the sound without this rule); else
    (auto/beams) the other auto-tracking focused beam. Never a fixed beam.
    """

    def __init__(self, main="auto", second="auto", raw=True, min_sep_deg=45.0, veto_deg=30.0, fresh_s=0.3,
                 second_rel=0.6, second_alive_s=0.3, second_leader_hops=2, lead=None):
        self.main = ("srp" if raw else "doa") if main == "auto" else main
        self.second_mode = second
        self.veto, self.fresh, self.rel, self.alive = veto_deg, fresh_s, second_rel, second_alive_s
        self.lead = lead or LeadDoa()
        self.fast = FastCluster()
        self.picker = SecondPicker(min_sep_deg)
        self.srp = (-1e9, [])          # (t, [(id, device_deg, value)])
        self.odas = (-1e9, [])
        self.born = {}
        self.led = {}                  # SRP track id -> hops it was the strongest track
        self.odas_led = {}             # the same for ODAS track ids
        self.leader_hops = second_leader_hops
        self.last_main = (-1e9, None)

    def set_srp(self, t, tracks):
        for tid, _, _ in tracks:
            self.born.setdefault(tid, t)
        if tracks:
            top = max(tracks, key=lambda x: x[2])[0]
            self.led[top] = self.led.get(top, 0) + 1
        self.srp = (t, tracks)

    def set_odas(self, t, tracks):
        self.odas = (t, tracks)

    def plan(self, t, doa_deg, speech, fast=(None, 0.0), beams=(), beams_fixed=True, hold_s=0.6):
        """-> (main: (device_deg, level 0..1) or None, hide_main, second: (id, device_deg, level) or None)."""
        self.fast.add(t, *fast)
        hide, main, others = False, None, []
        if self.main == "srp":
            st, tracks = self.srp
            if t - st <= self.fresh and tracks:
                tracks = sorted(tracks, key=lambda x: -x[2])
                best = tracks[0]
                cluster = self.fast.value()
                if cluster is not None and abs(_diff(cluster, best[1])) > self.veto:
                    hide = True                 # the two disagree: dark rather than maybe wrong
                else:
                    main = (best[1], min(1.0, best[2] / 0.6))
                # An echo is never the loudest; a second source has been, at some point (alone or louder).
                others = [x for x in tracks[1:]
                          if x[2] >= self.rel * best[2] and t - self.born.get(x[0], t) >= self.alive
                          and self.led.get(x[0], 0) >= self.leader_hops]
        else:
            deg = self.lead.update(t, doa_deg, speech, *fast)
            if deg is not None:
                main = (deg, 1.0)
            elif speech:
                hide = True                     # sweeping: dark rather than an LED crawling round the ring
        if main is not None:
            self.last_main = (t, main[0])
        t_main, deg_main = self.last_main
        if t - t_main > hold_s:
            return main, hide, None             # a second marker only beside a main one (ODAS tracks linger)
        ot, otracks = self.odas
        if self.second_mode == "off":
            cands = []
        elif t - ot <= 0.5 and self.second_mode in ("auto", "odas"):
            if otracks and main is not None:   # leaders counted only while there is sound
                top = max(otracks, key=lambda x: x[2])[0]
                self.odas_led[top] = self.odas_led.get(top, 0) + 1
            cands = [x for x in otracks if self.odas_led.get(x[0], 0) >= self.leader_hops]
        elif self.second_mode in ("auto", "srp") and self.main == "srp":
            cands = others
        elif self.second_mode in ("auto", "beams") and not beams_fixed:
            cands = [(k, d, 0.6) for k, (d, e) in enumerate(beams) if e > 0]
        else:
            cands = []
        return main, hide, self.picker.pick(deg_main, cands)


class Ring:
    """Owns the ring while HERE runs; restores the previous effect on close."""

    def __init__(self, dev):
        self.dev = dev
        self.marks = {}          # key -> (device_deg, color, level, last_update, hold_s)
        self.last = None
        self.prev_effect = dev.read("LED_EFFECT")[0]
        dev.write("LED_EFFECT", EFFECT_RING)

    def show(self, key, device_deg, color, level=1.0, hold_s=C.RING_HOLD_S):
        self.marks[key] = (device_deg, color, max(0.0, min(1.0, level)), time.time(), hold_s)

    def hide(self, key):
        self.marks.pop(key, None)

    def frame(self, now=None):
        """Current colours; markers fade linearly to dark over their hold time."""
        now = time.time() if now is None else now
        live = []
        for key, (deg, color, level, t, hold) in list(self.marks.items()):
            left = 1.0 - (now - t) / hold
            if left <= 0:
                self.marks.pop(key, None)
            else:
                live.append((deg, color, level * left))
        return draw(live)

    def flush(self, now=None):
        """Write the ring if it changed. Call from the thread that polls the device."""
        colors = self.frame(now)
        if colors != self.last:
            self.dev.write("LED_RING_COLOR", *colors)
            self.last = colors

    def close(self):
        self.dev.write("LED_RING_COLOR", *([0] * N_LEDS))
        self.dev.write("LED_EFFECT", self.prev_effect if self.prev_effect != EFFECT_RING else EFFECT_DOA)


def set_mode(dev, mode):
    """'firmware' = device's own DoA effect, 'off' = dark. ('host' is the Ring class.)"""
    dev.write("LED_EFFECT", {"firmware": EFFECT_DOA, "off": EFFECT_OFF}[mode])


def main():
    import argparse

    from .xvf3800 import XVF3800

    p = argparse.ArgumentParser(prog="python -m laptop.audio.ring", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--follow", action="store_true")
    g.add_argument("--sweep", action="store_true")
    g.add_argument("--firmware", action="store_true")
    args = p.parse_args()

    dev = XVF3800()
    try:
        if args.firmware:
            set_mode(dev, "firmware")
            print("ring handed back to the device's DoA effect")
            return
        ring = Ring(dev)
        try:
            if args.sweep:
                for i in range(N_LEDS):
                    deg = C.RING_LED0_DEG + i * (360.0 / N_LEDS) * C.RING_LED_STEP
                    dev.write("LED_RING_COLOR", *draw([(deg, C.RING_SPEECH_COLOR, 1.0)]))
                    print(f"LED {i:2d}  (device angle {deg % 360:5.1f})")
                    time.sleep(0.8)
            else:
                from .localize import aec_to_device

                print("Talk from around the array; the lit LED should face you. Ctrl+C to stop.")
                lead = LeadDoa(onset_s=getattr(C, "RING_ONSET_S", 0.8), slew_deg=getattr(C, "RING_SLEW_DEG", 5.0))
                while True:
                    deg, speech = dev.read("DOA_VALUE")
                    az, e = dev.read("AEC_AZIMUTH_VALUES")[2], dev.read("AEC_SPENERGY_VALUES")[2]
                    main = lead.update(time.time(), deg, speech, aec_to_device(math.degrees(az) % 360), e)
                    if main is not None:
                        ring.show("doa", main, C.RING_SPEECH_COLOR)
                    ring.flush()
                    shown = "  -  " if main is None else f"{main:5.1f}"
                    print(f"\r{'SPEECH' if speech else '  -   '} DoA {deg:3d}, shown {shown} -> "
                          f"LED {led_position(main if main is not None else deg):4.1f}   ", end="", flush=True)
                    time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            ring.close()
            print()
    finally:
        dev.close()


if __name__ == "__main__":
    main()
