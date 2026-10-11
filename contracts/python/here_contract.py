"""HERE contract v3 for Python (SPEC §12). Mirrors contracts/schemas/*.schema.json.

    from contracts.python.here_contract import Utterance, parse, validate

    msg = Utterance(src="laptop", t=now, utt_id="u-1", ...)
    text = msg.to_json()
    back = parse(text)              # -> Utterance (unknown types -> Unknown)
    validate(msg.to_dict())         # jsonschema check against the generated schema (raises on error)

Nullable fields (tone, bearing_deg on sound_event/alert, person_label) are always sent,
as null when None. Optional fields (seq, session, stage_ms, ref, detail...) are left out
when None.
"""

from __future__ import annotations

import dataclasses
import json
import typing
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import ClassVar, Literal, Optional

VERSION = 3
SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"

Role = Literal["laptop", "phone", "dashboard", "memory", "tool"]
Src = Literal["laptop", "phone", "relay", "dashboard", "memory", "tool"]
Tone = Literal["neutral", "excited", "laughing", "shouting", "whispering", "questioning", "sad"]
SoundLabel = Literal["speech", "doorbell", "dog_barking", "baby_crying", "knock", "alarm",
                     "music", "tv", "laughter", "phone_ringing", "loud_bang", "car_horn", "vehicle", "siren",
                     "door", "other", "unknown"]   # other = recognised, not in our list; unknown = not classifiable
AlertKind = Literal["doorbell", "name_called", "dog_barking", "baby_crying", "knock", "alarm", "loud_bang",
                    "car_horn", "siren", "door"]
SoundCategory = Literal["speech", "alert", "animal", "media", "other"]
Engine = Literal["faster_whisper", "scribe_realtime"]
Attention = Literal["ambient", "notice", "important", "urgent"]

TONES = typing.get_args(Tone)
SOUND_LABELS = typing.get_args(SoundLabel)
ALERT_KINDS = typing.get_args(AlertKind)


def optional(default=None):
    """A field that is omitted from the JSON when it is None."""
    return field(default=default, metadata={"omit_none": True})


def wrap(deg: float) -> float:
    """Normalize to (-180, 180], the range every WorldBearing uses."""
    a = (deg + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


# ---- nested types --------------------------------------------------------------------------

@dataclass(kw_only=True)
class Source:
    id: int
    bearing_deg: float
    energy: float
    speech: bool
    confidence: float


@dataclass(kw_only=True)
class Features:
    captions: bool = True
    tone: bool = True
    rings: bool = True
    alerts: bool = True
    belt: bool = True


@dataclass(kw_only=True)
class Ref:
    utt_id: Optional[str] = optional()
    event_id: Optional[str] = optional()


@dataclass(kw_only=True)
class ScenePerson:
    label: str
    bearing_deg: float
    speaking: bool


@dataclass(kw_only=True)
class SearchHit:
    t: float
    kind: Literal["utterance", "alert"]
    text: str
    score: float
    person_label: Optional[str] = optional()
    tone: Optional[Tone] = optional()
    bearing_deg: Optional[float] = optional()


# ---- envelope + messages ------------------------------------------------------------------

@dataclass(kw_only=True)
class Message:
    TYPE: ClassVar[str] = ""
    src: Src
    t: float
    v: int = VERSION
    seq: Optional[int] = optional()
    session: Optional[str] = optional()

    def to_dict(self) -> dict:
        return {"type": self.TYPE, **_dump(self)}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, d: dict):
        return _load(cls, d)


@dataclass(kw_only=True)
class Hello(Message):
    TYPE: ClassVar[str] = "hello"
    role: Role
    subscribe: list[str] = field(default_factory=list)
    app_version: Optional[str] = optional()


@dataclass(kw_only=True)
class Heartbeat(Message):
    TYPE: ClassVar[str] = "heartbeat"
    status: Literal["ok", "degraded", "error"]
    detail: Optional[str] = optional()
    thermal: Optional[Literal["nominal", "fair", "serious", "critical"]] = optional()
    engine: Optional[Engine] = optional()
    doa_source: Optional[Literal["builtin", "odas"]] = optional()


@dataclass(kw_only=True)
class ClockPing(Message):
    TYPE: ClassVar[str] = "clock_ping"
    t_phone: float


@dataclass(kw_only=True)
class ClockPong(Message):
    TYPE: ClassVar[str] = "clock_pong"
    t_phone: float
    t_laptop: float


@dataclass(kw_only=True)
class Config(Message):
    TYPE: ClassVar[str] = "config"
    user_name: str = ""
    name_aliases: list[str] = field(default_factory=list)
    focus_mode: bool = False
    belt_only_mode: bool = False
    features: Features = field(default_factory=Features)
    alerts_enabled: dict[str, bool] = field(default_factory=dict)
    belt_strength: int = 200


@dataclass(kw_only=True)
class Sources(Message):
    TYPE: ClassVar[str] = "sources"
    sources: list[Source] = field(default_factory=list)


@dataclass(kw_only=True)
class SpeechStart(Message):
    TYPE: ClassVar[str] = "speech_start"
    utt_id: str
    bearing_deg: float
    source_id: int
    confidence: float


@dataclass(kw_only=True)
class Utterance(Message):
    TYPE: ClassVar[str] = "utterance"
    utt_id: str
    t_start: float
    t_end: float
    text: str
    pending: str
    final: bool
    bearing_deg: float
    bearing_spread_deg: float
    source_id: int
    overlap: bool
    tone: Optional[Tone]
    tone_confidence: float
    loudness: float
    engine: Engine
    stage_ms: Optional[dict[str, float]] = optional()


@dataclass(kw_only=True)
class SoundEvent(Message):
    TYPE: ClassVar[str] = "sound_event"
    event_id: str
    label: SoundLabel
    category: SoundCategory
    emoji: str
    text: str
    confidence: float
    bearing_deg: Optional[float]
    t_start: float
    t_end: float
    count: int = 1
    source_id: Optional[int] = optional()     # the sources track it came from
    model: Optional[str] = optional()         # which classifier labelled it, e.g. "panns_cnn14"


@dataclass(kw_only=True)
class Alert(Message):
    TYPE: ClassVar[str] = "alert"
    alert_id: str
    kind: AlertKind
    label: str
    emoji: str
    priority: Literal[1, 2, 3]
    bearing_deg: Optional[float]
    active: bool
    confidence: float
    ref: Optional[Ref] = optional()


@dataclass(kw_only=True)
class PerceptionEnrichment(Message):
    """Optional enrichment of an earlier event (ref): attention (Hugging Face model), Gemini context. Never decides existence/direction."""
    TYPE: ClassVar[str] = "perception_enrichment"
    ref: Ref
    attention: Attention
    attention_confidence: float
    attention_model: str                                   # Hugging Face repo id, or "rules" (deterministic fallback)
    directed_at_user: Optional[float] = optional()         # probability the speech is aimed at the wearer
    context: Optional[str] = optional()                    # Gemini one-line note
    context_model: Optional[str] = optional()


@dataclass(kw_only=True)
class Attribution(Message):
    TYPE: ClassVar[str] = "attribution"
    ref: Ref
    person_label: Optional[str]
    method: Literal["bearing", "off_camera"]
    score: float


@dataclass(kw_only=True)
class Scene(Message):
    TYPE: ClassVar[str] = "scene"
    heading_deg: float
    people: list[ScenePerson] = field(default_factory=list)


@dataclass(kw_only=True)
class SearchRequest(Message):
    TYPE: ClassVar[str] = "search_request"
    query_id: str
    text: str
    limit: int = 5


@dataclass(kw_only=True)
class SearchResult(Message):
    TYPE: ClassVar[str] = "search_result"
    query_id: str
    results: list[SearchHit] = field(default_factory=list)


@dataclass(kw_only=True)
class Unknown(Message):
    """A message whose type this version doesn't know. Kept as raw fields; receivers ignore it."""
    TYPE: ClassVar[str] = ""
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return dict(self.raw)


MESSAGE_TYPES: dict[str, type[Message]] = {cls.TYPE: cls for cls in (
    Hello, Heartbeat, ClockPing, ClockPong, Config, Sources, SpeechStart, Utterance,
    SoundEvent, Alert, PerceptionEnrichment, Attribution, Scene, SearchRequest, SearchResult)}

# SPEC §12.10: what each role receives when its hello doesn't list subscriptions.
DEFAULT_SUBSCRIBE: dict[str, list[str]] = {
    "phone": ["sources", "speech_start", "utterance", "sound_event", "alert", "perception_enrichment", "config",
              "clock_pong", "heartbeat"],
    "laptop": ["config", "clock_ping"],
    "dashboard": ["*"],
    "memory": ["utterance", "alert", "sound_event", "perception_enrichment", "attribution", "search_request"],
    "tool": ["*"],
}


def parse(data: str | bytes | dict) -> Message:
    """JSON text or a decoded dict -> the matching dataclass (Unknown for unknown types)."""
    d = json.loads(data) if isinstance(data, (str, bytes)) else data
    cls = MESSAGE_TYPES.get(d.get("type", ""))
    if cls is None:
        return Unknown(src=d.get("src", "tool"), t=d.get("t", 0.0), raw=d)
    return cls.from_dict(d)


# ---- validation ----------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _validator(type_name: str):
    import jsonschema

    path = SCHEMA_DIR / f"{type_name}.schema.json"
    if not path.exists():
        path = SCHEMA_DIR / "envelope.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def validate(d: dict) -> None:
    """Raise jsonschema.ValidationError if `d` doesn't match its type's schema. Needs `jsonschema`."""
    _validator(d.get("type", "")).validate(d)


def errors(d: dict) -> list[str]:
    """All schema errors for `d`, as readable strings (empty when valid)."""
    return [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in _validator(d.get("type", "")).iter_errors(d)]


# ---- (de)serialization helpers -------------------------------------------------------------

def _dump(obj) -> dict:
    out = {}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        if value is None and f.metadata.get("omit_none"):
            continue
        out[f.name] = _dump_value(value)
    return out


def _dump_value(value):
    if dataclasses.is_dataclass(value):
        return _dump(value)
    if isinstance(value, list):
        return [_dump_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump_value(v) for k, v in value.items()}
    return value


@lru_cache(maxsize=None)
def _hints(cls):
    return typing.get_type_hints(cls)


def _load(cls, d: dict):
    hints = _hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in d:
            kwargs[f.name] = _load_value(hints[f.name], d[f.name])
    return cls(**kwargs)


def _load_value(hint, value):
    if value is None:
        return None
    origin = typing.get_origin(hint)
    if origin is typing.Union:
        inner = [a for a in typing.get_args(hint) if a is not type(None)]
        return _load_value(inner[0], value) if len(inner) == 1 else value
    if origin is list:
        (item,) = typing.get_args(hint)
        return [_load_value(item, v) for v in value]
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return _load(hint, value)
    return value


def type_name(msg: Message | dict) -> str:
    return msg.get("type", "") if isinstance(msg, dict) else (msg.TYPE or msg.to_dict().get("type", ""))


