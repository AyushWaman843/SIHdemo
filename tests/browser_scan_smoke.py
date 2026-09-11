"""Optional browser smoke test; uses fake speech/detector responses, real audio capture.

Run manually after installing playwright. Requires locally installed Chrome.
No audio is sent to external services by this test.
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright
from backend.app import main
from backend.app.models import WhisperContext


class Speech:
    async def transcribe(self, audio):
        return WhisperContext('Please share your OTP with me.', 'OTP request', 'HIGH', 'OTP', 0)


async def detector(path):
    await asyncio.sleep(.1)
    return {'fake_probability': .9, 'confidence': .95, 'status': 'FAKE', 'reasons': []}


def run():
    main.context_layer = Speech()
    main._safe_reality_defender = detector
    # Test the explicit missing-provider path for mic without any external call.
    import os
    os.environ['RESEMBLE_API_KEY'] = ''
    server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=5011, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True, args=[
                '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
                '--autoplay-policy=no-user-gesture-required'])
            page = browser.new_page(permissions=['microphone'], viewport={'width':1100,'height':1100})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:5011/demo')
            page.wait_for_function("document.querySelector('#clip').options.length > 0")
            for _ in range(2):
                page.locator('#start').click()
                page.wait_for_function("document.querySelector('#ratingHeading').textContent.includes('final assessment')", timeout=40000)
                assert 'OTP' in page.locator('#transcript').inner_text()
                assert '90.0%' in page.locator('#cloneScore').inner_text()
                assert page.locator('#rating').inner_text() == 'Suspicious'
                assert float(page.locator('#duration').inner_text().rstrip('s')) > 0
                page.wait_for_function("!document.querySelector('#start').disabled")
            page.locator('#micMode').click()
            page.locator('#start').click()
            page.wait_for_function("document.querySelector('#transcript').textContent.includes('OTP')", timeout=20000)
            page.locator('#stop').click()
            page.wait_for_function("document.querySelector('#ratingHeading').textContent.includes('final assessment')", timeout=10000)
            assert page.locator('#voiceLabel').inner_text() == 'Unavailable'
            assert page.locator('#rating').inner_text() == 'Suspicious'
            assert not errors, errors
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            print('PASS: two clip scans, microphone capture, transcript, separate results, finalization, mobile width; no browser exceptions. Mock providers/ASR.')
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == '__main__':
    run()
