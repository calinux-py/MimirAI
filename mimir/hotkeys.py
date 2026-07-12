from __future__ import annotations

import ctypes
import contextlib
import sys
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter


WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
VK_RETURN = 0x0D
VK_H = 0x48
VK_M = 0x4D


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


class HotkeyManager(QAbstractNativeEventFilter):
    SMART_ASSIST = 101
    TOGGLE_VISIBLE = 102
    TOGGLE_LISTENING = 103

    def __init__(
        self,
        on_smart_assist: Callable[[], None],
        on_toggle_visible: Callable[[], None],
        on_toggle_listening: Callable[[], None],
    ) -> None:
        super().__init__()
        self._callbacks = {
            self.SMART_ASSIST: on_smart_assist,
            self.TOGGLE_VISIBLE: on_toggle_visible,
            self.TOGGLE_LISTENING: on_toggle_listening,
        }
        self._hwnd = 0
        self._registered: list[int] = []

    def register(self, hwnd: int) -> None:
        if not sys.platform.startswith("win") or not hwnd:
            return
        self.unregister()
        self._hwnd = int(hwnd)
        user32 = ctypes.windll.user32
        definitions = [
            (self.SMART_ASSIST, MOD_CONTROL, VK_RETURN),
            (self.TOGGLE_VISIBLE, MOD_CONTROL | MOD_SHIFT, VK_H),
            (self.TOGGLE_LISTENING, MOD_CONTROL | MOD_SHIFT, VK_M),
        ]
        for hotkey_id, modifiers, key in definitions:
            if user32.RegisterHotKey(
                wintypes.HWND(self._hwnd), hotkey_id, modifiers, key
            ):
                self._registered.append(hotkey_id)

    def unregister(self) -> None:
        if not sys.platform.startswith("win") or not self._hwnd:
            return
        user32 = ctypes.windll.user32
        for hotkey_id in self._registered:
            with contextlib.suppress(Exception):
                user32.UnregisterHotKey(wintypes.HWND(self._hwnd), hotkey_id)
        self._registered.clear()

    def nativeEventFilter(self, event_type, message):
        if event_type not in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            return False, 0
        try:
            msg = MSG.from_address(int(message))
        except Exception:
            return False, 0
        if msg.message != WM_HOTKEY:
            return False, 0
        callback = self._callbacks.get(int(msg.wParam))
        if callback:
            callback()
            return True, 0
        return False, 0
