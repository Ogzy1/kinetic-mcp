"""Spatial perception layer: UIA reader, normalized app profiles, and ASCII grid compiler."""

from kinetic_mcp.spatial.ascii_grid import build_screen_grid, grid_to_pixel, pixel_to_grid
from kinetic_mcp.spatial.profile_store import ProfileStore
from kinetic_mcp.spatial.uia_reader import (
    WindowInfo,
    collect_uia_elements,
    enable_dpi_awareness,
    find_window,
    focus_window,
    get_cursor_pos,
    get_foreground_window_info,
    get_virtual_screen_box,
)

__all__ = [
    "ProfileStore",
    "WindowInfo",
    "build_screen_grid",
    "collect_uia_elements",
    "enable_dpi_awareness",
    "find_window",
    "focus_window",
    "get_cursor_pos",
    "get_foreground_window_info",
    "get_virtual_screen_box",
    "grid_to_pixel",
    "pixel_to_grid",
]
