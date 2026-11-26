"""Live Windows preview-box capture (GPU/DXGI/Win32 BitBlt fallback) piped into FrameMathAccumulator."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Iterable

import cv2
import numpy as np

from kinetic_mcp.models import PixelBox, VideoMathReport
from kinetic_mcp.perception.detectors import get_detector
from kinetic_mcp.perception.video_math import FrameMathAccumulator, format_math_report_text

try:
    import win32con
    import win32gui
    import win32ui
except ImportError:  # pragma: no cover
    win32con = None
    win32gui = None
    win32ui = None


def capture_region_win32_bgr(region: PixelBox) -> np.ndarray:
    """Capture a screen rectangle into a BGR NumPy array via Win32 GDI BitBlt."""
    if win32gui is None or win32ui is None or win32con is None:
        raise RuntimeError("pywin32 is required for Win32 screen capture")
    w = region.width
    h = region.height
    if w <= 0 or h <= 0:
        raise ValueError(f"Capture region must have positive dimensions, got {region}")

    hdesktop = win32gui.GetDesktopWindow()
    desktop_dc = win32gui.GetWindowDC(hdesktop)
    img_dc = win32ui.CreateDCFromHandle(desktop_dc)
    mem_dc = img_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(img_dc, w, h)
    mem_dc.SelectObject(bmp)
    try:
        mem_dc.BitBlt((0, 0), (w, h), img_dc, (region.left, region.top), win32con.SRCCOPY)
        raw = bmp.GetBitmapBits(True)
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
        return arr[:, :, :3].copy()
    finally:
        mem_dc.DeleteDC()
        img_dc.DeleteDC()
        win32gui.ReleaseDC(hdesktop, desktop_dc)
        win32gui.DeleteObject(bmp.GetHandle())


def _create_capturer():
    """Return fastest available frame grabber callable: DXcam -> Win32 BitBlt."""
    try:
        import dxcam  # type: ignore

        cam = dxcam.create(output_color="BGR")
        if cam is not None:
            def _grab_dxcam(region: PixelBox) -> np.ndarray | None:
                frame = cam.grab(region=(region.left, region.top, region.right, region.bottom))
                return frame
            return "dxcam", _grab_dxcam
    except Exception:
        pass
    return "win32_bitblt", capture_region_win32_bgr


def watch_preview_math(
    region: PixelBox,
    duration_s: float = 3.0,
    target_fps: float = 20.0,
    bucket_seconds: float = 0.25,
    detector: str = "general",
    mock_frames: Iterable[tuple[float, np.ndarray]] | None = None,
    frame_provider: Callable[[], np.ndarray] | None = None,
) -> VideoMathReport:
    """Watch a live preview rectangle on screen for duration_s and emit pure math telemetry.

    Raw captured pixels stay in local memory only and are discarded immediately after math extraction.
    """
    acc = FrameMathAccumulator()
    clamped_fps = min(60.0, max(5.0, float(target_fps)))
    dt = 1.0 / clamped_fps

    if mock_frames is not None:
        backend_name = "mock_preview"
        max_t = 0.0
        for t, frame in mock_frames:
            acc.push_frame(float(t), frame)
            max_t = max(max_t, float(t))
        effective_dur = max(duration_s, max_t + bucket_seconds)
    else:
        if frame_provider is not None:
            backend_name = "custom_provider"
            grab_fn = lambda _region: frame_provider()
        else:
            backend_name, grab_fn = _create_capturer()
        clamped_dur = min(max(0.25, duration_s), 20.0)
        start = time.perf_counter()
        next_tick = start
        while True:
            now = time.perf_counter()
            elapsed = now - start
            if elapsed >= clamped_dur:
                break
            frame = grab_fn(region)
            if frame is not None and frame.size > 0:
                acc.push_frame(elapsed, frame)
            next_tick += dt
            sleep_for = next_tick - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
        effective_dur = clamped_dur

    buckets = acc.build_buckets(bucket_seconds=bucket_seconds, duration_s=effective_dur)
    det_fn = get_detector(detector)
    events = det_fn(buckets)
    source_label = f"live_preview[{backend_name}] ({region.left},{region.top},{region.right},{region.bottom})"
    compact = format_math_report_text(
        source=source_label,
        duration_s=effective_dur,
        fps=float(clamped_fps),
        bucket_seconds=bucket_seconds,
        detector_name=detector,
        buckets=buckets,
        events=events,
    )
    return VideoMathReport(
        source=source_label,
        duration_s=round(effective_dur, 3),
        fps=float(clamped_fps),
        bucket_seconds=bucket_seconds,
        detector=detector,
        total_buckets=len(buckets),
        buckets=buckets,
        events=events,
        compact_text=compact,
    )


def grab_preview_sanity_frame(
    region: PixelBox,
    output_path: str | Path | None = None,
    max_width: int = 480,
) -> dict:
    """Optional 1% visual sanity check: save a single downscaled crop of a screen region."""
    bgr = capture_region_win32_bgr(region)
    h, w = bgr.shape[:2]
    if w > max_width:
        scale = max_width / w
        bgr = cv2.resize(bgr, (max_width, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
    out = Path(output_path) if output_path is not None else (Path.home() / ".kinetic_mcp" / "sanity_latest.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    ok = cv2.imwrite(str(out), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        raise OSError(f"Failed to write sanity frame to {out}")
    return {"path": str(out), "width": int(bgr.shape[1]), "height": int(bgr.shape[0])}
