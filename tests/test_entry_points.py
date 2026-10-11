"""Every entry point the demo starts actually starts: `--help` runs the module's imports and its argument parser
in a fresh interpreter. test_compiles catches syntax errors; this catches the rest of "python -m laptop is broken"
(an import that fails, a bad module-level line). The list comes from tools/demo.py, so a new service is covered."""

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import demo  # noqa: E402

TOOLS = ["tools/demo.py", "tools/check_gpu.py", "tools/drills.py", "tools/latency_probe.py", "tools/relay_tap.py",
         "tools/fake_events.py"]


def demo_entry_points():
    seen = {}
    for source in ("fake", "replay", "real"):
        args = Namespace(source=source, fixture="mock_scene", scene=None, cloud=False, session=None, full=False,
                         relay_port=demo.LOCAL_RELAY_PORT, dashboard_port=demo.DASHBOARD_PORT,
                         memory_port=demo.MEMORY_PORT, no_mirror=True, mirror=False)
        for argv in demo.commands(args, "ws://localhost:8080/ws", "demo").values():
            head = argv[1:3] if argv[1] == "-m" else argv[1:2]
            seen[" ".join(head)] = head
    return list(seen.values())


ENTRY_POINTS = demo_entry_points() + [[t] for t in TOOLS if (ROOT / t).exists()]


def test_demo_starts_the_core_services():
    names = {" ".join(e) for e in ENTRY_POINTS}
    assert {"-m relay", "-m laptop", "tools/fake_events.py"} <= names


@pytest.mark.parametrize("entry", ENTRY_POINTS, ids=lambda e: " ".join(e))
def test_entry_point_starts(entry):
    r = subprocess.run([sys.executable, *entry, "--help"], cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    assert r.returncode == 0, f"{' '.join(entry)} --help exited {r.returncode}:\n{r.stderr[-2000:]}"
    assert "usage" in r.stdout.lower(), r.stdout[:500]
