"""CYPHER's trust, reliability, and action decision engine."""

from __future__ import annotations

from .models import ChannelProfile, Decision, DetectorResult, WhisperContext


def evidence_reliability(detector: DetectorResult, channel: ChannelProfile) -> float:
    """Bad audio lowers evidence reliability; it never raises fake probability."""
    detector_confidence = max(0.0, min(1.0, detector.confidence))
    quality = channel.channel_quality_score
    speech = channel.speech_energy
    # Noise and clipping already contribute to quality; do not count them twice.
    return round(100.0 * (0.40 * detector_confidence + 0.52 * quality + 0.08 * speech), 1)


def assess_voice(detector: DetectorResult, channel: ChannelProfile) -> Decision:
    """Assess authenticity only; synthetic audio is not evidence of fraud."""
    reliability = evidence_reliability(detector, channel)
    probability = detector.synthetic_probability
    if probability is None or detector.error:
        decision, action = "Uncertain", "Continue"
        explanation = detector.error or "Waiting for enough voice-active audio for a detector result."
    elif channel.channel_quality_score < 0.60:
        decision = "Uncertain"
        action = "Review audio quality"
        provider_label = detector.label or "the detector"
        explanation = (
            f"{provider_label} returned a score, but channel quality is "
            f"{channel.channel_quality_score:.2f}, below CYPHER's 0.60 trust threshold. "
            "The detector result is shown for reference only."
        )
    elif reliability < 60.0 or 0.35 < probability < 0.65:
        decision = "Uncertain"
        action = "Review voice evidence"
        explanation = "The signal is not reliable enough for a hard authenticity decision."
    elif probability >= 0.65:
        decision = "Synthetic"
        action = "Continue"
        explanation = "The detector found a strong synthetic-audio signal with reliable evidence."
    else:
        decision, action = "Trusted", "Continue"
        explanation = "The detector found no strong synthetic-audio signal with reliable evidence."
    return Decision(probability, reliability, "UNKNOWN", decision, action, explanation, detector.latency_ms, 0)


def decide(detector: DetectorResult, channel: ChannelProfile, context: WhisperContext) -> Decision:
    voice = assess_voice(detector, channel)
    decision, action, explanation = voice.decision, voice.action, voice.explanation
    if context.operational_risk in {"HIGH", "CRITICAL"}:
        decision, action = 'Suspicious', 'Pause and verify independently'
        explanation = 'Conversation contains a high-risk request: ' + context.intent_category + '. Verify through a known official contact before sending money, credentials, or granting access.'
    elif context.operational_risk == 'MEDIUM':
        decision, action = 'Uncertain', 'Review request'
        explanation = 'Conversation contains warning signs that need review. ' + voice.explanation
    elif context.operational_risk != 'LOW':
        decision, action = 'Uncertain', 'Review context'
        explanation = 'Conversation context is unavailable or incomplete; fraud risk is not assessed. ' + voice.explanation
    elif voice.decision == 'Synthetic':
        decision, action = 'No fraud indicators detected', 'No fraud escalation; verify identity before sensitive actions'
        explanation = 'The voice is likely synthetic, but no fraud indicators were detected in the assessed conversation. Synthetic speech alone does not imply fraud. Identity and legitimacy remain unverified.'
    return Decision(voice.synthetic_probability, voice.evidence_reliability, context.operational_risk, decision, action, explanation, detector.latency_ms, context.latency_ms)
