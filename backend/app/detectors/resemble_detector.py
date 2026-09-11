"""Persistent Resemble Detect streaming WebSocket adapter."""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from collections.abc import AsyncIterator
from urllib.parse import quote

from ..models import DetectorResult
from .base import RealtimeDetector

try:
    import websockets
except ImportError:  # pragma: no cover - dependency is declared in requirements
    websockets = None

LOG = logging.getLogger(__name__)


def wav_header(sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Create the streaming WAV header recommended by Resemble."""
    byte_rate = sample_rate * channels * 2
    block_align = channels * 2
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 0xFFFFFFFF, b"WAVE", b"fmt ", 16, 1, channels,
        sample_rate, byte_rate, block_align, 16, b"data", 0xFFFFFFFF,
    )


class ResembleStreamingDetector(RealtimeDetector):
    provider = "resemble"

    def __init__(self, api_key: str, *, filename: str = "cypher-live.wav") -> None:
        if websockets is None:
            raise RuntimeError("Install the websockets package to use Resemble streaming.")
        self.api_key = api_key
        self.filename = filename
        self.websocket = None
        self._reader_task: asyncio.Task[None] | None = None
        self._result_queue: asyncio.Queue[DetectorResult] = asyncio.Queue(maxsize=16)
        self._connected_at = 0.0
        self._last_send_at = 0.0
        self._header_sent = False
        self._final_event = asyncio.Event()
        self.final_result = None

    async def connect(self) -> None:
        if self.websocket is not None:
            if getattr(self.websocket, "closed", False) is False and getattr(self.websocket, "state", 1) == 1:
                return
        query = f"?filename={quote(self.filename)}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            try:
                self.websocket = await websockets.connect(  # type: ignore[union-attr]
                    f"wss://stream.resemble.ai/api/v1/detect/audio{query}",
                    additional_headers=headers,
                    open_timeout=10,
                    ping_interval=20,
                )
            except TypeError:  # websockets < 14 uses extra_headers
                self.websocket = await websockets.connect(  # type: ignore[union-attr]
                    f"wss://stream.resemble.ai/api/v1/detect/audio{query}",
                    extra_headers=headers,
                    open_timeout=10,
                    ping_interval=20,
                )
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = getattr(response, "status_code", None) or getattr(response, "status", None)
            if status == 402 or "402" in str(exc):
                raise RuntimeError(
                    "Resemble rejected the stream with HTTP 402. Enable audio Deepfake Detection and add eligible credits or resolve the account usage limit."
                ) from exc
            raise
        self._connected_at = time.perf_counter()
        self._header_sent = False
        self._final_event.clear()
        ready = await asyncio.wait_for(self.websocket.recv(), timeout=10)
        payload = json.loads(ready)
        if payload.get("type") != "ready":
            raise RuntimeError(payload.get("error") or "Resemble stream did not become ready")
        self._reader_task = asyncio.create_task(self._read_messages())

    async def _read_messages(self) -> None:
        assert self.websocket is not None
        try:
            async for message in self.websocket:
                if not isinstance(message, str):
                    continue
                payload = json.loads(message)
                message_type = payload.get("type")
                if message_type == "final":
                    score = payload.get("aggregated_score")
                    label = payload.get("label")
                    self.final_result = DetectorResult(
                        float(score) if score is not None else None,
                        min(1.0, max(0.0, float(payload.get("consistency") or 0) / 100)),
                        0.0, self.provider, label=label,
                        error=None if score is not None else "No sufficient voice-active audio was evaluated.",
                    )
                    self._final_event.set()
                    if score is not None:
                        result = DetectorResult(
                            synthetic_probability=float(score),
                            confidence=min(1.0, max(0.0, float(payload.get("consistency") or 0.0) / 100.0)),
                            latency_ms=max(0.0, (time.perf_counter() - self._last_send_at) * 1000),
                            provider=self.provider,
                            label=str(label) if label else None,
                        )
                        try:
                            self._result_queue.put_nowait(result)
                        except asyncio.QueueFull:
                            pass
                    continue
                if message_type == "chunk":
                    info = payload.get("chunk_info") or {}
                    label = info.get("chunk_label") or payload.get("label")
                    if label == "skipped":
                        continue
                    score = payload.get("aggregated_score")
                    if score is None:
                        score = info.get("chunk_aggregated_score")
                    consistency = payload.get("consistency")
                    if consistency is None:
                        consistency = payload.get("consistency")
                    result = DetectorResult(
                        synthetic_probability=float(score) if score is not None else None,
                        confidence=min(1.0, max(0.0, float(consistency or 0.0) / 100.0)),
                        latency_ms=max(0.0, (time.perf_counter() - self._last_send_at) * 1000),
                        provider=self.provider,
                        label=str(label) if label else None,
                    )
                    try:
                        self._result_queue.put_nowait(result)
                    except asyncio.QueueFull:
                        self._result_queue.get_nowait()
                        self._result_queue.put_nowait(result)
                elif message_type == "error":
                    message_text = payload.get("error") or payload.get("error_message") or "Resemble stream failed"
                    result = DetectorResult(None, 0.0, 0.0, self.provider, error=str(message_text))
                    try:
                        self._result_queue.put_nowait(result)
                    except asyncio.QueueFull:
                        pass
        except Exception as exc:
            LOG.warning("Resemble reader stopped: %s", exc)
        finally:
            if not self._final_event.is_set():
                if self._result_queue.full():
                    self._result_queue.get_nowait()
                self._result_queue.put_nowait(DetectorResult(None, 0, 0, self.provider,
                    error="Resemble stream ended before a final result. Retry the scan."))

    async def stream_detect(self, audio_chunk: bytes) -> None:
        await self.connect()
        assert self.websocket is not None
        try:
            if not self._header_sent:
                await self.websocket.send(wav_header())
                self._header_sent = True
            await self.websocket.send(audio_chunk)
            self._last_send_at = time.perf_counter()
        except Exception as exc:
            raise RuntimeError("Resemble audio upload was interrupted. Start a new scan.") from exc

    async def results(self) -> AsyncIterator[DetectorResult]:
        while True:
            yield await self._result_queue.get()

    async def _drop_connection(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
            self._reader_task = None
        if self.websocket is not None:
            await self.websocket.close()
        self.websocket = None

    async def finish(self):
        if self.websocket is None:
            return None
        await self.websocket.send(json.dumps({"type": "end"}))
        await asyncio.wait_for(self._final_event.wait(), timeout=45)
        return self.final_result

    async def close(self) -> None:
        if self.websocket is None:
            return
        await self._drop_connection()
