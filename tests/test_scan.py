import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import numpy as np
from fastapi.testclient import TestClient

from backend.app.models import ChannelProfile, DetectorResult, WhisperContext
from backend.app.scan import ScanSession
from backend.app.main import app


class FakeWhisper:
    def __init__(self):
        self.windows = []

    async def transcribe(self, audio):
        self.windows.append(len(audio))
        text = 'You will get a message with six digits.' if len(self.windows) == 1 else 'Please share that with me.'
        return WhisperContext(text, 'None', 'LOW', None, 0)


class FakeDetector:
    def __init__(self):
        self.frames = []
        self.closed = False

    async def connect(self):
        await asyncio.sleep(.01)

    async def stream_detect(self, pcm):
        self.frames.append(pcm)

    async def results(self):
        await asyncio.Future()
        yield

    async def finish(self):
        return DetectorResult(.92, .99, 0, 'resemble')

    async def close(self):
        self.closed = True


class ScanTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_updates_without_provider_and_tail_is_flushed(self):
        messages = []
        async def send(message): messages.append(message)
        whisper = FakeWhisper()
        session = ScanSession(send, whisper)
        await session.start()
        try:
            for _ in range(5):
                await session.ingest(np.zeros(16000, dtype='<i2').tobytes())
            await session.finish()
            final = messages[-1]
            self.assertTrue(final['final'])
            self.assertEqual(whisper.windows, [64000, 16000])
            self.assertEqual(final['context']['level'], 'HIGH')
            self.assertEqual(final['voice']['label'], 'Unavailable')
            self.assertEqual(final['rating'], 'Suspicious')
            self.assertTrue(any(m.get('transcript') and not m.get('final') for m in messages))
        finally:
            await session.close()

    async def test_handshake_buffers_audio_once_and_final_score_is_kept(self):
        detector = FakeDetector()
        session = ScanSession(AsyncMock(), FakeWhisper(), detector)
        await session.start()
        pcm = np.zeros(1600, dtype='<i2').tobytes()
        await session.ingest(pcm)
        await asyncio.sleep(.03)
        await session.ingest(pcm)
        await session.finish()
        self.assertEqual(b''.join(detector.frames), pcm + pcm)
        self.assertEqual(session.result.synthetic_probability, .92)
        await session.close()
        self.assertTrue(detector.closed)

    async def test_voice_fraud_separation_and_quality_gate(self):
        session = ScanSession(AsyncMock(), FakeWhisper())
        session.channel = ChannelProfile(35, 0, 6500, .1, 1, .9, 'GOOD')
        session.result = DetectorResult(.03, .94, 0, 'test')
        from backend.app.risk import assess
        session.context = assess('Share your OTP immediately.')
        session.transcript = 'Share your OTP immediately.'
        result = session.snapshot()
        self.assertEqual(result['voice']['label'], 'No strong clone signal')
        self.assertEqual(result['rating'], 'Suspicious')
        session.channel.channel_quality_score = .4
        self.assertEqual(session.snapshot()['voice']['label'], 'Inconclusive')

    async def test_invalid_pcm_is_rejected(self):
        session = ScanSession(AsyncMock(), FakeWhisper())
        with self.assertRaises(ValueError): await session.ingest(b'123')
        with self.assertRaises(ValueError): await session.ingest(b'00' * 16001)

    async def test_failed_requested_ai_is_not_a_clean_final_assessment(self):
        from backend.app.risk import assess
        session = ScanSession(AsyncMock(), FakeWhisper(), semantic=True)
        session.channel = ChannelProfile(35, 0, 6500, .1, 1, .9, 'GOOD')
        session.result = DetectorResult(.99, .98, 0, 'test')
        session.context = assess('Welcome to this audio book.')
        session.context['ai_status'] = 'failed'
        self.assertEqual(session.snapshot(final=True)['rating'], 'Uncertain')
        session.context = assess('You won the lottery. Send me the 4-digit number on your phone to claim the prize.')
        session.context['ai_status'] = 'failed'
        self.assertEqual(session.snapshot(final=True)['rating'], 'Suspicious')

    async def test_synthetic_benign_context_is_neutral_but_incomplete_is_not(self):
        from backend.app.risk import assess
        session = ScanSession(AsyncMock(), FakeWhisper())
        session.channel = ChannelProfile(35, 0, 6500, .1, 1, .9, 'GOOD')
        session.result = DetectorResult(.99, .98, 0, 'test')
        session.transcript = 'Welcome to this audio book.'
        session.context = assess(session.transcript)
        result = session.snapshot(final=True)
        self.assertEqual(result['voice']['label'], 'Likely synthetic')
        self.assertEqual(result['rating'], 'No fraud indicators detected')
        self.assertEqual(result['detector']['synthetic_probability'], .99)
        session.coverage_incomplete = True
        self.assertEqual(session.snapshot(final=True)['rating'], 'Uncertain')
        session.coverage_incomplete = False
        session.channel.channel_quality_score = .4
        self.assertEqual(session.snapshot()['rating'], 'Uncertain')


class SocketTests(unittest.TestCase):
    def test_microphone_without_key_still_transcribes_and_finishes(self):
        with patch.dict('os.environ', {'RESEMBLE_API_KEY': ''}), patch('backend.app.main.context_layer', FakeWhisper()):
            with TestClient(app) as client:
                self.assertEqual(client.get('/demo').status_code, 200)
                for asset in ('/scan.js', '/capture.js'):
                    self.assertEqual(client.get(asset).status_code, 200)
                with client.websocket_connect('/ws/scan') as ws:
                    ws.send_json({'source': 'mic'})
                    self.assertEqual(ws.receive_json()['type'], 'ready')
                    ws.send_bytes(np.zeros(8000, dtype='<i2').tobytes())
                    ws.send_json({'type': 'end'})
                    for _ in range(12):
                        message = ws.receive_json()
                        if message.get('final'):
                            self.assertIn('message with six digits', message['transcript'])
                            self.assertEqual(message['voice']['label'], 'Unavailable')
                            break
                    else:
                        self.fail('No final result')

    def test_invalid_clip_has_no_provider_call(self):
        with TestClient(app) as client, patch('backend.app.main._safe_reality_defender', new_callable=AsyncMock) as provider:
            with client.websocket_connect('/ws/scan') as ws:
                ws.send_json({'source': 'clip', 'filename': '../.env'})
                self.assertEqual(ws.receive_json()['type'], 'error')
            provider.assert_not_called()
