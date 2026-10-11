"""Alerts (SPEC §15): sound alerts and name called, deduplicated and expired here, shown and felt on the phone.

Same kind from a similar direction within dedupe_s updates the existing alert (same alert_id).
An alert ends (`active: false`) expire_s after its last detection. The phone's `config` can switch
alerts off (features.alerts) or per kind (alerts_enabled).
"""

import itertools
import time

from contracts.python.here_contract import Alert, Ref
from laptop.geometry import angdiff
from laptop.sounds.labels import ALERT_DISPLAY


class AlertManager:
    def __init__(self, cfg, emit, clock=time.time):
        self.cfg = cfg
        self.emit = emit
        self.clock = clock
        self.active = {}                 # alert_id -> dict(kind, bearing, last, msg fields)
        self.ids = itertools.count(1)
        self.prefix = f"a{int(clock()) % 100000}"
        self.enabled = True
        self.kinds_enabled = {}

    def set_config(self, features_alerts: bool, alerts_enabled: dict):
        self.enabled = features_alerts
        self.kinds_enabled = dict(alerts_enabled or {})

    def min_confidence(self, kind):
        return self.cfg.get("min_confidence_by_kind", {}).get(kind, self.cfg["min_confidence"])

    def raise_alert(self, kind, bearing, confidence, utt_id=None, event_id=None, t=None, min_confidence=None):
        """Returns the Alert sent, or None if filtered out. min_confidence: the sound model's own minimum for this
        kind (score scales differ between classifiers), instead of alerts.min_confidence_by_kind."""
        floor = self.min_confidence(kind) if min_confidence is None else min_confidence
        if not self.enabled or self.kinds_enabled.get(kind) is False or confidence < floor:
            return None
        now = self.clock()
        t = now if t is None else t
        existing = None
        for aid, a in self.active.items():
            if a["kind"] != kind or now - a["last"] > self.cfg["dedupe_s"]:
                continue
            same_side = (a["bearing"] is None or bearing is None
                         or abs(angdiff(a["bearing"], bearing)) <= self.cfg["dedupe_deg"])
            if same_side:
                existing = aid
                break
        label, emoji, priority = ALERT_DISPLAY[kind]
        ref = Ref(utt_id=utt_id, event_id=event_id) if (utt_id or event_id) else None
        if existing is None:
            existing = f"{self.prefix}-{next(self.ids)}"
            self.active[existing] = {"kind": kind, "bearing": bearing, "last": now, "confidence": confidence}
            print(f"[alert] {label} at {'?' if bearing is None else f'{bearing:+.0f}'} deg ({confidence:.2f})")
        a = self.active[existing]
        a["last"] = now
        if bearing is not None:
            a["bearing"] = bearing
        a["confidence"] = max(a["confidence"], confidence)
        a["ref"] = ref or a.get("ref")
        msg = Alert(src="laptop", t=t, alert_id=existing, kind=kind, label=label, emoji=emoji, priority=priority,
                    bearing_deg=a["bearing"], active=True, confidence=round(min(1.0, a["confidence"]), 2),
                    ref=a["ref"])
        self.emit(msg)
        return msg

    def expire(self):
        """End alerts whose last detection is older than expire_s. Call ~1 Hz."""
        now = self.clock()
        for aid in [aid for aid, a in self.active.items() if now - a["last"] > self.cfg["expire_s"]]:
            a = self.active.pop(aid)
            label, emoji, priority = ALERT_DISPLAY[a["kind"]]
            self.emit(Alert(src="laptop", t=now, alert_id=aid, kind=a["kind"], label=label, emoji=emoji,
                            priority=priority, bearing_deg=a["bearing"], active=False,
                            confidence=round(min(1.0, a["confidence"]), 2), ref=a.get("ref")))

    def name_called(self, name, score, bearing, utt_id, t):
        return self.raise_alert("name_called", bearing, max(score, self.min_confidence("name_called")),
                                utt_id=utt_id, t=t)
