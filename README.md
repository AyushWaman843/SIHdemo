# CYPHER clip demo

## Conversation safety demo

### Optional semantic context analysis

Add `GROQ_API_KEY=...` and optionally `CONTEXT_MODEL=llama-3.3-70b-versatile` to `.env`, restart, and tick **Use AI context analysis** in the conversation panel. This explicitly enables sending that panel's transcript to Groq for language-model interpretation. Provider model access and charges depend on your account. The secret stays on the server. A model response must include verbatim transcript evidence and pass schema validation. Errors visibly fall back to local checks. No live Groq inference was exercised in the implementation tests; schema/error paths were mocked.

Live finalized utterances use the selected AI mode, with one request at a time and newer pending text replacing older pending work. Provisional microphone transcripts use local checks; recognition can revise these words. The page labels the analysis method for each result. The main clip decision still uses local transcript checks; the optional semantic result is a separate assessment in Conversation safety.

Local checks now connect a received SMS/message containing digits or a number with a subsequent disclosure request (including “share that with me”). Multiple address/contact questions raise review-level concern, not proof of fraud. These patterns and the language model can still misread context; evaluate a representative set of benign and scam conversations before claiming accuracy.

The page now includes Conversation safety below the audio results:

- Select Individual, Financial institution, Enterprise, or Government for tailored response recommendations.
- Start microphone context to assess English speech as the browser recognizes it. This uses the browser speech recognition service (which may send audio to its provider), requires microphone permission, and depends on browser support/connectivity. It is context monitoring, not live Reality Defender analysis. Stop ends recognition; automatic restart is disabled.
- Or select a clearly labeled demo text example / type a transcript and click Check conversation.
- Clip analysis also assesses the actual local Whisper transcript. Missing transcription is UNKNOWN, not low risk.
- Evidence phrases, a rule-based triage score, recommended actions, and a session alert timeline are shown. Download incident summary exports the findings and timeline; full transcripts are omitted, but evidence phrases can contain sensitive information.

High/critical conversation risk escalates the final CYPHER decision even with an authentic-looking voice or missing detector score. No calls, accounts, or transactions are automatically blocked. The risk score and channel score are uncalibrated heuristics; low risk means no listed rule matched, not proven safety. Rules are English-only and may miss paraphrases or misinterpret quotation/negation. No measured reduction in fraud is claimed.

Try the bank/OTP, CEO payment, and remote-access examples, followed by benign security advice. These text examples test the context layer and do not fabricate audio-detector scores.

Optional API upgrades (not configured automatically): Deepgram Live Audio provides streaming transcription (https://developers.deepgram.com/reference/speech-to-text/listen-streaming). Groq Whisper offers file/chunk transcription (https://console.groq.com/docs/speech-to-text). Both can supply transcripts for the context rules; neither endpoint alone establishes whether a conversation is fraudulent. Access, pricing, and limits depend on the provider/account. Local Whisper remains the clip transcription path.

CYPHER is a small, whole-file audio trust check. Select a prepared clip (shown as `Sample 1`, `Sample 2`, …), listen with the level animation, and click **Analyze**. The server sends that file to Reality Defender, then combines the detector result with a local channel-quality profile, optional Whisper intent/risk context, and CYPHER's reliability/action engine.

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create `.env` beside `app.py`:

```text
REALITY_DEFENDER_API_KEY=your_reality_defender_key
WHISPER_MODEL=tiny
```

The key is used only on the server. Start the app and open `http://127.0.0.1:5000/demo`:

```powershell
python app.py
```

## Adding clips

Put audio files directly in `clips/`, or use **Add a clip to the library** in the page. Supported formats are WAV, MP3, FLAC, M4A, AAC, OGG, and ALAC (up to 20 MB). Existing root-level audio files are also discovered for compatibility. The browser labels every file as a neutral Sample number.

## What the result means

- **Detector fake probability** comes from Reality Defender's audio ensemble. `NOT_APPLICABLE` or an API error remains unavailable; it is never treated as real.
- **Channel quality** uses an SNR proxy, spectral bandwidth, frame-level spectral integrity (to catch narrow-band/codec artifacts), silence, speech energy, and clipping.
- **Evidence reliability** and **operational risk** are independent of the detector score. Poor audio lowers reliability; it does not manufacture a higher fake probability.
- If channel quality is below `0.60`, CYPHER shows the provider score for reference but gates the final result to **Uncertain**.
- CYPHER returns **Trusted**, **Suspicious**, or **Uncertain**, with an action such as Continue, Warn, Verify MFA, or Escalate.

Whisper is optional and runs the tiny int8 CPU model when available. If it cannot load, the scan still completes with intent shown as `None`.

## Layout

```text
clips/                         place additional audio here
backend/app/main.py            FastAPI routes and minimal UI
backend/app/detectors/reality_defender.py  upload/poll API client
backend/app/channel.py         local signal-quality profile
backend/app/whisper_context.py optional intent/risk layer
backend/app/engine.py          reliability and action decision
```
