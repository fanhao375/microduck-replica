import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio.microphone_settings import MicrophoneSettings


class TestMicrophoneSettings(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / 'microphone-source.json'
        self.settings = MicrophoneSettings(self.dir)
        self.source = 'onboard'
        self.calls = []

    async def request(self, message):
        self.calls.append(message)
        if message['method'] == 'microphone':
            self.source = message['source']
        return {'microphone_source': self.source, 'listening': False}

    async def test_change_survives_new_manager_and_noop_does_not_set_twice(self):
        status = await self.settings.apply('both', self.request)
        self.assertEqual(status['microphone_source'], 'both')
        self.assertEqual(json.loads(self.path.read_text()), {'source': 'both'})
        await MicrophoneSettings(self.dir).apply('both', self.request)
        self.assertEqual(sum(m['method'] == 'microphone' for m in self.calls), 1)

    async def test_new_pref_is_saved_if_hardware_already_matches(self):
        self.source = 'external'
        await self.settings.apply('external', self.request)
        self.assertEqual(json.loads(self.path.read_text())['source'], 'external')
        self.assertEqual(self.calls, [{'method': 'status'}])

    async def test_failed_disk_rolls_back_active_route(self):
        await self.settings.apply('external', self.request)
        with patch('studio.microphone_settings.os.replace', side_effect=OSError('full')):
            with self.assertRaisesRegex(RuntimeError, '保存'):
                await self.settings.apply('both', self.request)
        self.assertEqual(self.source, 'external')
        self.assertEqual(json.loads(self.path.read_text())['source'], 'external')
        self.assertEqual(list(self.dir.iterdir()), [self.path])

    async def test_lost_set_ack_restores_previous_route(self):
        async def lose_ack(msg):
            state = await self.request(msg)
            if msg.get('source') == 'both':
                raise TimeoutError()
            return state

        with self.assertRaises((RuntimeError, TimeoutError)):
            await self.settings.apply('both', lose_ack)
        self.assertEqual(self.source, 'onboard')
        self.assertFalse(self.path.exists())

    async def test_concurrent_set_is_serial(self):
        async def slow(msg):
            await asyncio.sleep(.001)
            return await self.request(msg)
        await asyncio.gather(self.settings.apply('external', slow), self.settings.apply('both', slow))
        self.assertEqual([m['method'] for m in self.calls], ['status', 'microphone', 'status', 'microphone'])
        self.assertEqual(self.source, json.loads(self.path.read_text())['source'])

    async def test_explicit_onboard_selection_is_saved_on_a_fresh_card(self):
        await self.settings.apply('onboard', self.request)
        self.assertEqual(json.loads(self.path.read_text()), {'source': 'onboard'})
        self.assertEqual(self.calls, [{'method': 'status'}])

    async def test_can_repair_unknown_route_and_corrupt_saved_preference(self):
        self.path.write_text('{"source":"external","unsupported":1}')
        self.source = None
        await self.settings.apply('external', self.request)
        self.assertEqual(self.source, 'external')
        self.assertEqual(json.loads(self.path.read_text()), {'source': 'external'})

    async def test_failed_repair_of_unknown_route_recovers_last_saved(self):
        await self.settings.apply('external', self.request)
        self.source = None
        with patch('studio.microphone_settings.os.replace', side_effect=OSError('full')):
            with self.assertRaisesRegex(RuntimeError, '保存'):
                await self.settings.apply('both', self.request)
        self.assertEqual(self.source, 'external')
        self.assertEqual(json.loads(self.path.read_text()), {'source': 'external'})

    async def test_legacy_status_and_bad_input_do_not_write(self):
        async def legacy_status(message):
            self.calls.append(message)
            return {'listening': False}
        with self.assertRaises(RuntimeError):
            await self.settings.apply('both', legacy_status)
        with self.assertRaises(ValueError):
            await self.settings.apply('usb', self.request)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.calls, [{'method': 'status'}])
