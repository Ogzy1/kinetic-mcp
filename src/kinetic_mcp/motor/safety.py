"""Motor safety guardrails: corner fail-safe, user-override abort, focus lock, and bounds clamping."""

from __future__ import annotations

import math
from dataclasses import dataclass

from kinetic_mcp.models import PixelBox


class MotorSafetyError(RuntimeError):
    """Raised when a motor stroke is aborted by a safety guardrail."""


@dataclass(slots=True)
class SafetyPolicy:
    """Runtime safety constraints applied to every continuous motor stroke."""

    bounds: PixelBox | None = None
    failsafe_corner_px: int = 3
    user_override_px: int = 85
    require_window_title: str | None = None
    max_stroke_seconds: float = 15.0


def clamp_points(points: list[tuple[int, int]], bounds: PixelBox | None) -> list[tuple[int, int]]:
    """Clamp all trajectory points inside bounds when bounds are specified."""
    if bounds is None or bounds.width <= 0 or bounds.height <= 0:
        return list(points)
    return [bounds.clamp_point(x, y) for x, y in points]


def _point_to_segment_distance(
    p: tuple[int, int],
    a: tuple[int, int],
    b: tuple[int, int],
) -> float:
    """Euclidean distance from point p to line segment a->b."""
    abx = b[0] - a[0]
    aby = b[1] - a[1]
    ab_len_sq = abx * abx + aby * aby
    if ab_len_sq == 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / ab_len_sq
    t = max(0.0, min(1.0, t))
    proj_x = a[0] + t * abx
    proj_y = a[1] + t * aby
    return math.hypot(p[0] - proj_x, p[1] - proj_y)


def check_step_safety(
    expected_pos: tuple[int, int] | None,
    actual_pos: tuple[int, int],
    screen_box: PixelBox,
    policy: SafetyPolicy,
    foreground_title: str | None = None,
    prev_expected_pos: tuple[int, int] | None = None,
) -> None:
    """Verify corner fail-safe, physical user mouse override, and target window focus."""
    margin = max(0, policy.failsafe_corner_px)
    if margin > 0:
        in_primary_corner = 0 <= actual_pos[0] <= margin and 0 <= actual_pos[1] <= margin
        in_virtual_corner = (
            actual_pos[0] <= screen_box.left + margin and actual_pos[1] <= screen_box.top + margin
        )
        if in_primary_corner or in_virtual_corner:
            raise MotorSafetyError(
                f"Corner fail-safe triggered at cursor={actual_pos}; stroke aborted safely."
            )

    if expected_pos is not None and policy.user_override_px > 0:
        # Measure drift against the active segment [prev_expected -> expected] so async
        # Windows SendInput queue lag between steps never triggers a false abort.
        seg_start = prev_expected_pos if prev_expected_pos is not None else expected_pos
        drift = _point_to_segment_distance(actual_pos, seg_start, expected_pos)
        if drift > policy.user_override_px:
            raise MotorSafetyError(
                f"Physical mouse override detected (drift={drift:.1f}px > {policy.user_override_px}px); aborting."
            )

    if policy.require_window_title and foreground_title is not None:
        if policy.require_window_title.lower() not in foreground_title.lower():
            raise MotorSafetyError(
                f"Foreground window changed to '{foreground_title}' (expected '{policy.require_window_title}'); aborting."
            )
