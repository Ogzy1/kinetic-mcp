"""Persistent normalized AppProfile store for non-UIA creative canvases and UI layouts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from kinetic_mcp.models import AppProfile, NormalizedRegion, PixelBox


def _sanitize_app_id(app_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", app_id.strip().lower()).strip("_")
    if not cleaned:
        raise ValueError("app_id must contain at least one alphanumeric character")
    return cleaned


class ProfileStore:
    """Loads, matches, and persists JSON layout profiles in a directory."""

    def __init__(self, profiles_dir: Path) -> None:
        self.profiles_dir = Path(profiles_dir)
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, app_id: str) -> Path:
        return self.profiles_dir / f"{_sanitize_app_id(app_id)}.json"

    def list_profiles(self) -> list[AppProfile]:
        profiles: list[AppProfile] = []
        for path in sorted(self.profiles_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profiles.append(AppProfile.model_validate(data))
            except Exception:
                continue
        return profiles

    def get_profile(self, app_id: str) -> AppProfile | None:
        path = self._path_for(app_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return AppProfile.model_validate(data)

    def match_profile(self, window_title: str = "", process_name: str = "") -> AppProfile | None:
        title_l = window_title.lower()
        proc_l = process_name.lower()
        for profile in self.list_profiles():
            if proc_l and any(p.lower() == proc_l for p in profile.process_names):
                return profile
            if title_l and any(sub.lower() in title_l for sub in profile.window_title_contains):
                return profile
        return None

    def save_profile(self, profile: AppProfile) -> Path:
        safe_id = _sanitize_app_id(profile.app_id)
        profile = profile.model_copy(update={"app_id": safe_id})
        path = self._path_for(safe_id)
        path.write_text(profile.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def upsert_region(
        self,
        app_id: str,
        region_name: str,
        region_box: PixelBox,
        window_box: PixelBox,
        role: str = "region",
        description: str = "",
        window_title_hint: str = "",
        process_name: str = "",
    ) -> AppProfile:
        """Normalize an absolute pixel region against window_box and persist it."""
        if window_box.width <= 0 or window_box.height <= 0:
            raise ValueError("window_box must have positive width and height")

        def _clamp01(v: float) -> float:
            return round(min(1.0, max(0.0, v)), 4)

        left_r = _clamp01((region_box.left - window_box.left) / window_box.width)
        top_r = _clamp01((region_box.top - window_box.top) / window_box.height)
        right_r = _clamp01((region_box.right - window_box.left) / window_box.width)
        bottom_r = _clamp01((region_box.bottom - window_box.top) / window_box.height)

        if right_r <= left_r or bottom_r <= top_r:
            raise ValueError("region_box must overlap window_box with positive area")

        profile = self.get_profile(app_id)
        if profile is None:
            titles = [window_title_hint] if window_title_hint else []
            procs = [process_name] if process_name else []
            profile = AppProfile(
                app_id=_sanitize_app_id(app_id),
                window_title_contains=titles,
                process_names=procs,
            )
        else:
            if window_title_hint and window_title_hint not in profile.window_title_contains:
                profile.window_title_contains.append(window_title_hint)
            if process_name and process_name not in profile.process_names:
                profile.process_names.append(process_name)

        profile.regions[region_name] = NormalizedRegion(
            name=region_name,
            role=role,
            left_ratio=left_r,
            top_ratio=top_r,
            right_ratio=right_r,
            bottom_ratio=bottom_r,
            description=description,
        )
        self.save_profile(profile)
        return profile
