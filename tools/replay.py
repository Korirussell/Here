"""Replay a recorded session (audio.wav + sources.jsonl) through the laptop pipeline into the relay.

    python tools/replay.py fixtures/hall_two_people            # same as: python -m laptop --replay <fixture>
    python tools/replay.py hall_two_people --loop --speed 1.5
    python tools/replay.py mock_scene --offline                  # print messages, no relay

Record one with: python -m laptop --real --record <name>  (see fixtures/README.md).
Everything after the fixture name is passed to `python -m laptop`.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    sys.exit(subprocess.call([sys.executable, "-m", "laptop", "--replay", sys.argv[1], *sys.argv[2:]], cwd=ROOT))
