"""Generates contracts/schemas/*.schema.json from README §12 (contract v3).

This file is the one place the wire shapes are written down in code. After editing it:

    python contracts/build_schemas.py        # rewrite the schemas
    python -m pytest contracts               # examples, Python/Swift/TS mirrors, drift check

Then update contracts/python/here_contract.py, contracts/swift/HereContract.swift and
contracts/ts/contract.ts to match. Contract changes go through the integrator (README §12).
"""

import json
from pathlib import Path

VERSION = 3
OUT = Path(__file__).with_name("schemas")

# ---- shared types (§12.3) ---------------------------------------------------------------

ROLES = ["laptop", "phone", "dashboard", "memory", "tool"]
SRCS = ["laptop", "phone", "relay", "dashboard", "memory", "tool"]
TONES = ["neutral", "excited", "laughing", "shouting", "whispering", "questioning", "sad"]
SOUND_LABELS = ["speech", "doorbell", "dog_barking", "baby_crying", "knock", "alarm",
                "music", "tv", "laughter", "phone_ringing", "loud_bang", "car_horn", "vehicle", "siren", "door",
                "other", "unknown"]   # other = recognised, not in our list; unknown = heard, not classifiable
ALERT_KINDS = ["doorbell", "name_called", "dog_barking", "baby_crying", "knock", "alarm", "loud_bang",
               "car_horn", "siren", "door"]
SOUND_CATEGORIES = ["speech", "alert", "animal", "media", "other"]
ENGINES = ["faster_whisper", "scribe_realtime"]
ATTENTION = ["ambient", "notice", "important", "urgent"]

NUM = {"type": "number"}
STR = {"type": "string"}
BOOL = {"type": "boolean"}
UNIT = {"type": "number", "minimum": 0, "maximum": 1}
INT0 = {"type": "integer", "minimum": 0}
BEARING = {"type": "number", "exclusiveMinimum": -180, "maximum": 180,
           "description": "degrees, world frame (0 = array forward), clockwise positive, (-180, 180]"}
BEARING_OR_NULL = {"anyOf": [BEARING, {"type": "null"}]}
TONE_OR_NULL = {"anyOf": [{"enum": TONES}, {"type": "null"}]}
REF = {"type": "object", "properties": {"utt_id": STR, "event_id": STR}, "additionalProperties": False}

ENVELOPE = {
    "type": STR,
    "v": {"const": VERSION},
    "src": {"enum": SRCS},
    "t": {"type": "number", "description": "observation time, laptop clock, unix seconds"},
    "seq": {"type": "integer"},
    "session": {"type": "string", "description": "set by the relay"},
}
ENVELOPE_REQUIRED = ["type", "v", "src", "t"]


def obj(props, required=None):
    return {"type": "object", "properties": props, "required": list(props) if required is None else required,
            "additionalProperties": False}


# ---- messages: name -> (properties, required, description) ------------------------------
# `required=None` means every listed property is required.

MESSAGES = {
    # §12.4 session
    "hello": ({"role": {"enum": ROLES}, "subscribe": {"type": "array", "items": STR}, "app_version": STR},
              ["role", "subscribe"], "First message from every client."),
    "heartbeat": ({"status": {"enum": ["ok", "degraded", "error"]}, "detail": STR,
                   "thermal": {"enum": ["nominal", "fair", "serious", "critical"]},
                   "engine": {"enum": ENGINES}, "doa_source": {"enum": ["builtin", "odas"]}},
                  ["status"], "Every 2 s. thermal: phone only; engine, doa_source: laptop only."),
    "clock_ping": ({"t_phone": NUM}, None, "Phone -> laptop, for latency measurement."),
    "clock_pong": ({"t_phone": NUM, "t_laptop": NUM}, None, "Laptop -> phone."),
    "config": ({"user_name": STR,
                "name_aliases": {"type": "array", "items": STR},
                "focus_mode": BOOL,
                "belt_only_mode": BOOL,
                "features": obj({"captions": BOOL, "tone": BOOL, "rings": BOOL, "alerts": BOOL, "belt": BOOL}),
                "alerts_enabled": {"type": "object", "propertyNames": {"enum": ALERT_KINDS},
                                   "additionalProperties": BOOL},
                "belt_strength": {"type": "integer", "minimum": 0, "maximum": 255}},
               None, "Phone -> relay -> all; the relay re-sends the latest to new clients."),
    # §12.5 directions
    "sources": ({"sources": {"type": "array", "items": obj({
        "id": INT0, "bearing_deg": BEARING, "energy": UNIT, "speech": BOOL, "confidence": UNIT})}},
        None, "10-30 Hz while anything is active; on change otherwise. Built-in DoA: one source, id 0."),
    # §12.6 speech
    "speech_start": ({"utt_id": STR, "bearing_deg": BEARING, "source_id": INT0, "confidence": UNIT},
                     None, "Fast path: the moment speech starts at a direction."),
    "utterance": ({"utt_id": STR, "t_start": NUM, "t_end": NUM, "text": STR, "pending": STR, "final": BOOL,
                   "bearing_deg": BEARING, "bearing_spread_deg": {"type": "number", "minimum": 0},
                   "source_id": INT0, "overlap": BOOL, "tone": TONE_OR_NULL, "tone_confidence": UNIT,
                   "loudness": UNIT, "engine": {"enum": ENGINES},
                   "stage_ms": {"type": "object", "additionalProperties": NUM}},
                  ["utt_id", "t_start", "t_end", "text", "pending", "final", "bearing_deg", "bearing_spread_deg",
                   "source_id", "overlap", "tone", "tone_confidence", "loudness", "engine"],
                  "Sent repeatedly while the utterance grows (final=false), then once with final=true."),
    # §12.7 sounds and alerts
    "sound_event": ({"event_id": STR, "label": {"enum": SOUND_LABELS}, "category": {"enum": SOUND_CATEGORIES},
                     "emoji": STR, "text": STR, "confidence": UNIT, "bearing_deg": BEARING_OR_NULL,
                     "t_start": NUM, "t_end": NUM, "count": {"type": "integer", "minimum": 1},
                     "source_id": INT0, "model": STR},
                    ["event_id", "label", "category", "emoji", "text", "confidence", "bearing_deg", "t_start",
                     "t_end", "count"],
                    "A classified sound; repeats merged into count. Optional: source_id (the sources track it came "
                    "from), model (which classifier labelled it, e.g. panns_cnn14)."),
    "alert": ({"alert_id": STR, "kind": {"enum": ALERT_KINDS}, "label": STR, "emoji": STR,
               "priority": {"enum": [1, 2, 3]}, "bearing_deg": BEARING_OR_NULL, "active": BOOL,
               "confidence": UNIT, "ref": REF},
              ["alert_id", "kind", "label", "emoji", "priority", "bearing_deg", "active", "confidence"],
              "Top-right panel + belt. active=false when it ends."),
    "perception_enrichment": ({"ref": REF, "attention": {"enum": ATTENTION}, "attention_confidence": UNIT,
                               "attention_model": STR, "directed_at_user": UNIT, "context": STR, "context_model": STR},
                              ["ref", "attention", "attention_confidence", "attention_model"],
                              "Optional, arrives after the event it refers to (ref). attention: how prominent to make it "
                              "(a Hugging Face zero-shot model, or deterministic rules when attention_model is 'rules'); directed_at_user: "
                              "probability; context: one-line semantic note (Gemini). Never decides whether an "
                              "event exists or where it is."),
    # §12.8 phone -> relay (extras)
    "attribution": ({"ref": REF, "person_label": {"anyOf": [STR, {"type": "null"}]},
                     "method": {"enum": ["bearing", "off_camera"]}, "score": NUM},
                    None, "Which person an utterance/event was put on (extras only)."),
    "scene": ({"heading_deg": BEARING, "people": {"type": "array", "items": obj({
        "label": STR, "bearing_deg": BEARING, "speaking": BOOL})}},
        None, "~2-5 Hz, extras only."),
    # §12.9 extras
    "search_request": ({"query_id": STR, "text": STR, "limit": {"type": "integer", "minimum": 1}}, None, ""),
    "search_result": ({"query_id": STR, "results": {"type": "array", "items": obj({
        "t": NUM, "kind": {"enum": ["utterance", "alert"]}, "person_label": STR, "text": STR,
        "tone": TONE_OR_NULL, "bearing_deg": BEARING, "score": NUM},
        ["t", "kind", "text", "score"])}}, None, ""),
}

# §12.10 default subscriptions per role (the relay uses these when hello.subscribe is empty)
DEFAULT_SUBSCRIBE = {
    "phone": ["sources", "speech_start", "utterance", "sound_event", "alert", "perception_enrichment", "config",
              "clock_pong", "heartbeat"],
    "laptop": ["config", "clock_ping"],
    "dashboard": ["*"],
    "memory": ["utterance", "alert", "sound_event", "perception_enrichment", "attribution", "search_request"],
    "tool": ["*"],
}


def schema_for(name):
    props, required, desc = MESSAGES[name]
    req = list(props) if required is None else required
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://here.local/contracts/{name}.schema.json",
        "title": name,
        "description": desc,
        "type": "object",
        "properties": {**ENVELOPE, "type": {"const": name}, **props},
        "required": ENVELOPE_REQUIRED + req,
        "additionalProperties": False,
    }


def envelope_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://here.local/contracts/envelope.schema.json",
        "title": "envelope",
        "description": "Common fields of every message (README §12.2). Receivers ignore unknown types.",
        "type": "object",
        "properties": {**ENVELOPE, "type": {"enum": list(MESSAGES)}},
        "required": ENVELOPE_REQUIRED,
    }


def all_schemas():
    out = {"envelope": envelope_schema()}
    out.update({name: schema_for(name) for name in MESSAGES})
    return out


def render(schema):
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def main():
    OUT.mkdir(exist_ok=True)
    for name, schema in all_schemas().items():
        (OUT / f"{name}.schema.json").write_text(render(schema), encoding="utf-8")
    (OUT / "routing.json").write_text(render({"version": VERSION, "default_subscribe": DEFAULT_SUBSCRIBE}),
                                      encoding="utf-8")
    print(f"wrote {len(MESSAGES) + 1} schemas + routing.json to {OUT}")


if __name__ == "__main__":
    main()
