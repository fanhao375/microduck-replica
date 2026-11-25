"""Exercise the Settings card through an authenticated Studio and a local audio fixture."""
import asyncio
import json
import os
import unittest
from unittest import mock

from tests import test_http as http
from tests import test_vpad_e2e as browser
from studio import audio_interaction as audio

try:
    from playwright.async_api import async_playwright, expect
except ImportError:
    async_playwright = None


class MicrophoneHttp(unittest.IsolatedAsyncioTestCase):
    make_pad = http.HttpTest.make_pad
    call = http.HttpTest.call
    login = http.HttpTest.login
    asyncTearDown = http.HttpTest.asyncTearDown

    async def asyncSetUp(self):
        await http.HttpTest.asyncSetUp(self)
        self.state = {'microphone_source': 'both', 'microphone_level_dbfs': -32.4,
                      'listening': True, 'runtime_root': '/private', 'last': {'command': 'private'}}
        self.requests = []
        self.reject = None

        def dispatch(message, **kwargs):
            self.requests.append(message)
            if message.get('method') == 'microphone':
                if message['source'] == self.reject:
                    return {'ok': False, 'error': 'mixer busy'}
                self.state['microphone_source'] = message['source']
            return {'ok': True, 'status': dict(self.state)}

        self.wire = self.enterContext(mock.patch.object(audio, 'audio_request', side_effect=dispatch))
        self.token, _ = await self.login()
        self.cookie = f'{http.COOKIE}={self.token}'
        _, _, raw = await self.call('GET', '/api/session', headers={'Cookie': self.cookie})
        self.headers = {'Cookie': self.cookie, 'X-CSRF': json.loads(raw)['csrf'], 'Origin': self.origin}

    async def test_scoped_status_and_confirmed_changes_only(self):
        code, headers, raw = await self.call('GET', '/api/microphone', headers=self.headers)
        self.assertEqual(code, 200, raw)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertEqual(json.loads(raw)['data'], {'microphone_source': 'both', 'listening': True,
                         'microphone_level_dbfs': -32.4, 'error': None})
        for source in ('external', 'onboard', 'both'):
            code, _, raw = await self.call('POST', '/api/microphone', {'source': source}, self.headers)
            self.assertEqual(code, 200, raw)
            self.assertEqual(json.loads(raw)['data']['microphone_source'], source)
            self.assertEqual(json.loads((self.auth.dir / 'microphone-source.json').read_text()), {'source': source})
        changes = [call for call in self.wire.call_args_list if call.args[0] != {'method': 'status'}]
        self.assertEqual([call.args[0] for call in changes],
                         [{'method': 'microphone', 'source': s} for s in ('external', 'onboard', 'both')])
        self.assertTrue(all(call.kwargs['timeout'] >= 5 for call in changes))

    async def test_auth_origin_and_bounded_input(self):
        for method in ('GET', 'POST'):
            for headers, expected in (({}, 401), ({'Cookie': self.cookie}, 403),
                    ({**self.headers, 'Origin': 'https://other.example'}, 403)):
                code, _, _ = await self.call(method, '/api/microphone', headers=headers)
                self.assertEqual(code, expected, (method, headers))
        without_origin = {k: v for k, v in self.headers.items() if k != 'Origin'}
        code, _, _ = await self.call('POST', '/api/microphone', {'source': 'onboard'}, without_origin)
        self.assertEqual(code, 403)
        for body, expected in (({'source': 'other'}, 400), ({'source': []}, 400),
                ({'source': 'external', 'listening': True}, 400), (b'null', 400), (b'{'*2048, 413)):
            code, _, _ = await self.call('POST', '/api/microphone', body, self.headers)
            self.assertEqual(code, expected)
        self.assertEqual(self.requests, [])
        # Same-origin browser GETs normally omit Origin.
        code, _, _ = await self.call('GET', '/api/microphone', headers=without_origin)
        self.assertEqual(code, 200)

    async def test_no_guessed_source_or_stale_level(self):
        self.reject = 'external'
        code, _, raw = await self.call('POST', '/api/microphone', {'source': 'external'}, self.headers)
        self.assertEqual(code, 503)
        self.assertIn('mixer busy', json.loads(raw)['error'])
        self.state.update(listening=False, microphone_source=None, error='mixer unavailable')
        code, _, raw = await self.call('GET', '/api/microphone', headers=self.headers)
        self.assertEqual(code, 200)
        data = json.loads(raw)['data']
        self.assertIsNone(data['microphone_source'])
        self.assertIsNone(data['microphone_level_dbfs'])
        self.assertEqual(data['error'], 'mixer unavailable')
        del self.state['microphone_source']
        code, _, raw = await self.call('GET', '/api/microphone', headers=self.headers)
        self.assertEqual(code, 503)
        self.assertIn('更新', json.loads(raw)['error'])
        self.wire.side_effect = OSError()
        code, _, raw = await self.call('GET', '/api/microphone', headers=self.headers)
        self.assertEqual(code, 503)
        self.assertIn('duck-echo', json.loads(raw)['error'])

    @unittest.skipUnless(async_playwright and browser.EDGE and os.getenv('STUDIO_E2E') != '0',
                         'requires Playwright and Edge')
    async def test_settings_in_browser(self):
        async with async_playwright() as pw:
            edge = await pw.chromium.launch(executable_path=browser.EDGE, headless=True)
            try:
                page = await edge.new_page(viewport={'width': 390, 'height': 844})
                await page.context.add_cookies([{'name': http.COOKIE, 'value': self.token, 'url': self.origin}])
                await page.goto(self.origin + '/#/settings')
                card = page.get_by_role('region', name='麦克风输入')
                external = card.get_by_role('button', name='外接麦克风（J2 左口）')
                both = card.get_by_role('button', name='板载＋外接')
                onboard = card.get_by_role('button', name='板载麦克风')
                await expect(both).to_have_attribute('aria-pressed', 'true')
                await expect(card).to_contain_text('-32.4 dBFS')
                # An operator can keep a route already set on the codec as the boot preference.
                async with page.expect_response(lambda r: r.url.endswith('/api/microphone')
                                                and r.request.method == 'POST', timeout=2500):
                    await both.click()
                self.assertEqual(json.loads((self.auth.dir / 'microphone-source.json').read_text()), {'source': 'both'})
                await external.click()
                await expect(external).to_have_attribute('aria-pressed', 'true')
                self.reject = 'onboard'
                await onboard.click()
                await expect(card).to_contain_text('mixer busy')
                await expect(onboard).to_have_attribute('aria-pressed', 'false')
                self.state['listening'] = False
                await expect(card).to_contain_text('监听已关闭')
                await expect(card).not_to_contain_text('-32.4 dBFS')
                await card.get_by_text('比较收音距离').click()
                await expect(card).to_contain_text('叫一声')
                self.assertLessEqual(await card.evaluate('(el) => el.scrollWidth - el.clientWidth'), 1)
                self.assertTrue(all(r['method'] in ('status', 'microphone') for r in self.requests))
                movement = {'robot.init', 'robot.enable', 'robot.do', 'robot.head', 'robot.pose', 'robot.move'}
                self.assertFalse(any(method in movement for _, method in self.fake.calls))
                await page.evaluate("""Object.defineProperty(document, 'hidden', {value:true, configurable:true});
                    document.dispatchEvent(new Event('visibilitychange'));""")
                await asyncio.sleep(.2)
                reads = len(self.requests)
                await asyncio.sleep(2.6)
                self.assertEqual(len(self.requests), reads)
                self.state.pop('microphone_source')
                await page.evaluate("""delete document.hidden; document.dispatchEvent(new Event('visibilitychange'));""")
                await expect(card).to_contain_text('更新')
                await expect(external).to_be_disabled()
                await page.evaluate("location.hash = '#/logs'")
                await card.wait_for(state='detached')
                await asyncio.sleep(.2)
                reads = len(self.requests)
                await asyncio.sleep(2.6)
                self.assertEqual(len(self.requests), reads)
                await page.goto(self.origin + '/m.html#/voice')
                await expect(page.get_by_role('button', name='鸭子麦克风', exact=True)).to_be_visible()
                await page.evaluate("location.hash = '#/more'")
                await expect(page.get_by_role('button', name='鸭子麦克风 鸭子自己听，离线识别')).to_be_visible()
            finally:
                await edge.close()
