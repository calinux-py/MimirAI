from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

import numpy as np

from mimir.config import Settings


AudioCallback = Callable[[bytes], None]
StatusCallback = Callable[[str, str], None]
ActivityCallback = Callable[[bool], None]


class _AudioCaptureBase:
    def __init__(
        self,
        settings: Settings,
        on_audio: AudioCallback,
        on_status: StatusCallback,
        on_speech_activity: ActivityCallback | None = None,
        *,
        thread_name: str,
    ) -> None:
        self.settings = settings
        self._on_audio = on_audio
        self._on_status = on_status
        self._on_speech_activity = on_speech_activity
        self._thread_name = thread_name
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._preroll: deque[bytes] = deque()
        self._speech_active = False
        self._speech_indicator_active = False
        self._speech_indicator_silent_chunks = 0
        self._silent_chunks = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(
            target=self._run, name=self._thread_name, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._set_speech_activity(False)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def pause(self) -> None:
        self._paused.set()
        self._set_speech_activity(False)
        self._on_status("paused", "Listening paused")

    def resume(self) -> None:
        self._paused.clear()
        self._on_status("listening", "Listening")

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                import soundcard as sc

                device = self._resolve_device(sc)
                sample_rate = int(self.settings.audio_sample_rate)
                frames = max(
                    240, int(sample_rate * self.settings.audio_chunk_ms / 1000)
                )
                self._on_status("listening", self._listening_status(device))
                backoff = 1.0

                with self._open_shared_recorder(
                    device, sample_rate, frames
                ) as recorder:
                    while not self._stop.is_set():
                        data = recorder.record(numframes=frames)
                        if self._paused.is_set():
                            time.sleep(self.settings.audio_chunk_ms / 1000)
                            continue
                        pcm = float_audio_to_pcm16(
                            data,
                            source_rate=sample_rate,
                            target_rate=self.settings.realtime_sample_rate,
                        )
                        self._update_speech_activity(data, pcm)
                        if self._should_send_audio(data, pcm):
                            for chunk in self._drain_preroll_before_speech():
                                self._on_audio(chunk)
                            self._on_audio(pcm)
                        elif self.settings.audio_vad_enabled:
                            self._remember_preroll(pcm)
            except Exception as exc:
                self._set_speech_activity(False)
                self._on_status("recovering", f"Audio capture recovering: {exc}")
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 1.7, 10.0)

    @property
    def _channels(self) -> int:
        return 1

    def _resolve_device(self, sc):
        raise NotImplementedError

    def _listening_status(self, device) -> str:
        return f"Listening to {device.name}"

    def _open_shared_recorder(self, device, sample_rate: int, frames: int):
        kwargs = {
            "samplerate": sample_rate,
            "channels": self._channels,
            "blocksize": frames,
            "exclusive_mode": False,
        }
        try:
            return device.recorder(**kwargs)
        except TypeError:
            kwargs.pop("exclusive_mode", None)
            return device.recorder(**kwargs)

    def _update_speech_activity(self, samples: np.ndarray, pcm: bytes) -> None:
        if not pcm:
            self._set_speech_activity(False)
            return

        is_loud = audio_level(samples) >= self._vad_threshold
        if is_loud:
            self._speech_indicator_silent_chunks = 0
            self._set_speech_activity(True)
            return

        if not self._speech_indicator_active:
            return

        self._speech_indicator_silent_chunks += 1
        if self._speech_indicator_silent_chunks > self._chunk_count(
            self.settings.audio_vad_silence_ms
        ):
            self._speech_indicator_silent_chunks = 0
            self._set_speech_activity(False)

    def _set_speech_activity(self, active: bool) -> None:
        if self._speech_indicator_active == active:
            return
        self._speech_indicator_active = active
        if not active:
            self._speech_indicator_silent_chunks = 0
        if self._on_speech_activity is not None:
            self._on_speech_activity(active)

    def _should_send_audio(self, samples: np.ndarray, pcm: bytes) -> bool:
        if not pcm or not self.settings.audio_vad_enabled:
            return bool(pcm)

        is_loud = audio_level(samples) >= self._vad_threshold
        if is_loud:
            self._speech_active = True
            self._silent_chunks = 0
            return True

        if not self._speech_active:
            return False

        self._silent_chunks += 1
        if self._silent_chunks <= self._chunk_count(self.settings.audio_vad_silence_ms):
            return True

        self._speech_active = False
        self._silent_chunks = 0
        return False

    def _remember_preroll(self, pcm: bytes) -> None:
        if not pcm:
            return
        preroll_chunks = self._chunk_count(self.settings.audio_vad_preroll_ms)
        while len(self._preroll) >= preroll_chunks:
            self._preroll.popleft()
        self._preroll.append(pcm)

    def _drain_preroll_before_speech(self) -> list[bytes]:
        if self._silent_chunks != 0:
            return []
        chunks = list(self._preroll)
        self._preroll.clear()
        return chunks

    @property
    def _vad_threshold(self) -> float:
        return max(0.0001, float(self.settings.audio_vad_threshold))

    def _chunk_count(self, duration_ms: int) -> int:
        chunk_ms = max(1, int(self.settings.audio_chunk_ms))
        return max(1, int(duration_ms / chunk_ms))


class LoopbackAudioCapture(_AudioCaptureBase):
    def __init__(
        self,
        settings: Settings,
        on_audio: AudioCallback,
        on_status: StatusCallback,
        on_speech_activity: ActivityCallback | None = None,
    ) -> None:
        super().__init__(
            settings,
            on_audio,
            on_status,
            on_speech_activity,
            thread_name="LoopbackAudioCapture",
        )

    @property
    def _channels(self) -> int:
        return 2

    def _resolve_device(self, sc):
        speaker = sc.default_speaker()
        if speaker is None:
            raise RuntimeError("No default output device")
        return _resolve_loopback_device(sc, speaker)


class MicrophoneAudioCapture(_AudioCaptureBase):
    def __init__(
        self,
        settings: Settings,
        on_audio: AudioCallback,
        on_status: StatusCallback,
        on_speech_activity: ActivityCallback | None = None,
    ) -> None:
        super().__init__(
            settings,
            on_audio,
            on_status,
            on_speech_activity,
            thread_name="MicrophoneAudioCapture",
        )

    def _resolve_device(self, sc):
        microphone = sc.default_microphone()
        if microphone is None:
            raise RuntimeError("No default input microphone")
        return microphone

    def _listening_status(self, device) -> str:
        return f"Microphone listening to {device.name}"


def _resolve_loopback_device(sc, speaker):
    speaker_id = str(getattr(speaker, "id", "") or "")
    speaker_name = str(getattr(speaker, "name", "") or "")
    loopbacks = [
        microphone
        for microphone in sc.all_microphones(include_loopback=True)
        if str(getattr(microphone, "id", "")).startswith("{0.0.0")
    ]
    for microphone in loopbacks:
        if str(getattr(microphone, "id", "")) == speaker_id:
            return microphone
    for microphone in loopbacks:
        if str(getattr(microphone, "name", "")) == speaker_name:
            return microphone
    return sc.get_microphone(id=speaker_id or speaker_name, include_loopback=True)


def float_audio_to_pcm16(
    samples: np.ndarray,
    source_rate: int,
    target_rate: int = 24000,
) -> bytes:
    if samples.size == 0:
        return b""
    audio = _normalize_audio(samples)
    audio = _resample(audio, source_rate, target_rate)
    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767.0).astype(np.int16)
    return pcm.tobytes()


def audio_level(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    audio = _normalize_audio(samples)
    return float(np.sqrt(np.mean(np.square(audio))))


def _normalize_audio(samples: np.ndarray) -> np.ndarray:
    audio = np.asarray(samples, dtype=np.float32)
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    return np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return audio
    if source_rate > target_rate and source_rate % target_rate == 0:
        ratio = source_rate // target_rate
        usable = (len(audio) // ratio) * ratio
        if usable:
            return audio[:usable].reshape(-1, ratio).mean(axis=1)

    duration = len(audio) / float(source_rate)
    target_len = max(1, int(duration * target_rate))
    if len(audio) == 1:
        return np.repeat(audio, target_len)
    source_positions = np.linspace(
        0.0, len(audio) - 1, num=len(audio), dtype=np.float32
    )
    target_positions = np.linspace(
        0.0, len(audio) - 1, num=target_len, dtype=np.float32
    )
    return np.interp(target_positions, source_positions, audio).astype(np.float32)
