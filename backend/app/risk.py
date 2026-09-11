"""Explainable English-language conversation triage, not a fraud probability."""
import re

# Each rule needs an actionable phrase; merely mentioning banking is insufficient.
RULES = [
    ('Credentials requested', 75, r'\b(?:share|tell|send|read|give|provide)\b.{0,65}\b(?:otp|one.time password|password|pin|verification code|security code)\b'),
    ('Remote access requested', 70, r'\b(?:install|download|open|enable)\b.{0,45}\b(?:anydesk|teamviewer|remote access|screen shar\w*)\b'),
    ('Payment requested', 45, r'\b(?:transfer|send|pay|deposit|move)\b.{0,55}\b(?:money|funds|rupees|payment|account|wallet|crypto|gift cards?)\b'),
    ('Secrecy or verification bypass', 40, r"\b(?:keep (?:this|it) (?:secret|confidential)|do not tell|don't tell|bypass approval|skip verification)\b"),
    ('Threat or coercion', 40, r'\b(?:arrest|account.{0,20}(?:blocked|frozen|suspended)|legal action|digital arrest)\b'),
    ('Authority claim', 20, r"\b(?:i am|i'm|this is|calling from|we are)\b.{0,45}\b(?:bank|police|cbi|rbi|ceo|it support|government|tax department)\b"),
    ('Urgency', 15, r'\b(?:urgent|immediately|right now|within.{0,12}minutes)\b'),
    ('Sensitive data requested', 60, r'\b(?:send|share|upload|give)\b.{0,50}\b(?:customer data|employee records|aadhaar|aadhar|confidential files|classified documents)\b'),
]
SECTOR_ACTION = {
    'individual': 'End the request and call the organisation using an independently obtained official number.',
    'financial': 'Pause the transaction for human review and verify through the bank’s established callback process.',
    'enterprise': 'Pause payment or access changes; verify through a known internal contact and notify the security team.',
    'government': 'Pause disclosure or access changes; verify through the official directory and notify the security officer.',
}

def assess(text: str, sector: str = 'individual') -> dict:
    text = text.replace('\u2019', "'")
    findings = []
    actionable = []
    for sentence in re.split(r'[.!?;\n]+', text):
        # Exclude common safety advice/negated requests, not all quoted discussion.
        if re.search(r"\b(?:never|do not|don't|should not|must not)\s+(?:ever\s+)?(?:share|send|give|tell|install|transfer|pay|provide|read)\b", sentence, re.I):
            continue
        actionable.append(sentence.strip())
        for title, weight, pattern in RULES:
            match = re.search(pattern, sentence, re.I)
            if match and not any(f['category'] == title for f in findings):
                findings.append({'category': title, 'weight': weight, 'evidence': match.group(0).strip()})
    # Resolve indirect requests against nearby conversation, including ASR errors
    # like 'full digit number'. A phone number by itself is not a credential.
    conversation = '. '.join(actionable)
    delivery = r'\b(?:message|sms|text|notification)\b'
    token = r'\b(?:code|digits?|(?:four|six|4|6|full)[ -]digit|number)\b'
    request = r'\b(?:share|read|tell|give|send|repeat|say|provide)\b.{0,65}\b(?:me|us)\b'
    for match in re.finditer(delivery, conversation, re.I):
        nearby = conversation[max(0, match.start()-80):match.end()+280]
        if re.search(token, nearby, re.I) and re.search(request, nearby, re.I):
            # Tracking/order numbers are not login credentials by themselves.
            if re.search(r'\b(?:order|tracking|parcel|ticket|booking)\b', nearby, re.I) and not re.search(r'\b(?:otp|login|verification|security)\b', nearby, re.I):
                continue
            findings.append({'category':'Possible verification-code harvesting','weight':75,
                             'evidence':nearby.strip(), 'reason':'Caller links a received message/number to a request to disclose it; possible OTP even without that word.'})
            break
    personal = [
        r'\b(?:what(?: is|\'s)? (?:your|a) name|tell me your name)\b',
        r'\b(?:where do you live|(?:your|home|exact) address)\b',
        r'\b(?:what(?: is|\'s)? (?:your|the) phone number|tell me your phone number)\b',
    ]
    details = [m.group(0) for pattern in personal if (m := re.search(pattern, conversation, re.I))]
    if len(details) >= 2:
        findings.append({'category':'Personal information collection','weight':30,'evidence':'; '.join(details),
                         'reason':'Multiple identity/contact questions warrant review; they can also occur in legitimate calls.'})
    return result_from_findings(text, sector, findings)

def result_from_findings(text: str, sector: str, findings: list) -> dict:
    score = min(100, sum(f['weight'] for f in findings))
    level = 'UNKNOWN' if not text.strip() else 'CRITICAL' if score >= 85 else 'HIGH' if score >= 65 else 'MEDIUM' if score >= 30 else 'LOW'
    action = SECTOR_ACTION.get(sector, SECTOR_ACTION['individual']) if score >= 65 else 'Verify the request before taking sensitive actions.' if score >= 30 else 'No listed warning pattern found; identity and legitimacy remain unverified.' if text.strip() else 'Context unavailable. Obtain intelligible speech or enter a transcript for review.'
    return {'score': score if text.strip() else None, 'level': level, 'findings': findings,
            'action': action, 'sector': sector, 'method': 'Local conversation patterns; uncalibrated triage score',
            'assessment': 'Potential social engineering' if score >= 65 else 'Review request' if score >= 30 else 'No listed warning pattern' if text.strip() else 'Not assessed'}
