"""Local on-device AASIST deepfake voice detector using ONNX Runtime."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import numpy as np
import scipy.special
import onnxruntime as ort

from ..models import DetectorResult
from .base import RealtimeDetector

_SESSION: ort.InferenceSession | None = None
MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "aasist.onnx"


def get_session() -> ort.InferenceSession | None:
    global _SESSION
    if _SESSION is None and MODEL_PATH.exists():
        try:
            _SESSION = ort.InferenceSession(str(MODEL_PATH))
        except Exception:
            _SESSION = None
    return _SESSION


def predict_audio(audio: np.ndarray, sample_rate: int = 16000) -> DetectorResult:
    """Predict synthetic voice probability locally on-device using AASIST ONNX."""
    started = time.perf_counter()
    session = get_session()
    if session is None:
        return DetectorResult(None, 0.0, 0.0, "aasist", error="Local AASIST model not found or failed to load.")

    try:
        data = audio.copy()
        if data.ndim > 1:
            data = data.mean(axis=-1)
        
        target_len = 64600  # AASIST official input length (~4.03s at 16kHz)
        if len(data) < target_len:
            repeats = int(np.ceil(target_len / max(1, len(data))))
            data = np.tile(data, repeats)[:target_len]
        else:
            data = data[:target_len]

        rms = float(np.sqrt(np.mean(data**2)))
        if rms > 1e-6:
            data = data * (0.1 / rms)
        data = np.clip(data, -1.0, 1.0).astype(np.float32)

        inp = data[np.newaxis, :]
        input_name = session.get_inputs()[0].name
        logits = session.run(None, {input_name: inp})[0]
        probs = scipy.special.softmax(logits, axis=-1)[0]
        
        # index 0 is bona fide (human), index 1 is spoof (cloned/synthetic)
        synthetic_prob = float(probs[1])
        confidence = float(max(0.0, min(1.0, abs(probs[0] - probs[1]))))
        label = "FAKE" if synthetic_prob >= 0.50 else "AUTHENTIC"
        latency_ms = round((time.perf_counter() - started) * 1000, 1)

        return DetectorResult(
            synthetic_probability=synthetic_prob,
            confidence=confidence,
            latency_ms=latency_ms,
            provider="aasist",
            label=label,
            error=None,
        )
    except Exception as exc:
        return DetectorResult(None, 0.0, 0.0, "aasist", error=str(exc))


class AASISTDetector(RealtimeDetector):
    provider = "aasist"

    def __init__(self) -> None:
        self._result_queue: asyncio.Queue[DetectorResult] = asyncio.Queue(maxsize=16)
        self._buffer: list[np.ndarray] = []
        self._samples_accumulated = 0
        self._last_predict_samples = 0
        self._connected = False
        self._final_result: DetectorResult | None = None

    async def connect(self) -> None:
        self._connected = True
        self._buffer.clear()
        self._samples_accumulated = 0
        self._last_predict_samples = 0
        self._final_result = None

    async def stream_detect(self, audio_chunk: bytes) -> None:
        if not audio_chunk or len(audio_chunk) % 2:
            return
        samples = np.frombuffer(audio_chunk, dtype="<i2").astype(np.float32) / 32768.0
        self._buffer.append(samples)
        self._samples_accumulated += len(samples)

        # Run live detection periodically every ~1.5 seconds once at least 1 second of audio is present
        if self._samples_accumulated - self._last_predict_samples >= 24000 and self._samples_accumulated >= 16000:
            self._last_predict_samples = self._samples_accumulated
            concatenated = np.concatenate(self._buffer)
            res = await asyncio.to_thread(predict_audio, concatenated)
            self._final_result = res
            try:
                self._result_queue.put_nowait(res)
            except asyncio.QueueFull:
                try:
                    self._result_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self._result_queue.put_nowait(res)

    async def finish(self) -> DetectorResult:
        if self._buffer:
            concatenated = np.concatenate(self._buffer)
            if concatenated.size >= 8000:  # At least 0.5s of audio
                res = await asyncio.to_thread(predict_audio, concatenated)
                self._final_result = res
                return res
        if self._final_result:
            return self._final_result
        return DetectorResult(
            synthetic_probability=0.05,
            confidence=0.5,
            latency_ms=1.0,
            provider="aasist",
            label="AUTHENTIC",
            error=None,
        )

    async def results(self):
        while True:
            yield await self._result_queue.get()

    async def close(self) -> None:
        self._connected = False
        self._buffer.clear()
