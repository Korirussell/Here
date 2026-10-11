"""Guided array calibration (README §8): finds array_offset_deg and the device's rotation direction.

Talk from the array's front, then from its right; the helper averages the device's
direction while you speak, works out the offset and whether angles run clockwise,
checks the result, and saves both to laptop/config.yaml (audio.array_offset_deg / array_clockwise).

    python -m laptop.audio.calibrate
    python -m laptop.audio.calibrate --seconds 8 --no-save
    python -m laptop.audio.calibrate --wearer      # wearer talks 5 s from their mark -> audio.wearer_bearing_deg
    python -m laptop.audio.calibrate --verify      # 4-point check of the SAVED calibration (front, right, behind,
                                                   # left): per-position error, nothing saved
    python -m laptop.audio.calibrate --four-point  # calibrate (front + right), then check behind and left too
                                                   # before saving

A two-point fit can't catch a side mix-up: standing on the array's LEFT for the "right" step fits a perfectly
self-consistent but mirrored calibration (front and behind still read right). Only a left (or any off-axis)
check catches it, which is why --verify / --four-point measure all four, and why the fit is also checked
against the mic geometry's own handedness (handedness_problem below).

Stop `python -m laptop` (and xvf3800.py) first: only one program can use the array's control interface.
"""

import argparse
import re
import time

from laptop.geometry import angdiff, circular_mean, wrap

from . import config as C
from .xvf3800 import ENERGY_HOLD_S, XVF3800, sample, to_world

CONFIG_PATH = C.CONFIG_PATH   # laptop/config.yaml
SAMPLE_HZ = 20
MIN_SAMPLES = 15          # speech samples needed per position (~0.75 s of talking)
MAX_SPREAD_DEG = 20.0     # circular std above this means noise, echo or a second talker (tune)
RIGHT_TOLERANCE_DEG = 35  # the "right" reading must be within this of +-90 deg from front
CHECK_TOLERANCE_DEG = 25  # --verify / --four-point: a position reading further than this from its truth = CHECK
# Where a helper stands for each check position, and the world bearing it must read (README §8: clockwise).
POSITIONS = (("in FRONT", 0.0), ("to the RIGHT", 90.0), ("BEHIND", 180.0), ("to the LEFT", -90.0))


def collect(dev, seconds, beams=None):
    """Device angles (0..359) sampled while the array hears speech. beams: a list that also gets the azimuth of
    the strongest focused beam (mic-geometry frame) per speech sample, to fit aec_to_device for beams mode."""
    angles = []
    held_t = 0.0
    end = time.time() + seconds
    while time.time() < end:
        s = sample(dev)
        if max(s["beam_energy"]) > 0:
            held_t = s["t"]
        # The speech flag alone lingers in silence; also require recent speech energy.
        if s["speech"] and s["t"] - held_t <= ENERGY_HOLD_S:
            angles.append(s["doa_deg"])
            e = s["beam_energy"][:2]
            if beams is not None and max(e) > 0:
                beams.append(s["beam_az_deg"][0 if e[0] >= e[1] else 1])
        left = max(0.0, end - time.time())
        print(f"\r  listening... {left:4.1f} s left, {len(angles):3d} speech samples, "
              f"now {s['doa_deg']:3d} deg   ", end="", flush=True)
        time.sleep(1 / SAMPLE_HZ)
    print()
    return angles


def measure(dev, where, seconds, prompt=None, beams=None):
    """Prompt, record, and return the mean device angle for one position (retrying on bad data)."""
    while True:
        if beams is not None:
            beams.clear()
        input(prompt or f"\n> Stand about 1.5 m {where} of the array. Press Enter, then talk continuously "
              f"for {seconds:.0f} s (count, read aloud...).")
        angles = collect(dev, seconds, beams)
        if len(angles) < MIN_SAMPLES:
            print(f"  only {len(angles)} speech samples; talk louder or closer and try again.")
            continue
        mean, spread = circular_mean(angles)
        mean %= 360
        print(f"  {where}: {mean:.1f} deg (spread {spread:.1f} deg from {len(angles)} samples)")
        if spread > MAX_SPREAD_DEG:
            print(f"  spread is above {MAX_SPREAD_DEG:.0f} deg (noise, echo, or someone else talking).")
            if input("  Use it anyway? [y/N] ").strip().lower() != "y":
                continue
        return mean


def position_errors(readings, offset, clockwise):
    """readings: [(name, expected world bearing, mean device angle)] -> [(name, expected, world, error)] under the
    calibration (offset, clockwise). world uses the same to_world as every live path."""
    out = []
    for name, expected, device in readings:
        world = to_world(device, offset, not clockwise)
        out.append((name, expected, world, round(angdiff(world, expected), 1)))
    return out


def print_position_table(rows, tolerance=CHECK_TOLERANCE_DEG):
    """Per-position table; True when every position is within tolerance."""
    print("\n| position | should read | reads | error | |\n|---|---|---|---|---|")
    ok = True
    for name, expected, world, err in rows:
        good = abs(err) <= tolerance
        ok &= good
        print(f"| {name.lower():13s} | {expected:+6.0f} | {world:+7.1f} | {err:+6.1f} | {'OK' if good else 'CHECK'} |")
    if left_right_swapped(rows, tolerance):
        print("LEFT and RIGHT are swapped: re-run the calibration standing on the array's right "
              "(your right when you stand behind it facing its front).")
    return ok


def left_right_swapped(rows, tolerance=CHECK_TOLERANCE_DEG):
    """True when both side positions read on the opposite side (a mirrored calibration)."""
    sides = [r for r in rows if abs(abs(r[1]) - 90) < 1]
    return len(sides) == 2 and all(abs(angdiff(world, -expected)) <= tolerance for _, expected, world, _ in sides)


def handedness_problem(clockwise, aec_sign):
    """A warning when the fit is mirrored relative to the array's own mic geometry, else None.

    SRP-PHAT (and the firmware's beam azimuths) measure angles in the AEC_MIC_ARRAY_GEO frame: atan2(y, x),
    counter-clockwise seen from the array's top. World bearings run clockwise. So for an array lying face up,
    world = -aec + const, i.e. (clockwise ? +1 : -1) * aec_to_device_sign == -1. Measured 2026-10-11: the
    recalibrated config (clockwise false, sign 1) put a talker on the right at +85 through SRP; the previous one
    (clockwise true, sign 1) had left and right swapped. Face down (or a side mix-up during the fit) flips it.
    """
    if (1 if clockwise else -1) * aec_sign == -1:
        return None
    return ("the fit is MIRRORED relative to the array's mic geometry (device angles "
            f"{'clockwise' if clockwise else 'counter-clockwise'}, aec_to_device_sign {aec_sign:+d}). Either the "
            "'right' step was done from the array's LEFT, or the array is mounted face down. Check with --verify.")


def fit_beams(front_dev, right_dev, front_aec, right_aec, tolerance=30.0):
    """aec_to_device (offset, sign) from the same two positions seen by the device DoA and by the beams:
    device = offset + sign * aec. None when the beams disagree with the DoA about the angle between them."""
    d_dev, d_aec = angdiff(right_dev, front_dev), angdiff(right_aec, front_aec)
    if abs(abs(d_dev) - abs(d_aec)) > tolerance or abs(d_aec) < 20:
        return None
    sign = 1 if (d_dev > 0) == (d_aec > 0) else -1
    return round((front_dev - sign * front_aec) % 360.0, 1), sign


def save_beams(offset, sign, path=None):
    path = path or CONFIG_PATH
    text = path.read_text(encoding="utf-8")
    text, n1 = re.subn(r"^(\s+aec_to_device_offset_deg:\s*)[^\s#]+(.*)$",
                       lambda m: f"{m.group(1)}{offset:.1f}{m.group(2)}", text, flags=re.M)
    text, n2 = re.subn(r"^(\s+aec_to_device_sign:\s*)[^\s#]+(.*)$",
                       lambda m: f"{m.group(1)}{sign}{m.group(2)}", text, flags=re.M)
    if n1 != 1 or n2 != 1:
        raise SystemExit(f"couldn't find audio.aec_to_device_offset_deg / sign in {path}")
    path.write_text(text, encoding="utf-8")


def save(offset, clockwise, path=None):
    """Rewrite the two calibration lines in config.yaml in place, keeping comments and layout."""
    path = path or CONFIG_PATH
    text = path.read_text(encoding="utf-8")
    text, n1 = re.subn(r"^(\s+array_offset_deg:\s*)[^\s#]+(.*)$",
                       lambda m: f"{m.group(1)}{offset:.1f}{m.group(2)}", text, flags=re.M)
    text, n2 = re.subn(r"^(\s+array_clockwise:\s*)[^\s#]+(.*)$",
                       lambda m: f"{m.group(1)}{'true' if clockwise else 'false'}{m.group(2)}", text, flags=re.M)
    if n1 != 1 or n2 != 1:
        raise SystemExit(f"couldn't find audio.array_offset_deg / array_clockwise in {path}")
    path.write_text(text, encoding="utf-8")


def save_wearer(bearing, path=None):
    """Rewrite audio.wearer_bearing_deg in config.yaml in place (None writes null = filter off)."""
    path = path or CONFIG_PATH
    text = path.read_text(encoding="utf-8")
    value = "null" if bearing is None else f"{bearing:.1f}"
    text, n = re.subn(r"^(\s+wearer_bearing_deg:\s*)[^\s#]+(.*)$", lambda m: f"{m.group(1)}{value}{m.group(2)}",
                      text, flags=re.M)
    if n != 1:
        raise SystemExit(f"couldn't find audio.wearer_bearing_deg in {path}")
    path.write_text(text, encoding="utf-8")


def free_beams(dev):
    """Fixed beams off: with them on, DOA_VALUE names the active fixed beam (0 or 180 with the demo's [0, 180]),
    not the talker, and a killed `python -m laptop --real` leaves them on until a power cycle."""
    try:
        dev.write("AEC_FIXEDBEAMSONOFF", 0)
    except Exception as e:                       # noqa: BLE001 (older firmware): calibration still works
        print(f"(couldn't switch fixed beams off: {e}; if readings stick at 0/180, run xvf3800.py --reset)")


def verify(dev, seconds):
    """--verify: the saved calibration at all four positions; prints the per-position errors, saves nothing."""
    print(f"4-point check of the saved calibration: offset {C.ARRAY_OFFSET_DEG} deg, "
          f"{'clockwise' if C.ARRAY_CLOCKWISE else 'counter-clockwise'}")
    readings = [(name, expected, measure(dev, name, seconds)) for name, expected in POSITIONS]
    ok = print_position_table(position_errors(readings, C.ARRAY_OFFSET_DEG, C.ARRAY_CLOCKWISE))
    problem = handedness_problem(C.ARRAY_CLOCKWISE, C.AEC_TO_DEVICE_SIGN)
    if problem:
        print("WARNING: " + problem)
    print("\nall positions OK" if ok else "\nsome positions are off: re-run python -m laptop.audio.calibrate --four-point")
    return ok


def calibrate_wearer(dev, seconds, no_save):
    """The wearer talks from their tape mark; the array's world bearing to them is stored (README §5)."""
    print("Wearer calibration: the wearer stands on their mark (within 0.5 m of the array), facing its front.")
    print(f"Array calibration in use: offset {C.ARRAY_OFFSET_DEG} deg, "
          f"{'clockwise' if C.ARRAY_CLOCKWISE else 'counter-clockwise'} (run the full calibration first if unsure).")
    raw = measure(dev, "wearer", seconds, prompt=f"\n> Wearer: stand on your mark. Press Enter, then talk continuously for "
                  f"{seconds:.0f} s (count, read aloud...). Everyone else quiet.")
    bearing = to_world(raw, C.ARRAY_OFFSET_DEG, not C.ARRAY_CLOCKWISE)
    print(f"\nWearer at world bearing {bearing:+.1f} deg; their speech within +-{C.WEARER_IGNORE_DEG:.0f} deg "
          "will not be captioned or buzz the belt.")
    if no_save:
        print("not saved (--no-save)")
        return
    save_wearer(bearing)
    print(f"saved audio.wearer_bearing_deg to {CONFIG_PATH}")


def main():
    p = argparse.ArgumentParser(prog="python -m laptop.audio.calibrate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seconds", type=float, default=5.0, help="listening time per position (default 5)")
    p.add_argument("--no-save", action="store_true", help="print the result without writing laptop/config.yaml")
    p.add_argument("--wearer", action="store_true",
                   help="only measure the wearer's own voice -> audio.wearer_bearing_deg (README §5)")
    p.add_argument("--verify", action="store_true",
                   help="4-point check (front, right, behind, left) of the saved calibration; saves nothing")
    p.add_argument("--four-point", action="store_true",
                   help="after the front + right fit, also measure behind and left and print each position's error")
    args = p.parse_args()

    dev = XVF3800()
    free_beams(dev)
    if args.verify:
        try:
            verify(dev, args.seconds)
        except (KeyboardInterrupt, EOFError):
            print("\ncancelled")
        finally:
            dev.close()
        return
    if args.wearer:
        try:
            calibrate_wearer(dev, args.seconds, args.no_save)
        except (KeyboardInterrupt, EOFError):
            print("\ncancelled; nothing saved")
        finally:
            dev.close()
        return
    print("Array calibration (README §8)")
    print(f"current config: offset {C.ARRAY_OFFSET_DEG} deg, "
          f"{'clockwise' if C.ARRAY_CLOCKWISE else 'counter-clockwise'}")
    print("\n'Front' is the side you'll treat as the array's forward direction (mark it with tape).")
    print("'Right' is to your right when you stand behind the array facing its front.")
    print("Keep the room quiet apart from you.")

    try:
        front_beams, right_beams = [], []
        front = measure(dev, "in FRONT", args.seconds, beams=front_beams)
        while True:
            right = measure(dev, "to the RIGHT", args.seconds, beams=right_beams)
            d = angdiff(right, front)
            if abs(abs(d) - 90) <= RIGHT_TOLERANCE_DEG:
                break
            print(f"  right is {d:+.0f} deg from front; expected about +-90. "
                  "Check you're at the array's right side, then repeat.")
        clockwise = d > 0

        print(f"\nResult: array_offset_deg = {front:.1f}, device angles run "
              f"{'clockwise' if clockwise else 'counter-clockwise'} (right measured {d:+.0f} deg from front)")

        if args.four_point:
            readings = [("in FRONT", 0.0, front), ("to the RIGHT", 90.0, right)]
            readings += [(name, expected, measure(dev, name, args.seconds)) for name, expected in POSITIONS[2:]]
            if not print_position_table(position_errors(readings, front, clockwise)):
                if input("  Some positions are off. Save anyway? [y/N] ").strip().lower() != "y":
                    print("nothing saved")
                    return
        elif input("\nOptional check: measure from BEHIND too? [y/N] ").strip().lower() == "y":
            back = measure(dev, "BEHIND", args.seconds)
            rel = angdiff(back, front)
            world = wrap(rel if clockwise else -rel)
            ok = abs(abs(world) - 180) <= RIGHT_TOLERANCE_DEG
            print(f"  behind reads {world:+.0f} deg in world frame (expected 180): {'OK' if ok else 'CHECK'}")
    except (KeyboardInterrupt, EOFError):
        print("\ncancelled; nothing saved")
        return
    finally:
        dev.close()

    beams = None
    if len(front_beams) >= MIN_SAMPLES and len(right_beams) >= MIN_SAMPLES:
        beams = fit_beams(front, right, circular_mean(front_beams)[0] % 360, circular_mean(right_beams)[0] % 360)
    problem = handedness_problem(clockwise, beams[1] if beams else C.AEC_TO_DEVICE_SIGN)
    if problem:
        print("\nWARNING: " + problem)
        if not args.no_save and input("  Save anyway (only if the array really is face down)? [y/N] ").strip().lower() != "y":
            print("nothing saved")
            return
    if args.no_save:
        print(f"\nnot saved. Use: python -m laptop --real --offset {front:.1f}" + ("" if clockwise else " --ccw"))
        return
    save(front, clockwise)
    if beams is None:
        print("beam directions: not fitted (beams disagreed with the DoA or too few readings); beams mode keeps "
              f"aec_to_device {C.AEC_TO_DEVICE_OFFSET_DEG} / {C.AEC_TO_DEVICE_SIGN}")
    else:
        save_beams(*beams)
        print(f"beam directions (beams mode): aec_to_device_offset_deg {beams[0]}, sign {beams[1]:+d}")
    print(f"\nsaved to {CONFIG_PATH}. python -m laptop and xvf3800.py now use it by default.")
    print("Verify: python -m laptop.audio.calibrate --verify  (front 0, right +90, behind 180, left -90)")


if __name__ == "__main__":
    main()
