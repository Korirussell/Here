"""Bearings, wrapping and camera geometry (HERE_PLAN.md §6).

All bearings are degrees, clockwise positive, normalized to (-180, 180].
"""

import math


def wrap(a: float) -> float:
    """Normalize an angle to (-180, 180]."""
    a = (a + 180.0) % 360.0 - 180.0
    return 180.0 if a == -180.0 else a


def angdiff(a: float, b: float) -> float:
    """Signed smallest difference a - b, in (-180, 180]."""
    return wrap(a - b)


def circular_mean(angles, weights=None):
    """Mean of angles via unit vectors. Returns (mean_deg, circular_std_deg), or (None, None) if empty."""
    if weights is None:
        weights = [1.0] * len(angles)
    sx = sy = sw = 0.0
    for a, w in zip(angles, weights):
        r = math.radians(a)
        sx += w * math.cos(r)
        sy += w * math.sin(r)
        sw += w
    if sw <= 0:
        return None, None
    R = min(math.hypot(sx, sy) / sw, 1.0)   # rounding can push it a hair above 1
    mean = wrap(math.degrees(math.atan2(sy, sx)))
    std = math.degrees(math.sqrt(-2.0 * math.log(max(R, 1e-9))))
    return mean, std


def focal_px(width: float, hfov_deg: float) -> float:
    return (width / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)


def bearing_from_pixel(x: float, width: float, hfov_deg: float, heading_deg: float) -> float:
    """World bearing of horizontal pixel x in an upright frame whose optical axis points at heading."""
    return wrap(heading_deg + math.degrees(math.atan((x - width / 2.0) / focal_px(width, hfov_deg))))


def pixel_from_bearing(bearing_deg: float, width: float, hfov_deg: float, heading_deg: float):
    """Horizontal pixel for a world bearing, or None if it's outside the camera's view."""
    rel = angdiff(bearing_deg, heading_deg)
    if abs(rel) >= hfov_deg / 2.0:
        return None
    return width / 2.0 + focal_px(width, hfov_deg) * math.tan(math.radians(rel))


def motor_for(rel_bearing_deg: float) -> int:
    """Belt motor 1..8, clockwise from the front (§6)."""
    return int(round((rel_bearing_deg % 360.0) / 45.0)) % 8 + 1
