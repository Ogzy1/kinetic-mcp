# Kinetic MCP — Architecture

## 5-Layer Design

```text
┌─────────────────────────────────────────────────────────────────┐
│                 AI HOST (Antigravity / Claude / Cursor)         │
│            Mounts kinetic-mcp alongside app-specific MCPs       │
└──────────────────────────┬──────────────────────────────────────┘
                           │  MCP stdio (FastMCP + JSON-RPC)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: MCP ROUTER  (server.py)                               │
│  18 tools: screen_* | profile_* | motor_* | video_* | preview_*│
│  EngineRuntime DI container → resolves windows, profiles, motor │
├───────────────┬──────────────────────┬──────────────────────────┤
│               │                      │                          │
│   Layer 2     │     Layer 3          │   Layer 4 + 5            │
│   SPATIAL     │     MOTOR CORTEX     │   VIDEO/AUDIO MATH       │
│   EYES        │                      │   + LIVE PREVIEW          │
│               │                      │                          │
│ uia_reader.py │ win32_input.py       │ video_math.py            │
│  DPI-aware    │  64-bit SendInput    │  Farneback optical flow  │
│  UIA walker   │  mouse/keyboard      │  3×3 HUD spatial deltas  │
│  window enum  │                      │  zoom/pan classification │
│               │ trajectories.py      │                          │
│ profile_store │  Bézier, circle,     │ audio_math.py            │
│  [0..1] ratio │  polyline, scrub     │  FFT spectral flux       │
│  persistence  │  ease-in-out curves  │  RMS onset detection     │
│               │                      │                          │
│ ascii_grid.py │ safety.py            │ preview_capture.py       │
│  Coordinate-  │  Corner fail-safe    │  DXcam/BitBlt → math     │
│  indexed      │  User override       │  Live preview region     │
│  ASCII map    │  Bounds clamping     │                          │
│  + element    │  Focus lock          │ detectors/               │
│  lookup       │  Button release in   │  general, fps_combat,    │
│               │  finally blocks      │  sports_dribble          │
└───────────────┴──────────────────────┴──────────────────────────┘
```

## Data Flow

1. **Agent calls `screen_get_grid("CapCut")`**
   - UIA walker reads control tree (0.35s budget, max 75 elements, depth 5)
   - Profile store loads `profiles/capcut.json` normalized regions
   - ASCII grid compiler merges elements + regions + cursor into compact text map
   - **Result: ~400-800 tokens of text. Zero screenshots.**

2. **Agent calls `motor_drag(x1, y1, x2, y2)`**
   - Bezier path generator creates 30-50 smooth waypoints with ease-in-out
   - Safety policy checks corner position, drift, bounds, focus on every step
   - Win32 SendInput moves the real cursor along the path at 90 Hz
   - Mouse button held in try/finally for guaranteed release

3. **Agent calls `video_analyze_math("clip.mp4")`**
   - PyAV decodes at 240×135 resolution, 30 FPS
   - Per-bucket: Farneback optical flow → motion magnitude, 3×3 grid deltas, direction reversal
   - Per-bucket: FFT spectral flux + RMS energy → audio onset detection
   - Detector plugin scores buckets → HighlightEvent candidates
   - **Result: compact timestamped math. Zero video frames to the LLM.**

## Extension Points

1. **App Profiles** (`profiles/*.json`): Normalized UI layouts and shortcuts. Created via `screen_label_region` or manually.
2. **Detectors** (`perception/detectors/*.py`): Subclass `BaseDetector` and implement `detect(buckets) → events`.
3. **Input Backends**: Implement `InputBackend` interface for testing or cross-platform support.

## File Map

```
src/kinetic_mcp/
├── __init__.py          # Package init
├── config.py            # EngineConfig dataclass
├── models.py            # Pydantic schemas (PixelBox, MathBucket, etc.)
├── server.py            # FastMCP server + 18 tools + EngineRuntime
├── spatial/
│   ├── uia_reader.py    # DPI-aware UIA walker + window enumeration
│   ├── profile_store.py # JSON persistence for normalized app regions
│   └── ascii_grid.py    # ASCII grid compiler
├── motor/
│   ├── win32_input.py   # Win32 SendInput backend
│   ├── trajectories.py  # Path generators + MotorCortex executor
│   └── safety.py        # Safety policy + guardrail checks
└── perception/
    ├── video_math.py    # Farneback flow accumulator
    ├── audio_math.py    # FFT spectral flux onset detector
    ├── preview_capture.py # DXcam/BitBlt live capture → math
    └── detectors/       # Pluggable highlight detectors
        ├── base.py
        ├── general.py
        ├── fps_combat.py
        └── sports_dribble.py
```
