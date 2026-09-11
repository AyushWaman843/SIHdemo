"""CYPHER's small clip-library demo and optional realtime endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
LOG = logging.getLogger("cypher")
BASE_DIR = Path(__file__).resolve().parents[2]
CLIPS_DIR = BASE_DIR / "clips"
CLIPS_DIR.mkdir(exist_ok=True)
load_dotenv(BASE_DIR / ".env")

from .channel import profile_audio
from .detectors.reality_defender import analyze_file, as_detector_result
from .detectors.resemble_detector import ResembleStreamingDetector
from .engine import decide
from .models import WhisperContext
from .pipeline import LiveSession
from .whisper_context import WhisperContextLayer
from .risk import assess
from .semantic_risk import assess_semantic
from .scan import ScanSession

app = FastAPI(title="CYPHER", version="2.1.0")
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".alac"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
context_layer = WhisperContextLayer(os.getenv("WHISPER_MODEL", "tiny"))


class AnalyzeRequest(BaseModel):
    filename: str

class ContextRequest(BaseModel):
    text: str = Field(max_length=12000)
    sector: str = 'individual'
    semantic: bool = False

@app.post('/api/context')
async def context_check(request: ContextRequest):
    return await assess_semantic(request.text, request.sector, request.semantic)

@app.get('/context.js')
async def context_script():
    return FileResponse(Path(__file__).with_name('context.js'), media_type='text/javascript')


def _clip_path(filename: str) -> Path | None:
    safe = Path(filename).name
    if safe != filename or Path(safe).suffix.lower() not in ALLOWED_EXTENSIONS:
        return None
    for folder in (CLIPS_DIR, BASE_DIR):
        candidate = (folder / safe).resolve()
        if candidate.is_file() and candidate.parent in {CLIPS_DIR.resolve(), BASE_DIR.resolve()}:
            return candidate
    return None


def _clips() -> list[dict[str, str | int]]:
    found: dict[str, Path] = {}
    # Prefer the managed library; root-level files are only a compatibility fallback.
    for folder in (CLIPS_DIR, BASE_DIR):
        if folder.exists():
            for path in folder.iterdir():
                if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
                    found.setdefault(path.name, path)
    names = sorted(found, key=str.casefold)
    return [{"filename": name, "label": f"Sample {i}", "url": f"/clips/{name}", "bytes": found[name].stat().st_size} for i, name in enumerate(names, 1)]


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/demo")


@app.get("/demo", response_class=HTMLResponse)
async def demo() -> HTMLResponse:
    return HTMLResponse(Path(__file__).with_name('dashboard.html').read_text(encoding='utf-8'))


@app.get('/scan.js')
async def scan_script():
    return FileResponse(Path(__file__).with_name('scan.js'), media_type='text/javascript')


@app.get('/capture.js')
async def capture_script():
    return FileResponse(Path(__file__).with_name('capture.js'), media_type='text/javascript')


@app.websocket('/ws/scan')
async def scan_socket(websocket: WebSocket):
    await websocket.accept()
    session = None
    try:
        config = json.loads(await asyncio.wait_for(websocket.receive_text(), 10))
        source = config.get('source')
        if source not in {'mic', 'clip'}:
            raise ValueError('Choose microphone or clip.')
        file_check = None
        detector = None
        if source == 'clip':
            path = _clip_path(str(config.get('filename', '')))
            if path is None:
                raise ValueError('Clip not found.')
            async def file_check():
                return as_detector_result(await _safe_reality_defender(path))
        else:
            key = os.getenv('RESEMBLE_API_KEY', '').strip()
            detector = ResembleStreamingDetector(key) if key else None
        session = ScanSession(websocket.send_json, context_layer, detector, file_check,
                              config.get('semantic') is True, str(config.get('sector', 'individual')))
        await session.start()
        while True:
            message = await websocket.receive()
            if message['type'] == 'websocket.disconnect':
                break
            if message.get('bytes') is not None:
                await session.ingest(message['bytes'])
            elif message.get('text'):
                control = json.loads(message['text'])
                if control.get('type') == 'end':
                    if source == 'clip' and control.get('playback_complete') is not True:
                        session.coverage_incomplete = True
                        session.transcription_error = 'Playback stopped early. Only the played portion was transcribed; the detector checked the full file.'
                    await session.finish()
                    await websocket.close(code=1000)
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        LOG.warning('Integrated scan failed: %s', type(exc).__name__)
        try:
            await websocket.send_json({'type': 'error', 'message': str(exc)})
            await websocket.close(code=1008)
        except Exception:
            pass
    finally:
        if session:
            await session.close()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "provider": "reality-defender"}


@app.get("/api/clips")
async def list_clips() -> list[dict[str, str | int]]:
    return _clips()


@app.get("/clips/{filename}")
@app.get("/audio/{filename}")
async def clip_audio(filename: str):
    path = _clip_path(filename)
    if path is None:
        return HTMLResponse("Clip not found", status_code=404)
    media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg", ".alac": "audio/alac"}.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)


@app.post("/api/upload")
async def upload_clip(file: UploadFile = File(...)) -> dict[str, str | int]:
    name = Path(file.filename or "").name
    if not name or Path(name).suffix.lower() not in ALLOWED_EXTENSIONS:
        return {"error": "Upload a WAV, MP3, FLAC, M4A, AAC, OGG, or ALAC file."}
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return {"error": "Audio files must be 20 MB or smaller."}
    target = CLIPS_DIR / name
    target.write_bytes(data)
    return {"filename": name, "label": name, "bytes": len(data)}


async def _safe_whisper(audio) -> WhisperContext:
    try:
        return await asyncio.wait_for(context_layer.transcribe(audio), timeout=35)
    except Exception as exc:
        LOG.warning("Whisper context unavailable: %s", exc)
        return WhisperContext("", "Unavailable", "UNKNOWN", None, 0.0)


async def _safe_reality_defender(path: Path) -> dict:
    key = os.getenv("REALITY_DEFENDER_API_KEY", "").strip() or os.getenv("RD_API_KEY", "").strip()
    try:
        return await asyncio.to_thread(analyze_file, path, key)
    except Exception as exc:
        LOG.warning("Reality Defender analysis failed: %s", exc)
        return {"status": "ERROR", "fake_probability": None, "confidence": 0.0, "reasons": [str(exc)], "models": [], "latency_ms": 0.0}


@app.post("/api/analyze")
async def analyze(request: AnalyzeRequest) -> dict:
    path = _clip_path(request.filename)
    if path is None:
        return {"error": "Clip not found."}
    try:
        import librosa
        audio, sample_rate = await asyncio.to_thread(librosa.load, str(path), sr=16000, mono=True)
    except Exception as exc:
        LOG.exception("Could not decode clip")
        return {"error": f"Could not decode this audio file: {exc}"}

    channel, provider, whisper = await asyncio.gather(
        asyncio.to_thread(profile_audio, audio, sample_rate),
        _safe_reality_defender(path),
        _safe_whisper(audio),
    )
    detector = as_detector_result(provider)
    decision = decide(detector, channel, whisper)
    return {
        "filename": path.name,
        "duration_seconds": round(float(len(audio) / sample_rate), 2),
        "detector": {**detector.as_dict(), "status": provider.get("status"), "reasons": provider.get("reasons", []), "models": provider.get("models", [])},
        "channel": channel.as_dict(),
        "whisper": {"intent_category": whisper.intent_category, "operational_risk": whisper.operational_risk, "matched_phrase": whisper.matched_phrase, "latency_ms": whisper.latency_ms},
        "decision": decision.as_dict(),
        "context": assess(whisper.transcript),
        "transcript": whisper.transcript,
    }


@app.websocket("/ws/live")
async def live(websocket: WebSocket) -> None:
    await websocket.accept()
    api_key = os.getenv("RESEMBLE_API_KEY", "").strip()
    if not api_key:
        await websocket.send_json({"type": "error", "message": "Set RESEMBLE_API_KEY in .env before starting a live session."})
        await websocket.close(code=1008)
        return
    detector = ResembleStreamingDetector(api_key)
    session = LiveSession(detector, os.getenv("WHISPER_MODEL", "tiny"))
    sender = None
    try:
        await session.start()

        async def send_updates() -> None:
            while True:
                await websocket.send_json(await session.out_queue.get())

        sender = asyncio.create_task(send_updates())
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.ingest(message["bytes"])
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        LOG.exception("Live session failed")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if sender:
            sender.cancel()
        await session.close()
