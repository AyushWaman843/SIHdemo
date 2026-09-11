"""Lightweight impairment screening, not calibrated perceptual quality or SNR."""
from __future__ import annotations

import numpy as np
from .models import ChannelProfile


def profile_audio(audio: np.ndarray, sample_rate: int = 16000) -> ChannelProfile:
    """Score observable impairments without treating speech tonality as damage.

    25 ms frames exclude pauses from spectral statistics. A noise-floor proxy
    is reported only with >=100 ms of consecutive low-energy reference audio.
    Unknown noise is explicit; it is not assigned zero dB. Bandwidth is occupied
    speech bandwidth (99.5% energy rolloff), not an inferred codec cutoff.
    Thresholds are transparent prototype choices, not fitted model accuracy.
    """
    if sample_rate < 8000:
        raise ValueError("Channel profiling requires a sample rate of at least 8 kHz.")
    y = np.asarray(audio, dtype=np.float32).reshape(-1)
    notes = []
    if not y.size or not np.all(np.isfinite(y)):
        return ChannelProfile(None, 0, 0, 1, 0, 0, "INSUFFICIENT", ("No valid audio samples.",))
    # Remove DC offset before measuring usable level or spectral distribution.
    centered = y - np.mean(y)
    n = int(sample_rate * .025)
    if len(y) < n * 4:
        return ChannelProfile(None, 0, 0, 1, 0, 0, "INSUFFICIENT", ("Need at least 100 ms of audio.",))
    frames = centered[:len(y) // n * n].reshape(-1, n)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-16)
    level = float(np.percentile(rms, 85))
    if level < 1e-5:
        return ChannelProfile(None, 0, 0, 1, 0, 0, "INSUFFICIENT", ("No usable audio energy.",))
    active = rms >= max(level * .20, 1e-5)
    active_seconds = float(active.sum() * n / sample_rate)
    silence = float(np.mean(~active))
    # Avoid volume-normalizing before clipping: preserve both full-scale peaks
    # and repeated flat tops produced by clipping followed by attenuation.
    full_scale = float(np.mean(np.abs(y) >= .999))
    peak = float(np.max(np.abs(centered)))
    flat_top = ((np.abs(centered[1:-1]) >= .98 * peak)
                & (np.abs(np.diff(centered)[:-1]) < max(1e-7, peak * 1e-5))
                & (np.abs(np.diff(centered)[1:]) < max(1e-7, peak * 1e-5)))
    clipping = max(full_scale, float(np.mean(flat_top)))

    quiet = rms < level * .25
    # A consecutive run is less likely to be a single weak phoneme or padding.
    runs = np.convolve(quiet.astype(int), np.ones(4, dtype=int), mode='valid') == 4
    reference = np.convolve(runs.astype(int), np.ones(4, dtype=int), mode='full') > 0
    snr = None
    noise_penalty = 0.0
    if np.any(reference):
        floor = float(np.median(rms[reference]))
        snr = float(np.clip(20 * np.log10(level / max(floor, 1e-8)), 0, 60))
        noise_penalty = .45 * float(np.clip((25 - snr) / 20, 0, 1))
    else:
        notes.append("Noise floor unavailable: no clear low-energy reference interval.")

    powers = np.abs(np.fft.rfft(frames[active] * np.hanning(n), axis=1)) ** 2
    frequencies = np.fft.rfftfreq(n, 1 / sample_rate)
    speech_band = (frequencies >= 100) & (frequencies <= min(7600, sample_rate / 2))
    band = powers[:, speech_band]
    floor_power = np.maximum(band, np.max(band, axis=1, keepdims=True) * 1e-10 + 1e-20)
    flatness = np.exp(np.mean(np.log(floor_power), axis=1)) / np.mean(floor_power, axis=1)
    # Only broad noise-like spectra contribute. Low flatness (voiced/tonal
    # speech) is never a penalty. Use median to tolerate isolated fricatives.
    noise_like = .55 * float(np.clip((np.median(flatness) - .15) / .40, 0, 1))
    noise_penalty = max(noise_penalty, noise_like)
    cumulative = np.cumsum(powers, axis=1)
    rolloff_bins = np.argmax(cumulative >= cumulative[:, -1:] * .995, axis=1)
    bandwidth = float(np.percentile(frequencies[rolloff_bins], 85))
    # A modest loss of information, not proof of a damaged channel.
    bandwidth_penalty = .25 * float(np.clip((3200 - bandwidth) / 2400, 0, 1))
    clip_penalty = .70 * float(np.clip(clipping / .04, 0, 1))
    dbfs = 20 * np.log10(max(level, 1e-8))
    quiet_penalty = .30 * float(np.clip((-45 - dbfs) / 20, 0, 1))
    quality = float(np.clip(.95 - noise_penalty - bandwidth_penalty - clip_penalty - quiet_penalty, 0, 1))
    if noise_penalty > .05:
        notes.append("Noise-like energy or limited separation from the estimated noise floor.")
    if bandwidth_penalty > .02:
        notes.append("Limited occupied bandwidth; may reflect voice content, not channel damage.")
    if clip_penalty > .02:
        notes.append("Full-scale or repeated flat-topped samples suggest clipping.")
    if quiet_penalty > .02:
        notes.append("Audio level is very low.")
    if active_seconds < .25:
        quality = min(quality, .40)
        notes.append("Too little active audio for a stable quality assessment.")
    speech_energy = float(np.clip(active_seconds / .5, 0, 1))
    label = "GOOD" if quality >= .72 else "MODERATE" if quality >= .45 else "POOR"
    return ChannelProfile(snr, clipping, bandwidth, silence, speech_energy, quality, label, tuple(notes))
