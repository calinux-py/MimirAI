from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class TranscriptSegment:
    item_id: str
    text: str
    completed_at: float

    @property
    def clock(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.completed_at))


class ContextMemory:
    def __init__(self, max_age_seconds: int) -> None:
        self._max_age_seconds = max_age_seconds
        self._segments: deque[TranscriptSegment] = deque()
        self._lock = RLock()

    def add(self, item_id: str, text: str, completed_at: float | None = None) -> None:
        if text.lstrip().startswith("[VISUAL]"):
            clean = "\n".join(line.rstrip() for line in text.strip().splitlines())
        else:
            clean = " ".join(text.split())
        if not clean:
            return
        segment = TranscriptSegment(
            item_id=item_id,
            text=clean,
            completed_at=completed_at or time.time(),
        )
        with self._lock:
            self._segments.append(segment)
            self._trim_locked()

    def recent_segments(self) -> list[TranscriptSegment]:
        with self._lock:
            self._trim_locked()
            return list(self._segments)

    def transcript_text(self, max_chars: int = 12000) -> str:
        segments = self.recent_segments()
        lines = [f"[{segment.clock}] {segment.text}" for segment in segments]
        text = "\n".join(lines)
        if len(text) <= max_chars:
            return text
        return text[-max_chars:]

    def latest_thread_text(self, max_segments: int = 8, max_chars: int = 4000) -> str:
        if max_segments <= 0 or max_chars <= 0:
            return ""

        lines = [
            f"[{segment.clock}] {segment.text}"
            for segment in self.recent_segments()[-max_segments:]
        ]
        selected: list[str] = []
        size = 0
        for line in reversed(lines):
            line_size = len(line) + (1 if selected else 0)
            if selected and size + line_size > max_chars:
                break
            if not selected and len(line) > max_chars:
                selected.append(line[-max_chars:])
                break
            selected.append(line)
            size += line_size
        return "\n".join(reversed(selected))

    def latest_text(self) -> str:
        with self._lock:
            return self._segments[-1].text if self._segments else ""

    def clear(self) -> None:
        with self._lock:
            self._segments.clear()

    def _trim_locked(self) -> None:
        cutoff = time.time() - self._max_age_seconds
        while self._segments and self._segments[0].completed_at < cutoff:
            self._segments.popleft()
