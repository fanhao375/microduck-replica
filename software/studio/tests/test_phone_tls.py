import importlib.util
import os
from pathlib import Path
import shutil
import ssl
import tempfile
import unittest


@unittest.skipUnless(os.name == "posix" and shutil.which("openssl"), "Linux OpenSSL required")
class InstalledPhoneTlsTests(unittest.TestCase):
    def test_first_software_update_and_certificate_renewal(self):
        path = Path(__file__).resolve().parents[1] / "deploy" / "phone_tls.py"
        spec = importlib.util.spec_from_file_location("installed_phone_tls", path)
        tls = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tls)
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "microduck" / "tls"
            tls.provision(directory, "duck-1104", os.getgid())
            ca = (directory / "ca.crt").read_bytes()
            leaf = (directory / "duck.crt").read_bytes()
            self.assertEqual((directory / "ca.key").stat().st_mode & 0o777, 0o600)
            self.assertEqual((directory / "duck.key").stat().st_mode & 0o777, 0o640)
            self.assertTrue(tls.valid_leaf(directory, "duck-1104.local"))
            ssl.create_default_context(cafile=directory / "ca.crt")
            tls.provision(directory, "duck-1104", os.getgid())
            self.assertEqual((directory / "duck.crt").read_bytes(), leaf)
            tls.provision(directory, "duck-2208", os.getgid())
            self.assertEqual((directory / "ca.crt").read_bytes(), ca)
            self.assertNotEqual((directory / "duck.crt").read_bytes(), leaf)
            self.assertTrue(tls.valid_leaf(directory, "duck-2208"))
