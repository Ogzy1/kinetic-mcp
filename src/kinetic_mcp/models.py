"""Pydantic data schemas shared across spatial, motor, and perception modules."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


class PixelBox(BaseModel):
    """Axis-aligned screen rectangle in physical desktop pixels."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        return max(0, self.bottom - self.top)

    @property
    def center(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    def clamp_point(self, x: int, y: int) -> tuple[int, int]:
        cx = min(max(x, self.left), max(self.left, self.right - 1))
        cy = min(max(y, self.top), max(self.top, self.bottom - 1))
        return cx, cy


class NormalizedRegion(BaseModel):
    """App-relative UI region stored in [0.0, 1.0] ratios so layouts survive resizing."""

    name: str
    role: str = "region"
    left_ratio: float = Field(ge=0.0, le=1.0)
    top_ratio: float = Field(ge=0.0, le=1.0)
    right_ratio: float = Field(ge=0.0, le=1.0)
    bottom_ratio: float = Field(ge=0.0, le=1.0)
    description: str = ""

    def to_pixel_box(self, bounds: PixelBox) -> PixelBox:
        return PixelBox(
            left=bounds.left + round(self.left_ratio * bounds.width),
            top=bounds.top + round(self.top_ratio * bounds.height),
            right=bounds.left + round(self.right_ratio * bounds.width),
            bottom=bounds.top + round(self.bottom_ratio * bounds.height),
        )


class AppProfile(BaseModel):
    """Saved layout profile and shortcut map for a specific Windows application."""

    app_id: str
    window_title_contains: list[str] = Field(default_factory=list)
    process_names: list[str] = Field(default_factory=list)
    regions: dict[str, NormalizedRegion] = Field(default_factory=dict)
    shortcuts: dict[str, list[str]] = Field(default_factory=dict)
    notes: str = ""


class UIElement(BaseModel):
    """Unified element detected either via Windows UIA or a saved AppProfile region."""

    id: str
    label: str
    role: str
    source: Literal["uia", "profile"]
    box: PixelBox
    center: tuple[int, int]
    grid_col: int
    grid_row: int


class ScreenGridSnapshot(BaseModel):
    """Screenshot-free spatial text snapshot of the active screen or target window."""

    target_title: str
    target_box: PixelBox
    cursor_px: tuple[int, int]
    cursor_grid: tuple[int, int]
    grid_width: int
    grid_height: int
    ascii_map: str
    elements: list[UIElement]
    active_profile: str | None = None


class MathBucket(BaseModel):
    """Compact per-interval video and audio physics summary replacing raw pixels."""

    t_start: float
    t_end: float
    motion_mag: float
    motion_peak: float
    dominant_dir: str
    dir_reversal: float
    scene_delta: float
    center_delta: float
    hud_top_right_delta: float
    hud_bottom_delta: float
    audio_db: float
    audio_onset: float


class HighlightEvent(BaseModel):
    """Semantic edit/highlight candidate produced by a detector plugin."""

    t_start: float
    t_end: float
    peak_time: float
    event_type: str
    score: float = Field(ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    suggested_edit: str = ""


class VideoMathReport(BaseModel):
    """Full structured math timeline for a video file or live preview capture."""

    source: str
    duration_s: float
    fps: float
    bucket_seconds: float
    detector: str
    total_buckets: int
    buckets: list[MathBucket]
    events: list[HighlightEvent]
    compact_text: str
