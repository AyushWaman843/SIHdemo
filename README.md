# CYPHER integrated conversation safety

Select a saved clip or use your microphone. The page shows incremental
transcription, separate voice authenticity and fraud assessments, channel
quality, evidence reliability, and a final CYPHER rating.

## Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000/demo in Chrome or Edge. Restart after code or .env
changes, then refresh the browser. Configure .env using .env.example:

```dotenv
RESEMBLE_API_KEY=your_resemble_detect_key
REALITY_DEFENDER_API_KEY=your_reality_defender_key
GROQ_API_KEY=your_optional_groq_key
WHISPER_MODEL=tiny
CONTEXT_MODEL=llama-3.3-70b-versatile
```

Resemble needs Detect access and eligible credits. HTTP 402 requires an account
or billing change. Missing/rejected keys make voice detection unavailable while
transcription and fraud checks continue. Keys stay on the backend.

## Integrated flow

- **Audio library / Play & analyze:** Reality Defender checks the original file
  once. Actual playback audio streams to local Whisper for transcription.
- **Microphone / Start microphone:** microphone audio streams to Resemble Detect
  and local Whisper concurrently. Monitoring is muted to avoid feedback.
- **Use AI to understand the conversation:** optionally sends accumulated text
  to Groq. Local checks run first, and the higher risk assessment is retained.
  AI failures visibly fall back to local checks. These findings affect the
  integrated final rating.
  Failure notices identify authentication, rate-limit, network and validation
  problems without exposing secrets. A requested but incomplete AI review makes
  an otherwise LOW final assessment uncertain; local high-risk evidence still
  escalates. Lottery/prize claims tied to disclosing a short phone token are
  checked locally even when the transcript never says SMS or OTP.
- **Stop & finish:** flushes remaining speech and waits for the final detector
  response. Playback completion triggers this automatically. Stopping a clip
  early explicitly marks partial coverage: Reality Defender still checked the
  whole original file.

The browser sends 16 kHz mono PCM16 in approximately 100 ms frames. Local
tiny/int8 Whisper transcribes four-second chunks. CPU speed, model startup and
optional AI calls add latency. This is incremental transcription, not word-level
streaming ASR. The first use may download the speech model. Chunk boundaries
can reduce recognition quality. Fraud checks use accumulated text to connect
an SMS-code reference with a later disclosure request.

Sessions are capped at 120 seconds to bound memory and queues. Microphone
access requires localhost or HTTPS. Put more files in clips/ or upload through
the page (20 MB maximum). Supported library formats: WAV, MP3, FLAC, M4A, AAC,
OGG and ALAC; convert to WAV/MP3 if the browser cannot play a codec.
Live audio and transcripts stay in memory, not on disk. External services
receive audio/text as described above; library uploads are stored locally.

## Interpreting results

Voice authenticity shows the provider's synthetic score with an independent
gated verdict. Synthetic detection cannot verify speaker identity or establish
whose voice was cloned. Fraud assessment shows transcript evidence, method,
risk level, an uncalibrated triage score, and a recommended action.

The existing channel and decision engine remain: quality below 60%, low
reliability, or a borderline detector score makes voice authenticity inconclusive.
High/critical conversation risk independently escalates the final assessment.
Synthetic speech alone does not trigger a suspicious/fraud verdict. With reliable
voice evidence and LOW conversation risk, the final rating is "No fraud indicators
detected" while the voice panel still says "Likely synthetic". Medium conversation
risk requires review; missing context or poor voice evidence remains uncertain.
Poor quality never changes fake into real. No current warning does not establish
safety. No invented combined clone/fraud probability is displayed.

Channel profiling v2 screens for noise-like energy, clipping/flat tops, very low
level and limited occupied bandwidth. Low spectral flatness is no longer treated
as damage. Pauses are excluded from active spectral measurements. Noise separation
is estimated only with a sustained quiet reference; otherwise it is unavailable,
not zero dB. The 99.5% energy rolloff describes occupied bandwidth, not a codec
cutoff. Bandwidth gets a modest penalty because voice content also affects it.
Noise/clipping are no longer counted again in evidence reliability.

The formula starts at 0.95 and subtracts bounded impairment penalties. These
weights and the retained 0.60 gate are prototype choices, not calibrated accuracy.
Controlled tests check added noise, hard clipping (including attenuated clipping),
band limiting, volume changes, pauses, and missing input. This does not validate
perceptual quality or detector accuracy. Codec/phase artifacts can escape these
measurements: the existing el_0014_severe.wav scores about 90.6% in v2 despite
being a processed test clip. Do not use this score to claim such artifacts are
absent or that detector output is necessarily reliable. A labeled evaluation set
and provider-specific validation are still needed. No transaction is automatically
blocked and the app does not prove fraud.

## Testing

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -v
node --check backend/app/scan.js
node --check backend/app/capture.js
```

Tests cover separate fraud escalation, quality gating, provider failure,
incremental transcripts, final short-chunk flushing, handshake buffering without
duplication, invalid PCM and invalid clip paths. Providers and transcription
are mocked in pipeline tests; this does not measure model accuracy.

Optional Chrome smoke test (requires `pip install playwright`):
`python tests/browser_scan_smoke.py`. It exercises actual browser audio capture,
two successive clip scans, microphone capture, finalization, and mobile width,
with simulated transcription and detector responses and no external model calls.

Demo exercise: say “You will receive a message with six digits”, then “Please
share that with me”. Check the actual transcript and evidence. Repeat with
“Never share your OTP” as a benign control.

The new page uses /ws/scan. Legacy /api/analyze, /api/context and /ws/live remain
for compatibility; the older standalone context panel is no longer loaded.
Resemble protocol: https://docs.resemble.ai/detect/streaming.
