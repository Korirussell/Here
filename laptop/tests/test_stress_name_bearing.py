"""tools/stress_test.py score_two: name_called must point at the caller (golden two-talker condition)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from stress_test import NAME_BEARING_MAX_DEG, score_two  # noqa: E402


def alert(bearing):
    return (0.0, SimpleNamespace(TYPE="alert", active=True, kind="name_called", bearing_deg=bearing, alert_id="a1"))


def test_name_bearing_error_against_the_callers_line():
    meta = {"lines": [{"who": "Person 1", "bearing_deg": 35.0, "text": "Did you see the email?", "t_start": 1, "t_end": 3},
                      {"who": "Person 2", "bearing_deg": -35.0, "text": "Hey Kori, can you grab me a coffee?",
                       "t_start": 7, "t_end": 9}]}
    assert score_two([alert(-34.0)], 0.0, meta)["name_bearing_err"] == 1.0          # the caller: right
    assert score_two([alert(35.0)], 0.0, meta)["name_bearing_err"] > NAME_BEARING_MAX_DEG   # the other talker: fails
    assert score_two([alert(None)], 0.0, meta)["name_bearing_err"] == 180.0          # no bearing: fails
    assert score_two([], 0.0, meta)["name_bearing_err"] is None
