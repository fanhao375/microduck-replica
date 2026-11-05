"""Render real status pages in Edge; never connect to a robot or serial port."""
import functools
import http.server
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from tests.test_vpad_e2e import EDGE, NODE, ROOT, HERE, _kill, _node_ok, _port, _wait_http


@unittest.skipUnless(_node_ok() and EDGE and os.environ.get("STUDIO_E2E", "1") != "0",
                     "Node >=22 and Edge required; STUDIO_E2E=0 skips browser checks")
class CommunicationUiTest(unittest.TestCase):
    def test_readonly_status_live_details_order_and_imu(self):
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/fixture":
                    data = (b'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                            b'<link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/communication-health.css">'
                            b'<link rel="stylesheet" href="/imu.css"><script type="importmap">'
                            b'{"imports":{"three":"/vendor/three/three.module.js"}}</script><body></body>')
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    try:
                        super().do_GET()
                    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                        pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT / "web")))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        edge = None
        try:
            with tempfile.TemporaryDirectory(prefix="bus-health-ui-") as directory:
                cdp = _port(9372)
                try:
                    with open(Path(directory) / "edge.log", "w", encoding="utf-8") as log:
                        edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--no-sandbox", "--no-first-run",
                            "--no-default-browser-check", "--enable-unsafe-swiftshader", f"--remote-debugging-port={cdp}",
                            f"--user-data-dir={directory}/edge", "about:blank"], stdout=log, stderr=subprocess.STDOUT)
                        _wait_http(f"http://127.0.0.1:{cdp}/json/version", "status test browser", timeout=30)
                        result = subprocess.run([NODE, str(HERE / "communication_ui.mjs"),
                            f"http://127.0.0.1:{server.server_port}/fixture", str(cdp)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                finally:
                    _kill(edge)
                    edge = None
        finally:
            _kill(edge)
            server.shutdown()
            server.server_close()
            thread.join(5)
