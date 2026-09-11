"""CYPHER's small clip-library demo and optional realtime endpoint."""

from __future__ import annotations

import asyncio
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
    return HTMLResponse(DASHBOARD.replace('</body>', '<script src="/context.js"></script></body>'))


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


DASHBOARD = r"""
<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CYPHER — audio trust check</title>
<style>
:root{--ink:#17211d;--muted:#718078;--paper:#f6f8f5;--card:#fff;--line:#dce4de;--green:#147a56;--green2:#dff3e9;--amber:#a96c15;--red:#bb403c}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}.page{width:min(760px,calc(100% - 32px));margin:0 auto;padding:42px 0 64px}.brand{font-size:.78rem;letter-spacing:.2em;font-weight:800;color:var(--green)}h1{font-size:clamp(2rem,5vw,3.1rem);letter-spacing:-.06em;margin:7px 0 8px}h1+p{margin:0 0 28px;color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:20px;padding:24px;box-shadow:0 14px 38px #173b2b0a;margin-top:16px}label{display:block;font-size:.8rem;font-weight:700;color:var(--muted);margin-bottom:7px}select,input[type=file]{width:100%;padding:12px 13px;border:1px solid #bccac1;border-radius:10px;background:#fff;color:var(--ink);font:inherit}button{border:0;border-radius:10px;padding:12px 18px;background:var(--green);color:#fff;font:700 15px inherit;cursor:pointer}button:disabled{opacity:.5;cursor:wait}.row{display:flex;gap:10px;align-items:end}.row>div{flex:1}.upload{margin-top:15px}.upload small{display:block;color:var(--muted);margin-top:6px}.player{margin-top:24px;padding-top:20px;border-top:1px solid var(--line)}audio{width:100%;height:40px}.levels{height:76px;display:flex;align-items:center;justify-content:center;gap:4px;margin:10px 0 3px}.levels i{display:block;width:7px;height:8px;border-radius:8px;background:var(--green);opacity:.28;transition:height .08s ease,opacity .08s ease}.levels.playing i{opacity:.95}.status{font-size:.82rem;color:var(--muted);min-height:24px}.status.error{color:var(--red)}.result{display:none}.result.show{display:block}.result-head{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:15px;margin-bottom:18px}.result-head h2{margin:0;font-size:1.15rem}.pill{font-size:.75rem;font-weight:800;border-radius:999px;padding:5px 10px;background:var(--green2);color:var(--green)}.pill.suspicious{background:#fde8e5;color:var(--red)}.pill.uncertain{background:#fff1d9;color:var(--amber)}.numbers{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.number{border:1px solid var(--line);border-radius:12px;padding:13px}.number span{display:block;color:var(--muted);font-size:.76rem}.number strong{display:block;font-size:1.75rem;letter-spacing:-.04em;margin-top:5px}.bar{height:6px;background:#edf1ed;border-radius:9px;margin-top:9px;overflow:hidden}.bar b{display:block;height:100%;background:var(--green);border-radius:inherit;width:0}.decision{margin-top:16px;border-radius:13px;padding:17px;background:#f0f7f2}.decision.suspicious{background:#fff0ee}.decision.uncertain{background:#fff5e4}.decision strong{font-size:1.45rem}.decision p{margin:4px 0 0;color:#53625a}.details{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:16px;color:var(--muted);font-size:.82rem}.details b{color:var(--ink);font-weight:700}.models{margin-top:17px;color:var(--muted);font-size:.78rem}.models code{color:var(--ink)}@media(max-width:560px){.page{padding-top:25px}.card{padding:18px}.row{display:block}.row button{width:100%;margin-top:12px}.numbers{grid-template-columns:1fr}.details{grid-template-columns:1fr}}
</style></head><body><main class="page"><div class="brand">CYPHER</div><h1>Is this voice trustworthy?</h1><p>Choose a clip, listen to it, and run one whole-file check.</p>
<section class="card"><div class="row"><div><label for="clip">Audio sample</label><select id="clip"><option>Loading clips…</option></select></div><button id="analyze">Analyze</button></div><div class="upload"><label for="upload">Add a clip to the library</label><input id="upload" type="file" accept="audio/*"><small>Files are kept locally in <code>clips/</code>. WAV, MP3, FLAC, M4A, AAC, OGG and ALAC are supported.</small></div><div class="player"><audio id="player" controls preload="metadata"></audio><div class="levels" id="levels" aria-label="Voice level animation"></div><div class="status" id="status">Ready.</div></div></section>
<section class="card result" id="result"><div class="result-head"><h2>Scan result</h2><span class="pill" id="providerStatus">—</span></div><div class="numbers"><div class="number"><span>Detector fake probability</span><strong id="fake">—</strong><div class="bar"><b id="fakeBar"></b></div></div><div class="number"><span>Channel quality</span><strong id="quality">—</strong><div class="bar"><b id="qualityBar"></b></div></div><div class="number"><span>Evidence reliability</span><strong id="reliability">—</strong><div class="bar"><b id="reliabilityBar"></b></div></div></div><div class="decision uncertain" id="decision"><strong id="decisionTitle">Uncertain</strong><p id="decisionText"></p></div><div class="details"><div>Operational risk: <b id="risk">—</b></div><div>Channel: <b id="channel">—</b></div><div>SNR: <b id="snr">—</b></div><div>Bandwidth: <b id="bandwidth">—</b></div><div>Intent: <b id="intent">—</b></div><div>Action: <b id="action">—</b></div></div><div class="models" id="models"></div></section></main>
<script>
const $=id=>document.getElementById(id),select=$('clip'),player=$('player'),levels=$('levels');let clips=[],meter,meterSource,analyser,meterData;for(let i=0;i<34;i++){const el=document.createElement('i');levels.appendChild(el)}
async function loadClips(selected){const res=await fetch('/api/clips');clips=await res.json();select.innerHTML='';clips.forEach(c=>{const o=document.createElement('option');o.value=c.filename;o.textContent=c.label;select.appendChild(o)});if(selected)select.value=selected;updatePlayer()}function updatePlayer(){const c=clips.find(x=>x.filename===select.value);if(c){player.src=c.url;player.load();$('status').textContent='Ready.'}}select.onchange=updatePlayer;
function stopMeter(){levels.classList.remove('playing');if(meter){cancelAnimationFrame(meter);meter=null}}function animateMeter(){if(!analyser)return;analyser.getByteTimeDomainData(meterData);let sum=0;for(const n of meterData){const v=(n-128)/128;sum+=v*v}const rms=Math.min(1,Math.sqrt(sum/meterData.length)*4);[...levels.children].forEach((el,i)=>{const wave=Math.abs(Math.sin(performance.now()/190+i*.65));el.style.height=`${8+Math.max(0,rms*(44+wave*30))}px`});meter=requestAnimationFrame(animateMeter)}function startMeter(){try{if(!meterSource){const ac=new(window.AudioContext||window.webkitAudioContext)();analyser=ac.createAnalyser();analyser.fftSize=256;meterData=new Uint8Array(analyser.fftSize);meterSource=ac.createMediaElementSource(player);meterSource.connect(analyser);analyser.connect(ac.destination)}levels.classList.add('playing');animateMeter()}catch(e){levels.classList.add('playing')}}player.onplay=startMeter;player.onpause=stopMeter;player.onended=stopMeter;
function setBar(id,v){$(id).style.width=`${Math.max(0,Math.min(100,v||0))}%`}function showResult(d){if(d.error){$('status').textContent=d.error;$('status').className='status error';return}const det=d.detector,ch=d.channel,dec=d.decision,fake=det.synthetic_probability==null?null:det.synthetic_probability*100,quality=ch.channel_quality_score*100;$('result').classList.add('show');$('providerStatus').textContent=det.status||'UNAVAILABLE';$('providerStatus').className='pill '+(dec.decision==='Suspicious'?'suspicious':dec.decision==='Uncertain'?'uncertain':'');$('fake').textContent=fake==null?'Unavailable':fake.toFixed(1)+'%';$('quality').textContent=quality.toFixed(1)+'%';$('reliability').textContent=dec.evidence_reliability.toFixed(1)+'%';setBar('fakeBar',fake);setBar('qualityBar',quality);setBar('reliabilityBar',dec.evidence_reliability);$('decisionTitle').textContent=dec.decision;$('decisionText').textContent=dec.explanation;$('decision').className='decision '+dec.decision.toLowerCase();$('risk').textContent=dec.operational_risk;$('channel').textContent=ch.degradation_label;$('snr').textContent=ch.snr_db.toFixed(1)+' dB';$('bandwidth').textContent=(ch.bandwidth_hz/1000).toFixed(1)+' kHz';$('intent').textContent=d.whisper.intent_category;$('action').textContent=dec.action;$('models').innerHTML=det.models?.length?'Audio models: '+det.models.map(m=>`<code>${m.name}: ${m.status}</code>`).join(' · '):(det.reasons?.length?'Provider note: '+det.reasons.join('; '):'')}
async function analyze(){const filename=select.value;if(!filename)return;$('analyze').disabled=true;$('result').classList.remove('show');$('status').className='status';$('status').textContent='Playing and analyzing…';player.src=clips.find(c=>c.filename===filename)?.url||'';player.currentTime=0;player.play().catch(()=>{});try{const res=await fetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename})});showResult(await res.json());if($('result').classList.contains('show'))$('status').textContent='Analysis complete.'}catch(e){$('status').textContent='Could not reach the server: '+e.message;$('status').className='status error'}finally{$('analyze').disabled=false}}
$('analyze').onclick=analyze;$('upload').onchange=async e=>{const file=e.target.files[0];if(!file)return;$('status').textContent='Adding clip…';const body=new FormData();body.append('file',file);try{const res=await fetch('/api/upload',{method:'POST',body});const data=await res.json();if(data.error)throw Error(data.error);await loadClips(data.filename);$('status').textContent='Added '+data.filename+'.'}catch(err){$('status').textContent=err.message;$('status').className='status error'}e.target.value=''};loadClips();
</script></body></html>
"""
