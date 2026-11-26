"""FPS combat / quickscope / elimination detector driven by flick-flash-HUD-audio math."""

from __future__ import annotations

from kinetic_mcp.models import HighlightEvent, MathBucket
from kinetic_mcp.perception.detectors.base import merge_candidate_windows, register_detector


@register_detector("fps_combat")
def detect_fps_combat(buckets: list[MathBucket]) -> list[HighlightEvent]:
    """Detect sniper quickscopes and combat eliminations from physics + HUD + audio curves."""
    if not buckets:
        return []

    candidates: list[tuple[int, float, list[str], str]] = []
    n = len(buckets)

    for idx, b in enumerate(buckets):
        lo = max(0, idx - 1)
        hi = min(n, idx + 2)
        window = buckets[lo:hi]

        max_flick = max(w.motion_peak for w in window)
        has_scope_or_flick = any(
            w.dominant_dir in ("zoom_in", "pan_left", "pan_right") and w.motion_peak >= 0.30
            for w in window
        )
        # Distinguish a genuine center hitmarker/flash from uniform whole-screen camera panning
        center_excess = b.center_delta - (b.scene_delta * 0.72)
        has_center_flash = b.center_delta >= 0.35 and (center_excess >= 0.14 or b.center_delta >= 0.85)
        has_post_flick_stop = b.motion_mag < max_flick * 0.65 if max_flick >= 0.35 else False

        max_hud = max(w.hud_top_right_delta for w in window)
        max_audio = max(w.audio_onset for w in window)

        score = 0.0
        sigs: list[str] = []

        if max_flick >= 0.38 or has_scope_or_flick:
            score += 0.22
            sigs.append("flick_or_scope_motion")
        if has_post_flick_stop:
            score += 0.10
            sigs.append("flick_lock_stop")
        if has_center_flash:
            score += 0.30
            sigs.append("center_hit_flash")
        if max_audio >= 0.42:
            score += 0.25
            sigs.append("gunshot_or_kill_audio")
        if max_hud >= 0.18 or b.hud_bottom_delta >= 0.22:
            score += 0.18
            sigs.append("hud_killfeed_delta")

        # Require focal center flash + corroborating shot/flick signal so continuous panning is ignored
        if has_center_flash and score >= 0.58:
            candidates.append((idx, min(1.0, score), sigs, "speed_ramp_into_shot + cut_after_impact"))

    return merge_candidate_windows(
        candidates,
        buckets,
        max_gap_buckets=0,
        pad_before=0.35,
        pad_after=0.35,
        event_type="fps_elimination_candidate",
    )
