"""One bounded audio session: independent transcript, detector and risk updates."""
import asyncio
import numpy as np

from .channel import profile_audio
from .engine import assess_voice, decide
from .models import DetectorResult, WhisperContext
from .risk import assess
from .semantic_risk import assess_semantic

RATE = 16000
MAX_SECONDS = 120
CHUNK = 4 * RATE


class ScanSession:
    def __init__(self, send, whisper, detector=None, file_check=None, semantic=False, sector="individual"):
        self.send, self.whisper, self.detector, self.file_check = send, whisper, detector, file_check
        self.semantic, self.sector = semantic, sector
        self.audio = np.zeros(0, dtype=np.float32)
        self.pending = np.zeros(0, dtype=np.float32)
        self.queue = asyncio.Queue(maxsize=32)
        self.result = DetectorResult(None, 0, 0, "reality-defender" if file_check else "resemble")
        self.channel = profile_audio(self.audio)
        self.transcript = ""
        self.context = assess("", sector)
        self.transcription_error = None
        self.tasks = []
        self.connected = False
        self.last_profile = 0
        self.send_lock = asyncio.Lock()
        self.audio_lock = asyncio.Lock()
        self.coverage_incomplete = False

    async def start(self):
        self.tasks.append(asyncio.create_task(self.transcribe()))
        self.provider_task = asyncio.create_task(self.run_provider())
        self.tasks.append(self.provider_task)
        await self.send({"type": "ready", "sample_rate": RATE, "max_seconds": MAX_SECONDS})

    async def run_provider(self):
        try:
            if self.file_check:
                self.result = await self.file_check()
                await self.publish()
            elif self.detector:
                await self.detector.connect()
                async with self.audio_lock:
                    # Serialize replay with ingestion: no dropped or duplicated frames.
                    if self.audio.size:
                        await self.detector.stream_detect((self.audio * 32768).astype("<i2").tobytes())
                    self.connected = True
                async for result in self.detector.results():
                    self.result = result
                    await self.publish()
            else:
                raise RuntimeError("Set RESEMBLE_API_KEY in .env and enable Resemble Detect access.")
        except Exception as exc:
            self.connected = False
            self.result = DetectorResult(None, 0, 0, self.result.provider, error=str(exc))
            await self.publish()

    async def ingest(self, pcm):
        if not pcm or len(pcm) % 2 or len(pcm) > RATE * 2:
            raise ValueError("Expected at most one second of 16 kHz mono PCM16 per frame.")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        if self.audio.size + samples.size > RATE * MAX_SECONDS:
            raise ValueError("Demo sessions are limited to 120 seconds. Stop and start a new scan.")
        async with self.audio_lock:
            self.audio = np.concatenate((self.audio, samples))
            if self.connected:
                try:
                    await self.detector.stream_detect(pcm)
                except Exception as exc:
                    self.connected = False
                    self.result = DetectorResult(None, 0, 0, "resemble", error=str(exc))
        self.pending = np.concatenate((self.pending, samples))
        while self.pending.size >= CHUNK:
            await self.queue.put(self.pending[:CHUNK].copy())
            self.pending = self.pending[CHUNK:]
        if self.audio.size - self.last_profile >= RATE:
            self.last_profile = self.audio.size
            self.channel = await asyncio.to_thread(profile_audio, self.audio.copy(), RATE)
            await self.publish()

    async def transcribe(self):
        while True:
            window = await self.queue.get()
            try:
                result = await self.whisper.transcribe(window)
                if result.intent_category == "Unavailable":
                    self.coverage_incomplete = True
                    self.transcription_error = "Transcription unavailable. Check the Whisper model installation."
                if result.transcript.strip():
                    self.transcript = (self.transcript + " " + result.transcript.strip()).strip()
                    # Accumulated text keeps requests linked across audio chunks.
                    self.context = assess(self.transcript, self.sector)
                    await self.publish()
                    if self.semantic:
                        semantic = await assess_semantic(self.transcript, self.sector, True)
                        # An AI downgrade cannot silently erase an actionable local warning.
                        if (semantic.get("score") or 0) >= (self.context.get("score") or 0):
                            self.context = semantic
                        else:
                            self.context["method"] += "; AI checked; stronger local warning retained"
                            self.context['ai_status'] = semantic.get('ai_status', 'failed')
                            if semantic.get('notice'):
                                self.context['notice'] = semantic['notice']
                        await self.publish()
                else:
                    if float(np.sqrt(np.mean(window * window))) > .01:
                        self.coverage_incomplete = True
                        self.transcription_error = "Some audible audio was not transcribed; review conversation coverage."
                    await self.publish()
            except Exception:
                self.coverage_incomplete = True
                self.transcription_error = "A transcription chunk failed; conversation coverage is incomplete."
                await self.publish()
            finally:
                self.queue.task_done()

    def snapshot(self, final=False):
        first = max(self.context["findings"], key=lambda f: f["weight"], default={})
        risk = self.context["level"]
        ai_incomplete = self.semantic and self.context.get('ai_status') != 'complete'
        if (self.coverage_incomplete or ai_incomplete) and risk == "LOW":
            risk = "UNKNOWN"
        context = WhisperContext(self.transcript, first.get("category", "None"), risk,
                                 first.get("evidence"), 0)
        decision = decide(self.result, self.channel, context)
        if ai_incomplete and self.context['level'] == 'LOW':
            decision.explanation = 'Requested AI conversation review is incomplete. Local checks found no listed pattern, but cannot provide the requested AI assessment.'
            decision.action = 'Review conversation or retry AI analysis'
        # Voice-only assessment must never inherit a fraud warning as a clone label.
        voice = assess_voice(self.result, self.channel)
        if self.file_check and self.coverage_incomplete and final:
            voice.decision = "Uncertain"
            voice.explanation = "The detector checked the full file, but playback/transcription coverage is incomplete."
            if decision.decision in {"Trusted", "No fraud indicators detected"}:
                decision.decision = "Uncertain"
                decision.explanation = voice.explanation
        voice_label = {"Trusted": "No strong clone signal", "Synthetic": "Likely synthetic", "Uncertain": "Inconclusive"}[voice.decision]
        if self.result.synthetic_probability is None or self.result.error:
            voice_label = "Unavailable" if self.result.error or final else "Waiting for detector"
        # No arbitrary blended fraud/clone probability: keep the existing gate and rating.
        rating = "No current warning" if decision.decision == "Trusted" else decision.decision
        return {"type": "update", "final": final, "transcript": self.transcript,
                "transcription_error": self.transcription_error,
                "context": self.context, "detector": self.result.as_dict(),
                "channel": self.channel.as_dict(), "decision": decision.as_dict(),
                "voice": {"label": voice_label, "explanation": voice.explanation},
                "rating": rating, "duration_seconds": round(self.audio.size / RATE, 1)}

    async def publish(self, final=False):
        async with self.send_lock:
            await self.send(self.snapshot(final))

    async def finish(self):
        if self.pending.size:
            await self.queue.put(self.pending.copy())
            self.pending = np.zeros(0, dtype=np.float32)
        self.channel = await asyncio.to_thread(profile_audio, self.audio, RATE)

        async def finish_provider():
            if self.file_check:
                await self.provider_task
            elif self.detector:
                # Allow an in-progress handshake to finish before sending end.
                for _ in range(220):
                    if self.connected or self.provider_task.done():
                        break
                    await asyncio.sleep(.1)
                if self.connected:
                    try:
                        self.result = await self.detector.finish() or DetectorResult(None, 0, 0, "resemble")
                    except Exception:
                        self.result = DetectorResult(None, 0, 0, "resemble", error="No final Resemble result arrived. Check provider access and retry.")
                    self.provider_task.cancel()
                    await asyncio.gather(self.provider_task, return_exceptions=True)

        try:
            await asyncio.wait_for(asyncio.gather(self.queue.join(), finish_provider()), timeout=210)
        except asyncio.TimeoutError:
            self.coverage_incomplete = True
            self.transcription_error = "Processing timed out; results may be incomplete."
            self.result = DetectorResult(None, 0, 0, self.result.provider, error="Final processing timed out.")
        await self.publish(final=True)

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.detector:
            await self.detector.close()
