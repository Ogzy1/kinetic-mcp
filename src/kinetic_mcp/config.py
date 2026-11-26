"""Runtime configuration and directory defaults for Kinetic MCP."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_profiles_dir() -> Path:
    env_dir = os.environ.get("KINETIC_MCP_PROFILES_DIR")
    if env_dir:
        return Path(env_dir)
    repo_profiles = Path(__file__).resolve().parents[2] / "profiles"
    if repo_profiles.exists():
        return repo_profiles
    return Path.home() / ".kinetic_mcp" / "profiles"


@dataclass(slots=True)
class EngineConfig:
    """Configuration for spatial perception, motor safety, and video analysis."""

    grid_width: int = 48
    grid_height: int = 16
    profiles_dir: Path = field(default_factory=_default_profiles_dir)
    failsafe_corner_px: int = 3
    user_override_px: int = 85
    default_step_hz: int = 90
    max_stroke_seconds: float = 15.0
    video_analysis_width: int = 240
    video_analysis_height: int = 135
    default_bucket_seconds: float = 0.25
    dry_run: bool = False


CONFIG = EngineConfig()
