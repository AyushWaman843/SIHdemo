"""Controlled signal checks, not a validation of perceptual or detector accuracy."""
import unittest
import numpy as np
from scipy.signal import butter, sosfilt
from backend.app.channel import profile_audio


class ChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sr = 16000
        t = np.arange(64000) / cls.sr
        voiced = sum(np.sin(2*np.pi*150*k*t) / k for k in range(1, 31))
        envelope = .15 + .85 * np.sin(2*np.pi*2*t)**2
        cls.clean = (.2 * voiced * envelope).astype(np.float32)

    def test_continuous_tonal_speech_is_not_called_noisy(self):
        t = np.arange(32000) / self.sr
        result = profile_audio(.15*np.sin(2*np.pi*180*t))
        self.assertIsNone(result.snr_db)
        self.assertGreaterEqual(result.channel_quality_score, .6)

    def test_noise_and_clipping_reduce_quality(self):
        rng = np.random.default_rng(42)
        clean = profile_audio(self.clean).channel_quality_score
        noisy = profile_audio(self.clean + rng.normal(0,.20,self.clean.size)).channel_quality_score
        clipped = profile_audio(np.clip(self.clean*8,-1,1)).channel_quality_score
        attenuated_clip = profile_audio(np.clip(self.clean*8,-1,1)*.3).channel_quality_score
        self.assertGreater(clean, noisy)
        self.assertGreater(clean, clipped)
        self.assertGreater(clean, attenuated_clip)
        self.assertLess(clipped, .6)

    def test_band_limiting_has_modest_penalty(self):
        filtered = sosfilt(butter(8,1000,fs=self.sr,output='sos'),self.clean)
        clean, narrow = profile_audio(self.clean), profile_audio(filtered)
        self.assertLess(narrow.bandwidth_hz, clean.bandwidth_hz)
        self.assertLess(narrow.channel_quality_score, clean.channel_quality_score)

    def test_silence_invalid_short_and_gain(self):
        for audio in (np.zeros(32000),np.array([]),np.full(16000,np.nan),np.ones(100)):
            self.assertEqual(profile_audio(audio).degradation_label, 'INSUFFICIENT')
        normal, quieter = profile_audio(self.clean), profile_audio(self.clean*.5)
        self.assertAlmostEqual(normal.channel_quality_score,quieter.channel_quality_score,places=3)

    def test_pause_is_not_a_large_quality_penalty(self):
        normal = profile_audio(self.clean).channel_quality_score
        paused = profile_audio(np.concatenate([np.zeros(16000),self.clean,np.zeros(16000)]))
        self.assertGreaterEqual(paused.channel_quality_score,normal-.05)
        self.assertIsNotNone(paused.snr_db)
