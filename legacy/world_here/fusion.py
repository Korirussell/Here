"""The world model: people, off-camera speakers, DoA history and speech attribution (HERE_PLAN.md §9)."""

from collections import deque
from dataclasses import dataclass, field

from here_common.geometry import angdiff, circular_mean, wrap

from . import config as C


@dataclass
class Entity:
    id: int
    kind: str
    bearing: float | None
    last_seen: float               # last time on camera (people) or heard (off-camera)
    bbox: list | None = None
    face_bbox: list | None = None
    raw_class: str = ""
    on_camera: bool = False
    speaking: float = 0.0          # lip-motion score from vision_here (0..1)
    speaking_t: float = 0.0


@dataclass
class UttState:
    utt_id: str
    speaker_id: int
    bearing: float | None
    overlap: bool
    msg: dict
    updated: float                 # hub receive time; captions age from this, so late finals still show
    final: bool = False


@dataclass
class World:
    heading_raw: float | None = None
    heading_offset: float = 0.0
    entities: dict = field(default_factory=dict)       # id -> Entity
    doa: deque = field(default_factory=lambda: deque(maxlen=4000))
    utts: dict = field(default_factory=dict)            # utt_id -> UttState
    last_spoke: dict = field(default_factory=dict)      # entity id -> t
    next_offcam_id: int = C.OFFCAM_ID_START
    config: dict = field(default_factory=lambda: dict(C.DEFAULT_CONFIG))

    # ---- inputs -------------------------------------------------------------

    @property
    def heading(self):
        """Calibrated heading: the world bearing the wearer faces."""
        return None if self.heading_raw is None else wrap(self.heading_raw - self.heading_offset)

    def on_heading(self, msg):
        self.heading_raw = msg["heading_raw_deg"]

    def calibrate_heading_zero(self):
        self.heading_offset = self.heading_raw or 0.0
        return self.heading_offset

    def on_doa(self, msg):
        self.doa.append((msg["t"], msg["bearing_deg"], msg["confidence"], msg["speech"]))

    def on_detections(self, msg):
        t = msg["t"]
        seen = set()
        for o in msg["objects"]:
            eid = o["track_id"]
            seen.add(eid)
            e = self.entities.get(eid)
            if e is None:
                e = self.entities[eid] = Entity(eid, o["kind"], o["bearing_deg"], t)
            e.kind, e.bearing, e.last_seen = o["kind"], o["bearing_deg"], t
            e.bbox, e.face_bbox, e.raw_class = o["bbox"], o.get("face_bbox"), o["raw_class"]
            e.on_camera = True
            if o.get("speaking") is not None:
                e.speaking, e.speaking_t = o["speaking"], t
        for e in self.entities.values():
            if e.on_camera and e.id not in seen and t - e.last_seen > C.VISIBLE_TIMEOUT_S:
                e.on_camera = False
        for eid in [e.id for e in self.entities.values() if t - e.last_seen > C.ARCHIVE_S]:
            del self.entities[eid]

    def on_utterance(self, msg, received=None):
        """Attribute an utterance (partial or final) to a speaker entity (§9.1)."""
        received = msg["t_end"] if received is None else received
        t_start, t_end = msg["t_start"], msg["t_end"]
        state = self.utts.get(msg["utt_id"])
        samples = self._doa_between(t_start - C.DOA_WINDOW_PAD_S, t_end + C.DOA_WINDOW_PAD_S)
        mean, std = circular_mean([s[1] for s in samples], [s[2] for s in samples]) if samples else (None, None)
        overlap = std is not None and std > C.OVERLAP_STD_DEG

        if state is None:
            speaker_id = self._choose_speaker(mean, t_end)
            state = self.utts[msg["utt_id"]] = UttState(msg["utt_id"], speaker_id, mean, overlap, msg, received)
        else:
            # Identity sticks unless the DoA clearly moves (> MOVE_DEG for > MOVE_HOLD_S).
            recent = self._doa_between(t_end - C.MOVE_HOLD_S, t_end + C.DOA_WINDOW_PAD_S)
            if recent and state.bearing is not None and recent[-1][0] - recent[0][0] >= 0.8 * C.MOVE_HOLD_S:
                rmean, _ = circular_mean([s[1] for s in recent], [s[2] for s in recent])
                if abs(angdiff(rmean, state.bearing)) > C.MOVE_DEG:
                    state.speaker_id, state.bearing = self._choose_speaker(rmean, t_end), rmean
            elif state.bearing is None and mean is not None:
                state.speaker_id, state.bearing = self._choose_speaker(mean, t_end), mean
            state.overlap, state.msg, state.updated = overlap, msg, received

        state.final = msg["final"]
        self.last_spoke[state.speaker_id] = t_end
        sp = self.entities.get(state.speaker_id)
        if sp is not None and not sp.on_camera and state.bearing is not None:
            sp.bearing, sp.last_seen = state.bearing, t_end
        return state

    def _doa_between(self, t0, t1):
        return [d for d in self.doa
                if t0 <= d[0] <= t1 and d[3] and d[2] >= C.DOA_MIN_CONFIDENCE]

    def _lips(self, e, t):
        """Recent lip-motion score of an entity, 0 if stale or never seen."""
        return e.speaking if abs(t - e.speaking_t) <= C.LIPS_MAX_AGE_S else 0.0

    def _choose_speaker(self, bearing, t):
        if bearing is None:
            # No direction: the person whose lips are moving, else the only person on camera.
            visible = [e for e in self.entities.values() if e.on_camera and e.kind == "person"]
            talking = [e for e in visible if self._lips(e, t) >= C.LIPS_SPEAKING_MIN]
            if talking:
                return max(talking, key=lambda e: self._lips(e, t)).id
            return visible[0].id if len(visible) == 1 else C.UNKNOWN_SPEAKER_ID

        # 1. Nearest visible person within MATCH_VISIBLE_DEG; prefer the most recent speaker on near-ties.
        cands = sorted(
            ((abs(angdiff(e.bearing, bearing)), e) for e in self.entities.values()
             if e.on_camera and e.kind == "person" and e.bearing is not None
             and abs(angdiff(e.bearing, bearing)) <= C.MATCH_VISIBLE_DEG),
            key=lambda c: c[0])
        if cands:
            # Several people near the speech bearing: moving lips decide (§9.1 mouth-movement scoring).
            talking = [e for _, e in cands if self._lips(e, t) >= C.LIPS_SPEAKING_MIN]
            if len(cands) > 1 and talking:
                return max(talking, key=lambda e: self._lips(e, t)).id
            best_d, best = cands[0]
            close = [e for d, e in cands if d - best_d <= C.RECENT_SPEAKER_TIEBREAK_DEG]
            return max(close, key=lambda e: self.last_spoke.get(e.id, 0.0)).id

        # 2. Reuse an off-camera speaker (or a person who left the frame) active recently near this bearing.
        reuse = [e for e in self.entities.values()
                 if not e.on_camera and e.bearing is not None and e.kind in ("person", "unknown_source")
                 and t - max(e.last_seen, self.last_spoke.get(e.id, 0.0)) <= C.OFFCAM_REUSE_S
                 and abs(angdiff(e.bearing, bearing)) <= C.MATCH_OFFCAM_DEG]
        if reuse:
            return min(reuse, key=lambda e: abs(angdiff(e.bearing, bearing))).id

        # 3. New off-camera speaker entity.
        eid = self.next_offcam_id
        self.next_offcam_id += 1
        self.entities[eid] = Entity(eid, "person", bearing, t)
        return eid

    # ---- output -------------------------------------------------------------

    def frame_meta(self, vf, now):
        """Build the hub → ar_here `frame` message for one video frame (§8.8)."""
        names = {p["id"]: p["name"] for p in self.config.get("known_people", [])}
        faces, objects = [], []
        for e in self.entities.values():
            if not e.on_camera or e.bbox is None:
                continue
            if e.kind == "person":
                x, y, w, h = e.bbox
                box = e.face_bbox or [x + w * 0.25, y, w * 0.5, min(h * 0.2, w * 0.6)]
                faces.append({"id": e.id, "bbox": [round(v, 1) for v in box], "bearing": round(e.bearing, 1)})
            else:
                objects.append({"id": e.id, "kind": e.kind, "bbox": [round(v, 1) for v in e.bbox],
                                "bearing": round(e.bearing, 1)})

        latest = {}
        for s in list(self.utts.values()):
            age = now - s.updated
            if (s.final and age > C.CAPTION_HOLD_S) or age > C.CAPTION_STALE_S:
                del self.utts[s.utt_id]
                continue
            if s.speaker_id not in latest or s.updated > latest[s.speaker_id].updated:
                latest[s.speaker_id] = s

        captions = []
        for sid, s in latest.items():
            m = s.msg
            if not m["text"] and not m["pending"]:
                continue                      # retracted (e.g. a final that turned out to be noise)
            cap = {"speaker_id": sid, "text": m["text"], "pending": m["pending"], "final": m["final"],
                   "tone": m.get("tone"), "tone_confidence": m.get("tone_confidence", 0.0),
                   "loudness": m.get("loudness", 0.0), "overlap": s.overlap}
            if sid in names:
                cap["speaker_label"] = names[sid]
            sp = self.entities.get(sid)
            if (sp is None or not sp.on_camera) and s.bearing is not None:
                cap["bearing"] = round(s.bearing, 1)
            captions.append(cap)

        return {
            "type": "frame", "v": 1, "src": "world_here", "t": vf["t"],
            "frame_id": vf["frame_id"], "width": vf["width"], "height": vf["height"],
            "camera": {"hfov_deg": vf["hfov_deg"], "yaw_deg": round(self.heading or 0.0, 1)},
            "faces": faces, "captions": captions, "objects": objects,
        }
