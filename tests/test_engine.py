import unittest

from backend.app.engine import decide
from backend.app.models import ChannelProfile, DetectorResult, WhisperContext


class DecisionTests(unittest.TestCase):
    def test_fraud_escalation_requires_context_regardless_of_voice(self):
        channel = ChannelProfile(35, 0, 6500, .1, 1, .9, 'GOOD')
        for probability in (.03, .99, None):
            for risk in ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL', 'UNKNOWN'):
                with self.subTest(probability=probability, risk=risk):
                    result = decide(DetectorResult(probability, .98, 0, 'test'), channel,
                                    WhisperContext('Example', 'Example request', risk, None, 0))
                    self.assertEqual(result.decision == 'Suspicious', risk in {'HIGH', 'CRITICAL'})
                    if risk in {'MEDIUM', 'UNKNOWN'}:
                        self.assertEqual(result.decision, 'Uncertain')
                    if probability is None and risk == 'LOW':
                        self.assertEqual(result.decision, 'Uncertain')

    def test_quality_gate_preserves_raw_synthetic_score(self):
        result = decide(DetectorResult(.99, .98, 0, 'test'),
                        ChannelProfile(35, 0, 6500, .1, 1, .4, 'POOR'),
                        WhisperContext('Welcome', 'None', 'LOW', None, 0))
        self.assertEqual(result.decision, 'Uncertain')
        self.assertEqual(result.synthetic_probability, .99)
