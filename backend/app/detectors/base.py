"""Abstract detector contract used by the realtime pipeline."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import DetectorResult


class RealtimeDetector(ABC):
    provider = "unknown"

    @abstractmethod
    async def connect(self) -> None:
        """Open or restore the detector session."""

    @abstractmethod
    async def stream_detect(self, audio_chunk: bytes) -> None:
        """Queue a PCM chunk without blocking audio capture."""

    @abstractmethod
    async def results(self):
        """Yield incremental detector results."""

    @abstractmethod
    async def close(self) -> None:
        """Close the provider session."""
