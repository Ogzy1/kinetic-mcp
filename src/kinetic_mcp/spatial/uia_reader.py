"""Windows UI Automation (UIA) and Win32 window inspection with safe timeouts."""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass

from kinetic_mcp.models import PixelBox

try:
    import win32api
    import win32con
    import win32gui
    import win32process
except ImportError:  # pragma: no cover - non-Windows safety
    win32api = None
    win32con = None
    win32gui = None
    win32process = None

try:
    import uiautomation as auto
except ImportError:  # pragma: no cover
    auto = None


_DPI_INITIALIZED = False

_INTERACTIVE_CONTROL_TYPES = {
    "ButtonControl",
    "CheckBoxControl",
    "ComboBoxControl",
    "EditControl",
    "HyperlinkControl",
    "ListItemControl",
    "MenuItemControl",
    "RadioButtonControl",
    "ScrollBarControl",
    "SliderControl",
    "SplitButtonControl",
    "TabItemControl",
    "TreeItemControl",
}


@dataclass(slots=True)
class WindowInfo:
    hwnd: int
    title: str
    process_name: str
    pid: int
    box: PixelBox


def enable_dpi_awareness() -> None:
    """Enable Per-Monitor V2 DPI awareness so Win32 and UIA pixel coordinates match."""
    global _DPI_INITIALIZED
    if _DPI_INITIALIZED:
        return
    try:
        user32 = ctypes.windll.user32
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    _DPI_INITIALIZED = True


def get_virtual_screen_box() -> PixelBox:
    """Return physical desktop bounds spanning all monitors."""
    enable_dpi_awareness()
    if win32api is None:
        return PixelBox(left=0, top=0, right=1920, bottom=1080)
    left = int(win32api.GetSystemMetrics(76))   # SM_XVIRTUALSCREEN
    top = int(win32api.GetSystemMetrics(77))    # SM_YVIRTUALSCREEN
    width = int(win32api.GetSystemMetrics(78))  # SM_CXVIRTUALSCREEN
    height = int(win32api.GetSystemMetrics(79)) # SM_CYVIRTUALSCREEN
    return PixelBox(left=left, top=top, right=left + width, bottom=top + height)


def get_primary_screen_box() -> PixelBox:
    """Return primary monitor bounds in physical pixels."""
    enable_dpi_awareness()
    if win32api is None:
        return PixelBox(left=0, top=0, right=1920, bottom=1080)
    return PixelBox(
        left=0,
        top=0,
        right=int(win32api.GetSystemMetrics(0)),
        bottom=int(win32api.GetSystemMetrics(1)),
    )


def require_cursor_pos() -> tuple[int, int]:
    """Return live cursor position or raise an actionable error if Windows denies desktop input."""
    enable_dpi_awareness()
    if win32api is None:
        return (0, 0)
    try:
        pt = win32api.GetCursorPos()
        return (int(pt[0]), int(pt[1]))
    except Exception as exc:
        raise RuntimeError(
            "Windows denied live cursor access (GetCursorPos Access Denied). "
            "Ensure the desktop is unlocked, no UAC prompt is active, and kinetic-mcp is running "
            "in an interactive Windows session rather than a sandboxed shell."
        ) from exc


def get_cursor_pos() -> tuple[int, int]:
    """Return current Windows cursor (x, y) in physical pixels, degrading gracefully if desktop is locked."""
    try:
        return require_cursor_pos()
    except Exception:
        if win32gui is not None:
            try:
                _flags, _hcursor, pt = win32gui.GetCursorInfo()
                return (int(pt[0]), int(pt[1]))
            except Exception:
                pass
        return (0, 0)


def _process_name_for_pid(pid: int) -> str:
    if pid <= 0:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_uint(len(buf))
        ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        if not ok:
            return ""
        full_path = buf.value
        return full_path.rsplit("\\", 1)[-1]
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def get_window_info(hwnd: int) -> WindowInfo | None:
    if win32gui is None or not hwnd or not win32gui.IsWindow(hwnd):
        return None
    title = win32gui.GetWindowText(hwnd) or ""
    rect = win32gui.GetWindowRect(hwnd)
    box = PixelBox(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
    pid = 0
    if win32process is not None:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    return WindowInfo(
        hwnd=int(hwnd),
        title=title,
        process_name=_process_name_for_pid(pid),
        pid=int(pid),
        box=box,
    )


def get_foreground_window_info() -> WindowInfo | None:
    enable_dpi_awareness()
    if win32gui is None:
        return None
    hwnd = win32gui.GetForegroundWindow()
    return get_window_info(hwnd)


def list_windows() -> list[WindowInfo]:
    """Return visible top-level windows with non-empty titles and positive size."""
    enable_dpi_awareness()
    if win32gui is None:
        return []
    results: list[WindowInfo] = []

    def _callback(hwnd: int, _extra: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
            return True
        info = get_window_info(hwnd)
        if info and info.title.strip() and info.box.width > 50 and info.box.height > 50:
            results.append(info)
        return True

    win32gui.EnumWindows(_callback, None)
    return results


def find_window(title_contains: str) -> WindowInfo | None:
    needle = title_contains.strip().lower()
    if not needle:
        return get_foreground_window_info()
    for win in list_windows():
        if needle in win.title.lower() or needle in win.process_name.lower():
            return win
    return None


def focus_window(hwnd: int) -> bool:
    if win32gui is None or win32con is None or not hwnd:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def collect_uia_elements(
    hwnd: int | None,
    target_box: PixelBox,
    max_elements: int = 75,
    max_depth: int = 5,
    budget_seconds: float = 0.35,
) -> list[dict]:
    """Walk the UIA tree under a window within a strict time/depth budget.

    Returns lightweight dicts: {label, role, box}. Never raises on broken UIA providers.
    """
    enable_dpi_awareness()
    if auto is None:
        return []

    deadline = time.perf_counter() + budget_seconds
    collected: list[dict] = []
    seen_rects: set[tuple[int, int, int, int, str]] = set()

    try:
        with auto.UIAutomationInitializerInThread():
            root = auto.ControlFromHandle(hwnd) if hwnd else auto.GetRootControl()
            if root is None:
                return []

            queue: list[tuple[object, int]] = [(root, 0)]
            while queue and len(collected) < max_elements:
                if time.perf_counter() > deadline:
                    break
                ctrl, depth = queue.pop(0)
                if depth > 0:
                    try:
                        rect = ctrl.BoundingRectangle
                        ctype = ctrl.ControlTypeName or "Control"
                        name = (ctrl.Name or "").strip()
                        is_offscreen = bool(getattr(ctrl, "IsOffscreen", False))
                    except Exception:
                        continue

                    if rect and not is_offscreen:
                        l, t, r, b = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
                        w, h = r - l, b - t
                        # Filter empty, giant container, or off-target controls
                        in_bounds = (
                            r > target_box.left
                            and l < target_box.right
                            and b > target_box.top
                            and t < target_box.bottom
                        )
                        is_useful = (
                            (ctype in _INTERACTIVE_CONTROL_TYPES and w >= 6 and h >= 6)
                            or (bool(name) and w >= 12 and h >= 12 and w < target_box.width * 0.85)
                        )
                        if in_bounds and is_useful:
                            clipped = PixelBox(
                                left=max(l, target_box.left),
                                top=max(t, target_box.top),
                                right=min(r, target_box.right),
                                bottom=min(b, target_box.bottom),
                            )
                            if clipped.width > 0 and clipped.height > 0:
                                label = name or ctype.replace("Control", "")
                                key = (clipped.left, clipped.top, clipped.right, clipped.bottom, label[:24])
                                if key not in seen_rects:
                                    seen_rects.add(key)
                                    collected.append(
                                        {
                                            "label": label[:40],
                                            "role": ctype.replace("Control", "").lower(),
                                            "box": clipped,
                                        }
                                    )

                if depth < max_depth:
                    try:
                        children = ctrl.GetChildren()
                        for child in children[:40]:
                            queue.append((child, depth + 1))
                    except Exception:
                        continue
    except Exception:
        return collected

    return collected
