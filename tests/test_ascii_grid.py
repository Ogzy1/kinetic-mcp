"""Tests for spatial ASCII grid mapping and normalized AppProfile persistence."""

from kinetic_mcp.models import PixelBox
from kinetic_mcp.spatial import ProfileStore, build_screen_grid, grid_to_pixel, pixel_to_grid


def test_pixel_grid_roundtrip_and_clamping() -> None:
    box = PixelBox(left=100, top=200, right=1900, bottom=1100)
    col, row = pixel_to_grid(1000, 650, box, 72, 24)
    assert col == 36
    assert row == 12

    px, py = grid_to_pixel(col, row, box, 72, 24)
    assert 980 <= px <= 1040
    assert 630 <= py <= 690

    # Out-of-bounds clamping
    assert pixel_to_grid(-50, -50, box, 72, 24) == (0, 0)
    assert pixel_to_grid(5000, 5000, box, 72, 24) == (71, 23)


def test_profile_store_resize_invariance(tmp_path) -> None:
    store = ProfileStore(tmp_path)
    win_1080 = PixelBox(left=0, top=0, right=1920, bottom=1080)
    preview_1080 = PixelBox(left=480, top=108, right=1440, bottom=648)

    prof = store.upsert_region(
        app_id="CapCut Test",
        region_name="preview_box",
        region_box=preview_1080,
        window_box=win_1080,
        role="preview",
        window_title_hint="CapCut",
    )
    assert prof.app_id == "capcut_test"

    # Reload from disk and project onto a moved/resized 1280x720 window
    reloaded = store.match_profile(window_title="My Edit - CapCut")
    assert reloaded is not None
    win_720 = PixelBox(left=100, top=50, right=1380, bottom=770)
    projected = reloaded.regions["preview_box"].to_pixel_box(win_720)
    assert projected.left == 100 + round(0.25 * 1280)
    assert projected.top == 50 + round(0.10 * 720)
    assert projected.right == 100 + round(0.75 * 1280)
    assert projected.bottom == 50 + round(0.60 * 720)


def test_build_screen_grid_merges_uia_and_profile(tmp_path) -> None:
    store = ProfileStore(tmp_path)
    win = PixelBox(left=0, top=0, right=1000, bottom=500)
    prof = store.upsert_region(
        app_id="paint",
        region_name="canvas",
        region_box=PixelBox(left=100, top=100, right=900, bottom=450),
        window_box=win,
        role="canvas",
    )
    uia = [
        {"label": "Brush", "role": "button", "box": PixelBox(left=40, top=10, right=120, bottom=50)},
        {"label": "Save", "role": "button", "box": PixelBox(left=140, top=10, right=200, bottom=50)},
    ]
    snap = build_screen_grid(
        target_title="Paint",
        target_box=win,
        cursor_px=(500, 250),
        raw_uia_elements=uia,
        profile=prof,
        grid_w=60,
        grid_h=20,
    )
    assert "^" in snap.ascii_map
    assert "[P1:" in snap.ascii_map
    assert "[1:Brush" in snap.ascii_map
    assert len(snap.elements) == 3
