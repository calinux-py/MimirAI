from __future__ import annotations

import ctypes
import contextlib
import sys
from ctypes import wintypes


IS_WINDOWS = sys.platform.startswith("win")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMSBT_NONE = 1
DWMWCP_DONOTROUND = 1
ACCENT_DISABLED = 0


def _hwnd(hwnd: int) -> wintypes.HWND:
    return wintypes.HWND(int(hwnd))


def force_topmost(hwnd: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    with contextlib.suppress(Exception):
        ctypes.windll.user32.SetWindowPos(
            _hwnd(hwnd),
            wintypes.HWND(HWND_TOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )


def set_capture_exclusion(hwnd: int, enabled: bool = True) -> bool:
    if not IS_WINDOWS or not hwnd:
        return False
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else 0
    try:
        ok = ctypes.windll.user32.SetWindowDisplayAffinity(_hwnd(hwnd), affinity)
        if not ok and enabled:
            ok = ctypes.windll.user32.SetWindowDisplayAffinity(_hwnd(hwnd), WDA_MONITOR)
        return bool(ok)
    except Exception:
        return False


def set_click_through(hwnd: int, enabled: bool) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(_hwnd(hwnd), GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(_hwnd(hwnd), GWL_EXSTYLE, style)
    except Exception:
        pass


class ACCENTPOLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_int),
        ("AnimationId", ctypes.c_int),
    ]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


def enable_acrylic(hwnd: int, enabled: bool = True) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    try:
        accent = ACCENTPOLICY()
        accent.AccentState = ACCENT_DISABLED
        accent.AccentFlags = 0
        accent.GradientColor = 0
        accent.AnimationId = 0
        data = WINCOMPATTRDATA()
        data.Attribute = 19
        data.Data = ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p)
        data.SizeOfData = ctypes.sizeof(accent)
        ctypes.windll.user32.SetWindowCompositionAttribute(
            _hwnd(hwnd), ctypes.byref(data)
        )
    except Exception:
        pass

    try:
        dark = ctypes.c_int(0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd(hwnd),
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark),
            ctypes.sizeof(dark),
        )
    except Exception:
        pass

    try:
        backdrop = ctypes.c_int(DWMSBT_NONE)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd(hwnd),
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop),
            ctypes.sizeof(backdrop),
        )
    except Exception:
        pass

    try:
        corner_pref = ctypes.c_int(DWMWCP_DONOTROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd(hwnd),
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner_pref),
            ctypes.sizeof(corner_pref),
        )
    except Exception:
        pass
