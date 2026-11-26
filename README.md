<p align="center">
  <h1 align="center">⚡ Kinetic MCP</h1>
  <p align="center">
    <strong>Give AI agents real hands and eyes on Windows — no screenshots.</strong>
  </p>
  <p align="center">
    <a href="#installation">Install</a> · <a href="#quick-start">Quick Start</a> · <a href="#tool-reference">Tools</a> · <a href="#architecture">Architecture</a> · <a href="#safety">Safety</a>
  </p>
</p>

---

Kinetic MCP is a standalone [Model Context Protocol](https://modelcontextprotocol.io/) server that gives any AI agent **screenshot-free spatial awareness**, **smooth continuous mouse/keyboard control**, and **local video-to-math analysis** on Windows.

It works with any MCP client — Antigravity, Claude Desktop, Cursor, or your own — and composes cleanly alongside app-specific MCPs (Blender MCP, Photoshop MCP, etc.).

## Why?

| Problem | Kinetic MCP Fix |
|---|---|
| AI agents take screenshots → send to cloud → guess pixel coords → click → wait → repeat. Slow, expensive, imprecise. | **ASCII spatial grid** from Windows UI Automation. The AI "sees" every button, slider, and region as text coordinates. Zero vision tokens. |
| Agents can only do discrete click-wait-screenshot loops. No drawing, no dragging, no scrubbing timelines. | **Continuous motor control**: smooth Bézier moves, circle/path drawing, timeline scrubbing, drag-and-drop — all via native Win32 `SendInput`. |
| Analyzing video for edits requires sending frames to the LLM. A 30s clip at 1 FPS = 30 images = thousands of tokens, and it still misses fast action. | **Local video-to-math engine**: Farneback optical flow, 3×3 spatial deltas, audio onset detection, pluggable highlight detectors. Zero frames leave your machine. |

## Installation

```powershell
# Clone and set up
git clone https://github.com/YOUR_USERNAME/kinetic-mcp.git
cd kinetic-mcp

# Create venv and install
uv venv --system-site-packages .venv
.venv\Scripts\activate
pip install -e ".[dev]"

# Verify
pytest
```

**Requirements:** Python ≥ 3.11, Windows 10/11

## Quick Start

### 1. Add to your MCP client

```json
{
  "mcpServers": {
    "kinetic-mcp": {
      "command": "python",
      "args": ["-m", "kinetic_mcp.server"]
    }
  }
}
```

### 2. Try it

```text
Agent → screen_get_grid(window_title="Paint")

Paint 48x16 ^(24,8)
+------------------------------------------------+
|[1:File]  [2:Home]  [3:View]                    |00
|                                                |01
|                                                |02
|  ...                                           |...
+------------------------------------------------+
[1] File @(3,0) menubar
[2] Home @(10,0) tab
[3] View @(17,0) tab

Agent → motor_click_element(element_id="2")
Agent → motor_draw_circle(cx=400, cy=300, radius=80)
```

The agent reads the UI as text, clicks elements by ID, and draws with real mouse movements. No screenshots at any step.

## Tool Reference

### 🔍 Spatial Eyes (5 tools)

| Tool | What it does |
|---|---|
| `screen_list_windows` | List all open visible windows with titles, PIDs, and screen bounds |
| `screen_focus_window` | Bring a window to the foreground |
| `screen_get_grid` | Render a window's UIA controls + saved regions as an ASCII coordinate grid |
| `screen_label_region` | Teach and save a named UI region (e.g. `preview_box`, `timeline_track`) |
| `profile_list` | List all saved app profiles and their regions/shortcuts |

### 🤚 Motor Cortex (9 tools)

| Tool | What it does |
|---|---|
| `motor_move_grid` | Smooth Bézier glide to a grid cell |
| `motor_click_element` | Glide to a UIA element by ID and click |
| `motor_drag` | Hold-and-drag between two points (pixels or grid) |
| `motor_draw_circle` | Draw a continuous circle or arc |
| `motor_draw_path` | Draw a smooth stroke through waypoints |
| `motor_scrub_timeline` | Drag a timeline playhead with decelerating ease-out |
| `motor_hotkey` | Press keyboard shortcuts via `SendInput` |
| `motor_type_text` | Type Unicode text |
| `motor_scroll` | Scroll mouse wheel |

### 📊 Video & Preview Math (4 tools)

| Tool | What it does |
|---|---|
| `video_list_detectors` | List available highlight detectors (general, fps_combat, sports_dribble) |
| `video_analyze_math` | Analyze a video file → motion curves, audio onsets, highlight events |
| `preview_watch_math` | Watch a live editor preview box and return real-time motion math |
| `preview_sanity_snapshot` | Optional 1% visual check: grab one downscaled JPEG of a region |

## Architecture

```text
AI Host (Antigravity / Claude / Cursor)
                │  MCP stdio
                ▼
┌──────────────────────────────────────────────┐
│  MCP Router (server.py) — 18 tools           │
├──────────┬──────────────┬────────────────────┤
│ Spatial  │ Motor Cortex │ Video/Audio Math   │
│  Eyes    │              │   Engine           │
├──────────┼──────────────┼────────────────────┤
│ UIA tree │ Win32 Send-  │ Farneback optical  │
│ ASCII    │  Input       │  flow + 3×3 HUD    │
│  grid    │ Bézier paths │ PyAV audio FFT     │
│ Profile  │ Safety       │  onset detection   │
│  store   │  guardrails  │ Pluggable          │
│          │              │  detectors         │
└──────────┴──────────────┴────────────────────┘
```

**Key design principles:**
- **Zero screenshots to the LLM.** All perception is text grids or local math. Only `preview_sanity_snapshot` saves a local JPEG for optional aesthetic checks.
- **Composable, not monolithic.** Works alongside Blender MCP, DaVinci MCP, etc. Kinetic handles cross-app motor + spatial. App MCPs handle deep scene graphs.
- **Safety always on.** Corner fail-safe, physical override detection, bounds clamping. See [Safety](#safety).

## Safety

Three hardware-level guardrails protect every motor stroke:

| Guardrail | How it works |
|---|---|
| **Corner Fail-Safe** | Slam your physical mouse to screen corner `(0,0)` → instantly aborts the stroke and releases all held buttons |
| **Physical Override** | Move your mouse during an AI stroke → detected via drift measurement → immediate abort + button release |
| **Bounds Clamping** | All trajectory points are clamped inside the target window bounds. Normalized `[0.0, 1.0]` profiles survive window resizes |

Buttons are **always** released in `finally` blocks — even on crash.

## Extending Kinetic MCP

### App Profiles

Teach kinetic-mcp any app's layout by calling `screen_label_region`. Regions are saved as normalized ratios in `profiles/<app_id>.json`:

```json
{
  "app_id": "capcut",
  "window_title_contains": ["CapCut"],
  "regions": {
    "preview_box": { "left_ratio": 0.35, "top_ratio": 0.05, ... },
    "timeline_track": { "left_ratio": 0.0, "top_ratio": 0.65, ... }
  }
}
```

### Highlight Detectors

Add domain-specific video analysis by creating a detector in `src/kinetic_mcp/perception/detectors/`:

```python
from kinetic_mcp.perception.detectors.base import BaseDetector

class MyDetector(BaseDetector):
    name = "my_domain"
    description = "Detects my-domain-specific highlights"

    def detect(self, buckets, audio_buckets=None):
        # Score MathBucket sequences → HighlightEvent list
        ...
```

Built-in detectors: `general`, `fps_combat`, `sports_dribble`.

## Contributing

1. Fork → branch → `pip install -e ".[dev]"` → `pytest`
2. Add tools to `server.py`, spatial logic to `spatial/`, motor primitives to `motor/`, detectors to `perception/detectors/`
3. Keep zero-screenshot and safety-always-on principles
4. PR with tests

## License

[MIT](LICENSE)
