from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    status = Signal(str, str)
    listening_changed = Signal(bool)
    microphone_changed = Signal(bool)
    speech_activity_changed = Signal(bool)

    transcript_delta = Signal(str, str)
    transcript_final = Signal(str, str, float)
    manual_transcript_boundary_finished = Signal(str, bool)
    transcribing_changed = Signal(bool)
    latest_text = Signal(str)

    ai_started = Signal(str, str, str)
    ai_delta = Signal(str, str, str)
    ai_finished = Signal(str, str, str)
    notes_updated = Signal(str)
