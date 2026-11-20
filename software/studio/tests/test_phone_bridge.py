import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio import phone_bridge


class PhoneAsrTest(unittest.TestCase):
    def test_diagnostics_do_not_persist_uploaded_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            with (patch.object(phone_bridge, 'DEBUG_DIR', folder, create=True),
                  patch.object(phone_bridge, '_load_model', return_value=(object(), None))):
                result = phone_bridge.transcribe_detail(bytes(6400))
            self.assertEqual(result['info']['reason'], 'silent')
            self.assertNotIn('sample', result['info'])
            self.assertEqual(list(Path(folder).iterdir()), [])
