"""Typed values shared by the streaming pipeline and dashboard."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class DetectorResult:
    synthetic_probability: float | None
    confidence: float
    latency_ms: float
    provider: str
    label: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChannelProfile:
    snr_db: float | None
    clipping_ratio: float
    bandwidth_hz: float
    silence_ratio: float
    speech_energy: float
    channel_quality_score: float
    degradation_label: str
    notes: tuple[str, ...] = ()
    version: str = "channel-v2"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WhisperContext:
    transcript: str
    intent_category: str
    operational_risk: str
    matched_phrase: str | None
    latency_ms: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Decision:
    synthetic_probability: float | None
    evidence_reliability: float
    operational_risk: str
    decision: str
    action: str
    explanation: str
    detector_latency_ms: float
    whisper_latency_ms: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TimelineSegment:
    timestamp: float
    synthetic_probability: float | None
    reliability: float
    risk: str
    decision: str
    latency_ms: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
