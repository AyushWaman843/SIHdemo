"""CYPHER's trust, reliability, and action decision engine."""

from __future__ import annotations

from .models import ChannelProfile, Decision, DetectorResult, WhisperContext


def evidence_reliability(detector: DetectorResult, channel: ChannelProfile) -> float:
    """Bad audio lowers evidence reliability; it never raises fake probability."""
    detector_confidence = max(0.0, min(1.0, detector.confidence))
    quality = channel.channel_quality_score
    snr = max(0.0, min(1.0, (channel.snr_db - 5.0) / 30.0))
    clipping = max(0.0, min(1.0, 1.0 - channel.clipping_ratio / 0.02))
    speech = channel.speech_energy
    return round(100.0 * (0.40 * detector_confidence + 0.32 * quality + 0.12 * snr + 0.08 * clipping + 0.08 * speech), 1)


def decide(detector: DetectorResult, channel: ChannelProfile, context: WhisperContext) -> Decision:
    reliability = evidence_reliability(detector, channel)
    probability = detector.synthetic_probability
    high_risk = context.operational_risk in {"HIGH", "CRITICAL"}
    if probability is None or detector.error:
        decision, action = "Uncertain", "Continue"
        explanation = detector.error or "Waiting for enough voice-active audio for a detector result."
    elif channel.channel_quality_score < 0.60:
        decision = "Uncertain"
        action = "Verify MFA" if high_risk else "Continue"
        provider_label = detector.label or "the detector"
        explanation = (
            f"{provider_label} returned a score, but channel quality is "
            f"{channel.channel_quality_score:.2f}, below CYPHER's 0.60 trust threshold. "
            "The detector result is shown for reference only."
        )
    elif reliability < 60.0 or 0.35 < probability < 0.65:
        decision = "Uncertain"
        action = "Verify MFA" if high_risk else "Continue"
        explanation = "The signal is not reliable enough for a hard authenticity decision."
    elif probability >= 0.65:
        decision = "Suspicious"
        action = "Escalate" if high_risk else "Warn"
        explanation = "The detector found a strong synthetic-audio signal with reliable evidence."
    else:
        decision, action = "Trusted", "Continue"
        explanation = "The detector found no strong synthetic-audio signal with reliable evidence."
    if high_risk:
        decision, action = 'Suspicious', 'Pause and verify independently'
        explanation = 'Conversation contains a high-risk request: ' + context.intent_category + '. Verify through a known official contact before sending money, credentials, or granting access.'
    elif context.operational_risk == 'MEDIUM':
        action = 'Review request'
        if decision == 'Trusted':
            decision = 'Uncertain'
            explanation = 'Voice authenticity does not establish a safe request. Review the conversation warning signs.'
    elif context.operational_risk == 'UNKNOWN' and decision == 'Trusted':
        decision, action = 'Uncertain', 'Review context'
        explanation = 'No strong synthetic signal, but conversation context is unavailable; safety is not assessed.'
    return Decision(probability, reliability, context.operational_risk, decision, action, explanation, detector.latency_ms, context.latency_ms)
