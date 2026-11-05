"""Real WebSocket routing uses the current owner without implicit takeover."""
import unittest
from tests import test_http as fixture


class ReadOrderHttpTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.HttpTest.asyncSetUp
    asyncTearDown = fixture.HttpTest.asyncTearDown
    make_pad = fixture.HttpTest.make_pad
    call = fixture.HttpTest.call
    login = fixture.HttpTest.login

    async def connect(self):
        token, _ = await self.login()
        client = fixture.WsClient(self.port, origin=self.origin, cookie=token)
        await client.open()
        self.addCleanup(client.close)
        return client

    async def test_native_unsupported_is_readonly_and_explains_order_guard(self):
        client = await self.connect()
        await client.send({"t": "hb", "visible": True, "route": "/servos"})
        await client.pump(until=lambda: any(m.get("t") == "servo" for m in client.msgs))
        self.assertEqual(self.servo.state, "closed")
        packet = next(m for m in client.msgs if m.get("t") == "servo")
        self.assertFalse(packet["communication"]["read_order_guard"]["allowed"])
        self.assertEqual(self.rd.telemetry_viewers, 1)
        await client.send({"t": "cmd", "name": "servo.read_order", "id": "order", "args": {"mode": "id_ascending"}})
        await client.pump(until=lambda: any(m.get("id") == "order" for m in client.msgs))
        reply = next(m for m in client.msgs if m.get("id") == "order")
        self.assertFalse(reply["ok"])
        self.assertIn("读取顺序", reply["reason"])
        await client.send({"t": "hb", "visible": True, "route": "/"})
        await client.pump(until=lambda: self.rd.telemetry_viewers == 0)
        self.assertEqual(self.rd.telemetry_viewers, 0)

    async def test_explicit_debug_owner_order_command_persists_and_reads_back(self):
        self.servo.state_dir = self.tmp.name
        self.assertTrue(self.servo.open()[0])
        self.addCleanup(self.servo.close)
        client = await self.connect()
        await client.send({"t": "cmd", "name": "servo.read_order", "id": "order", "args": {"mode": "id_last", "last_id": 13}})
        await client.pump(until=lambda: any(m.get("id") == "order" for m in client.msgs))
        reply = next(m for m in client.msgs if m.get("id") == "order")
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["data"]["saved"])
        self.assertEqual(reply["data"]["read_order"]["ids"][-1], 13)
        self.assertEqual(self.servo.holding(), 0)


if __name__ == "__main__":
    unittest.main()
