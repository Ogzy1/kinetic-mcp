"""Pluggable community highlight detectors operating on MathBucket telemetry."""

from kinetic_mcp.perception.detectors.base import (
    get_detector,
    list_detector_info,
    list_detectors,
    register_detector,
)
from kinetic_mcp.perception.detectors import fps_combat, general, sports_dribble  # noqa: F401

__all__ = ["get_detector", "list_detector_info", "list_detectors", "register_detector"]
