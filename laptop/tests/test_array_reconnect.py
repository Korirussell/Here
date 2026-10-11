"""Booth robustness: the ReSpeaker unplugged / replugged or the laptop sleeping mid-demo (builtin_doa watchdog)."""

import asyncio
import contextlib
import time

from laptop.audio import builtin_doa
from laptop.audio import config as C
from laptop.audio.builtin_doa import ReSpeaker


def bare():
    a = ReSpeaker.__new__(ReSpeaker)        # no USB, no audio device
    a.last_audio = time.monotonic()
    a.problems, a.lost, a.reconnects = [], None, 0
    a.ring = None
    return a


def test_watchdog_reasons(tmp_path, monkeypatch):
    a = bare()
    monkeypatch.setattr(C, "DRILL_FILE", str(tmp_path / "drop"), raising=False)
    assert a.watchdog(0, 0.05) is None
    assert "USB control failed" in a.watchdog(10, 0.05)                 # unplugged: control transfers fail
    assert "paused" in a.watchdog(0, 30.0)                               # the laptop slept
    a.last_audio = time.monotonic() - 3.0
    assert "no audio" in a.watchdog(0, 0.05)                             # unplugged: the audio callback stopped
    a.last_audio = time.monotonic()
    (tmp_path / "drop").write_text("")
    assert "drill" in a.watchdog(0, 0.05) and not (tmp_path / "drop").exists()   # a drill fires once


def test_lost_array_degrades_the_heartbeat_then_reconnects_without_a_restart(monkeypatch):
    monkeypatch.setattr(C, "RECONNECT_S", 0.01, raising=False)
    a = bare()
    a.describe = lambda: []
    a.stream = lambda: contextlib.nullcontext()
    events, seen = [], []
    polls = iter(["no audio for 2.0 s"])

    async def poll():
        try:
            return next(polls)                  # first session: the array goes away
        except StopIteration:
            events.append("polling again")
            await asyncio.sleep(10)             # second session: healthy

    attempts = iter([SystemExit("ReSpeaker audio input not found"), None])

    def reopen():
        seen.append(list(a.problems))           # what the heartbeat says while it's gone
        err = next(attempts)
        if err:
            raise err
        a.problems = []                         # connect() rebuilt the setup

    a.poll_doa = poll
    a.reopen = reopen
    a.release = lambda restore: events.append(f"release restore={restore}")

    async def main():
        task = asyncio.create_task(a.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if "polling again" in events:
                break
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())
    assert seen[0] == ["ReSpeaker lost (no audio for 2.0 s); reconnecting"]
    assert "try 1" in seen[1][0] and "not found" in seen[1][0]          # retried, and says why
    assert a.reconnects == 1 and a.lost is None and a.problems == []
    assert events[:2] == ["release restore=True", "polling again"]
    assert events[-1] == "release restore=True"                          # Ctrl+C still restores the routing


def test_poll_returns_after_repeated_usb_failures(monkeypatch):
    monkeypatch.setattr(C, "DOA_RATE_HZ", 200, raising=False)
    a = bare()

    def tick():
        a.last_audio = time.monotonic()
        raise builtin_doa.usb.core.USBError("No such device")

    a.tick = tick
    reason = asyncio.run(asyncio.wait_for(a.poll_doa(), 5))
    assert "USB control failed" in reason
