"""Asynchronous, optional Whisper context extraction for operational risk."""

from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from .models import WhisperContext
from .risk import assess

LOG = logging.getLogger(__name__)

PHRASES: dict[str, tuple[str, str]] = {
    "one time password": ("OTP request", "HIGH"),
    "otp": ("OTP request", "HIGH"),
    "password": ("Password", "CRITICAL"),
    "transfer money": ("Money transfer", "CRITICAL"),
    "transfer": ("Money transfer", "HIGH"),
    "upi": ("UPI", "CRITICAL"),
    "bank account": ("Bank account", "CRITICAL"),
    "confidential": ("Confidential data", "HIGH"),
    "urgent": ("Urgency", "MEDIUM"),
    "immediately": ("Urgency", "MEDIUM"),
}


class WhisperContextLayer:
    def __init__(self, model_name: str = "tiny") -> None:
        self.model_name = model_name
        self._model = None
        self._load_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()
        self.latest = WhisperContext("", "Unavailable", "UNKNOWN", None, 0.0)

    async def _load(self):
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel

                    self._model = await asyncio.to_thread(
                        WhisperModel, self.model_name, device="cpu", compute_type="int8"
                    )
                except Exception as exc:
                    LOG.warning("Whisper unavailable: %s", exc)
                    self._model = False
        return self._model

    @staticmethod
    def _classify(text: str) -> tuple[str, str, str | None]:
        result = assess(text)
        first = max(result['findings'], key=lambda f: f['weight'], default=None)
        return (first['category'] if first else 'None', result['level'], first['evidence'] if first else None)

    async def transcribe(self, audio: np.ndarray) -> WhisperContext:
        started = time.perf_counter()
        model = await self._load()
        if not model:
            result = WhisperContext("", "Unavailable", "UNKNOWN", None, (time.perf_counter() - started) * 1000)
            self.latest = result
            return result
        try:
            def run():
                segments, _ = model.transcribe(audio.astype(np.float32), language="en", beam_size=1, vad_filter=True)
                return " ".join(segment.text.strip() for segment in segments).strip()
            async with self._inference_lock:
                transcript = await asyncio.to_thread(run)
        except Exception as exc:
            LOG.warning("Whisper transcription failed: %s", exc)
            result = WhisperContext("", "Unavailable", "UNKNOWN", None, (time.perf_counter() - started) * 1000)
            self.latest = result
            return result
        category, risk, phrase = self._classify(transcript)
        result = WhisperContext(transcript, category, risk, phrase, (time.perf_counter() - started) * 1000)
        self.latest = result
        return result
