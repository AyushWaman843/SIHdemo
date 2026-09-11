"""Pella adapter placeholder retained for future detector swaps."""

from __future__ import annotations

from .base import RealtimeDetector


class PellaDetector(RealtimeDetector):
    provider = "pella"

    async def connect(self) -> None:
        raise RuntimeError("Pella is intentionally disabled on the synchronous live path.")

    async def stream_detect(self, audio_chunk: bytes) -> None:
        raise RuntimeError("Pella is intentionally disabled on the synchronous live path.")

    async def results(self):
        if False:
            yield None

    async def close(self) -> None:
        return None
