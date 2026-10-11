"""Contract checks across Python, TypeScript and Swift (SPEC §12). Run: python -m pytest contracts"""

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from contracts import build_schemas
from contracts.python import here_contract as hc

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))
TYPES = list(build_schemas.MESSAGES)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_type_has_an_example():
    assert sorted(p.stem for p in EXAMPLES) == sorted(TYPES)


def test_schemas_match_generator():
    for name, schema in build_schemas.all_schemas().items():
        on_disk = (ROOT / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8")
        assert on_disk == build_schemas.render(schema), f"{name}: run python contracts/build_schemas.py"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.stem)
def test_example_valid_and_round_trips(path):
    d = load(path)
    assert hc.errors(d) == []
    msg = hc.parse(d)
    assert type(msg) is hc.MESSAGE_TYPES[d["type"]]
    assert msg.to_dict() == d
    assert hc.parse(msg.to_json()).to_dict() == d


@pytest.mark.parametrize("mutate, why", [
    (lambda d: d.update(v=2), "old version"),
    (lambda d: d["sources"][0].update(bearing_deg=-180), "-180 is outside (-180, 180]"),
    (lambda d: d["sources"][0].update(bearing_deg=181), "> 180"),
    (lambda d: d["sources"][0].update(energy=1.5), "energy > 1"),
    (lambda d: d.pop("t"), "missing t"),
    (lambda d: d.update(src="audio_here"), "v1 service name"),
    (lambda d: d.update(bogus=1), "unknown field"),
])
def test_invalid_sources_rejected(mutate, why):
    d = copy.deepcopy(load(ROOT / "examples" / "sources.json"))
    mutate(d)
    assert hc.errors(d), why


def test_nullable_fields_are_sent_as_null():
    a = hc.Alert(src="laptop", t=1.0, alert_id="a", kind="doorbell", label="Doorbell", emoji="🔔", priority=2,
                 bearing_deg=None, active=True, confidence=0.7)
    d = a.to_dict()
    assert d["bearing_deg"] is None and "ref" not in d and "seq" not in d
    assert hc.errors(d) == []
    u = load(ROOT / "examples" / "utterance.json")
    u["tone"] = None
    assert hc.errors(u) == [] and hc.parse(u).to_dict()["tone"] is None


def test_unknown_type_is_kept_not_raised():
    m = hc.parse({"type": "future_thing", "v": 3, "src": "laptop", "t": 1.0, "x": 1})
    assert isinstance(m, hc.Unknown) and m.to_dict()["x"] == 1


def test_wrap():
    assert hc.wrap(-180) == 180 and hc.wrap(190) == -170 and hc.wrap(540) == 180


def test_routing_table_matches_python():
    routing = load(ROOT / "schemas" / "routing.json")
    assert routing["default_subscribe"] == hc.DEFAULT_SUBSCRIBE == build_schemas.DEFAULT_SUBSCRIBE


def test_python_has_every_type():
    assert sorted(hc.MESSAGE_TYPES) == sorted(TYPES)


def test_swift_decodes_every_type():
    swift = (ROOT / "swift" / "HereContract.swift").read_text(encoding="utf-8")
    for name in TYPES:
        assert f'case "{name}":' in swift, f"HereContract.swift: no decode case for {name}"
    # every snake_case field in the schemas appears as a CodingKey raw value or a plain property
    for name in TYPES:
        props = build_schemas.schema_for(name)["properties"]
        for key in props:
            if "_" in key:
                assert f'"{key}"' in swift, f"HereContract.swift: missing CodingKey for {name}.{key}"


def test_typescript_accepts_every_example(tmp_path):
    ts = (ROOT / "ts" / "contract.ts").read_text(encoding="utf-8")
    for name in TYPES:
        assert f"type: '{name}'" in ts, f"contract.ts: no interface for {name}"
    tsc = shutil.which("tsc")
    if tsc is None:
        pytest.skip("tsc not installed")
    names = {name: "".join(p.title() for p in name.split("_")) for name in TYPES}
    names["config"] = "Config"
    shutil.copy(ROOT / "ts" / "contract.ts", tmp_path / "contract.ts")
    lines = [f"import type {{ {', '.join(sorted(set(names.values())))} }} from './contract'", ""]
    for p in EXAMPLES:
        lines.append(f"export const ex_{p.stem}: {names[p.stem]} = {json.dumps(load(p), ensure_ascii=False)}")
    (tmp_path / "examples.ts").write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = subprocess.run([tsc, "--noEmit", "--strict", "--target", "es2020", "--moduleResolution", "node",
                        str(tmp_path / "examples.ts")], capture_output=True, text=True, shell=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_examples_cover_spec_12_11():
    readme = (ROOT.parent / "docs" / "SPEC.md").read_text(encoding="utf-8")
    block = readme.split("### 12.11 Example", 1)[1].split("```json", 1)[1].split("```", 1)[0]
    for line in filter(None, (l.strip() for l in block.splitlines())):
        d = json.loads(line)
        assert hc.errors(d) == [], d["type"]
        assert load(ROOT / "examples" / f"{d['type']}.json") == d


def test_no_snake_case_typos_in_swift_coding_keys():
    swift = (ROOT / "swift" / "HereContract.swift").read_text(encoding="utf-8")
    all_keys = {k for name in TYPES for k in build_schemas.schema_for(name)["properties"]}
    nested = {"utt_id", "event_id", "bearing_deg", "person_label"}
    for raw in re.findall(r'= "([a-z]+_[a-z_]+)"', swift):
        assert raw in all_keys | nested | set(build_schemas.SOUND_LABELS) | set(build_schemas.ALERT_KINDS) \
            | {"faster_whisper", "scribe_realtime", "off_camera"} | set(TYPES), f"unknown key {raw}"
