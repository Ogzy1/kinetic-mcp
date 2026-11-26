"""Sports zigzag / ankle-breaker dribble detector driven by direction reversal and center energy."""

from __future__ import annotations

from kinetic_mcp.models import HighlightEvent, MathBucket
from kinetic_mcp.perception.detectors.base import merge_candidate_windows, register_detector


@register_detector("sports_dribble")
def detect_sports_dribble(buckets: list[MathBucket]) -> list[HighlightEvent]:
    """Detect rapid lateral velocity reversals (zigzag cuts, ankle-breakers, explosive breaks)."""
    if not buckets:
        return []

    candidates: list[tuple[int, float, list[str], str]] = []
    n = len(buckets)

    for idx, b in enumerate(buckets):
        lo = max(0, idx - 1)
        hi = min(n, idx + 2)
        window = buckets[lo:hi]

        max_rev = max(w.dir_reversal for w in window)
        max_mot = max(w.motion_peak for w in window)
        max_ctr = max(w.center_delta for w in window)
        max_aud = max(w.audio_onset for w in window)

        score = 0.0
        sigs: list[str] = []

        if max_rev >= 0.30:
            score += 0.42
            sigs.append(f"direction_reversal({max_rev:.2f})")
        if max_mot >= 0.35:
            score += 0.28
            sigs.append("burst_acceleration")
        if max_ctr >= 0.25 or b.scene_delta >= 0.25:
            score += 0.15
            sigs.append("center_action")
        if max_aud >= 0.35:
            score += 0.15
            sigs.append("crowd_or_kick_audio")

        if max_rev >= 0.28 and score >= 0.50:
            candidates.append((idx, min(1.0, score), sigs, "cut_before_cut -> slow_mo_0.5x_at_reversal"))

    return merge_candidate_windows(
        candidates,
        buckets,
        max_gap_buckets=1,
        pad_before=0.30,
        pad_after=0.45,
        event_type="sports_skill_cut_candidate",
    )
