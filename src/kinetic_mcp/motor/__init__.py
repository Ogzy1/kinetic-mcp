"""Continuous motor cortex: DPI-aware Win32 SendInput, smooth trajectories, and safety guard."""

from kinetic_mcp.motor.safety import MotorSafetyError, SafetyPolicy, clamp_points
from kinetic_mcp.motor.trajectories import (
    MotorCortex,
    generate_bezier_path,
    generate_circle_path,
    generate_polyline_path,
    generate_scrub_path,
)
from kinetic_mcp.motor.win32_input import InputBackend, MockInputBackend, Win32InputBackend

__all__ = [
    "InputBackend",
    "MockInputBackend",
    "MotorCortex",
    "MotorSafetyError",
    "SafetyPolicy",
    "Win32InputBackend",
    "clamp_points",
    "generate_bezier_path",
    "generate_circle_path",
    "generate_polyline_path",
    "generate_scrub_path",
]
