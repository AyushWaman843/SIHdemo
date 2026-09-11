"""Small synchronous client for Reality Defender's file-analysis API."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

from ..models import DetectorResult


BASE_URL = "https://api.prd.realitydefender.xyz"
ACTIVE_STATUSES = {"PENDING", "PROCESSING", "IN_PROGRESS", "QUEUED", "ANALYZING", "UPLOADING"}


def _status_probability(status: str | None, final_score: Any) -> float | None:
    try:
        if final_score is not None:
            value = float(final_score)
            # Media Detail reports the ensemble score on a 0-100 scale.
            return max(0.0, min(1.0, value / 100.0 if value > 1 else value))
    except (TypeError, ValueError):
        pass
    if status in {"AUTHENTIC", "REAL", "GENUINE"}:
        return 0.05
    if status in {"MANIPULATED", "FAKE", "SUSPICIOUS"}:
        return 0.95
    return None


def _reason_text(metadata: dict[str, Any]) -> list[str]:
    reasons = []
    for item in metadata.get("reasons") or []:
        if isinstance(item, dict):
            reasons.append(str(item.get("message") or item.get("explanation") or item.get("name") or item.get("code")))
        elif item:
            reasons.append(str(item))
    return reasons


def analyze_file(path: Path, api_key: str, timeout_seconds: int = 90) -> dict[str, Any]:
    """Upload one file, poll once, and return a safe, compact provider result."""
    if not api_key:
        raise RuntimeError("Set REALITY_DEFENDER_API_KEY in .env before analyzing clips.")

    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    presign = requests.post(
        f"{BASE_URL}/api/files/aws-presigned",
        headers=headers,
        json={"fileName": path.name},
        timeout=30,
    )
    if not presign.ok:
        try:
            err_data = presign.json()
            err_msg = err_data.get("explanation") or err_data.get("message") or presign.text
            raise RuntimeError(f"Reality Defender: {err_msg}")
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            presign.raise_for_status()
    payload = presign.json()
    response = payload.get("response") or {}
    signed_url = response.get("signedUrl")
    request_id = payload.get("requestId") or payload.get("request_id")
    if not signed_url or not request_id:
        raise RuntimeError("Reality Defender returned an incomplete upload response.")

    upload = requests.put(signed_url, data=path.read_bytes(), timeout=60)
    upload.raise_for_status()

    started = time.perf_counter()
    result: dict[str, Any] = {}
    while time.perf_counter() - started < timeout_seconds:
        detail = requests.get(
            f"{BASE_URL}/api/media/users/{request_id}",
            headers=headers,
            timeout=30,
        )
        detail.raise_for_status()
        result = detail.json()
        summary = result.get("resultsSummary") or {}
        status = str(summary.get("status") or "").upper()
        if status and status not in ACTIVE_STATUSES:
            break
        time.sleep(2)
    else:
        raise TimeoutError("Reality Defender did not finish before the polling timeout.")

    summary = result.get("resultsSummary") or {}
    metadata = summary.get("metadata") or {}
    status = str(summary.get("status") or "UNABLE_TO_EVALUATE").upper()
    probability = _status_probability(status, metadata.get("finalScore"))
    confidence = 0.0 if probability is None else max(0.0, min(1.0, abs(probability - 0.5) * 2))
    models: list[dict[str, Any]] = []
    for model in result.get("models") or []:
        if not isinstance(model, dict):
            continue
        name = model.get("modelName") or model.get("name") or model.get("model")
        model_status = model.get("status") or model.get("prediction")
        if name and ("aud" in str(name).lower() or "audio" in str(name).lower()):
            models.append({"name": name, "status": model_status})

    return {
        "status": status,
        "fake_probability": probability,
        "confidence": confidence,
        "reasons": _reason_text(metadata),
        "models": models,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def as_detector_result(provider: dict[str, Any]) -> DetectorResult:
    return DetectorResult(
        synthetic_probability=provider.get("fake_probability"),
        confidence=float(provider.get("confidence") or 0.0),
        latency_ms=float(provider.get("latency_ms") or 0.0),
        provider=provider.get("provider", "reality-defender"),
        label=provider.get("status"),
        error="; ".join(provider.get("reasons") or []) or None,
    )
