"""Producer-consumer live audio session used by the FastAPI WebSocket route."""

from __future__ import annotations

import asyncio
import logging
import os
import time

import numpy as np

from .channel import profile_audio
from .engine import decide
from .models import ChannelProfile, TimelineSegment
from .whisper_context import WhisperContextLayer

LOG = logging.getLogger(__name__)
SAMPLE_RATE = 16000
WINDOW_SECONDS = max(1.0, float(os.getenv("WINDOW_SECONDS", "4")))
WINDOW_STRIDE = max(0.1, float(os.getenv("WINDOW_STRIDE", "1")))
WINDOW_SAMPLES = int(SAMPLE_RATE * WINDOW_SECONDS)
STRIDE_SAMPLES = int(SAMPLE_RATE * WINDOW_STRIDE)


class LiveSession:
    def __init__(self, detector, whisper_model: str = "tiny") -> None:
        self.detector = detector
        self.whisper = WhisperContextLayer(whisper_model)
        self.audio_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.whisper_queue: asyncio.Queue[np.ndarray] = asyncio.Queue(maxsize=1)
        self.out_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=32)
        self.timeline: list[TimelineSegment] = []
        self._audio_task: asyncio.Task | None = None
        self._result_task: asyncio.Task | None = None
        self._whisper_task: asyncio.Task | None = None
        self._buffer = np.zeros(0, dtype=np.float32)
        self._since_window = 0
        self._started = time.perf_counter()
        self.latest_profile = profile_audio(np.zeros(1, dtype=np.float32), SAMPLE_RATE)

    async def start(self) -> None:
        await self.detector.connect()
        self._audio_task = asyncio.create_task(self._audio_producer())
        self._result_task = asyncio.create_task(self._result_consumer())
        self._whisper_task = asyncio.create_task(self._whisper_worker())
        await self.emit({"type": "session", "provider": self.detector.provider, "sample_rate": SAMPLE_RATE, "window_seconds": WINDOW_SECONDS, "stride_seconds": WINDOW_STRIDE})

    async def emit(self, payload: dict) -> None:
        try:
            self.out_queue.put_nowait(payload)
        except asyncio.QueueFull:
            await self.out_queue.get()
            await self.out_queue.put(payload)

    async def ingest(self, pcm: bytes) -> None:
        if not pcm or len(pcm) % 2:
            return
        await self.audio_queue.put(pcm)

    async def _audio_producer(self) -> None:
        while True:
            pcm = await self.audio_queue.get()
            await self.detector.stream_detect(pcm)
            samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
            self._buffer = np.concatenate((self._buffer, samples))
            if self._buffer.size > WINDOW_SAMPLES:
                self._buffer = self._buffer[-WINDOW_SAMPLES:]
            self._since_window += samples.size
            while self._since_window >= STRIDE_SAMPLES:
                self._since_window -= STRIDE_SAMPLES
                window = self._buffer.copy()
                self.latest_profile = profile_audio(window, SAMPLE_RATE)
                if self.whisper_queue.full():
                    try:
                        self.whisper_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                await self.whisper_queue.put(window)

    async def _whisper_worker(self) -> None:
        while True:
            window = await self.whisper_queue.get()
            await self.whisper.transcribe(window)

    async def _result_consumer(self) -> None:
        async for detector_result in self.detector.results():
            if detector_result.error:
                await self.emit({"type": "detector_error", "message": detector_result.error, "provider": detector_result.provider})
                continue
            decision = decide(detector_result, self.latest_profile, self.whisper.latest)
            segment = TimelineSegment(
                timestamp=time.perf_counter() - self._started,
                synthetic_probability=detector_result.synthetic_probability,
                reliability=decision.evidence_reliability,
                risk=decision.operational_risk,
                decision=decision.decision,
                latency_ms=detector_result.latency_ms,
            )
            self.timeline.append(segment)
            self.timeline = self.timeline[-120:]
            await self.emit({
                "type": "decision",
                "detector": detector_result.as_dict(),
                "channel": self.latest_profile.as_dict(),
                "whisper": self.whisper.latest.as_dict(),
                "decision": decision.as_dict(),
                "timeline_segment": segment.as_dict(),
            })

    async def close(self) -> None:
        for task in (self._audio_task, self._result_task, self._whisper_task):
            if task:
                task.cancel()
        await self.detector.close()
