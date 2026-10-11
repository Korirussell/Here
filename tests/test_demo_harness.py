"""tools/demo.py builds the right stack for each source, without starting anything."""

import sys
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import demo  # noqa: E402

URL, SESSION = "ws://localhost:8080/ws", "demo"


def cmds(**kw):
    args = Namespace(source="fake", fixture="mock_scene", scene=None, cloud=False, session=None, full=False,
                     relay_port=demo.LOCAL_RELAY_PORT, dashboard_port=demo.DASHBOARD_PORT,
                     memory_port=demo.MEMORY_PORT)
    for k, v in kw.items():
        setattr(args, k, v)
    return demo.commands(args, URL, SESSION)


def test_local_stack_starts_a_relay_and_points_the_source_at_it():
    c = cmds()
    assert c["relay"][1:] == ["-m", "relay", "--host", "0.0.0.0", "--port", str(demo.LOCAL_RELAY_PORT)]
    assert c["source"][1:] == ["tools/fake_events.py", "--loop", "--relay", URL, "--session", SESSION]


def test_scene_plays_a_fixture_scene_on_a_loop():
    assert cmds(scene="rear_speech")["source"][1:4] == ["tools/fake_events.py", "--scene", "rear_speech"]


def test_sources():
    assert "--replay" in cmds(source="replay")["source"] and "fixtures/mock_scene" in cmds(source="replay")["source"]
    assert "--real" in cmds(source="real")["source"]
    assert "source" not in cmds(source="none")


def test_cloud_mode_has_no_local_relay():
    assert "relay" not in cmds(cloud=True)


def test_extras_start_only_when_their_module_exists():
    for name in ("dashboard", "memory"):
        assert (name in cmds()) == (ROOT / "extras" / name / "__main__.py").exists()


def test_health_url():
    assert demo.health_url("ws://localhost:8080/ws") == "http://127.0.0.1:8080/health"
    assert demo.health_url("wss://relay.example.com/ws") == "https://relay.example.com/health"


def test_up_refuses_a_scene_that_is_not_there(tmp_path):
    assert not demo.scene_exists("all", tmp_path) and not demo.scene_exists("rear_speech", tmp_path)
    (tmp_path / "fixtures" / "events").mkdir(parents=True)
    (tmp_path / "fixtures" / "events" / "rear_speech.jsonl").write_text("")
    assert demo.scene_exists("all", tmp_path) and demo.scene_exists("rear_speech", tmp_path)
    assert not demo.scene_exists("doorbell", tmp_path)


def test_a_cloud_relay_in_env_is_mirrored_never_primary(monkeypatch):
    cloud = "wss://relay.example/ws"
    monkeypatch.setenv("HERE_RELAY_URL", cloud)
    base = dict(cloud=False, no_mirror=False)
    assert demo.cloud_mirror(Namespace(**base)) == cloud
    assert demo.cloud_mirror(Namespace(**{**base, "no_mirror": True})) is None
    assert demo.cloud_mirror(Namespace(**{**base, "cloud": True})) is None      # it's the primary then
    args = Namespace(source="real", fixture="mock_scene", scene=None, cloud=False, relay_port=demo.LOCAL_RELAY_PORT)
    c = demo.commands(args, URL, SESSION, cloud)
    assert c["source"][-4:] == ["--session", SESSION, "--mirror", cloud] and "relay" in c
    monkeypatch.setenv("HERE_RELAY_URL", "ws://192.168.1.5:8080/ws")             # a LAN relay isn't a cloud mirror
    assert demo.cloud_mirror(Namespace(**base)) is None


def test_no_mirror_also_silences_mirrors_from_laptop_env(monkeypatch):
    # deploy_vultr.py --save-token writes HERE_RELAY_MIRRORS into laptop/.env; children load that file themselves,
    # so `up --no-mirror` has to override it in their environment or they keep mirroring to the cloud.
    monkeypatch.setenv("HERE_RELAY_MIRRORS", "wss://relay.example/ws")
    ports = {"relay": 1, "dashboard": 2, "memory": 3}
    assert demo.child_env(URL, SESSION, "", ports, no_mirror=True)["HERE_RELAY_MIRRORS"] == ""
    assert demo.child_env(URL, SESSION, "", ports)["HERE_RELAY_MIRRORS"] == "wss://relay.example/ws"
    from laptop import settings
    monkeypatch.setenv("HERE_RELAY_MIRRORS", "")
    assert settings.load()["relay"]["mirrors"] == []
