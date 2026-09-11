"""Small, explainable channel-quality profiler for 16 kHz mono PCM."""

from __future__ import annotations

import numpy as np

from .models import ChannelProfile


def profile_audio(audio: np.ndarray, sample_rate: int = 16000) -> ChannelProfile:
    """Estimate quality from dynamics, noise floor, bandwidth, silence, and clipping."""
    y = np.asarray(audio, dtype=np.float32).reshape(-1)
    if y.size == 0:
        return ChannelProfile(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, "POOR")
    abs_y = np.abs(y)
    clipping = float(np.mean(abs_y >= 0.985))
    frame_size = max(256, int(sample_rate * 0.025))
    frame_count = max(1, int(np.ceil(len(y) / frame_size)))
    padded = np.pad(y, (0, frame_count * frame_size - len(y)))
    frames = padded.reshape(frame_count, frame_size)
    frame_rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    noise = float(np.percentile(frame_rms, 15))
    speech = float(np.percentile(frame_rms, 85))
    snr_db = float(np.clip(20.0 * np.log10((speech + 1e-6) / (noise + 1e-6)), 0.0, 60.0))
    silence_ratio = float(np.mean(frame_rms < max(noise * 1.8, 0.003)))
    speech_energy = float(np.clip((speech - 0.005) / 0.08, 0.0, 1.0))
    # A narrow-band/codec-damaged signal can retain a deceptively high RMS SNR.
    # Measure frame spectral flatness as a cheap independent artifact check.
    frame_spectrum = np.abs(np.fft.rfft(frames * np.hanning(frame_size), axis=1)) + 1e-10
    frame_frequencies = np.fft.rfftfreq(frame_size, 1.0 / sample_rate)
    band = (frame_frequencies >= 300.0) & (frame_frequencies <= min(7600.0, sample_rate / 2.0))
    band_power = frame_spectrum[:, band]
    flatness = np.exp(np.mean(np.log(band_power), axis=1)) / (np.mean(band_power, axis=1) + 1e-12)
    median_flatness = float(np.median(flatness))
    low_flatness_artifact = float(np.clip((0.08 - median_flatness) / 0.08, 0.0, 1.0))
    high_flatness_artifact = float(np.clip((median_flatness - 0.55) / 0.35, 0.0, 1.0))
    spectral_artifact = max(low_flatness_artifact, high_flatness_artifact)

    spectrum = np.abs(np.fft.rfft(y * np.hanning(len(y)))) ** 2 + 1e-12
    frequencies = np.fft.rfftfreq(len(y), 1.0 / sample_rate)
    total = float(np.sum(spectrum))
    bandwidth_hz = float(frequencies[np.searchsorted(np.cumsum(spectrum), total * 0.95)]) if total else 0.0
    snr_score = float(np.clip((snr_db - 6.0) / 30.0, 0.0, 1.0))
    bandwidth_score = float(np.clip((bandwidth_hz - 1800.0) / 5000.0, 0.0, 1.0))
    clipping_score = 1.0 - float(np.clip(clipping / 0.02, 0.0, 1.0))
    silence_score = 1.0 - float(np.clip((silence_ratio - 0.65) / 0.35, 0.0, 1.0))
    base_quality = 0.42 * snr_score + 0.25 * bandwidth_score + 0.18 * clipping_score + 0.15 * silence_score
    quality = float(np.clip(base_quality - 0.30 * spectral_artifact, 0.0, 1.0))
    label = "GOOD" if quality >= 0.72 else "MODERATE" if quality >= 0.45 else "POOR"
    return ChannelProfile(snr_db, clipping, bandwidth_hz, silence_ratio, speech_energy, quality, label)
