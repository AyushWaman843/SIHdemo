"""AASIST adapter placeholder retained for offline experiments."""

from __future__ import annotations

from .base import RealtimeDetector


class AASISTDetector(RealtimeDetector):
    provider = "aasist"

    async def connect(self) -> None:
        raise RuntimeError("AASIST is not configured for the live streaming path.")

    async def stream_detect(self, audio_chunk: bytes) -> None:
        raise RuntimeError("AASIST is not configured for the live streaming path.")

    async def results(self):
        if False:
            yield None

    async def close(self) -> None:
        return None
