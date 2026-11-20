"""First-use authentication against the real ASGI routes, without hardware."""

import asyncio
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
        data = json.loads(raw) if "application/json" in response_headers["content-type"] else raw
        return start["status"], response_headers, data

    async def test_first_use_serves_the_page_and_requires_login_for_logs(self):
        status, _, html = await self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"app.js", html)
        status, _, response = await self.request("GET", "/api/logs")
        self.assertEqual(status, 401)
        self.assertFalse(response["ok"])

    async def test_only_setting_a_password_is_available_to_the_setup_form(self):
        _, _, session = await self.request("GET", "/api/session")
        status, _, _ = await self.request(
            "POST", "/api/password", {"action": "clear"},
            {"origin": self.origin, "x-csrf": session["csrf"]},
        )
        self.assertEqual(status, 403)
        self.assertFalse(self.auth.has_password())

    async def test_control_socket_requires_login_before_first_setup(self):
        scope = {
            "type": "websocket", "path": "/ws", "scheme": "ws", "query_string": b"",
            "headers": [(b"host", b"127.0.0.1:18000"), (b"origin", self.origin.encode())],
            "server": ("127.0.0.1", 18000), "client": ("127.0.0.1", 19000),
        }
        messages = []

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        self.assertEqual([m["type"] for m in messages], ["websocket.accept", "websocket.close"])
        self.assertEqual(messages[-1]["code"], 4401)

    async def test_two_setup_submissions_create_one_login(self):
        _, _, session = await self.request("GET", "/api/session")
        headers = {"origin": self.origin, "x-csrf": session["csrf"]}
        password = "temporary-test-password"
        form = {"action": "set", "password": password, "confirm": password}
        responses = await asyncio.gather(*(self.request("POST", "/api/password", form, headers)
                                           for _ in range(2)))
        self.assertCountEqual([status for status, _, _ in responses], [200, 401])
        self.assertEqual(sum("set-cookie" in headers for _, headers, _ in responses), 1)
        self.assertTrue(self.auth.check_password(password))

    async def test_change_keeps_current_session_and_clear_returns_to_setup(self):
        password = "temporary-test-password"
        self.auth.set_password(password)
        logins = []
        for _ in range(2):
            status, headers, _ = await self.request("POST", "/api/login", {"password": password},
                                                    {"origin": self.origin})
            self.assertEqual(status, 200)
            logins.append({"cookie": headers["set-cookie"].split(";", 1)[0]})
        _, _, session = await self.request("GET", "/api/session", headers=logins[0])
        headers = {**logins[0], "origin": self.origin, "x-csrf": session["csrf"]}
        changed = "temporary-new-password"
        status, _, _ = await self.request("POST", "/api/password",
                                          {"password": changed, "confirm": changed}, headers)
        self.assertEqual(status, 200)
        self.assertEqual((await self.request("GET", "/api/session", headers=logins[0]))[0], 200)
        self.assertEqual((await self.request("GET", "/api/session", headers=logins[1]))[0], 401)
        status, _, _ = await self.request("POST", "/api/password", {"action": "clear"}, headers)
        self.assertEqual(status, 200)
        self.assertFalse(self.auth.has_password())
        self.assertEqual((await self.request("GET", "/api/logs", headers=logins[0]))[0], 401)
        status, _, reset = await self.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertFalse(reset["has_password"])

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
        status, _, response = await self.request("GET", "/api/logs", headers={"cookie": cookie})
        self.assertEqual(status, 200)
        self.assertTrue(response["ok"])

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
