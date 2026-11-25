"""All hardware writes exercise an in-memory model of amixer cget/cset."""
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import install


class MicrophoneRoutesTests(unittest.TestCase):
    mic = 'Right PGA Mixer Mic3R Switch'
    line = 'Right PGA Mixer Line1L Switch'

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'microphone.json'
        self.values = dict(install.CAPTURE_SETTINGS)
        self.calls = []
        self.reject = None

        def run(args, check=True):
            self.assertEqual(args[:3], ['amixer', '-c', 'aic3104'])
            if args[3] == 'controls':
                return SimpleNamespace(stdout='\n'.join("name='%s'" % n for n in self.values))
            name = args[4].removeprefix('name=')
            if args[3] == 'cset':
                self.calls.append((name, args[5]))
                self.values[name] = args[5]
                if self.reject and self.reject(name, args[5]):
                    raise subprocess.CalledProcessError(1, args)
            return SimpleNamespace(stdout='  : values=' + self.values[name])

        self.addCleanup(patch.stopall)
        patch.object(install, 'run', side_effect=run).start()

    def routes(self):
        self.assertTrue(hasattr(install, 'MicrophoneRoutes'), 'microphone route selection is missing')
        return install.MicrophoneRoutes('aic3104', self.path)

    def test_all_modes_route_into_the_right_adc_and_do_not_touch_gain_or_speaker(self):
        routes = self.routes()
        routes.restore()
        self.assertEqual(routes.source, 'onboard')
        for source, mic, line in [('external', 'off', 'on'), ('both', 'on', 'on'), ('onboard', 'on', 'off')]:
            routes.select(source)
            self.assertEqual((self.values[self.mic], self.values[self.line]), (mic, line))
            self.assertEqual(routes.source, source)
            self.assertFalse(self.path.exists())
        self.assertTrue(all(name in (self.mic, self.line) for name, _ in self.calls))

    def test_daemon_restore_reapplies_studio_preference_after_original_boot_prepare(self):
        routes = self.routes()
        self.path.write_text(json.dumps({'source': 'both'}))
        backup = self.path.with_name('baseline.json')
        baseline = {'card': 'aic3104', 'values': {'saved': 'untouched'}}
        backup.write_text(json.dumps(baseline))
        install.prepare_microphone('aic3104', backup)
        self.assertEqual(json.loads(backup.read_text()), baseline)
        self.assertEqual(self.values[self.line], 'off')
        routes.restore()
        self.assertEqual(routes.source, 'both')
        self.assertEqual(self.values[self.line], 'on')
        self.assertEqual(self.values[self.mic], 'on')

    def test_partial_hardware_failure_restores_previous_route(self):
        routes = self.routes()
        routes.select('external')
        expected = self.values.copy()
        self.reject = lambda n, v: n == self.line and v == 'off'
        with self.assertRaises((RuntimeError, subprocess.SubprocessError)):
            routes.select('onboard')
        self.assertEqual(self.values, expected)
        self.assertEqual(routes.source, 'external')

    def test_corrupt_setting_is_reported_and_explicit_selection_recovers(self):
        self.path.write_text('{"source":["external"]}')
        routes = self.routes()
        with self.assertRaises(ValueError):
            routes.restore()
        self.assertIsNone(routes.source)
        self.assertEqual(self.calls, [])
        routes.select('onboard')
        self.assertEqual(routes.source, 'onboard')

    def test_failed_mixer_rollback_reports_unknown_route(self):
        routes = self.routes()
        routes.select('external')
        self.reject = lambda n, v: n == self.line
        with self.assertRaises(RuntimeError):
            routes.select('onboard')
        self.assertIsNone(routes.source)

    def test_routing_only_applies_to_addressable_right_channel_alsa_devices(self):
        self.assertTrue(hasattr(install.MicrophoneRoutes, 'for_capture'), 'ALSA card detection is missing')
        factory = install.MicrophoneRoutes.for_capture
        self.assertEqual(factory('plughw:CARD=aic3104,DEV=0', 1).card, 'aic3104')
        self.assertEqual(factory('hw:0,0', 1).card, '0')
        self.assertIsNone(factory('default', 1))
        self.assertIsNone(factory('plughw:CARD=aic3104,DEV=0', 0))

    def test_onboard_alias_works_without_external_control_and_reports_unavailable_mode(self):
        self.values['Right PGA Mixer Mic2R Switch'] = self.values.pop(self.mic)
        del self.values[self.line]
        routes = self.routes()
        routes.restore()
        self.assertEqual(routes.source, 'onboard')
        with self.assertRaises(RuntimeError):
            routes.select('both')
        self.assertEqual(routes.source, 'onboard')
        self.assertEqual(self.calls, [])
        self.assertFalse(self.path.exists())
