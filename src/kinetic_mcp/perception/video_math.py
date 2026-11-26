"""Core FrameMathAccumulator and offline video-to-math pipeline (zero screenshots sent to LLM)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import av
import cv2
import numpy as np

from kinetic_mcp.models import HighlightEvent, MathBucket, VideoMathReport
from kinetic_mcp.perception.audio_math import extract_audio_buckets
from kinetic_mcp.perception.detectors import get_detector


@dataclass(slots=True)
class _FrameSample:
    t: float
    motion_mag: float
    dominant_dir: str
    dir_reversal: float
    scene_delta: float
    center_delta: float
    hud_top_right_delta: float
    hud_bottom_delta: float


class FrameMathAccumulator:
    """Incrementally turns incoming video/preview frames into optical-flow & grid-delta telemetry."""

    def __init__(self, width: int = 240, height: int = 135) -> None:
        self.width = max(64, width)
        self.height = max(48, height)
        self._prev_gray: np.ndarray | None = None
        self._prev_mean_flow: tuple[float, float] | None = None
        self._samples: list[_FrameSample] = []

        # Precompute unit radial vectors from frame center for zoom_in / zoom_out detection
        ys, xs = np.mgrid[0 : self.height, 0 : self.width].astype(np.float32)
        cx = (self.width - 1) / 2.0
        cy = (self.height - 1) / 2.0
        rx = xs - cx
        ry = ys - cy
        rnorm = np.sqrt(rx * rx + ry * ry) + 1e-5
        self._rad_x = rx / rnorm
        self._rad_y = ry / rnorm

    def _classify_direction(self, flow: np.ndarray, mean_dx: float, mean_dy: float, mean_mag: float) -> str:
        if mean_mag < 0.35:
            return "steady"

        # Radial dot product measures expansion (zoom_in) vs contraction (zoom_out)
        radial = float(np.mean(flow[..., 0] * self._rad_x + flow[..., 1] * self._rad_y))
        mean_vec_norm = math.hypot(mean_dx, mean_dy)
        if abs(radial) > max(0.35, mean_vec_norm * 1.15):
            return "zoom_in" if radial > 0 else "zoom_out"

        if mean_vec_norm < mean_mag * 0.38:
            return "chaotic"

        if abs(mean_dx) >= abs(mean_dy):
            return "pan_right" if mean_dx > 0 else "pan_left"
        return "tilt_down" if mean_dy > 0 else "tilt_up"

    def push_frame(self, timestamp_s: float, bgr_frame: np.ndarray) -> None:
        """Analyze one BGR frame in memory and store only compact scalar math."""
        resized = cv2.resize(bgr_frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)

        if self._prev_gray is None:
            self._prev_gray = gray
            self._samples.append(
                _FrameSample(
                    t=timestamp_s,
                    motion_mag=0.0,
                    dominant_dir="steady",
                    dir_reversal=0.0,
                    scene_delta=0.0,
                    center_delta=0.0,
                    hud_top_right_delta=0.0,
                    hud_bottom_delta=0.0,
                )
            )
            return

        flow = cv2.calcOpticalFlowFarneback(
            self._prev_gray,
            gray,
            None,
            pyr_scale=0.5,
            levels=2,
            winsize=11,
            iterations=2,
            poly_n=5,
            poly_sigma=1.1,
            flags=0,
        )
        mag_arr = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        raw_mag = float(np.mean(mag_arr))
        # Normalize relative to analysis width so values stay intuitive (~0..1+)
        norm_mag = round(min(2.0, raw_mag / (self.width * 0.035)), 4)

        mean_dx = float(np.mean(flow[..., 0]))
        mean_dy = float(np.mean(flow[..., 1]))
        dom_dir = self._classify_direction(flow, mean_dx, mean_dy, raw_mag)

        reversal = 0.0
        if self._prev_mean_flow is not None:
            px, py = self._prev_mean_flow
            n1 = math.hypot(px, py)
            n2 = math.hypot(mean_dx, mean_dy)
            if n1 > 0.25 and n2 > 0.25:
                cos_sim = (px * mean_dx + py * mean_dy) / (n1 * n2)
                if cos_sim < 0.0:
                    speed_factor = min(1.0, (n1 + n2) / 4.0)
                    reversal = round(min(1.0, ((-cos_sim)) * speed_factor), 4)

        diff = cv2.absdiff(gray, self._prev_gray).astype(np.float32) / 255.0
        scene_delta = round(min(1.0, float(np.mean(diff)) * 4.0), 4)

        # 3x3 spatial region energy breakdown
        h3, w3 = self.height // 3, self.width // 3
        center_patch = diff[h3 : 2 * h3, w3 : 2 * w3]
        top_right_patch = diff[0:h3, 2 * w3 : self.width]
        bottom_patch = diff[2 * h3 : self.height, :]

        center_delta = round(min(1.0, float(np.mean(center_patch)) * 4.5), 4)
        hud_tr = round(min(1.0, float(np.mean(top_right_patch)) * 5.0), 4)
        hud_bot = round(min(1.0, float(np.mean(bottom_patch)) * 4.5), 4)

        self._prev_gray = gray
        self._prev_mean_flow = (mean_dx, mean_dy)
        self._samples.append(
            _FrameSample(
                t=timestamp_s,
                motion_mag=norm_mag,
                dominant_dir=dom_dir,
                dir_reversal=reversal,
                scene_delta=scene_delta,
                center_delta=center_delta,
                hud_top_right_delta=hud_tr,
                hud_bottom_delta=hud_bot,
            )
        )

    def build_buckets(
        self,
        bucket_seconds: float,
        duration_s: float | None = None,
        audio_buckets: list[tuple[float, float]] | None = None,
    ) -> list[MathBucket]:
        """Aggregate per-frame samples into fixed time buckets merged with audio signals."""
        if not self._samples:
            return []
        b_sec = max(0.05, bucket_seconds)
        end_t = duration_s if duration_s and duration_s > 0 else max(s.t for s in self._samples) + b_sec
        num_buckets = max(1, int(math.ceil(end_t / b_sec)))

        grouped: list[list[_FrameSample]] = [[] for _ in range(num_buckets)]
        for s in self._samples:
            idx = min(num_buckets - 1, max(0, int(s.t / b_sec)))
            grouped[idx].append(s)

        buckets: list[MathBucket] = []
        for idx in range(num_buckets):
            t0 = round(idx * b_sec, 3)
            t1 = round(min(end_t, (idx + 1) * b_sec), 3)
            items = grouped[idx]
            audio_db, audio_onset = (
                audio_buckets[idx]
                if audio_buckets is not None and idx < len(audio_buckets)
                else (-60.0, 0.0)
            )
            if not items:
                buckets.append(
                    MathBucket(
                        t_start=t0,
                        t_end=t1,
                        motion_mag=0.0,
                        motion_peak=0.0,
                        dominant_dir="steady",
                        dir_reversal=0.0,
                        scene_delta=0.0,
                        center_delta=0.0,
                        hud_top_right_delta=0.0,
                        hud_bottom_delta=0.0,
                        audio_db=audio_db,
                        audio_onset=audio_onset,
                    )
                )
                continue

            # Pick non-steady dominant direction with highest motion if active
            active = [x for x in items if x.dominant_dir != "steady"]
            if active:
                top_item = max(active, key=lambda x: x.motion_mag)
                dom_dir = top_item.dominant_dir
            else:
                dom_dir = "steady"

            buckets.append(
                MathBucket(
                    t_start=t0,
                    t_end=t1,
                    motion_mag=round(sum(x.motion_mag for x in items) / len(items), 3),
                    motion_peak=round(max(x.motion_mag for x in items), 3),
                    dominant_dir=dom_dir,
                    dir_reversal=round(max(x.dir_reversal for x in items), 3),
                    scene_delta=round(max(x.scene_delta for x in items), 3),
                    center_delta=round(max(x.center_delta for x in items), 3),
                    hud_top_right_delta=round(max(x.hud_top_right_delta for x in items), 3),
                    hud_bottom_delta=round(max(x.hud_bottom_delta for x in items), 3),
                    audio_db=audio_db,
                    audio_onset=audio_onset,
                )
            )
        return buckets


def format_math_report_text(
    source: str,
    duration_s: float,
    fps: float,
    bucket_seconds: float,
    detector_name: str,
    buckets: list[MathBucket],
    events: list[HighlightEvent],
    max_lines: int = 80,
) -> str:
    """Format compact LLM-friendly math timeline + detected highlight candidates."""
    lines = [
        f"VIDEO_MATH: {source} | duration={duration_s:.2f}s | fps={fps:.1f} | bucket={bucket_seconds:.2f}s | detector={detector_name}",
    ]
    if events:
        lines.append(f"DETECTED_EVENTS ({len(events)}):")
        for idx, ev in enumerate(events, start=1):
            lines.append(
                f"  E{idx} [{ev.t_start:.2f}s - {ev.t_end:.2f}s] peak={ev.peak_time:.2f}s "
                f"type={ev.event_type} score={ev.score:.2f} "
                f"signals={','.join(ev.signals)} edit={ev.suggested_edit}"
            )
    else:
        lines.append("DETECTED_EVENTS (0): No high-confidence spikes detected.")

    lines.append("TIMELINE_CURVE:")
    step = max(1, math.ceil(len(buckets) / max(1, max_lines)))
    for b in buckets[::step]:
        flags: list[str] = []
        if b.motion_peak >= 0.45:
            flags.append("FAST")
        if b.dir_reversal >= 0.35:
            flags.append("FLIP")
        if b.scene_delta >= 0.45:
            flags.append("FLASH/CUT")
        if b.audio_onset >= 0.55:
            flags.append("BEAT/BANG")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        lines.append(
            f"  {b.t_start:06.2f}-{b.t_end:06.2f}s | mot={b.motion_mag:.2f}(pk={b.motion_peak:.2f},{b.dominant_dir}) "
            f"| rev={b.dir_reversal:.2f} | dlt={b.scene_delta:.2f} ctr={b.center_delta:.2f} "
            f"hudTR={b.hud_top_right_delta:.2f} | aud={b.audio_db:.1f}dB ons={b.audio_onset:.2f}{flag_str}"
        )
    return "\n".join(lines)


def analyze_video_file(
    file_path: str | Path,
    bucket_seconds: float = 0.25,
    detector: str = "general",
    sample_fps: float = 20.0,
    max_seconds: float | None = None,
    analysis_width: int = 240,
    analysis_height: int = 135,
) -> VideoMathReport:
    """Analyze an entire video file locally into math buckets + highlight events (zero vision tokens)."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Video file not found: {path}")

    acc = FrameMathAccumulator(width=analysis_width, height=analysis_height)
    source_fps = 30.0
    duration_s = 0.0
    last_sampled_t = -1e9
    min_dt = 1.0 / max(2.0, sample_fps)

    with av.open(str(path)) as container:
        video_streams = [s for s in container.streams if s.type == "video"]
        if not video_streams:
            raise ValueError(f"No video stream found in {path}")
        vstream = video_streams[0]
        if vstream.average_rate:
            source_fps = float(vstream.average_rate)
        if container.duration:
            duration_s = float(container.duration / 1_000_000.0)
        if max_seconds is not None and max_seconds > 0:
            duration_s = min(duration_s, max_seconds) if duration_s > 0 else max_seconds

        time_base = float(vstream.time_base) if vstream.time_base else (1.0 / max(1.0, source_fps))
        frame_idx = 0
        for frame in container.decode(vstream):
            if frame.pts is not None:
                t = float(frame.pts * time_base)
            else:
                t = frame_idx / max(1.0, source_fps)
            frame_idx += 1
            if max_seconds is not None and max_seconds > 0 and t > max_seconds:
                break
            duration_s = max(duration_s, t)
            if t - last_sampled_t < min_dt and frame_idx > 1:
                continue
            last_sampled_t = t
            bgr = frame.to_ndarray(format="bgr24")
            acc.push_frame(t, bgr)

    duration_s = max(duration_s, bucket_seconds)
    num_buckets = max(1, int(math.ceil(duration_s / max(0.05, bucket_seconds))))
    audio_buckets = extract_audio_buckets(path, num_buckets=num_buckets, bucket_seconds=bucket_seconds)
    buckets = acc.build_buckets(
        bucket_seconds=bucket_seconds,
        duration_s=duration_s,
        audio_buckets=audio_buckets,
    )

    det_fn = get_detector(detector)
    events = det_fn(buckets)
    compact = format_math_report_text(
        source=path.name,
        duration_s=duration_s,
        fps=source_fps,
        bucket_seconds=bucket_seconds,
        detector_name=detector,
        buckets=buckets,
        events=events,
    )
    return VideoMathReport(
        source=str(path),
        duration_s=round(duration_s, 3),
        fps=round(source_fps, 2),
        bucket_seconds=bucket_seconds,
        detector=detector,
        total_buckets=len(buckets),
        buckets=buckets,
        events=events,
        compact_text=compact,
    )
