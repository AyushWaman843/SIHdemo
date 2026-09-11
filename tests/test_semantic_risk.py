import asyncio
import unittest
from unittest.mock import patch, Mock
from backend.app.semantic_risk import assess_semantic, _call

class SemanticTests(unittest.TestCase):
    def test_opt_in_and_fallback(self):
        with patch('backend.app.semantic_risk._call', side_effect=RuntimeError('test')) as call:
            asyncio.run(assess_semantic('Hello',enabled=False))
            call.assert_not_called()
            result=asyncio.run(assess_semantic('Share your OTP',enabled=True))
            self.assertEqual(result['level'],'HIGH')
            self.assertIn('fallback',result['method'])

    def test_model_evidence_must_exist(self):
        response=Mock()
        response.json.return_value={'choices':[{'message':{'content':'{"findings":[{"category":"Test","weight":75,"evidence":"invented","reason":"test"}]}'}}]}
        with patch.dict('os.environ',{'GROQ_API_KEY':'test'}), patch('backend.app.semantic_risk.requests.post',return_value=response):
            with self.assertRaises(ValueError): _call('Hello')
