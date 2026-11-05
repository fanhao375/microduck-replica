"""Real WebSocket routing uses the current owner without implicit takeover."""
import unittest
import json
from pathlib import Path
from unittest import mock
from tests import test_http as fixture
from studio import servo
from studio.bus.sim_bus import SimSerial


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


class RestoredReadOrderHttpTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ServoSessionBatch3Test.asyncSetUp
    asyncTearDown = fixture.ServoSessionBatch3Test.asyncTearDown
    start = fixture.ServoSessionBatch3Test.start
    stop = fixture.ServoSessionBatch3Test.stop
    marker = fixture.ServoSessionBatch3Test.marker
    logs = fixture.ServoSessionBatch3Test.logs
    until = fixture.ServoSessionBatch3Test.until
    client = fixture.ServoSessionBatch3Test.client
    cmd = fixture.ServoSessionBatch3Test.cmd

    async def test_real_owner_restore_loads_trusted_shared_order_and_gain_baseline_from_marker(self):
        shared = Path(self.tmp.name)/"servo-read-order.json"
        shared.write_text('{"mode":"id_last","last_id":13}', encoding="utf-8")
        native = self.fake.bus["native"]
        native["read_order"] = {"mode": "id_last", "last_id": 13,
            "ids": [i for i in servo.IDS if i != 13]+[13], "session": "native-one", "generation": 1,
            "configuration_path": str(shared)}
        native["hd1910"].update(temporary_gains_applied=False, restore_pending=False)
        self.fake.bus["policy_enabled"] = False
        real_factory = servo.feetech.FeetechBus
        def fake_bus(port, baud=1_000_000):
            return real_factory(None, ser=SimSerial(servo.IDS))
        with mock.patch("studio.servo.feetech.FeetechBus", side_effect=fake_bus), \
                mock.patch("studio.servo.robotd_active", return_value=False), \
                mock.patch("studio.app._bus_take", return_value=(True, None)):
            session = servo.ServoSession(self.log, sim=False, port="FAKE-PORT", state_dir=str(self.state_dir))
            await self.start(robotd_up=True, servo=session)
            self.assertTrue(await self.until(lambda: self.rd.bus.value is not None))
            client = await self.client()
            reply = await self.cmd(client, "open", "servo.open")
            self.assertTrue(reply["ok"], reply)
            saved_marker = json.loads(self.marker.read_text(encoding="utf-8"))
            self.assertEqual(saved_marker.get("read_order_configuration_path"), str(shared))
            self.assertTrue(saved_marker["gains_restored_before_takeover"])
            self.assertEqual(set(saved_marker["gain_baseline"]), {str(i) for i in servo.IDS})
            client.close()
            await self.stop()
            self.s1.close()
            self.s1 = None
            restarted = servo.ServoSession(self.log, sim=False, port="FAKE-PORT", state_dir=str(self.state_dir))
            await self.start(robotd_up=False, servo=restarted)
            self.assertTrue(await self.until(lambda: restarted.state == "open"))
            self.assertTrue(restarted.restored)
            self.assertEqual(restarted.read_order["mode"], "id_last")
            self.assertEqual(restarted.read_order["ids"][-1], 13)
            self.assertEqual(restarted.read_order["configuration_path"], str(shared))
            self.assertTrue(restarted.read_order_guard()["allowed"])
            result = restarted.set_read_order({"mode": "default"})
            self.assertTrue(result["saved"])
            self.assertEqual(json.loads(shared.read_text(encoding="utf-8")), {"mode": "default"})


if __name__ == "__main__":
    unittest.main()
