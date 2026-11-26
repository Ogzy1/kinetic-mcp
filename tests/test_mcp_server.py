"""End-to-end tests for FastMCP tool registration and offline workflow execution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import av
import cv2
import numpy as np

from kinetic_mcp.config import EngineConfig
from kinetic_mcp.models import PixelBox
from kinetic_mcp.motor import MockInputBackend
from kinetic_mcp.server import EngineRuntime, create_server
from kinetic_mcp.spatial import WindowInfo


def _call_tool(mcp, name: str, args: dict):
    res = asyncio.run(mcp.call_tool(name, args))
    if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], dict):
        return res[1].get("result", res[1])
    if isinstance(res, (list, tuple)):
        if len(res) == 1 and hasattr(res[0], "text"):
            return json.loads(res[0].text)
        if all(hasattr(item, "text") for item in res):
            return [json.loads(item.text) for item in res]
    return res


def test_mcp_server_tools_and_spatial_motor_workflow(tmp_path) -> None:
    backend = MockInputBackend(cursor_pos=(400, 300))
    fake_win = WindowInfo(
        hwnd=12345,
        title="CapCut - Montage",
        process_name="CapCut.exe",
        pid=999,
        box=PixelBox(left=0, top=0, right=1600, bottom=900),
    )
    cfg = EngineConfig(profiles_dir=tmp_path / "profiles", default_step_hz=60)
    rt = EngineRuntime(
        config=cfg,
        input_backend=backend,
        window_finder=lambda t: fake_win if ("capcut" in t.lower() or not t) else None,
        fg_window_fn=lambda: fake_win,
        desktop_box_fn=lambda: PixelBox(left=0, top=0, right=1600, bottom=900),
        cursor_pos_fn=lambda: backend.cursor_pos,
        uia_collector=lambda hwnd, box: [
            {"label": "Export", "role": "button", "box": PixelBox(left=1450, top=20, right=1560, bottom=55)}
        ],
    )
    mcp = create_server(runtime=rt)

    tools = asyncio.run(mcp.list_tools())
    tool_names = {t.name for t in tools}
    for expected in (
        "screen_get_grid",
        "screen_label_region",
        "motor_click_element",
        "motor_draw_circle",
        "motor_scrub_timeline",
        "video_list_detectors",
        "video_analyze_math",
        "preview_watch_math",
    ):
        assert expected in tool_names

    grid_res = _call_tool(mcp, "screen_get_grid", {"window_title": "CapCut"})
    assert "[1:Export" in grid_res["ascii_map"]

    # Teach a custom region on the grid and confirm it persists
    label_res = _call_tool(
        mcp,
        "screen_label_region",
        {
            "app_id": "capcut",
            "region_name": "timeline_track",
            "col1": 5,
            "row1": 16,
            "col2": 65,
            "row2": 22,
            "role": "timeline",
        },
    )
    assert label_res["saved"] is True

    # Refresh grid -> profile region [P1:timeline] now appears!
    grid_res2 = _call_tool(mcp, "screen_get_grid", {"window_title": "CapCut"})
    assert "capcut" in grid_res2["ascii_map"].lower() or "[P1:" in grid_res2["ascii_map"]
    assert "[P1:" in grid_res2["ascii_map"]

    # Smooth timeline scrub + circle stroke + click UIA element
    scrub_res = _call_tool(mcp, "motor_scrub_timeline", {"delta_x": 150, "duration_s": 0.05})
    assert scrub_res["held_button"] == "left"
    assert backend.buttons_held == set()

    circle_res = _call_tool(mcp, "motor_draw_circle", {"cx": 500, "cy": 400, "radius": 30, "duration_s": 0.05})
    assert circle_res["steps"] >= 16

    click_res = _call_tool(mcp, "motor_click_element", {"element_id": "1", "duration_s": 0.05})
    assert click_res["label"] == "Export"

    det_res = _call_tool(mcp, "video_list_detectors", {})
    assert any(d["name"] == "fps_combat" for d in det_res)

    # Run video_analyze_math directly through MCP tool using a tiny self-contained MP4
    vid_path = Path(tmp_path) / "mcp_test.mp4"
    with av.open(str(vid_path), mode="w") as container:
        vs = container.add_stream("mpeg4", rate=20)
        vs.width, vs.height, vs.pix_fmt = 160, 90, "yuv420p"
        for i in range(30):
            frame_bgr = np.zeros((90, 160, 3), dtype=np.uint8)
            if 10 <= i <= 15:
                cv2.rectangle(frame_bgr, ((i - 10) * 18, 20), ((i - 10) * 18 + 40, 70), (255, 255, 255), -1)
            vf = av.VideoFrame.from_ndarray(frame_bgr, format="bgr24")
            for pkt in vs.encode(vf):
                container.mux(pkt)
        for pkt in vs.encode(None):
            container.mux(pkt)

    vid_res = _call_tool(mcp, "video_analyze_math", {"video_path": str(vid_path), "detector": "general"})
    assert vid_res["total_buckets"] >= 4
    assert "VIDEO_MATH" in vid_res["compact_text"]
