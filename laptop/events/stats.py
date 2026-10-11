"""Rolling stage timings (K1 D, Shared/INTEGRATION_CONTRACT.md §7): capture, doa, serialize, socket_send.

    capture      audio: ADC time of a block's first sample -> the driver callback (the device + WASAPI buffer)
    doa          direction: one USB DoA poll (sample + ring flush) on the control pipe
    serialize    a message -> JSON text in the relay client
    socket_send  the client handed the message to its outbox -> the websocket write finished

Each sample also lands in the message's log line (`_stage`, logs/laptop.jsonl); the --dashboard shows p50/p95.
"""

from __future__ import annotations

from collections import deque

STAGES = ("capture", "doa", "serialize", "socket_send")


class StageStats:
    def __init__(self, window: int = 400):
        self.samples: dict[str, deque] = {}
        self.window = window

    def add(self, stage: str, ms: float) -> None:
        self.samples.setdefault(stage, deque(maxlen=self.window)).append(float(ms))

    def latest(self, stage: str):
        q = self.samples.get(stage)
        return q[-1] if q else None

    def summary(self) -> dict[str, tuple[float, float, int]]:
        """stage -> (p50 ms, p95 ms, samples), known stages first."""
        out = {}
        for stage in list(STAGES) + sorted(k for k in self.samples if k not in STAGES):
            q = self.samples.get(stage)
            if q:
                xs = sorted(q)
                out[stage] = (round(xs[len(xs) // 2], 2), round(xs[min(len(xs) - 1, int(len(xs) * 0.95))], 2),
                              len(xs))
        return out

    def line(self) -> str:
        return "  ".join(f"{k} {p50:.1f}/{p95:.1f}" for k, (p50, p95, _) in self.summary().items()) or "-"


STATS = StageStats()          # one per process: the client, the array and the dashboard share it
