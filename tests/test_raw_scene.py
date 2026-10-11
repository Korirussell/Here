"""tools/make_raw_scene.py: rotating the square array by permuting mics, on simulated plane waves."""

import sys
from pathlib import Path

import numpy as np
import pytest

from laptop.audio.localize import XVF3800_GEOMETRY, SrpPhat, in_geometry_order
from laptop.geometry import angdiff
from laptop.tests.test_raw import FS, plane_wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import make_raw_scene  # noqa: E402


@pytest.mark.parametrize("steps", [0, 1, 2, 3])
def test_rotation_by_permutation_turns_the_source_90_deg(steps):
    src = np.random.default_rng(4).standard_normal(FS)
    geo = plane_wave(src, 170.0)
    az, conf = SrpPhat(XVF3800_GEOMETRY).locate(make_raw_scene.rotate(geo, steps))
    assert abs(angdiff(az, 170.0 + 90 * steps)) <= 3 and conf > 0.5


def test_to_usb_inverts_geometry_order():
    order = [2, 0, 3, 1]
    raw = np.arange(12).reshape(3, 4)
    assert np.array_equal(make_raw_scene.to_usb(in_geometry_order(raw, order), order), raw)
