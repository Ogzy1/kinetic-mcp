"""Tests for local video-to-math conversion, highlight detectors, and live preview capture."""

from __future__ import annotations

from pathlib import Path
import av
import cv2
import numpy as np

from kinetic_mcp.models import PixelBox
from kinetic_mcp.perception import analyze_video_file, watch_preview_math


def _build_synthetic_gameplay_video(out_path: Path) -> None:
    """Create a real MP4 with:
    - 0.0..1.0s: calm scene + silent audio
    - 1.0..1.75s: rapid pan flick -> stop + bright center flash + top-right HUD change + audio bang
    - 2.0..3.0s: sharp left->right zigzag reversal for sports dribble test
    """
    fps = 24
    width, height = 320, 180
    total_frames = fps * 3  # 3.0 seconds
    sample_rate = 16000

    with av.open(str(out_path), mode="w") as container:
        vstream = container.add_stream("mpeg4", rate=fps)
        vstream.width = width
        vstream.height = height
        vstream.pix_fmt = "yuv420p"

        astream = container.add_stream("aac", rate=sample_rate)
        astream.layout = "mono"

        # Non-periodic textured world (random blocks/landmarks) so optical flow has zero aliasing
        rng = np.random.default_rng(42)
        world = np.full((height, width * 3, 3), 50, dtype=np.uint8)
        for _ in range(450):
            bx = int(rng.integers(0, width * 3 - 20))
            by = int(rng.integers(0, height - 20))
            bw = int(rng.integers(8, 28))
            bh = int(rng.integers(8, 24))
            col = tuple(int(c) for c in rng.integers(80, 210, size=3))
            cv2.rectangle(world, (bx, by), (bx + bw, by + bh), col, -1)

        cam_x = width
        for f_idx in range(total_frames):
            t_s = f_idx / fps
            if 1.0 <= t_s < 1.30:
                cam_x += 10  # Fast camera flick right
            elif 2.0 <= t_s < 2.45:
                cam_x -= 9   # Zigzag cut left
            elif 2.45 <= t_s < 2.90:
                cam_x += 10  # Rapid reversal right!

            cam_x = max(0, min(width * 2, cam_x))
            frame_bgr = world[:, cam_x : cam_x + width].copy()

            # Center hitmarker flash + top-right killfeed pop right after flick stops (1.30..1.55s)
            if 1.30 <= t_s <= 1.55:
                cv2.circle(frame_bgr, (width // 2, height // 2), 26, (255, 255, 255), -1)
                cv2.rectangle(frame_bgr, (width - 90, 5), (width - 5, 35), (255, 80, 80), -1)

            vframe = av.VideoFrame.from_ndarray(frame_bgr, format="bgr24")
            for pkt in vstream.encode(vframe):
                container.mux(pkt)

        for pkt in vstream.encode(None):
            container.mux(pkt)

        # Build 3.0s mono audio with sharp gunshot transient at 1.32s and crowd swell at 2.48s
        total_samples = sample_rate * 3
        audio = np.random.default_rng(7).normal(0.0, 0.003, size=total_samples).astype(np.float32)
        shot_i = int(1.32 * sample_rate)
        audio[shot_i : shot_i + 1200] += 0.85 * np.sin(
            2 * np.pi * 420 * np.arange(1200, dtype=np.float32) / sample_rate
        )
        swell_i = int(2.48 * sample_rate)
        audio[swell_i : swell_i + 3200] += 0.35 * np.sin(
            2 * np.pi * 240 * np.arange(3200, dtype=np.float32) / sample_rate
        )

        frame_size = 1024
        for s0 in range(0, total_samples, frame_size):
            chunk = audio[s0 : s0 + frame_size]
            if chunk.size < frame_size:
                chunk = np.pad(chunk, (0, frame_size - chunk.size))
            aframe = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="flt", layout="mono")
            aframe.sample_rate = sample_rate
            aframe.pts = s0
            for pkt in astream.encode(aframe):
                container.mux(pkt)
        for pkt in astream.encode(None):
            container.mux(pkt)


def test_analyze_video_detects_fps_kill_and_sports_zigzag(tmp_path: Path) -> None:
    vid = tmp_path / "synthetic_test.mp4"
    _build_synthetic_gameplay_video(vid)

    fps_report = analyze_video_file(vid, bucket_seconds=0.25, detector="fps_combat")
    assert fps_report.total_buckets >= 11
    assert any(0.9 <= ev.peak_time <= 1.85 for ev in fps_report.events), (
        f"Expected FPS combat peak near 1.3s, got {fps_report.events}\n{fps_report.compact_text}"
    )

    sports_report = analyze_video_file(vid, bucket_seconds=0.25, detector="sports_dribble")
    assert any(b.dir_reversal >= 0.4 for b in sports_report.buckets), (
        f"Expected direction reversal near 2.45s, got {[b.dir_reversal for b in sports_report.buckets]}"
    )
    assert any(2.0 <= ev.peak_time <= 3.0 for ev in sports_report.events), (
        f"Expected sports zigzag event near 2.45s, got {sports_report.events}"
    )


def test_watch_preview_box_with_synthetic_stream() -> None:
    box = PixelBox(left=100, top=100, right=420, bottom=280)
    state = {"idx": 0}

    def fake_grab() -> np.ndarray:
        i = state["idx"]
        state["idx"] += 1
        img = np.zeros((180, 320, 3), dtype=np.uint8)
        # Simulate sudden flash/motion mid-preview
        x = (i * 22) % 260
        color = (255, 255, 255) if 4 <= i <= 8 else (120, 200, 120)
        cv2.rectangle(img, (x, 50), (x + 50, 130), color, -1)
        return img

    report = watch_preview_math(
        region=box,
        duration_s=0.6,
        target_fps=20.0,
        bucket_seconds=0.2,
        detector="general",
        frame_provider=fake_grab,
    )
    assert report.total_buckets >= 2
    assert any(b.motion_peak > 0.05 for b in report.buckets)
    assert "VIDEO_MATH" in report.compact_text
