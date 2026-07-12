from __future__ import annotations

import sys
import time
from pathlib import Path

if __package__ is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from mimir import win32
from mimir.assistant import AssistantEngine
from mimir.audio import LoopbackAudioCapture, MicrophoneAudioCapture
from mimir.config import SecretStore, load_settings, save_settings
from mimir.events import EventBus
from mimir.hotkeys import HotkeyManager
from mimir.realtime import RealtimeTranscriber
from mimir.ui import (
    GlassWindow,
    ScreenshotSelectionOverlay,
    SettingsDialog,
    style_context_menu,
)


class MimirController:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.app.setStyle("Fusion")
        self.app.setApplicationName("Mimir")
        self.app.setWindowIcon(_tray_icon())
        self.app.setQuitOnLastWindowClosed(False)
        self.settings = load_settings()
        self.secrets = SecretStore()
        self.api_key = self.secrets.get_api_key()
        self.bus = EventBus()

        self.assistant = AssistantEngine(self.settings, self.bus)
        self.system_transcriber = RealtimeTranscriber(
            self.settings,
            self.bus,
            source_label="SYSTEM",
            status_label="System audio transcription",
        )
        self.microphone_transcriber = RealtimeTranscriber(
            self.settings,
            self.bus,
            source_label="USER",
            status_label="Microphone transcription",
        )
        self.audio = LoopbackAudioCapture(
            self.settings,
            self.system_transcriber.push_audio,
            self.bus.status.emit,
            self.bus.speech_activity_changed.emit,
        )
        self.microphone_audio = MicrophoneAudioCapture(
            self.settings,
            self.microphone_transcriber.push_audio,
            self.bus.status.emit,
            self.bus.speech_activity_changed.emit,
        )

        self.window = GlassWindow(
            self.settings,
            self.bus,
            on_save_key=self.save_api_key,
            on_smart_assist=self.smart_assist,
            on_ask=self.ask,
            on_toggle_listening=self.toggle_listening,
            on_toggle_microphone=self.toggle_microphone,
            on_capture_visual=self.capture_visual_context,
            on_refresh_notes=self.assistant.refresh_notes,
            on_show_settings=self.show_settings,
            get_auto_assist_seconds=self.assistant.next_auto_assist_in_seconds,
            on_postpone_auto_assist=lambda: self.assistant.postpone_auto_assist(5),
            on_hold_auto_assist_countdown=self.assistant.hold_auto_assist_countdown,
            on_release_auto_assist_countdown=self.assistant.release_auto_assist_countdown_hold,
            on_clear_panel_context=self._on_clear_panel_context,
            on_send_current_transcript=self.send_current_transcript,
            on_smarter=self.assistant.smarter,
            on_complete_onboarding=self.complete_onboarding,
            on_set_microphone_enabled=self.set_microphone_enabled,
        )
        self.bus.transcript_final.connect(self.assistant.ingest_transcript)
        self.bus.manual_transcript_boundary_finished.connect(
            self._on_manual_transcript_boundary_finished
        )
        self.bus.listening_changed.connect(self.assistant.set_auto_assist_listening)
        self.bus.listening_changed.emit(True)
        self.bus.microphone_changed.emit(self.settings.microphone_enabled)

        self.hotkeys = HotkeyManager(
            on_smart_assist=self.smart_assist,
            on_toggle_visible=self.toggle_visible,
            on_toggle_listening=self.toggle_listening,
        )
        self.app.installNativeEventFilter(self.hotkeys)
        self.tray = self._build_tray()
        self.tray.show()

        self._topmost_timer = QTimer()
        self._topmost_timer.setInterval(1500)
        self._topmost_timer.timeout.connect(self._maintain_window_flags)
        self._topmost_timer.start()

        self._auto_assist_poll_timer = QTimer()
        self._auto_assist_poll_timer.setInterval(1000)
        self._auto_assist_poll_timer.timeout.connect(self.assistant.poll_auto_assist)

        self._place_window()
        if self.settings.onboarding_completed and self.api_key:
            self.start_services()
            if self.settings.start_compact:
                self.window.show_compact()
            else:
                self.window.show_panel()
        else:
            self.window.show_onboarding(require_key=not bool(self.api_key))
        self.window.show()
        self.hotkeys.register(int(self.window.winId()))
        self._screenshot_overlay: ScreenshotSelectionOverlay | None = None

    def run(self) -> int:
        try:
            return self.app.exec()
        finally:
            self.shutdown()

    def start_services(self) -> None:
        if not self.api_key:
            return
        self.assistant.set_api_key(self.api_key)
        self.system_transcriber.start(self.api_key)
        self.audio.start()
        if self.settings.microphone_enabled:
            self._start_microphone_services()
        self._auto_assist_poll_timer.start()
        self.bus.status.emit("listening", "Starting realtime listener")
        self.bus.listening_changed.emit(True)
        self.bus.microphone_changed.emit(self.settings.microphone_enabled)

    def save_api_key(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self.secrets.set_api_key(self.api_key)
        self.settings.onboarding_completed = True
        save_settings(self.settings)
        self.start_services()

    def complete_onboarding(self) -> None:
        self.settings.onboarding_completed = True
        save_settings(self.settings)
        self.start_services()

    def smart_assist(self) -> None:
        if not self.window.isVisible():
            self.window.show()
        self.window.show_assist()
        self.assistant.smart_assist()
        self._maintain_window_flags()

    def ask(
        self,
        question: str,
        display_title: str | None = None,
        transcript_override: str | None = None,
        ask_deeper: bool = False,
        response_mode: str = "ask",
        nudge: bool = False,
    ) -> None:
        self.assistant.ask(
            question,
            display_title=display_title,
            transcript_override=transcript_override,
            ask_deeper=ask_deeper,
            response_mode=response_mode,
            nudge=nudge,
        )

    def _on_clear_panel_context(self, panel: str) -> None:
        if panel == "transcript":
            self.assistant.clear_transcript_memory()

    def toggle_visible(self) -> None:
        if self.window.isVisible():
            self.window.hide()
        else:
            self.window.show()
            self._maintain_window_flags()

    def toggle_listening(self) -> None:
        if self.audio.is_paused:
            self.audio.resume()
            self.system_transcriber.resume()
            if self.settings.microphone_enabled:
                self._start_microphone_services()
                self.microphone_audio.resume()
                self.microphone_transcriber.resume()
            self.bus.listening_changed.emit(True)
            self.bus.status.emit("listening", "Listening")
        else:
            self.audio.pause()
            self.system_transcriber.pause()
            self.microphone_audio.pause()
            self.microphone_transcriber.pause()
            self.bus.listening_changed.emit(False)
            self.bus.status.emit("paused", "Listening paused")

    def toggle_microphone(self) -> None:
        self.set_microphone_enabled(not self.settings.microphone_enabled)

    def set_microphone_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.settings.microphone_enabled:
            self.bus.microphone_changed.emit(enabled)
            return
        self.settings.microphone_enabled = enabled
        if enabled:
            self._start_microphone_services()
            if self.audio.is_paused:
                self.microphone_audio.pause()
                self.microphone_transcriber.pause()
            self.bus.status.emit("listening", "Microphone transcription enabled")
        else:
            self.microphone_audio.stop()
            self.microphone_transcriber.stop()
            self.bus.status.emit("listening", "Microphone transcription disabled")
        save_settings(self.settings)
        self.bus.microphone_changed.emit(self.settings.microphone_enabled)

    def send_current_transcript(self) -> None:
        request_id = f"manual-{time.monotonic_ns()}"
        self.assistant.begin_manual_transcript_boundary()
        if not self.system_transcriber.request_transcript_boundary(request_id):
            self.assistant.finish_manual_transcript_boundary(send=False)
            self.bus.status.emit(
                "error",
                "Unable to send the current transcript while listening is unavailable",
            )

    def _on_manual_transcript_boundary_finished(
        self, request_id: str, success: bool
    ) -> None:
        self.assistant.finish_manual_transcript_boundary(send=success)

    def capture_visual_context(self) -> None:
        self.bus.status.emit("busy", "Select a screen area")
        self.window.hide()
        QTimer.singleShot(180, self._begin_visual_capture)

    def _begin_visual_capture(self) -> None:
        screens = self.app.screens()
        if not screens:
            self.bus.status.emit("error", "No screen available for capture")
            self.window.show()
            self._maintain_window_flags()
            return
        captures: list[tuple[QRect, QPixmap]] = []
        desktop_geometry = QRect()
        for screen in screens:
            screenshot = screen.grabWindow(0)
            if screenshot.isNull():
                continue
            geometry = screen.geometry()
            captures.append((geometry, screenshot))
            desktop_geometry = (
                geometry
                if desktop_geometry.isNull()
                else desktop_geometry.united(geometry)
            )
        if not captures or desktop_geometry.isNull():
            self.bus.status.emit("error", "Screenshot capture failed")
            self.window.show()
            self._maintain_window_flags()
            return
        overlay = ScreenshotSelectionOverlay(
            captures,
            desktop_geometry,
            on_capture=self._on_visual_capture_selected,
            on_cancel=self._on_visual_capture_cancelled,
        )
        overlay.setGeometry(desktop_geometry)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        overlay.setFocus()
        self._screenshot_overlay = overlay

    def _on_visual_capture_selected(self, image_png: bytes) -> None:
        self._screenshot_overlay = None
        self.window.show()
        self.window.show_panel()
        self._maintain_window_flags()
        self.assistant.describe_visual(image_png)

    def _on_visual_capture_cancelled(self) -> None:
        self._screenshot_overlay = None
        self.bus.status.emit("listening", "")
        self.window.show()
        self._maintain_window_flags()

    def _start_microphone_services(self) -> None:
        if not self.api_key:
            return
        self.microphone_transcriber.start(self.api_key)
        self.microphone_audio.start()

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self.api_key, self.window)
        accepted = dialog.exec()
        if dialog.replay_requested:
            self.window.show_onboarding(require_key=False)
            self._maintain_window_flags()
            return
        if accepted:
            new_key = dialog.apply_to_settings()
            if new_key:
                self.api_key = new_key
                self.secrets.set_api_key(new_key)
                self.assistant.set_api_key(new_key)
            save_settings(self.settings)
            self._maintain_window_flags()

    def shutdown(self) -> None:
        save_settings(self.settings)
        self.hotkeys.unregister()
        self._auto_assist_poll_timer.stop()
        self.audio.stop()
        self.microphone_audio.stop()
        self.system_transcriber.stop()
        self.microphone_transcriber.stop()

    def _place_window(self) -> None:
        if self.settings.x is not None and self.settings.y is not None:
            self.window.move(self.settings.x, self.settings.y)
            return
        screen = self.app.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        self.window.move(
            area.right() - self.settings.compact_width - 32, area.top() + 48
        )

    def _maintain_window_flags(self) -> None:
        if not self.window.isVisible():
            return
        hwnd = int(self.window.winId())
        win32.force_topmost(hwnd)
        win32.enable_acrylic(hwnd, self.settings.acrylic)
        win32.set_capture_exclusion(hwnd, self.settings.capture_exclusion)

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(self.app.windowIcon(), self.app)
        menu = QMenu()
        style_context_menu(menu, self.settings.shell_alpha)
        menu.aboutToShow.connect(
            lambda: style_context_menu(menu, self.settings.shell_alpha)
        )

        def add_action(label: str, callback) -> None:
            action = QAction(label, menu)
            action.triggered.connect(callback)
            menu.addAction(action)

        add_action("Show / Hide", self.toggle_visible)
        add_action("Smart Assist", self.smart_assist)
        add_action("Pause / Resume Listening", self.toggle_listening)
        add_action("Microphone On / Off", self.toggle_microphone)
        menu.addSeparator()
        add_action("Settings", self.show_settings)
        menu.addSeparator()
        add_action("Quit", self.app.quit)
        tray.setContextMenu(menu)
        tray.setToolTip("Mimir — private live assist")
        tray.activated.connect(
            lambda reason: (
                self.toggle_visible() if reason == QSystemTrayIcon.Trigger else None
            )
        )
        return tray


def _tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(55, 95, 255))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(8, 8, 48, 48, 16, 16)
    painter.setBrush(QColor(255, 255, 255))
    painter.drawEllipse(24, 18, 16, 16)
    painter.drawRoundedRect(18, 38, 28, 7, 3, 3)
    painter.end()
    return QIcon(pixmap)


def main() -> int:
    controller = MimirController()
    return controller.run()
