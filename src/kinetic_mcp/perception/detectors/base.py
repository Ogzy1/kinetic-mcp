"""Detector plugin registry and shared bucket-merging helpers."""

from __future__ import annotations

from typing import Callable

from kinetic_mcp.models import HighlightEvent, MathBucket

DetectorFn = Callable[[list[MathBucket]], list[HighlightEvent]]

_DETECTOR_REGISTRY: dict[str, DetectorFn] = {}


def register_detector(name: str):
    """Decorator to register a new video-math highlight detector plugin."""

    def _decorator(fn: DetectorFn) -> DetectorFn:
        _DETECTOR_REGISTRY[name.strip().lower()] = fn
        return fn

    return _decorator


def get_detector(name: str) -> DetectorFn:
    key = name.strip().lower()
    if key not in _DETECTOR_REGISTRY:
        available = ", ".join(sorted(_DETECTOR_REGISTRY))
        raise ValueError(f"Unknown detector {name!r}. Available: {available}")
    return _DETECTOR_REGISTRY[key]


def list_detectors() -> list[str]:
    return sorted(_DETECTOR_REGISTRY)


def list_detector_info() -> list[dict[str, str]]:
    """Return registered detector names with short descriptions for MCP clients."""
    return [
        {
            "name": name,
            "description": ((fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""),
        }
        for name, fn in sorted(_DETECTOR_REGISTRY.items())
    ]


def merge_candidate_windows(
    candidates: list[tuple[int, float, list[str], str]],
    buckets: list[MathBucket],
    max_gap_buckets: int = 1,
    pad_before: float = 0.25,
    pad_after: float = 0.35,
    event_type: str = "highlight",
) -> list[HighlightEvent]:
    """Merge nearby triggered bucket indices into clean edit windows."""
    if not candidates or not buckets:
        return []
    candidates = sorted(candidates, key=lambda item: item[0])
    groups: list[list[tuple[int, float, list[str], str]]] = [[candidates[0]]]
    for cand in candidates[1:]:
        if cand[0] - groups[-1][-1][0] <= max_gap_buckets + 1:
            groups[-1].append(cand)
        else:
            groups.append([cand])

    total_end = buckets[-1].t_end
    events: list[HighlightEvent] = []
    for grp in groups:
        best = max(grp, key=lambda x: x[1])
        first_b = buckets[grp[0][0]]
        last_b = buckets[grp[-1][0]]
        peak_b = buckets[best[0]]
        signals: list[str] = []
        for _, _, sigs, _ in grp:
            for s in sigs:
                if s not in signals:
                    signals.append(s)
        events.append(
            HighlightEvent(
                t_start=round(max(0.0, first_b.t_start - pad_before), 3),
                t_end=round(min(total_end, last_b.t_end + pad_after), 3),
                peak_time=round((peak_b.t_start + peak_b.t_end) / 2.0, 3),
                event_type=event_type,
                score=round(min(1.0, best[1]), 3),
                signals=signals,
                suggested_edit=best[3],
            )
        )
    return events
