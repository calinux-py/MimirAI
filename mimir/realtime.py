from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass

import websockets

from mimir.config import Settings
from mimir.events import EventBus


@dataclass(frozen=True)
class _TranscriptBoundaryCommand:
    request_id: str


@dataclass
class _PendingManualCommit:
    request_id: str
    event_id: str
    future: asyncio.Future[str]


class RealtimeTranscriber:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        *,
        source_label: str = "",
        status_label: str = "Realtime transcription",
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.source_label = source_label.strip().upper()
        self.status_label = status_label
        self._api_key = ""
        self._queue: queue.Queue[bytes | _TranscriptBoundaryCommand | None] = (
            queue.Queue(maxsize=60)
        )
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_audio_bytes = 0
        self._last_commit = 0.0
        self._delta_items_labeled: set[str] = set()
        self._session_update_waiters: deque[asyncio.Future[None]] = deque()
        self._pending_manual_commits: deque[_PendingManualCommit] = deque()
        self._manual_item_requests: dict[str, str] = {}
        self._manual_request_lock = threading.RLock()
        self._active_manual_requests: set[str] = set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, api_key: str) -> None:
        self._api_key = api_key.strip()
        if not self._api_key or self.is_running:
            return
        self._stop.clear()
        self._paused.clear()
        self._drain_queue()
        self._thread = threading.Thread(
            target=self._thread_main, name="RealtimeTranscriber", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._put_audio(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def request_transcript_boundary(self, request_id: str) -> bool:
        request_id = request_id.strip()
        if (
            not request_id
            or not self.is_running
            or self._stop.is_set()
            or self._paused.is_set()
        ):
            return False
        with self._manual_request_lock:
            self._active_manual_requests.add(request_id)
        if self._put_audio(_TranscriptBoundaryCommand(request_id)):
            return True
        with self._manual_request_lock:
            self._active_manual_requests.discard(request_id)
        return False

    def push_audio(self, pcm16_24khz: bytes) -> None:
        if not pcm16_24khz or self._stop.is_set() or self._paused.is_set():
            return
        self._put_audio(pcm16_24khz)

    def _put_audio(self, data: bytes | _TranscriptBoundaryCommand | None) -> bool:
        try:
            self._queue.put_nowait(data)
            return True
        except queue.Full:
            try:
                dropped = self._queue.get_nowait()
                if isinstance(dropped, _TranscriptBoundaryCommand):
                    self._finish_manual_boundary(dropped.request_id, False)
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(data)
                return True
            except queue.Full:
                return False

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _thread_main(self) -> None:
        asyncio.run(self._run())

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
            except Exception:
                if self._stop.is_set():
                    break
                self.bus.status.emit("recovering", f"{self.status_label} reconnecting")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.7, 12.0)

    async def _connect_once(self) -> None:
        url = "wss://api.openai.com/v1/realtime?intent=transcription"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Safety-Identifier": self.settings.safety_identifier,
        }
        async with _connect(url, headers) as ws:
            self._pending_audio_bytes = 0
            self._last_commit = time.monotonic()
            self.bus.transcribing_changed.emit(False)
            await ws.send(json.dumps(self._session_update()))
            await self._wait_for_initial_session_update(ws)
            self.bus.status.emit("listening", f"{self.status_label} connected")
            sender = asyncio.create_task(self._send_audio(ws))
            try:
                async for raw in ws:
                    self._handle_event(json.loads(raw))
                    if self._stop.is_set():
                        break
            finally:
                sender.cancel()
                try:
                    with contextlib.suppress(asyncio.CancelledError):
                        await sender
                finally:
                    self._fail_unresolved_protocol_requests()

    async def _wait_for_initial_session_update(
        self, ws: websockets.WebSocketClientProtocol
    ) -> None:
        while not self._stop.is_set():
            event = json.loads(await ws.recv())
            self._handle_event(event)
            if event.get("type") == "session.updated":
                return

    async def _send_audio(self, ws: websockets.WebSocketClientProtocol) -> None:
        commit_interval = max(0.4, self.settings.transcription_commit_ms / 1000)
        manual_commits = not self._uses_server_vad()
        while not self._stop.is_set():
            data = await asyncio.to_thread(self._queue.get)
            if data is None:
                break
            if isinstance(data, _TranscriptBoundaryCommand):
                await self._force_transcript_boundary(ws, data.request_id)
                continue
            audio = base64.b64encode(data).decode("ascii")
            await ws.send(
                json.dumps({"type": "input_audio_buffer.append", "audio": audio})
            )
            if manual_commits:
                self._pending_audio_bytes += len(data)
                now = time.monotonic()
                if (
                    self._pending_audio_bytes
                    and now - self._last_commit >= commit_interval
                ):
                    await self._commit_audio(ws)
        if manual_commits and self._pending_audio_bytes:
            await self._commit_audio(ws)

    async def _commit_audio(self, ws: websockets.WebSocketClientProtocol) -> None:
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        self._pending_audio_bytes = 0
        self._last_commit = time.monotonic()

    async def _force_transcript_boundary(
        self, ws: websockets.WebSocketClientProtocol, request_id: str
    ) -> None:
        loop = asyncio.get_running_loop()
        commit_event_id = f"manual_boundary_{time.monotonic_ns()}"
        commit_future: asyncio.Future[str] = loop.create_future()
        pending = _PendingManualCommit(request_id, commit_event_id, commit_future)
        self._pending_manual_commits.append(pending)
        committed = False
        vad_disabled = False
        try:
            await self._update_turn_detection(ws, None)
            vad_disabled = True
            if not commit_future.done():
                await ws.send(
                    json.dumps(
                        {
                            "event_id": commit_event_id,
                            "type": "input_audio_buffer.commit",
                        }
                    )
                )
            await asyncio.wait_for(asyncio.shield(commit_future), timeout=5.0)
            committed = True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.bus.status.emit(
                "listening", f"Could not end {self.status_label.lower()}: {exc}"
            )
        finally:
            if pending in self._pending_manual_commits:
                self._pending_manual_commits.remove(pending)
            if vad_disabled and not self._stop.is_set():
                try:
                    await self._update_turn_detection(ws, self._server_vad_config())
                except Exception as exc:
                    self.bus.status.emit(
                        "recovering",
                        f"{self.status_label} restoring auto detection: {exc}",
                    )
                    raise
            if not committed:
                self._finish_manual_boundary(request_id, False)

    async def _update_turn_detection(
        self,
        ws: websockets.WebSocketClientProtocol,
        turn_detection: dict | None,
    ) -> None:
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._session_update_waiters.append(waiter)
        await ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {"input": {"turn_detection": turn_detection}},
                    },
                }
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(waiter), timeout=5.0)
        finally:
            if waiter in self._session_update_waiters:
                self._session_update_waiters.remove(waiter)

    def _handle_event(self, event: dict) -> None:
        event_type = event.get("type", "")
        if event_type == "session.updated":
            if self._session_update_waiters:
                waiter = self._session_update_waiters.popleft()
                if not waiter.done():
                    waiter.set_result(None)
        elif event_type == "input_audio_buffer.committed":
            if self._pending_manual_commits:
                pending = self._pending_manual_commits.popleft()
                raw_item_id = str(event.get("item_id", ""))
                if raw_item_id:
                    self._manual_item_requests[raw_item_id] = pending.request_id
                    if not pending.future.done():
                        pending.future.set_result(raw_item_id)
                elif not pending.future.done():
                    pending.future.set_exception(
                        RuntimeError("The server committed audio without an item ID")
                    )
        elif event_type == "conversation.item.input_audio_transcription.delta":
            item_id = self._source_item_id(str(event.get("item_id", "live")))
            delta = str(event.get("delta", ""))
            if delta:
                self.bus.transcribing_changed.emit(True)
                labeled_delta = self._label_delta(item_id, delta)
                self.bus.latest_text.emit(labeled_delta)
                self.bus.transcript_delta.emit(item_id, labeled_delta)
        elif event_type == "conversation.item.input_audio_transcription.completed":
            raw_item_id = str(event.get("item_id", f"item_{int(time.time() * 1000)}"))
            item_id = self._source_item_id(raw_item_id)
            transcript = str(event.get("transcript", "")).strip()
            if transcript:
                transcript = self._label_text(transcript)
                completed_at = time.time()
                self.bus.transcript_final.emit(item_id, transcript, completed_at)
                self.bus.latest_text.emit(transcript)
            self._delta_items_labeled.discard(item_id)
            self.bus.transcribing_changed.emit(False)
            self.bus.status.emit("listening", "Listening")
            request_id = self._manual_item_requests.pop(raw_item_id, "")
            if request_id:
                self._finish_manual_boundary(request_id, True)
        elif event_type == "conversation.item.input_audio_transcription.failed":
            raw_item_id = str(event.get("item_id", ""))
            message = event.get("error", {}).get("message", "Transcription failed")
            self.bus.transcribing_changed.emit(False)
            self.bus.status.emit("listening", str(message))
            request_id = self._manual_item_requests.pop(raw_item_id, "")
            if request_id:
                self._finish_manual_boundary(request_id, False)
        elif event_type == "input_audio_buffer.speech_started":
            self.bus.transcribing_changed.emit(True)
            self.bus.status.emit("hearing", f"{self.status_label} speech detected")
        elif event_type == "input_audio_buffer.speech_stopped":
            self.bus.transcribing_changed.emit(True)
            self.bus.status.emit("transcribing", f"{self.status_label} transcribing")
        elif event_type == "error":
            message = event.get("error", {}).get("message", "Realtime error")
            event_id = str(event.get("event_id", ""))
            for pending in list(self._pending_manual_commits):
                if pending.event_id == event_id:
                    self._pending_manual_commits.remove(pending)
                    if not pending.future.done():
                        pending.future.set_exception(RuntimeError(str(message)))
                    break
            self.bus.transcribing_changed.emit(False)
            self.bus.status.emit("error", str(message))

    def _finish_manual_boundary(self, request_id: str, success: bool) -> None:
        with self._manual_request_lock:
            if request_id not in self._active_manual_requests:
                return
            self._active_manual_requests.remove(request_id)
        self.bus.manual_transcript_boundary_finished.emit(request_id, success)

    def _fail_unresolved_protocol_requests(self) -> None:
        for waiter in list(self._session_update_waiters):
            if not waiter.done():
                waiter.cancel()
        self._session_update_waiters.clear()
        for pending in list(self._pending_manual_commits):
            if not pending.future.done():
                pending.future.cancel()
            self._finish_manual_boundary(pending.request_id, False)
        self._pending_manual_commits.clear()
        for request_id in list(self._manual_item_requests.values()):
            self._finish_manual_boundary(request_id, False)
        self._manual_item_requests.clear()

    def _session_update(self) -> dict:
        return {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {
                    "input": {
                        "format": {
                            "type": "audio/pcm",
                            "rate": self.settings.realtime_sample_rate,
                        },
                        "noise_reduction": {"type": "near_field"},
                        "transcription": {
                            "model": self.settings.transcription_model,
                            "language": self.settings.language or "en",
                        },
                        "turn_detection": self._server_vad_config(),
                    }
                },
            },
        }

    def _uses_server_vad(self) -> bool:
        return True

    @staticmethod
    def _server_vad_config() -> dict:
        return {
            "type": "server_vad",
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 500,
        }

    def _source_item_id(self, item_id: str) -> str:
        if not self.source_label:
            return item_id
        return f"{self.source_label.lower()}:{item_id}"

    def _label_text(self, text: str) -> str:
        clean = text.strip()
        if not self.source_label or clean.startswith(f"[{self.source_label}]"):
            return clean
        return f"[{self.source_label}] {clean}"

    def _label_delta(self, item_id: str, delta: str) -> str:
        if not self.source_label:
            return delta
        if item_id in self._delta_items_labeled:
            return delta
        self._delta_items_labeled.add(item_id)
        return f"[{self.source_label}] {delta}"


def _connect(url: str, headers: dict[str, str]):
    try:
        return websockets.connect(
            url, additional_headers=headers, ping_interval=20, ping_timeout=20
        )
    except TypeError:
        return websockets.connect(
            url, extra_headers=headers, ping_interval=20, ping_timeout=20
        )
