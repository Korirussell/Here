"""ReSpeaker XVF3800 over its USB control interface, plus a live direction readout (SPEC §11, §21).

`XVF3800` (register reads/writes) and `sample()` are what laptop/audio/builtin_doa.py uses.
Run directly for a standalone compass readout with no relay and no models:

Requires: pip install pyusb libusb-package pyyaml
(Windows: the "reSpeaker Control" interface must use the WinUSB driver; it does by default.)

    python laptop/audio/xvf3800.py                       # live printout at 20 Hz
    python laptop/audio/xvf3800.py --log doa_log.jsonl   # also log raw readings
    python laptop/audio/xvf3800.py --offset 190 --ccw    # override the saved calibration (SPEC §8)
    python laptop/audio/xvf3800.py --dump                # print every raw register once, then exit

(Formerly audio_here/respeaker_probe.py. Its v1 `--hub` publishing is gone: `python -m laptop --real`
sends contract v3 `sources` to the relay.)
"""

import argparse
import asyncio
import json
import math
import struct
import sys
import threading
import time

import libusb_package
import usb.core
import usb.util

try:  # saved calibration (python -m audio_here.calibrate); works as a script or as part of the package
    from . import config as C
except ImportError:
    import config as C

VID, PID = 0x2886, 0x001A
CONTROL_SUCCESS = 0
SERVICER_COMMAND_RETRY = 64
USB_TIMEOUT_MS = 1000
ENERGY_HOLD_S = 0.5  # device speech energy arrives in bursts; hold the peak this long (tune)

# name: (resid, cmdid, count, struct format), from Seeed's python_control/xvf_host.py
PARAMETERS = {
    "VERSION": (48, 0, 3, "B"),
    # [deg 0..359, speech 0/1]; this is what drives the LED ring
    "DOA_VALUE": (20, 18, 2, "H"),
    # radians: focused beam 1, focused beam 2, free-running beam, auto-select beam
    "AEC_AZIMUTH_VALUES": (33, 75, 4, "f"),
    # per-beam speech energy, same order; > 0 means speech
    "AEC_SPENERGY_VALUES": (33, 80, 4, "f"),
    # radians: [processed DoA (NaN when no speech), auto-select beam DoA]
    "AUDIO_MGR_SELECTED_AZIMUTHS": (35, 11, 2, "f"),
    "AEC_MIC_ARRAY_GEO": (33, 74, 12, "f"),  # x, y, z per mic, metres
    "AEC_RT60": (33, 9, 1, "f"),
    # 0 off, 1 breath, 2 rainbow, 3 single colour, 4 doa (firmware-driven), 5 ring (LED_RING_COLOR)
    "LED_EFFECT": (20, 12, 1, "B"),
    "LED_BRIGHTNESS": (20, 13, 1, "B"),
    "LED_COLOR": (20, 16, 1, "I"),
    "LED_DOA_COLOR": (20, 17, 2, "I"),
    "LED_RING_COLOR": (20, 19, 12, "I"),  # 0xRRGGBB per LED, shown in effect 5
    # USB capture routing, (category, source) per channel. Category 6 = processed beams: 0, 1 the two
    # slow-moving focused beams, 2 the fast-moving beam, 3 auto-select; 8 = "user chosen" (auto-select copy,
    # the L default); 3 = amplified raw mic 0..3; 0 = silence. Volatile: power-cycling restores the defaults.
    # Fixed focused beams: the two beams stay at set azimuths (radians, mic-geometry frame) instead of chasing
    # the loudest voice-like sound (a crying baby took both beams in the real two-source test).
    "AEC_FIXEDBEAMSONOFF": (33, 37, 1, "i"),
    "AEC_FIXEDBEAMSAZIMUTH_VALUES": (33, 81, 2, "f"),
    "AEC_FIXEDBEAMSGATING": (33, 83, 1, "B"),
    "AUDIO_MGR_OP_L": (35, 15, 2, "B"),
    "AUDIO_MGR_OP_R": (35, 19, 2, "B"),
    # 6-channel firmware: what USB ch 3..6 carry (category 3 = amplified raw mic n), and the pre-SHF mic gain
    # (linear) that "amplified" refers to. Volatile, like the routing.
    "AUDIO_MGR_OP_CH3": (35, 28, 2, "B"),
    "AUDIO_MGR_OP_CH4": (35, 29, 2, "B"),
    "AUDIO_MGR_OP_CH5": (35, 30, 2, "B"),
    "AUDIO_MGR_OP_CH6": (35, 31, 2, "B"),
    "AUDIO_MGR_MIC_GAIN": (35, 0, 1, "f"),
}

FMT_SIZE = {"B": 1, "H": 2, "f": 4, "i": 4, "I": 4}


def wrap(deg):
    """Map any angle into (-180, 180]."""
    a = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


class XVF3800:
    def __init__(self):
        self.dev = libusb_package.find(idVendor=VID, idProduct=PID)
        if self.dev is None:
            sys.exit("ReSpeaker XVF3800 not found (VID 0x2886 PID 0x001A). Is it plugged in?")
        try:
            self.read("VERSION")
        except usb.core.USBError as e:
            if e.errno == 13:
                sys.exit("ReSpeaker control interface is busy: only one program can use it at a time.\n"
                         "Stop audio_here / respeaker_probe.py / calibrate (Ctrl+C in that window) and retry.")
            raise

    # The DoA poll and the LED ring share one control pipe from different threads.
    _lock = threading.Lock()

    def read(self, name):
        with self._lock:
            return self._read(name)

    def _read(self, name):
        resid, cmdid, count, fmt = PARAMETERS[name]
        length = count * FMT_SIZE[fmt] + 1  # first byte is a status code
        request = usb.util.CTRL_IN | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE
        for attempt in range(100):
            try:
                resp = self.dev.ctrl_transfer(request, 0, 0x80 | cmdid, resid, length, USB_TIMEOUT_MS)
            except usb.core.USBError:
                # The device occasionally stalls a control read ("Pipe error"); retry a few times.
                if attempt >= 4:
                    raise
                time.sleep(0.005)
                continue
            if resp[0] == CONTROL_SUCCESS:
                return struct.unpack("<" + fmt * count, bytes(resp[1:length]))
            if resp[0] != SERVICER_COMMAND_RETRY:
                raise RuntimeError(f"{name}: device status {resp[0]}")
            time.sleep(0.001)
        raise RuntimeError(f"{name}: device kept asking to retry")

    def write(self, name, *values):
        """Write a parameter. Reads set 0x80 on the command id; writes don't (Seeed xvf_host.py)."""
        resid, cmdid, count, fmt = PARAMETERS[name]
        if len(values) != count:
            raise ValueError(f"{name} takes {count} values, got {len(values)}")
        payload = struct.pack("<" + fmt * count, *values)
        request = usb.util.CTRL_OUT | usb.util.CTRL_TYPE_VENDOR | usb.util.CTRL_RECIPIENT_DEVICE
        with self._lock:
            for attempt in range(5):
                try:
                    self.dev.ctrl_transfer(request, 0, cmdid, resid, payload, USB_TIMEOUT_MS)
                    return
                except usb.core.USBError:
                    if attempt >= 4:
                        raise
                    time.sleep(0.005)

    def mic_geometry(self):
        """Mic positions [(x, y, z)] in metres, in device order (= raw channels 2..5 on 6-ch firmware)."""
        g = self.read("AEC_MIC_ARRAY_GEO")
        return [tuple(g[i:i + 3]) for i in range(0, 12, 3)]

    def close(self):
        usb.util.dispose_resources(self.dev)


def sample(dev):
    """One raw reading of everything direction-related."""
    t = time.time()
    doa_deg, speech = dev.read("DOA_VALUE")
    azimuths = dev.read("AEC_AZIMUTH_VALUES")
    energies = dev.read("AEC_SPENERGY_VALUES")
    processed, autosel = dev.read("AUDIO_MGR_SELECTED_AZIMUTHS")
    return {
        "t": t,
        "doa_deg": doa_deg,
        "speech": bool(speech),
        "beam_az_deg": [round(math.degrees(a) % 360, 1) for a in azimuths],
        "beam_energy": [round(e, 1) for e in energies],
        "processed_deg": None if math.isnan(processed) else round(math.degrees(processed) % 360, 1),
        "autosel_deg": round(math.degrees(autosel) % 360, 1),
    }


def to_world(raw_deg, offset_deg, ccw):
    """Device angle -> world bearing (section 6: clockwise positive, 0 = array forward)."""
    rel = raw_deg - offset_deg
    return round(wrap(-rel if ccw else rel), 1)


def to_doa_message(s, args, seq, energy):
    """One readout line's numbers (v1 `doa` shape, kept for the log). confidence/energy_db are (tune) proxies."""
    energy_db = 10 * math.log10(energy) if energy > 0 else -100.0
    # Speech energy spans roughly 1e2..1e6 in practice; map 20..60 dB onto 0..1.
    confidence = min(1.0, max(0.0, (energy_db - 20) / 40)) if s["speech"] else 0.0
    return {
        "type": "doa", "v": 1, "src": "audio_here", "t": s["t"], "seq": seq,
        "bearing_deg": to_world(s["doa_deg"], args.offset, args.ccw),
        "confidence": round(confidence, 2),
        "speech": s["speech"],
        "energy_db": round(energy_db, 1),
    }


def compass(deg, width=36):
    """Text strip from -180 to 180 with a marker at deg."""
    pos = int((wrap(deg) + 180) / 360 * (width - 1))
    strip = ["-"] * width
    strip[width // 2] = "|"
    strip[pos] = "O"
    return "".join(strip)


async def run(args):
    dev = XVF3800()
    print(f"ReSpeaker XVF3800 firmware {'.'.join(map(str, dev.read('VERSION')))}")
    print(f"calibration: offset {args.offset} deg, device is {'counter-clockwise' if args.ccw else 'clockwise'}")

    log = open(args.log, "a", encoding="utf-8", buffering=1) if args.log else None

    period = 1.0 / args.rate
    seq = 0
    rate_count, rate_since = 0, time.time()
    rate_hz = 0.0
    held_energy, held_t = 0.0, 0.0
    next_tick = time.perf_counter()
    try:
        while True:
            s = sample(dev)
            seq += 1
            rate_count += 1
            if s["t"] - rate_since >= 1.0:
                rate_hz = rate_count / (s["t"] - rate_since)
                rate_count, rate_since = 0, s["t"]

            energy = max(s["beam_energy"])
            if energy > 0 or s["t"] - held_t > ENERGY_HOLD_S:
                held_energy, held_t = energy, s["t"]
            msg = to_doa_message(s, args, seq, held_energy)
            if log:
                log.write(json.dumps({**s, "bearing_deg": msg["bearing_deg"]}) + "\n")
            speech = "SPEECH" if s["speech"] else "  -   "
            proc = f"{s['processed_deg']:5.1f}" if s["processed_deg"] is not None else "  -  "
            line = (f"\r{speech} world {msg['bearing_deg']:6.1f} [{compass(msg['bearing_deg'])}] "
                    f"raw {s['doa_deg']:3d} proc {proc} conf {msg['confidence']:.2f} "
                    f"{msg['energy_db']:6.1f} dB {rate_hz:4.0f} Hz   ")
            if args.scroll:
                print(line.strip())
            else:
                print(line, end="", flush=True)

            next_tick += period
            await asyncio.sleep(max(0.0, next_tick - time.perf_counter()))
    finally:
        print()
        if log:
            log.close()
        dev.close()


def dump():
    dev = XVF3800()
    for name in PARAMETERS:
        try:
            print(f"{name}: {dev.read(name)}")
        except RuntimeError as e:   # e.g. LED_RING_COLOR is write-only on firmware 2.0.6 (status 65)
            print(f"{name}: not readable ({e})")
    dev.close()


def reset():
    """Volatile settings HERE changes, back to the firmware defaults (after a killed run; power-cycling does it too)."""
    dev = XVF3800()
    dev.write("AEC_FIXEDBEAMSONOFF", 0)
    dev.write("AUDIO_MGR_OP_L", 8, 0)
    dev.write("AUDIO_MGR_OP_R", 7, 3)
    raw = []
    for k, reg in enumerate(("AUDIO_MGR_OP_CH3", "AUDIO_MGR_OP_CH4", "AUDIO_MGR_OP_CH5", "AUDIO_MGR_OP_CH6")):
        try:
            dev.write(reg, 1, k)              # 6-channel firmware only: raw mics unamplified (the default)
            raw.append(dev.read(reg))
        except (usb.core.USBError, RuntimeError):
            break
    print(f"reset: fixed beams off, OP_L {dev.read('AUDIO_MGR_OP_L')}, OP_R {dev.read('AUDIO_MGR_OP_R')}"
          f"{f', raw mics {raw}' if raw else ''}")
    dev.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rate", type=float, default=20.0, help="samples per second (default 20)")
    p.add_argument("--offset", type=float, default=C.ARRAY_OFFSET_DEG,
                   help=f"device angle that means 'array forward' (default {C.ARRAY_OFFSET_DEG}, from laptop/config.yaml)")
    p.add_argument("--ccw", action=argparse.BooleanOptionalAction, default=not C.ARRAY_CLOCKWISE,
                   help="device angles increase counter-clockwise (default from laptop/config.yaml)")
    p.add_argument("--log", help="append raw readings as JSONL to this file")
    p.add_argument("--scroll", action="store_true", help="one line per sample instead of updating in place")
    p.add_argument("--dump", action="store_true", help="print every known register once and exit")
    p.add_argument("--reset", action="store_true", help="restore HERE's volatile settings (beams, routing) and exit")
    args = p.parse_args()

    if args.reset:
        reset()
        return
    if args.dump:
        dump()
        return
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
