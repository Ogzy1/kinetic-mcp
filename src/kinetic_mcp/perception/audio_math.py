"""Fast audio RMS dB and onset/transient extraction from video/audio streams via PyAV + NumPy."""

from __future__ import annotations

import math
from pathlib import Path

import av
import numpy as np


def compute_audio_buckets(
    samples: np.ndarray,
    sample_rate: int,
    num_buckets: int,
    bucket_seconds: float,
) -> list[tuple[float, float]]:
    """Compute (audio_db, audio_onset) per time bucket from mono float32 samples.

    Scans overlapping sub-windows across the entire bucket so brief gunshots or beat hits
    anywhere inside a bucket are never missed.
    """
    if num_buckets <= 0:
        return []
    if samples.size == 0 or sample_rate <= 0:
        return [(-60.0, 0.0) for _ in range(num_buckets)]

    samples = np.asarray(samples, dtype=np.float32).ravel()
    samples_per_bucket = max(1, int(round(sample_rate * bucket_seconds)))

    rms_list: list[float] = []
    onset_raw_list: list[float] = []
    prev_spec: np.ndarray | None = None
    prev_sub_rms = 0.0

    win_size = 512
    hop_size = 256
    hanning = np.hanning(win_size).astype(np.float32)

    for b_idx in range(num_buckets):
        start = b_idx * samples_per_bucket
        end = min(samples.size, start + samples_per_bucket)
        chunk = samples[start:end]
        if chunk.size == 0:
            rms_list.append(0.0)
            onset_raw_list.append(0.0)
            continue

        bucket_rms = float(np.sqrt(np.mean(chunk * chunk)))
        rms_list.append(bucket_rms)

        # Slide overlapping sub-windows across the entire bucket to catch brief impulses
        max_sub_onset = 0.0
        pos = 0
        while pos < chunk.size:
            seg = chunk[pos : pos + win_size]
            if seg.size < win_size:
                seg = np.pad(seg, (0, win_size - seg.size))
            sub_rms = float(np.sqrt(np.mean(seg * seg)))
            spec = np.abs(np.fft.rfft(seg * hanning, n=win_size))

            flux = 0.0
            if prev_spec is not None and prev_spec.shape == spec.shape:
                pos_diff = np.maximum(0.0, spec - prev_spec)
                flux = float(np.sum(pos_diff) / (np.sum(prev_spec) + 1e-2))

            rms_jump = max(0.0, sub_rms - prev_sub_rms) * 8.0
            sub_onset = 0.55 * flux + 0.45 * rms_jump
            if sub_onset > max_sub_onset:
                max_sub_onset = sub_onset

            prev_spec = spec
            prev_sub_rms = 0.7 * prev_sub_rms + 0.3 * sub_rms
            if pos + win_size >= chunk.size:
                break
            pos += hop_size

        onset_raw_list.append(max_sub_onset)

    max_onset = max(onset_raw_list) if onset_raw_list else 1.0
    norm_div = max(0.5, max_onset)

    results: list[tuple[float, float]] = []
    for rms, raw_ons in zip(rms_list, onset_raw_list):
        db = 20.0 * math.log10(max(rms, 1e-3))
        db = round(max(-60.0, min(0.0, db)), 2)
        onset = round(min(1.0, max(0.0, raw_ons / norm_div)), 3)
        results.append((db, onset))
    return results


def extract_audio_buckets(
    file_path: str | Path,
    num_buckets: int,
    bucket_seconds: float,
    target_rate: int = 16000,
) -> list[tuple[float, float]]:
    """Decode audio track in memory using PyAV and summarize into per-bucket (db, onset)."""
    if num_buckets <= 0:
        return []
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Video/audio file not found: {path}")

    chunks: list[np.ndarray] = []
    try:
        with av.open(str(path)) as container:
            audio_streams = [s for s in container.streams if s.type == "audio"]
            if not audio_streams:
                return [(-60.0, 0.0) for _ in range(num_buckets)]
            stream = audio_streams[0]
            resampler = av.AudioResampler(format="flt", layout="mono", rate=target_rate)
            for frame in container.decode(stream):
                for of in resampler.resample(frame):
                    arr = of.to_ndarray().reshape(-1)
                    if arr.size:
                        chunks.append(arr.astype(np.float32, copy=False))
            for of in resampler.resample(None):
                arr = of.to_ndarray().reshape(-1)
                if arr.size:
                    chunks.append(arr.astype(np.float32, copy=False))
    except Exception:
        return [(-60.0, 0.0) for _ in range(num_buckets)]

    if not chunks:
        return [(-60.0, 0.0) for _ in range(num_buckets)]

    samples = np.concatenate(chunks)
    return compute_audio_buckets(samples, target_rate, num_buckets, bucket_seconds)
