"""FastMCP server exposing screenshot-free spatial UI, continuous motor, and video-math tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from mcp.server.fastmcp import FastMCP

from kinetic_mcp.config import CONFIG, EngineConfig
from kinetic_mcp.models import PixelBox, ScreenGridSnapshot, UIElement
from kinetic_mcp.motor import InputBackend, MotorCortex, SafetyPolicy, Win32InputBackend
from kinetic_mcp.perception import (
    analyze_video_file,
    grab_preview_sanity_frame,
    list_detector_info,
    watch_preview_math,
)
from kinetic_mcp.spatial import (
    ProfileStore,
    WindowInfo,
    build_screen_grid,
    collect_uia_elements,
    enable_dpi_awareness,
    find_window,
    focus_window,
    get_cursor_pos,
    get_foreground_window_info,
    get_virtual_screen_box,
    grid_to_pixel,
)
from kinetic_mcp.spatial.uia_reader import list_windows, require_cursor_pos


@dataclass
class EngineRuntime:
    """Dependency container holding state, backends, and active spatial snapshot."""

    config: EngineConfig = field(default_factory=lambda: CONFIG)
    profiles: ProfileStore = field(init=False)
    input_backend: InputBackend | None = None
    motor: MotorCortex = field(init=False)
    last_snapshot: ScreenGridSnapshot | None = None
    last_window: WindowInfo | None = None
    window_finder: Callable[[str], WindowInfo | None] = find_window
    fg_window_fn: Callable[[], WindowInfo | None] = get_foreground_window_info
    desktop_box_fn: Callable[[], PixelBox] = get_virtual_screen_box
    cursor_pos_fn: Callable[[], tuple[int, int]] = get_cursor_pos
    uia_collector: Callable[..., list[dict]] = collect_uia_elements

    def __post_init__(self) -> None:
        self.profiles = ProfileStore(self.config.profiles_dir)
        backend = self.input_backend or Win32InputBackend()
        self.input_backend = backend
        motor_cursor_fn = require_cursor_pos if self.cursor_pos_fn is get_cursor_pos else self.cursor_pos_fn
        self.motor = MotorCortex(
            backend=backend,
            screen_box_fn=self.desktop_box_fn,
            cursor_pos_fn=motor_cursor_fn,
            foreground_title_fn=lambda: (w.title if (w := self.fg_window_fn()) else None),
            step_hz=self.config.default_step_hz,
        )

    def resolve_target_window(self, window_title: str = "") -> tuple[WindowInfo | None, str, PixelBox]:
        if window_title == "__desktop__":
            box = self.desktop_box_fn()
            return None, "Windows Desktop", box
        win = self.window_finder(window_title) if window_title else self.fg_window_fn()
        if win is None:
            box = self.desktop_box_fn()
            return None, "Windows Desktop", box
        return win, win.title, win.box

    def resolve_region_box(self, window_title: str = "", region_name: str = "preview_box") -> PixelBox:
        win, title, box = self.resolve_target_window(window_title)
        proc = win.process_name if win else ""
        profile = self.profiles.match_profile(window_title=title, process_name=proc)
        if profile and region_name in profile.regions:
            return profile.regions[region_name].to_pixel_box(box)
        if self.last_snapshot is not None:
            for el in self.last_snapshot.elements:
                if el.label.lower() == region_name.lower() or el.id.lower() == region_name.lower():
                    return el.box
        return box

    def active_safety_policy(self, clamp_to_target: bool = False) -> SafetyPolicy:
        bounds = None
        if clamp_to_target and self.last_snapshot is not None:
            bounds = self.last_snapshot.target_box
        return SafetyPolicy(
            bounds=bounds,
            failsafe_corner_px=self.config.failsafe_corner_px,
            user_override_px=self.config.user_override_px,
            max_stroke_seconds=self.config.max_stroke_seconds,
        )

    def find_element(self, element_id: str) -> UIElement:
        if self.last_snapshot is None:
            raise ValueError("No screen grid snapshot cached yet; call screen_get_grid first.")
        needle = element_id.strip().lstrip("[").rstrip("]")
        for el in self.last_snapshot.elements:
            if el.id.lower() == needle.lower() or el.label.lower() == needle.lower():
                return el
        raise KeyError(f"Element {element_id!r} not found in latest grid snapshot.")


def create_server(runtime: EngineRuntime | None = None) -> FastMCP:
    """Create and configure the Kinetic MCP FastMCP server."""
    enable_dpi_awareness()
    rt = runtime or EngineRuntime()
    mcp = FastMCP("kinetic-mcp")

    # ------------------------------------------------------------------
    # Layer 2: Spatial Eyes (Screenshot-Free ASCII Grid + App Profiles)
    # ------------------------------------------------------------------

    @mcp.tool()
    def screen_list_windows() -> list[dict]:
        """List open visible Windows applications with titles, process names, and screen bounds."""
        return [
            {
                "hwnd": w.hwnd,
                "title": w.title,
                "process_name": w.process_name,
                "pid": w.pid,
                "box": w.box.model_dump(),
            }
            for w in list_windows()
        ]

    @mcp.tool()
    def screen_focus_window(window_title: str) -> dict:
        """Restore and bring a matching Windows application to the foreground."""
        win = rt.window_finder(window_title)
        if win is None:
            return {"focused": False, "error": f"No open window matching {window_title!r}"}
        ok = focus_window(win.hwnd)
        return {"focused": ok, "title": win.title, "process_name": win.process_name, "box": win.box.model_dump()}

    @mcp.tool()
    def screen_get_grid(
        window_title: str = "",
        grid_width: int = 48,
        grid_height: int = 16,
        include_uia: bool = True,
    ) -> dict:
        """Read the target window as a compact ASCII coordinate grid. No screenshots.

        Pass window_title='__desktop__' for virtual desktop, or a partial title like 'CapCut'.
        """
        win, title, box = rt.resolve_target_window(window_title)
        proc = win.process_name if win else ""
        profile = rt.profiles.match_profile(window_title=title, process_name=proc)
        uia_items = rt.uia_collector(win.hwnd if win else None, box) if include_uia else []
        cursor = rt.cursor_pos_fn()

        snap = build_screen_grid(
            target_title=title,
            target_box=box,
            cursor_px=cursor,
            raw_uia_elements=uia_items,
            profile=profile,
            grid_w=max(20, min(140, grid_width)),
            grid_h=max(10, min(60, grid_height)),
        )
        rt.last_snapshot = snap
        rt.last_window = win
        return {
            "ascii_map": snap.ascii_map,
            "n": len(snap.elements),
        }

    @mcp.tool()
    def screen_label_region(
        app_id: str,
        region_name: str,
        col1: int,
        row1: int,
        col2: int,
        row2: int,
        role: str = "region",
        description: str = "",
        window_title: str = "",
    ) -> dict:
        """Teach and permanently save a named app region (e.g., preview_box, timeline_track) from grid coords.

        Regions are saved as normalized [0..1] window ratios in profiles/<app_id>.json so they
        stay accurate even when the app window is resized or moved.
        """
        win, title, win_box = rt.resolve_target_window(window_title)
        if rt.last_snapshot is not None and not window_title:
            win_box = rt.last_snapshot.target_box
            title = rt.last_snapshot.target_title

        gw = rt.last_snapshot.grid_width if rt.last_snapshot else rt.config.grid_width
        gh = rt.last_snapshot.grid_height if rt.last_snapshot else rt.config.grid_height

        c_lo, c_hi = min(col1, col2), max(col1, col2)
        r_lo, r_hi = min(row1, row2), max(row1, row2)
        x1, y1 = grid_to_pixel(c_lo, r_lo, win_box, gw, gh)
        x2, y2 = grid_to_pixel(c_hi, r_hi, win_box, gw, gh)

        # Expand single-cell bounds slightly to cover cell span
        half_cw = max(2, win_box.width // (2 * max(1, gw)))
        half_ch = max(2, win_box.height // (2 * max(1, gh)))
        reg_box = PixelBox(
            left=max(win_box.left, x1 - half_cw),
            top=max(win_box.top, y1 - half_ch),
            right=min(win_box.right, x2 + half_cw),
            bottom=min(win_box.bottom, y2 + half_ch),
        )
        profile = rt.profiles.upsert_region(
            app_id=app_id,
            region_name=region_name,
            region_box=reg_box,
            window_box=win_box,
            role=role,
            description=description,
            window_title_hint=title if title != "Windows Desktop" else "",
            process_name=win.process_name if win else "",
        )
        return {
            "saved": True,
            "app_id": profile.app_id,
            "region": profile.regions[region_name].model_dump(),
            "pixel_box": reg_box.model_dump(),
        }

    @mcp.tool()
    def profile_list() -> list[dict]:
        """List all saved application layout profiles and their known regions/shortcuts."""
        return [
            {
                "app_id": p.app_id,
                "window_title_contains": p.window_title_contains,
                "regions": list(p.regions.keys()),
                "shortcuts": p.shortcuts,
            }
            for p in rt.profiles.list_profiles()
        ]

    # ------------------------------------------------------------------
    # Layer 3: Continuous Motor Cortex (Smooth Windows Hands)
    # ------------------------------------------------------------------

    @mcp.tool()
    def motor_move_grid(
        col: int,
        row: int,
        duration_s: float = 0.25,
        dry_run: bool = False,
    ) -> dict:
        """Smoothly glide the Windows cursor to ASCII grid cell (col, row)."""
        snap = rt.last_snapshot
        box = snap.target_box if snap else rt.desktop_box_fn()
        gw = snap.grid_width if snap else rt.config.grid_width
        gh = snap.grid_height if snap else rt.config.grid_height
        px, py = grid_to_pixel(col, row, box, gw, gh)
        return rt.motor.move_smooth(
            px,
            py,
            duration_s=duration_s,
            policy=rt.active_safety_policy(),
            dry_run=dry_run or rt.config.dry_run,
        )

    @mcp.tool()
    def motor_click_element(
        element_id: str,
        button: str = "left",
        clicks: int = 1,
        duration_s: float = 0.2,
        dry_run: bool = False,
    ) -> dict:
        """Smoothly move to a labeled UIA element ID (e.g. '1') or profile region (e.g. 'P1') and click."""
        el = rt.find_element(element_id)
        res = rt.motor.click(
            el.center[0],
            el.center[1],
            button=button,
            clicks=clicks,
            duration_s=duration_s,
            policy=rt.active_safety_policy(),
            dry_run=dry_run or rt.config.dry_run,
        )
        res["element_id"] = el.id
        res["label"] = el.label
        return res

    @mcp.tool()
    def motor_drag(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        button: str = "left",
        duration_s: float = 0.35,
        use_grid: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Smoothly hold and drag from (x1, y1) to (x2, y2) in pixels or grid cells."""
        if use_grid:
            snap = rt.last_snapshot
            box = snap.target_box if snap else rt.desktop_box_fn()
            gw = snap.grid_width if snap else rt.config.grid_width
            gh = snap.grid_height if snap else rt.config.grid_height
            x1, y1 = grid_to_pixel(x1, y1, box, gw, gh)
            x2, y2 = grid_to_pixel(x2, y2, box, gw, gh)
        return rt.motor.drag_smooth(
            x1,
            y1,
            x2,
            y2,
            button=button,
            duration_s=duration_s,
            policy=rt.active_safety_policy(),
            dry_run=dry_run or rt.config.dry_run,
        )

    @mcp.tool()
    def motor_draw_circle(
        cx: int,
        cy: int,
        radius: int,
        duration_s: float = 0.45,
        sweep_deg: float = 360.0,
        button: str = "left",
        use_grid: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Draw a smooth continuous circle or arc in a creative canvas app (Paint, Photoshop, Blender)."""
        if use_grid:
            snap = rt.last_snapshot
            box = snap.target_box if snap else rt.desktop_box_fn()
            gw = snap.grid_width if snap else rt.config.grid_width
            gh = snap.grid_height if snap else rt.config.grid_height
            cx, cy = grid_to_pixel(cx, cy, box, gw, gh)
            radius = max(4, round(radius * (box.width / max(1, gw))))
        return rt.motor.draw_circle(
            cx,
            cy,
            radius,
            duration_s=duration_s,
            sweep_deg=sweep_deg,
            button=button,
            policy=rt.active_safety_policy(),
            dry_run=dry_run or rt.config.dry_run,
        )

    @mcp.tool()
    def motor_draw_path(
        waypoints: list[list[int]],
        duration_s: float = 0.6,
        button: str = "left",
        use_grid: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """Draw a smooth continuous stroke through multiple [x, y] or [col, row] waypoints."""
        pts: list[tuple[int, int]] = []
        snap = rt.last_snapshot
        box = snap.target_box if snap else rt.desktop_box_fn()
        gw = snap.grid_width if snap else rt.config.grid_width
        gh = snap.grid_height if snap else rt.config.grid_height
        for wp in waypoints:
            if len(wp) != 2:
                raise ValueError(f"Each waypoint must be [x, y], got {wp!r}")
            if use_grid:
                pts.append(grid_to_pixel(int(wp[0]), int(wp[1]), box, gw, gh))
            else:
                pts.append((int(wp[0]), int(wp[1])))
        return rt.motor.draw_path(
            pts,
            duration_s=duration_s,
            button=button,
            policy=rt.active_safety_policy(),
            dry_run=dry_run or rt.config.dry_run,
        )

    @mcp.tool()
    def motor_scrub_timeline(
        delta_x: int,
        x: int | None = None,
        y: int | None = None,
        region_name: str = "timeline_track",
        window_title: str = "",
        duration_s: float = 0.35,
        dry_run: bool = False,
    ) -> dict:
        """Smoothly drag/scrub horizontally along a video editor timeline with ease-out stop precision."""
        if x is None or y is None:
            tbox = rt.resolve_region_box(window_title=window_title, region_name=region_name)
            sx, sy = tbox.center
        else:
            sx, sy = x, y
        return rt.motor.scrub_timeline(
            sx,
            sy,
            delta_x=delta_x,
            duration_s=duration_s,
            policy=rt.active_safety_policy(),
            dry_run=dry_run or rt.config.dry_run,
        )

    @mcp.tool()
    def motor_hotkey(keys: list[str], dry_run: bool = False) -> dict:
        """Press a keyboard shortcut chord cleanly via Win32 SendInput (e.g. ['ctrl', 'b'])."""
        if not (dry_run or rt.config.dry_run):
            assert rt.input_backend is not None
            rt.input_backend.hotkey(keys)
        return {"hotkey": keys, "dry_run": dry_run or rt.config.dry_run}

    @mcp.tool()
    def motor_type_text(text: str, dry_run: bool = False) -> dict:
        """Type Unicode text cleanly via Win32 SendInput."""
        if not (dry_run or rt.config.dry_run):
            assert rt.input_backend is not None
            rt.input_backend.type_text(text)
        return {"chars": len(text), "dry_run": dry_run or rt.config.dry_run}

    @mcp.tool()
    def motor_scroll(clicks: int, horizontal: bool = False, dry_run: bool = False) -> dict:
        """Scroll mouse wheel vertically or horizontally."""
        if not (dry_run or rt.config.dry_run):
            assert rt.input_backend is not None
            rt.input_backend.scroll(clicks, horizontal=horizontal)
        return {"clicks": clicks, "horizontal": horizontal, "dry_run": dry_run or rt.config.dry_run}

    # ------------------------------------------------------------------
    # Layer 4 & 5: Video & Live Preview Math Engine (+ 1% Sanity Check)
    # ------------------------------------------------------------------

    @mcp.tool()
    def video_list_detectors() -> list[dict[str, str]]:
        """List available highlight/action math detectors (e.g. general, fps_combat, sports_dribble)."""
        return list_detector_info()

    @mcp.tool()
    def video_analyze_math(
        video_path: str,
        detector: str = "general",
        bucket_seconds: float = 0.25,
        sample_fps: float = 30.0,
        max_seconds: float | None = None,
        include_buckets: bool = False,
    ) -> dict:
        """Analyze a video file locally into motion, scene delta, 3x3 HUD/center activity, and audio onset math.

        Zero vision tokens: runs entirely locally on CPU and returns compact timestamped math + cut candidates.
        """
        report = analyze_video_file(
            file_path=Path(video_path),
            bucket_seconds=bucket_seconds,
            detector=detector,
            sample_fps=sample_fps,
            max_seconds=max_seconds,
        )
        payload = {
            "source": report.source,
            "duration_s": report.duration_s,
            "fps": report.fps,
            "bucket_seconds": report.bucket_seconds,
            "detector": report.detector,
            "total_buckets": report.total_buckets,
            "events": [e.model_dump() for e in report.events],
            "compact_text": report.compact_text,
        }
        if include_buckets:
            payload["buckets"] = [b.model_dump() for b in report.buckets]
        return payload

    @mcp.tool()
    def preview_watch_math(
        window_title: str = "",
        region_name: str = "preview_box",
        duration_s: float = 3.0,
        target_fps: float = 24.0,
        bucket_seconds: float = 0.25,
        detector: str = "general",
        include_buckets: bool = False,
    ) -> dict:
        """Watch a calibrated editor preview box locally for N seconds and return only motion/delta math.

        Never sends screenshots or video frames to the LLM; raw pixels are discarded immediately.
        """
        box = rt.resolve_region_box(window_title=window_title, region_name=region_name)
        report = watch_preview_math(
            region=box,
            duration_s=duration_s,
            target_fps=target_fps,
            bucket_seconds=bucket_seconds,
            detector=detector,
        )
        payload = {
            "source": report.source,
            "region_box": box.model_dump(),
            "duration_s": report.duration_s,
            "fps": report.fps,
            "detector": report.detector,
            "events": [e.model_dump() for e in report.events],
            "compact_text": report.compact_text,
        }
        if include_buckets:
            payload["buckets"] = [b.model_dump() for b in report.buckets]
        return payload

    @mcp.tool()
    def preview_sanity_snapshot(
        window_title: str = "",
        region_name: str = "preview_box",
        max_width: int = 480,
    ) -> dict:
        """Optional 1% visual sanity check: grab ONE downscaled JPEG of a calibrated region when aesthetics matter."""
        box = rt.resolve_region_box(window_title=window_title, region_name=region_name)
        snap = grab_preview_sanity_frame(box, max_width=max_width)
        snap["region_box"] = box.model_dump()
        return snap

    return mcp


def main() -> None:
    server = create_server()
    server.run()


if __name__ == "__main__":
    main()
