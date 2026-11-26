"""Continuous geometric trajectory math and execution engine for smooth Windows motor control."""

from __future__ import annotations

import math
import time
from typing import Callable

from kinetic_mcp.models import PixelBox
from kinetic_mcp.motor.safety import SafetyPolicy, check_step_safety, clamp_points
from kinetic_mcp.motor.win32_input import InputBackend


def _ease_in_out(t: float) -> float:
    return 0.5 * (1.0 - math.cos(math.pi * t))


def generate_bezier_path(
    start: tuple[int, int],
    end: tuple[int, int],
    steps: int = 24,
    curvature: float = 0.12,
) -> list[tuple[int, int]]:
    """Generate a smooth eased quadratic Bezier cursor trajectory from start to end."""
    steps = max(2, steps)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    ctrl_x = (start[0] + end[0]) / 2.0 - dy * curvature
    ctrl_y = (start[1] + end[1]) / 2.0 + dx * curvature

    pts: list[tuple[int, int]] = []
    for i in range(steps + 1):
        t = _ease_in_out(i / steps)
        u = 1.0 - t
        px = round(u * u * start[0] + 2 * u * t * ctrl_x + t * t * end[0])
        py = round(u * u * start[1] + 2 * u * t * ctrl_y + t * t * end[1])
        pts.append((px, py))
    return pts


def generate_circle_path(
    center: tuple[int, int],
    radius: int,
    steps: int = 48,
    start_angle_deg: float = 0.0,
    sweep_deg: float = 360.0,
) -> list[tuple[int, int]]:
    """Generate a smooth circular or arc drawing path around center."""
    steps = max(8, steps)
    radius = max(1, radius)
    pts: list[tuple[int, int]] = []
    for i in range(steps + 1):
        frac = i / steps
        ang = math.radians(start_angle_deg + sweep_deg * frac)
        px = round(center[0] + radius * math.cos(ang))
        py = round(center[1] + radius * math.sin(ang))
        pts.append((px, py))
    return pts


def generate_polyline_path(
    waypoints: list[tuple[int, int]],
    steps_per_segment: int = 12,
) -> list[tuple[int, int]]:
    """Interpolate a sequence of waypoints into smooth micro-steps."""
    if len(waypoints) < 2:
        return list(waypoints)
    steps_per_segment = max(2, steps_per_segment)
    pts: list[tuple[int, int]] = []
    for seg_idx in range(len(waypoints) - 1):
        a = waypoints[seg_idx]
        b = waypoints[seg_idx + 1]
        start_i = 0 if seg_idx == 0 else 1
        for i in range(start_i, steps_per_segment + 1):
            t = i / steps_per_segment
            px = round(a[0] + (b[0] - a[0]) * t)
            py = round(a[1] + (b[1] - a[1]) * t)
            pts.append((px, py))
    return pts


def generate_scrub_path(
    start: tuple[int, int],
    delta_x: int,
    delta_y: int = 0,
    steps: int = 24,
) -> list[tuple[int, int]]:
    """Generate a decelerating timeline-scrub path so the playhead stops accurately."""
    steps = max(4, steps)
    pts: list[tuple[int, int]] = []
    for i in range(steps + 1):
        t = i / steps
        eased = 1.0 - (1.0 - t) ** 2
        pts.append((round(start[0] + delta_x * eased), round(start[1] + delta_y * eased)))
    return pts


class MotorCortex:
    """Executes continuous pointer strokes and keyboard shortcuts under SafetyPolicy."""

    def __init__(
        self,
        backend: InputBackend,
        screen_box_fn: Callable[[], PixelBox],
        cursor_pos_fn: Callable[[], tuple[int, int]],
        foreground_title_fn: Callable[[], str | None] | None = None,
        step_hz: int = 90,
    ) -> None:
        self.backend = backend
        self.screen_box_fn = screen_box_fn
        self.cursor_pos_fn = cursor_pos_fn
        self.foreground_title_fn = foreground_title_fn
        self.step_hz = max(20, step_hz)

    def _steps_for(self, duration_s: float, policy: SafetyPolicy) -> tuple[int, float]:
        clamped_dur = min(max(0.02, duration_s), policy.max_stroke_seconds)
        steps = max(4, int(round(clamped_dur * self.step_hz)))
        return steps, clamped_dur

    def execute_points(
        self,
        points: list[tuple[int, int]],
        duration_s: float,
        hold_button: str | None = None,
        policy: SafetyPolicy | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Move through points smoothly, optionally holding a mouse button, with safety abort."""
        pol = policy or SafetyPolicy()
        safe_pts = clamp_points(points, pol.bounds)
        if not safe_pts:
            return {"steps": 0, "points": [], "dry_run": dry_run}

        if dry_run:
            return {
                "steps": len(safe_pts),
                "start": safe_pts[0],
                "end": safe_pts[-1],
                "held_button": hold_button,
                "dry_run": True,
            }

        screen_box = self.screen_box_fn()
        clamped_dur = min(max(0.01, duration_s), pol.max_stroke_seconds)
        interval = clamped_dur / max(1, len(safe_pts) - 1)

        fg_title = self.foreground_title_fn() if self.foreground_title_fn else None
        actual = self.cursor_pos_fn()
        check_step_safety(None, actual, screen_box, pol, fg_title)

        self.backend.move_abs(*safe_pts[0])
        prev_expected = safe_pts[0]
        expected = safe_pts[0]

        pressed = False
        try:
            if hold_button:
                self.backend.mouse_down(hold_button)
                pressed = True
            for pt in safe_pts[1:]:
                time.sleep(interval)
                actual = self.cursor_pos_fn()
                fg_title = self.foreground_title_fn() if self.foreground_title_fn else None
                check_step_safety(
                    expected,
                    actual,
                    screen_box,
                    pol,
                    fg_title,
                    prev_expected_pos=prev_expected,
                )
                self.backend.move_abs(*pt)
                prev_expected = expected
                expected = pt
        finally:
            if pressed and hold_button:
                self.backend.mouse_up(hold_button)

        return {
            "steps": len(safe_pts),
            "start": safe_pts[0],
            "end": safe_pts[-1],
            "held_button": hold_button,
            "duration_s": round(clamped_dur, 3),
            "dry_run": False,
        }

    def move_smooth(
        self,
        x: int,
        y: int,
        duration_s: float = 0.25,
        curvature: float = 0.10,
        policy: SafetyPolicy | None = None,
        dry_run: bool = False,
    ) -> dict:
        pol = policy or SafetyPolicy()
        start = self.cursor_pos_fn()
        steps, dur = self._steps_for(duration_s, pol)
        pts = generate_bezier_path(start, (x, y), steps=steps, curvature=curvature)
        return self.execute_points(pts, dur, hold_button=None, policy=pol, dry_run=dry_run)

    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
        duration_s: float = 0.15,
        policy: SafetyPolicy | None = None,
        dry_run: bool = False,
    ) -> dict:
        pol = policy or SafetyPolicy()
        move_res = self.move_smooth(x, y, duration_s=duration_s, policy=pol, dry_run=dry_run)
        if not dry_run:
            for i in range(max(1, clicks)):
                self.backend.mouse_down(button)
                time.sleep(0.025)
                self.backend.mouse_up(button)
                if i + 1 < clicks:
                    time.sleep(0.06)
        move_res["button"] = button
        move_res["clicks"] = max(1, clicks)
        return move_res

    def drag_smooth(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        button: str = "left",
        duration_s: float = 0.4,
        curvature: float = 0.04,
        policy: SafetyPolicy | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Smooth drag with extra precision: more steps, gentler curvature, slight ease-in-out."""
        pol = policy or SafetyPolicy()
        steps, dur = self._steps_for(duration_s, pol)
        # Use 1.5x more steps than a plain move for drag precision
        pts = generate_bezier_path((x1, y1), (x2, y2), steps=max(steps, int(steps * 1.5)), curvature=curvature)
        return self.execute_points(pts, dur, hold_button=button, policy=pol, dry_run=dry_run)

    def draw_circle(
        self,
        cx: int,
        cy: int,
        radius: int,
        duration_s: float = 0.45,
        sweep_deg: float = 360.0,
        button: str = "left",
        policy: SafetyPolicy | None = None,
        dry_run: bool = False,
    ) -> dict:
        pol = policy or SafetyPolicy()
        steps, dur = self._steps_for(duration_s, pol)
        pts = generate_circle_path((cx, cy), radius=radius, steps=max(16, steps), sweep_deg=sweep_deg)
        return self.execute_points(pts, dur, hold_button=button, policy=pol, dry_run=dry_run)

    def draw_path(
        self,
        waypoints: list[tuple[int, int]],
        duration_s: float = 0.6,
        button: str = "left",
        policy: SafetyPolicy | None = None,
        dry_run: bool = False,
    ) -> dict:
        pol = policy or SafetyPolicy()
        steps, dur = self._steps_for(duration_s, pol)
        per_seg = max(3, steps // max(1, len(waypoints) - 1))
        pts = generate_polyline_path(waypoints, steps_per_segment=per_seg)
        return self.execute_points(pts, dur, hold_button=button, policy=pol, dry_run=dry_run)

    def scrub_timeline(
        self,
        x: int,
        y: int,
        delta_x: int,
        delta_y: int = 0,
        duration_s: float = 0.4,
        button: str = "left",
        policy: SafetyPolicy | None = None,
        dry_run: bool = False,
    ) -> dict:
        """Scrub with decelerating ease-out for precise playhead landing."""
        pol = policy or SafetyPolicy()
        steps, dur = self._steps_for(duration_s, pol)
        pts = generate_scrub_path((x, y), delta_x=delta_x, delta_y=delta_y, steps=max(steps, 30))
        return self.execute_points(pts, dur, hold_button=button, policy=pol, dry_run=dry_run)
