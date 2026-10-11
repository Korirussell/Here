"""tools/deploy_vultr.py: the remote plan, dry run, sslip names and laptop/.env editing. Nothing connects anywhere."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import deploy_vultr as dv  # noqa: E402

NEON = "postgresql://user:hunter2@ep-x.neon.tech/here?sslmode=require"


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError(f"tried to run {a[0] if a else k}")
    monkeypatch.setattr(subprocess, "run", refuse)


def test_sslip_name_from_ip():
    assert dv.sslip_host("203.0.113.7") == "203-0-113-7.sslip.io"
    with pytest.raises(ValueError):
        dv.sslip_host("example.com")


def test_deploy_plan_order_and_memory_optional():
    steps = [s.desc.split(" ")[0] for s in dv.plan("deploy", "h.sslip.io", memory=False)]
    assert steps == ["packages,", "ship", "Python", "relay", "relay", "Caddy"]
    with_memory = dv.plan("deploy", "h.sslip.io", memory=True, neon_url=NEON)
    assert with_memory[-1].desc.startswith("memory")
    assert "extras/memory/requirements.txt" in with_memory[2].script


def test_token_is_generated_only_when_missing_and_never_echoed():
    s = dv.step_token().script
    assert "if ! grep -qE '^HERE_RELAY_TOKEN=.+'" in s
    assert "echo $" not in s and "cat " not in s


def test_neon_url_travels_on_stdin_not_in_the_script():
    s = dv.step_memory(NEON)
    assert NEON not in s.script and "hunter2" not in s.stdin_label
    assert s.stdin == (NEON + "\n").encode()


def test_caddy_step_sets_host_idempotently():
    s = dv.step_caddy("203-0-113-7.sslip.io").script
    assert "sed -i '/^HERE_HOST=/d'" in s and "HERE_HOST=203-0-113-7.sslip.io" in s


def test_update_and_status_plans():
    assert [s.desc.split(" ")[0] for s in dv.plan("update", "h", memory=True)] == ["ship", "Python", "restart"]
    assert "here-memory" in dv.plan("update", "h", memory=True)[-1].script
    assert len(dv.plan("status", "h", memory=False)) == 1


def test_dry_run_touches_nothing_and_redacts(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(f"NEON_DATABASE_URL={NEON}\n", encoding="utf-8")
    assert dv.main(["--ip", "203.0.113.7", "--memory", "--save-token", "--dry-run", "--memory-env", str(env)]) == 0
    out = capsys.readouterr().out
    assert "hunter2" not in out and "set, redacted" in out
    assert "[7/7]" in out and "203-0-113-7.sslip.io" in out


def test_remote_command_quotes_the_script():
    cmd = dv.remote_command("echo 'a b'")
    assert cmd.startswith("bash -c ") and "'\"'\"'" in cmd


def test_read_env_value_strips_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1   # note\nB=          # only a comment\nC=x#y\n", encoding="utf-8")
    assert dv.read_env_value(env, "A") == "1"
    assert dv.read_env_value(env, "B") == ""
    assert dv.read_env_value(env, "C") == "x#y"
    assert dv.read_env_value(tmp_path / "missing", "A") == ""


def test_update_laptop_env_sets_token_and_adds_mirror_once(tmp_path):
    env = tmp_path / ".env"
    env.write_text("HERE_RELAY_URL=ws://localhost:8080/ws\nHERE_RELAY_TOKEN=change-me\n"
                   "HERE_RELAY_MIRRORS=wss://old/ws\nELEVENLABS_API_KEY=k\n", encoding="utf-8")
    for _ in range(2):
        dv.update_laptop_env(env, "tok", "wss://h.sslip.io/ws")
    text = env.read_text(encoding="utf-8")
    assert "HERE_RELAY_TOKEN=tok\n" in text
    assert "HERE_RELAY_MIRRORS=wss://old/ws,wss://h.sslip.io/ws\n" in text
    assert "ELEVENLABS_API_KEY=k" in text and "HERE_RELAY_URL=ws://localhost:8080/ws" in text


def test_update_laptop_env_creates_from_example(tmp_path):
    (tmp_path / ".env.example").write_text("HERE_RELAY_TOKEN=change-me\nHERE_SESSION=demo\n", encoding="utf-8")
    env = tmp_path / ".env"
    dv.update_laptop_env(env, "tok", "wss://h/ws")
    text = env.read_text(encoding="utf-8")
    assert "HERE_RELAY_TOKEN=tok" in text and "HERE_SESSION=demo" in text and "HERE_RELAY_MIRRORS=wss://h/ws" in text


def test_update_without_memory_flag_still_updates_an_installed_memory_service():
    # ADIL_TODO documents `deploy_vultr.py --ip X update` (no --memory): a memory service deployed earlier must get
    # its requirements and a restart too, or it keeps serving the old code.
    venv, restart = dv.plan("update", "h", memory=False)[1:]
    assert "here-memory" in venv.script and "extras/memory/requirements.txt" in venv.script
    assert "here-memory" in restart.script
    assert "here-relay" in restart.script


def test_redeploy_without_a_local_neon_url_keeps_the_servers(tmp_path):
    # Run the memory step's shell logic locally (bash, REMOTE -> tmp) up to writing the .env.
    import shutil
    def works(b):                                # on Windows PATH's bash may be WSL's launcher with no distro
        p = subprocess.Popen([b, "-c", "sed --version >/dev/null && echo ok"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        return p.communicate(timeout=20)[0].strip() == b"ok"
    candidates = [r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")]
    bash = next((b for b in candidates if b and Path(b).exists() and works(b)), None)
    if not bash:
        pytest.skip("no working bash")
    remote = tmp_path.as_posix()
    (tmp_path / "relay").mkdir()
    (tmp_path / "extras" / "memory").mkdir(parents=True)
    (tmp_path / "relay" / ".env").write_text("HERE_RELAY_TOKEN=tok\n")
    (tmp_path / "extras" / "memory" / ".env").write_text(f"NEON_DATABASE_URL={NEON}\n")
    script = dv.step_memory("").script.replace(dv.REMOTE, remote)
    script = script.split("chown here:here")[0]              # stop before chown / systemctl
    proc = subprocess.Popen([bash, "-c", script], stdin=subprocess.PIPE)   # local bash only (run() is refused above)
    proc.communicate(b"\n", timeout=20)
    assert proc.returncode == 0
    env = (tmp_path / "extras" / "memory" / ".env").read_text()
    assert f"NEON_DATABASE_URL={NEON}\n" in env and "HERE_RELAY_TOKEN=tok\n" in env
