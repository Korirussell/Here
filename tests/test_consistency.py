"""Cross-language consistency the compilers can't check for us (SPEC §12, §13, §14, §16).

Swift and the ESP32 sketch aren't built on the laptop, so this reads their sources and checks they
agree with the contract and with each other.
"""

import json
import re
from pathlib import Path

from contracts.python import here_contract as hc

ROOT = Path(__file__).resolve().parents[1]
SWIFT_BELT = (ROOT / "ios/HERE/Belt/BeltProtocol.swift").read_text(encoding="utf-8")
SWIFT_TUNABLES = (ROOT / "ios/HERE/App/Tunables.swift").read_text(encoding="utf-8")
SWIFT_CONTRACT = (ROOT / "contracts/swift/HereContract.swift").read_text(encoding="utf-8")
TS_CONTRACT = (ROOT / "contracts/ts/contract.ts").read_text(encoding="utf-8")
FW_CONFIG = (ROOT / "belt/firmware/here_belt/belt_config.h").read_text(encoding="utf-8")
FW_SKETCH = (ROOT / "belt/firmware/here_belt/here_belt.ino").read_text(encoding="utf-8")
FW_TYPES = (ROOT / "belt/firmware/here_belt/belt_types.h").read_text(encoding="utf-8")


FW6_SKETCH = (ROOT / "belt/firmware/here_belt_6motor/here_belt_6motor.ino").read_text(encoding="utf-8")
CONTROLLER = (ROOT / "belt/controller/index.html").read_text(encoding="utf-8")


def test_belt_uuids_match_between_firmware_and_app():
    """The app talks to the belt the team built (here_belt_6motor); the 8-motor here_belt's UUIDs stay as legacy."""
    fw = {k: v.lower() for k, v in re.findall(r'#define (SERVICE_UUID|CMD_UUID)\s+"([0-9a-fA-F-]+)"', FW6_SKETCH)}
    sw = {k: v.lower() for k, v in re.findall(r'static let (service|command) = "([0-9A-Fa-f-]+)"', SWIFT_BELT)}
    assert fw["SERVICE_UUID"] == sw["service"]
    assert fw["CMD_UUID"] == sw["command"]
    old = {k: v.lower() for k, v in re.findall(r'#define (BELT_\w+_UUID)\s+"([0-9a-fA-F-]+)"', FW_CONFIG)}
    legacy = {k: v.lower() for k, v in re.findall(r'static let (legacyService|legacyCommand) = "([0-9A-Fa-f-]+)"', SWIFT_BELT)}
    assert old["BELT_SERVICE_UUID"] == legacy["legacyService"]
    assert old["BELT_COMMAND_UUID"] == legacy["legacyCommand"]


def test_six_motor_layout_matches_between_app_and_controller():
    """Where each logical motor sits on the wearer: BeltSixMotor.degrees == the web controller's MOTORS table."""
    sw = [float(x) for x in re.search(r"static let degrees: \[Double\] = \[([^\]]+)\]", SWIFT_BELT).group(1).split(",")]
    js = [float(x) for x in re.findall(r"\{ name: '[^']+', deg: (\d+) \}", CONTROLLER)]
    assert sw == js and len(sw) == 6
    assert re.search(r"MOTOR_COUNT = 6", FW6_SKETCH)


def test_belt_pattern_ids_match():
    fw = dict((name, int(v)) for name, v in re.findall(r"(\w+) = (\d+)", FW_TYPES.split("enum PatternId", 1)[1].split("};", 1)[0]))
    sw = dict((name, int(v)) for name, v in re.findall(r"(\w+) = (\d+)", SWIFT_BELT.split("enum BeltPattern", 1)[1].split("\n", 1)[1].split("\n")[0]))
    camel = {"STOP": "stop", "SPEECH": "speech", "NAME_CALLED": "nameCalled", "ALERT": "alert",
             "EMERGENCY": "emergency", "TICK": "tick", "TEST": "test"}
    assert {camel[k]: v for k, v in fw.items()} == sw
    # SPEC §16 table
    assert sw == {"stop": 0, "speech": 1, "nameCalled": 2, "alert": 3, "emergency": 4, "tick": 5, "test": 6}


def test_packet_is_six_bytes_version_1_in_both():
    assert "#define PACKET_VERSION       0x01" in FW_CONFIG
    assert "Data([0x01, pattern.rawValue" in SWIFT_BELT


def test_routing_defaults_agree_everywhere():
    sw = json.loads("{" + re.search(r"defaultSubscribe: \[String: \[String\]\] = \[(.*?)\n    \]", SWIFT_CONTRACT, re.S)
                    .group(1).replace("[", "[").replace("]", "]").strip().rstrip(",") + "}")
    assert sw == hc.DEFAULT_SUBSCRIBE
    for role, types in hc.DEFAULT_SUBSCRIBE.items():
        assert f"{role}: {json.dumps(types).replace(chr(34), chr(39)).replace(', ', ', ')}" in TS_CONTRACT.replace('", "', "', '")


def test_app_thresholds_match_readme():
    def val(name):
        return float(re.search(rf"static let {name} = ([\d.]+)", SWIFT_TUNABLES).group(1))
    assert val("matchDeg") == 15                 # §13 face within 15 deg
    assert val("moveDeg") == 30 and val("moveHoldS") == 1.0      # §13 sticky
    assert val("relabelWindowS") == 5            # §13 lost face back within 5 s keeps its label
    assert val("beltFrontDeg") == 30             # §14 belt: |rel| > 30
    assert val("beltSamePersonS") == 5           # §14 not again for the same person within 5 s
    assert val("beltNonAlertGapS") == 1.5        # §14 max one non-alert pattern per 1.5 s
    assert val("heartbeatS") == 2                # §12.4 heartbeat every 2 s


def test_relay_and_laptop_share_role_defaults():
    from relay.relay import DEFAULT_SUBSCRIBE as relay_defaults
    assert relay_defaults == hc.DEFAULT_SUBSCRIBE


def test_nothing_outside_reference_mentions_ranger_code():
    hits = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        if (not p.is_file() or rel.startswith((".git/", ".venv/"))
                or p.suffix not in {".py", ".swift", ".ts", ".tsx", ".ino", ".h", ".js"}):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"Treehacks2025|YuanSamuel|from\s+reference|import\s+reference", text) and p.name != Path(__file__).name:
            hits.append(rel)
    assert hits == [], hits
