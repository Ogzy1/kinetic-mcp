"""General action, hard-cut, and audio-beat highlight detector."""

from __future__ import annotations

from kinetic_mcp.models import HighlightEvent, MathBucket
from kinetic_mcp.perception.detectors.base import merge_candidate_windows, register_detector


@register_detector("general")
def detect_general_highlights(buckets: list[MathBucket]) -> list[HighlightEvent]:
    """Score general visual + audio energy bursts above the clip's baseline."""
    if not buckets:
        return []

    energies = [
        0.42 * min(1.0, b.motion_peak)
        + 0.30 * b.scene_delta
        + 0.28 * b.audio_onset
        for b in buckets
    ]
    baseline = sorted(energies)[len(energies) // 2]
    threshold = max(0.32, baseline + 0.18)

    candidates: list[tuple[int, float, list[str], str]] = []
    for idx, (b, e) in enumerate(zip(buckets, energies)):
        if e < threshold:
            continue
        sigs: list[str] = []
        if b.motion_peak >= 0.45:
            sigs.append(f"motion_burst({b.dominant_dir})")
        if b.scene_delta >= 0.35:
            sigs.append("scene_flash_or_cut")
        if b.audio_onset >= 0.45:
            sigs.append("audio_peak")
        if not sigs:
            sigs.append("composite_energy_spike")
        edit = "beat_cut_or_zoom" if b.audio_onset >= 0.5 else "highlight_cut"
        candidates.append((idx, min(1.0, e * 1.15), sigs, edit))

    return merge_candidate_windows(candidates, buckets, event_type="action_or_beat_peak")
