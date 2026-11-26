"""Perception layer: video-to-math, audio transients, live preview capture, and detector plugins."""

from kinetic_mcp.perception.audio_math import extract_audio_buckets
from kinetic_mcp.perception.detectors import get_detector, list_detector_info, list_detectors
from kinetic_mcp.perception.preview_capture import (
    grab_preview_sanity_frame,
    watch_preview_math,
)
from kinetic_mcp.perception.video_math import FrameMathAccumulator, analyze_video_file

__all__ = [
    "FrameMathAccumulator",
    "analyze_video_file",
    "extract_audio_buckets",
    "get_detector",
    "grab_preview_sanity_frame",
    "list_detector_info",
    "list_detectors",
    "watch_preview_math",
]
