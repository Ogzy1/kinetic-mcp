"""Low-level DPI-aware Win32 SendInput driver plus a deterministic mock backend for tests."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Protocol

from kinetic_mcp.models import PixelBox
from kinetic_mcp.spatial.uia_reader import enable_dpi_awareness, get_virtual_screen_box

# Win32 SendInput constants
_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1

_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000
_MOUSEEVENTF_VIRTUALDESK = 0x4000
_MOUSEEVENTF_ABSOLUTE = 0x8000

_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_KEYEVENTF_SCANCODE = 0x0008

_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


_VK_MAP: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
    "lwin": 0x5B,
    "multiply": 0x6A,
    "add": 0x6B,
    "subtract": 0x6D,
    ";": 0xBA,
    "=": 0xBB,
    ",": 0xBC,
    "-": 0xBD,
    ".": 0xBE,
    "/": 0xBF,
    "`": 0xC0,
    "[": 0xDB,
    "\\": 0xDC,
    "]": 0xDD,
    "'": 0xDE,
}
for _i in range(1, 13):
    _VK_MAP[f"f{_i}"] = 0x6F + _i
for _d in "0123456789":
    _VK_MAP[_d] = ord(_d)
for _c in "abcdefghijklmnopqrstuvwxyz":
    _VK_MAP[_c] = ord(_c.upper())

_EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B}


def resolve_vk(key: str) -> int:
    norm = key.strip().lower()
    if norm in _VK_MAP:
        return _VK_MAP[norm]
    if len(norm) == 1:
        return ord(norm.upper())
    raise ValueError(f"Unsupported key name: {key!r}")


def _to_virtual_abs(x: int, y: int, vscreen: PixelBox) -> tuple[int, int]:
    w = max(1, vscreen.width)
    h = max(1, vscreen.height)
    nx = int(round(((x - vscreen.left) * 65535) / max(1, w - 1)))
    ny = int(round(((y - vscreen.top) * 65535) / max(1, h - 1)))
    return min(65535, max(0, nx)), min(65535, max(0, ny))


class InputBackend(Protocol):
    def move_abs(self, x: int, y: int) -> None: ...
    def mouse_down(self, button: str = "left") -> None: ...
    def mouse_up(self, button: str = "left") -> None: ...
    def scroll(self, clicks: int, horizontal: bool = False) -> None: ...
    def hotkey(self, keys: list[str]) -> None: ...
    def type_text(self, text: str) -> None: ...


class Win32InputBackend:
    """Real Windows SendInput backend with Per-Monitor DPI awareness and virtual desktop scaling."""

    def __init__(self) -> None:
        enable_dpi_awareness()
        self._user32 = ctypes.windll.user32

    def _send(self, inputs: list[_INPUT]) -> None:
        if not inputs:
            return
        arr = (_INPUT * len(inputs))(*inputs)
        sent = self._user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
        if sent != len(inputs):
            raise OSError(f"SendInput injected {sent}/{len(inputs)} events")

    def move_abs(self, x: int, y: int) -> None:
        vscreen = get_virtual_screen_box()
        ax, ay = _to_virtual_abs(x, y, vscreen)
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.union.mi = _MOUSEINPUT(
            dx=ax,
            dy=ay,
            mouseData=0,
            dwFlags=_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK,
            time=0,
            dwExtraInfo=0,
        )
        self._send([inp])

    def _mouse_flag(self, button: str, down: bool) -> int:
        btn = button.lower()
        if btn == "left":
            return _MOUSEEVENTF_LEFTDOWN if down else _MOUSEEVENTF_LEFTUP
        if btn == "right":
            return _MOUSEEVENTF_RIGHTDOWN if down else _MOUSEEVENTF_RIGHTUP
        if btn == "middle":
            return _MOUSEEVENTF_MIDDLEDOWN if down else _MOUSEEVENTF_MIDDLEUP
        raise ValueError(f"Unsupported mouse button: {button!r}")

    def mouse_down(self, button: str = "left") -> None:
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.union.mi = _MOUSEINPUT(0, 0, 0, self._mouse_flag(button, True), 0, 0)
        self._send([inp])

    def mouse_up(self, button: str = "left") -> None:
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.union.mi = _MOUSEINPUT(0, 0, 0, self._mouse_flag(button, False), 0, 0)
        self._send([inp])

    def scroll(self, clicks: int, horizontal: bool = False) -> None:
        if clicks == 0:
            return
        flag = _MOUSEEVENTF_HWHEEL if horizontal else _MOUSEEVENTF_WHEEL
        delta = ctypes.c_uint32(int(clicks * 120)).value
        inp = _INPUT(type=_INPUT_MOUSE)
        inp.union.mi = _MOUSEINPUT(0, 0, delta, flag, 0, 0)
        self._send([inp])

    def _key_input(self, vk: int, key_up: bool) -> _INPUT:
        scan = self._user32.MapVirtualKeyW(vk, 0)
        flags = _KEYEVENTF_KEYUP if key_up else 0
        if vk in _EXTENDED_VKS:
            flags |= _KEYEVENTF_EXTENDEDKEY
        inp = _INPUT(type=_INPUT_KEYBOARD)
        inp.union.ki = _KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
        return inp

    def hotkey(self, keys: list[str]) -> None:
        if not keys:
            return
        vks = [resolve_vk(k) for k in keys]
        events = [self._key_input(vk, False) for vk in vks]
        events.extend(self._key_input(vk, True) for vk in reversed(vks))
        self._send(events)

    def type_text(self, text: str) -> None:
        if not text:
            return
        events: list[_INPUT] = []
        for ch in text:
            code = ord(ch)
            down = _INPUT(type=_INPUT_KEYBOARD)
            down.union.ki = _KEYBDINPUT(wVk=0, wScan=code, dwFlags=_KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)
            up = _INPUT(type=_INPUT_KEYBOARD)
            up.union.ki = _KEYBDINPUT(
                wVk=0, wScan=code, dwFlags=_KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP, time=0, dwExtraInfo=0
            )
            events.extend((down, up))
        self._send(events)


@dataclass
class MockInputBackend:
    """In-memory mock input backend for deterministic dry-run and unit tests."""

    cursor_pos: tuple[int, int] = (500, 500)
    events: list[tuple] = field(default_factory=list)
    buttons_held: set[str] = field(default_factory=set)

    def move_abs(self, x: int, y: int) -> None:
        self.cursor_pos = (x, y)
        self.events.append(("move", x, y))

    def mouse_down(self, button: str = "left") -> None:
        self.buttons_held.add(button)
        self.events.append(("mouse_down", button))

    def mouse_up(self, button: str = "left") -> None:
        self.buttons_held.discard(button)
        self.events.append(("mouse_up", button))

    def scroll(self, clicks: int, horizontal: bool = False) -> None:
        self.events.append(("scroll", clicks, horizontal))

    def hotkey(self, keys: list[str]) -> None:
        self.events.append(("hotkey", list(keys)))

    def type_text(self, text: str) -> None:
        self.events.append(("type_text", text))
