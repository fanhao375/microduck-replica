"""First-use authentication against the real ASGI routes, without hardware."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1] / "payload/opt/microduck-studio/app"
sys.path[:0] = [str(APP / "deps"), str(APP)]

from studio.app import build_app  # noqa: E402
from studio.auth import Auth  # noqa: E402
from studio.log import Log  # noqa: E402


class FirstPasswordTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "state"
        self.auth = Auth(self.state)
        self.app = build_app(
            auth=self.auth, robotd=None,
            log=Log(Path(self.temporary.name) / "logs", echo=False, background=False),
            web_dir=APP / "web", duck={"label": "Test duck"}, state_dir=self.state,
        )
        self.origin = "http://127.0.0.1:18000"

    async def request(self, method, path, body=None, headers=None):
        payload = json.dumps(body).encode() if body is not None else b""
        request_headers = {"host": "127.0.0.1:18000", **(headers or {})}
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(key.lower().encode(), value.encode())
                        for key, value in request_headers.items()],
            "server": ("127.0.0.1", 18000), "client": ("127.0.0.1", 19000),
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            sent.append(message)

        # No lifespan runs: authentication routes do not need a hardware worker.
        await self.app(scope, receive, send)
        start = next(message for message in sent if message["type"] == "http.response.start")
        raw = b"".join(message.get("body", b"") for message in sent
                       if message["type"] == "http.response.body")
        response_headers = {key.decode(): value.decode() for key, value in start["headers"]}
        return start["status"], response_headers, json.loads(raw)

    async def test_first_password_sets_cookie_and_persists_authenticated_state(self):
        status, _, session = await self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertIs(session["has_password"], False)
        password = "temporary-first-use-password"
        status, headers, result = await self.request(
            "POST", "/api/password", {"action": "set", "password": password, "confirm": password},
            {"origin": self.origin, "x-csrf": session["csrf"]},
        )
        self.assertEqual(status, 200)
        self.assertIs(result["has_password"], True)
        self.assertTrue(Auth(self.state).check_password(password))
        status, _, result = await self.request("GET", "/api/session")
        self.assertEqual(status, 401)
        self.assertIs(result["has_password"], True)
        cookie = headers["set-cookie"].split(";", 1)[0]
        status, _, session = await self.request("GET", "/api/session", headers={"cookie": cookie})
        self.assertEqual(status, 200)
        self.assertIs(session["has_password"], True)
        self.assertNotEqual(session["csrf"], "open")

    async def test_first_password_still_requires_same_origin_and_open_session_csrf(self):
        _, _, session = await self.request("GET", "/api/session")
        for headers in ({"origin": self.origin},
                        {"origin": "http://other.example", "x-csrf": session["csrf"]}):
            with self.subTest(headers=headers):
                status, _, _ = await self.request(
                    "POST", "/api/password",
                    {"action": "set", "password": "temporary-password", "confirm": "temporary-password"},
                    headers,
                )
                self.assertEqual(status, 403)
                self.assertFalse(self.auth.has_password())


if __name__ == "__main__":
    unittest.main()
