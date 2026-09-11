import unittest
from backend.app.risk import assess
from backend.app.engine import decide
from backend.app.models import DetectorResult, ChannelProfile, WhisperContext

class RiskTests(unittest.TestCase):
    def test_indirect_code_and_personal_questions(self):
        text = 'hi can you tell me what a name is where do you live exactly yeah and what is the phone number stop your text you will get a message giving you a full digit number and can you please share that with me okay yeah thank you'
        result = assess(text)
        self.assertEqual(result['level'], 'CRITICAL')
        self.assertIn('Possible verification-code harvesting', [f['category'] for f in result['findings']])
        self.assertEqual(assess('You will receive a text with six digits. Please read it to me.')['level'], 'HIGH')
        self.assertEqual(assess('Where do you live? What is your phone number?')['level'], 'MEDIUM')

    def test_delivery_and_negation(self):
        self.assertEqual(assess('Your parcel message has a tracking number. Please share that with me.')['level'], 'LOW')
        self.assertEqual(assess('You will get an SMS with a number. Never share it with me.')['level'], 'LOW')

    def test_advice_is_not_a_request(self):
        self.assertEqual(assess('Never share your OTP or password.')['level'], 'LOW')
        self.assertEqual(assess('Your bank account balance is available.')['level'], 'LOW')

    def test_scam_and_unknown(self):
        self.assertEqual(assess('I am calling from your bank. Share your OTP immediately.')['level'], 'CRITICAL')
        self.assertEqual(assess('')['level'], 'UNKNOWN')
        self.assertEqual(assess('Install AnyDesk on your computer.')['level'], 'HIGH')

    def test_real_voice_and_missing_detector_still_escalate(self):
        channel=ChannelProfile(35,0,6500,.1,1,.9,'GOOD')
        context=WhisperContext('Share your OTP','Credentials requested','HIGH','Share your OTP',0)
        for probability in (.03, None):
            result=decide(DetectorResult(probability,.94,0,'test'),channel,context)
            self.assertEqual(result.decision,'Suspicious')
            self.assertIn('verify',result.action)
            self.assertEqual(result.synthetic_probability,probability)

if __name__ == '__main__': unittest.main()
