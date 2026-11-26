"""Tests for continuous geometric trajectories, safety aborts, and bounds clamping."""

import math
import pytest

from kinetic_mcp.models import PixelBox
from kinetic_mcp.motor import (
    MockInputBackend,
    MotorCortex,
    MotorSafetyError,
    SafetyPolicy,
    generate_bezier_path,
    generate_circle_path,
    generate_scrub_path,
)


def test_trajectory_geometry() -> None:
    bez = generate_bezier_path((100, 100), (500, 300), steps=20)
    assert bez[0] == (100, 100)
    assert bez[-1] == (500, 300)
    assert len(bez) == 21

    circle = generate_circle_path((400, 400), radius=50, steps=36)
    assert len(circle) == 37
    # All points stay on radius ~50 and loop returns near start
    for px, py in circle:
        assert abs(math.hypot(px - 400, py - 400) - 50.0) <= 1.5
    assert circle[0] == circle[-1]

    scrub = generate_scrub_path((300, 600), delta_x=120, steps=10)
    assert scrub[0] == (300, 600)
    assert scrub[-1] == (420, 600)
    # Decelerating step deltas shrink toward end
    first_step = scrub[1][0] - scrub[0][0]
    last_step = scrub[-1][0] - scrub[-2][0]
    assert first_step > last_step


def test_motor_stroke_and_safety_abort_releases_button() -> None:
    backend = MockInputBackend(cursor_pos=(300, 300))
    screen = PixelBox(left=0, top=0, right=1920, bottom=1080)
    cortex = MotorCortex(
        backend=backend,
        screen_box_fn=lambda: screen,
        cursor_pos_fn=lambda: backend.cursor_pos,
        foreground_title_fn=lambda: "CapCut",
        step_hz=60,
    )

    res = cortex.draw_circle(400, 400, radius=40, duration_s=0.05)
    assert res["steps"] >= 16
    assert backend.buttons_held == set()
    assert ("mouse_down", "left") in backend.events
    assert ("mouse_up", "left") in backend.events

    # Simulate user physically hijacking mouse into corner fail-safe during stroke
    call_count = {"n": 0}

    def hijacked_cursor() -> tuple[int, int]:
        call_count["n"] += 1
        if call_count["n"] >= 3:
            return (1, 1)  # Corner fail-safe!
        return backend.cursor_pos

    cortex_hijack = MotorCortex(
        backend=backend,
        screen_box_fn=lambda: screen,
        cursor_pos_fn=hijacked_cursor,
        foreground_title_fn=lambda: "CapCut",
        step_hz=60,
    )
    with pytest.raises(MotorSafetyError, match="Corner fail-safe"):
        cortex_hijack.drag_smooth(200, 200, 600, 200, duration_s=0.08)
    # Must always release left mouse button even on abort
    assert backend.buttons_held == set()


def test_bounds_clamping_and_focus_lock() -> None:
    backend = MockInputBackend(cursor_pos=(200, 200))
    screen = PixelBox(left=0, top=0, right=1920, bottom=1080)
    win_box = PixelBox(left=100, top=100, right=500, bottom=400)

    cortex = MotorCortex(
        backend=backend,
        screen_box_fn=lambda: screen,
        cursor_pos_fn=lambda: backend.cursor_pos,
        foreground_title_fn=lambda: "Notepad",
        step_hz=60,
    )

    # Wrong foreground window blocks stroke
    with pytest.raises(MotorSafetyError, match="Foreground window"):
        cortex.move_smooth(300, 300, duration_s=0.04, policy=SafetyPolicy(require_window_title="CapCut"))

    # Out-of-bounds target clamps cleanly inside win_box
    res = cortex.move_smooth(900, 900, duration_s=0.04, policy=SafetyPolicy(bounds=win_box))
    assert res["end"] == (499, 399)


def test_async_step_lag_allowed_but_offpath_override_aborts() -> None:
    backend = MockInputBackend(cursor_pos=(100, 100))
    screen = PixelBox(left=0, top=0, right=1920, bottom=1080)
    lag_state = {"prev": (100, 100)}

    def lagging_cursor() -> tuple[int, int]:
        # Simulate Windows reporting cursor one step behind expected along a fast jump
        res = lag_state["prev"]
        lag_state["prev"] = backend.cursor_pos
        return res

    cortex_lag = MotorCortex(
        backend=backend,
        screen_box_fn=lambda: screen,
        cursor_pos_fn=lagging_cursor,
        step_hz=30,
    )
    res = cortex_lag.move_smooth(800, 100, duration_s=0.06, curvature=0.0)
    assert res["end"] == (800, 100)

    # Off-path physical mouse yank (> user_override_px) still aborts and releases button
    yank_calls = {"n": 0}

    def yanked_cursor() -> tuple[int, int]:
        yank_calls["n"] += 1
        if yank_calls["n"] >= 3:
            return (backend.cursor_pos[0], backend.cursor_pos[1] + 180)
        return backend.cursor_pos

    cortex_yank = MotorCortex(
        backend=backend,
        screen_box_fn=lambda: screen,
        cursor_pos_fn=yanked_cursor,
        step_hz=60,
    )
    with pytest.raises(MotorSafetyError, match="Physical mouse override"):
        cortex_yank.drag_smooth(200, 200, 500, 200, duration_s=0.08)
    assert backend.buttons_held == set()
