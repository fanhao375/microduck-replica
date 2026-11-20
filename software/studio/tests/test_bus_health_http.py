"""Real WebSocket routing uses the current owner without implicit takeover."""
import unittest
import asyncio
from unittest import mock
from tests import test_http as fixture


class OverviewDiagnosticsHttpTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_visible_imu_route_receives_ten_hz_views_and_hidden_page_stops_them(self):
        sequence = 0
        def fake_view(_backend):
            nonlocal sequence
            sequence += 1
            return {"t": "imu", "fake_sequence": sequence}
        with mock.patch("studio.imu.ImuBackend.view", fake_view):
            client = await self.connect()
            await client.send({"t": "hb", "visible": True, "route": "/"})
            client.msgs.clear()
            await client.pump(until=lambda: False, timeout=.85)
            normal = sum(m.get("t") == "imu" for m in client.msgs)
            await client.send({"t": "hb", "visible": True, "route": "/imu"})
            client.msgs.clear()
            await client.pump(until=lambda: False, timeout=.85)
            active = sum(m.get("t") == "imu" for m in client.msgs)
            self.assertGreaterEqual(active, 6)
            self.assertLessEqual(normal, 3)
            self.assertGreater(active, normal*2)
            await client.send({"t": "hb", "visible": False, "route": "/imu"})
            await client.pump(until=lambda: self.rd.viewers == 0, timeout=.5)
            client.msgs.clear()
            before = sequence
            await client.pump(until=lambda: False, timeout=.6)
            self.assertFalse(any(m.get("t") == "imu" for m in client.msgs))
            self.assertEqual(sequence, before)

    async def test_overview_diagnostics_streams_cached_views_and_stops_on_collapse_hide_and_leave(self):
        with mock.patch("studio.imu.ImuBackend.view", return_value={"t": "imu", "fake": True}), \
                mock.patch.object(self.servo, "open", side_effect=AssertionError("no takeover")) as opening, \
                mock.patch("studio.app._bus_take") as take, mock.patch("studio.app._bus_give_back") as giveback, \
                mock.patch("studio.servo.feetech.FeetechBus", side_effect=AssertionError("no UART")) as uart:
            client = await self.connect()
            for route in ("/", "/pad"):
                await client.send({"t": "hb", "visible": True, "route": route, "diagnostics_visible": True})
                await client.pump(until=lambda: self.rd.telemetry_viewers == 1, timeout=.5)
                self.assertEqual(self.rd.telemetry_viewers, 1)
                client.msgs.clear()
                await client.pump(until=lambda: False, timeout=.75)
                self.assertGreaterEqual(sum(m.get("t") == "servo" for m in client.msgs), 5)
                self.assertGreaterEqual(sum(m.get("t") == "imu" for m in client.msgs), 5)
            for heartbeat in (
                {"t": "hb", "visible": True, "route": "/pad", "diagnostics_visible": False},
                {"t": "hb", "visible": True, "route": "/settings", "diagnostics_visible": True},
                {"t": "hb", "visible": False, "route": "/", "diagnostics_visible": True},
            ):
                await client.send(heartbeat)
                await client.pump(until=lambda: self.rd.telemetry_viewers == 0, timeout=.5)
                self.assertEqual(self.rd.telemetry_viewers, 0)
                await client.pump(until=lambda: False, timeout=.15)
                client.msgs.clear()
                await client.pump(until=lambda: False, timeout=.65)
                self.assertFalse(any(m.get("t") == "servo" for m in client.msgs))
                self.assertLessEqual(sum(m.get("t") == "imu" for m in client.msgs), 2)
            opening.assert_not_called()
            take.assert_not_called()
            giveback.assert_not_called()
            uart.assert_not_called()

    async def test_overview_missing_or_non_boolean_visibility_does_not_retain_fast_demand(self):
        client = await self.connect()
        for value in (None, 1, "true"):
            await client.send({"t": "hb", "visible": True, "route": "/", "diagnostics_visible": True})
            await client.pump(until=lambda: self.rd.telemetry_viewers == 1, timeout=.5)
            self.assertEqual(self.rd.telemetry_viewers, 1)
            heartbeat = {"t": "hb", "visible": True, "route": "/"}
            if value is not None:
                heartbeat["diagnostics_visible"] = value
            await client.send(heartbeat)
            await client.pump(until=lambda: self.rd.telemetry_viewers == 0, timeout=.5)
            self.assertEqual(self.rd.telemetry_viewers, 0)
        self.assertEqual(self.servo.state, "closed")

    async def test_overview_demand_is_independent_for_multiple_clients(self):
        first, second = await self.connect(), await self.connect()
        await first.send({"t": "hb", "visible": True, "route": "/", "diagnostics_visible": True})
        await second.send({"t": "hb", "visible": True, "route": "/pad", "diagnostics_visible": True})
        await first.pump(until=lambda: self.rd.telemetry_viewers == 2, timeout=.5)
        self.assertEqual(self.rd.telemetry_viewers, 2)
        await first.send({"t": "hb", "visible": True, "route": "/", "diagnostics_visible": False})
        await first.pump(until=lambda: self.rd.telemetry_viewers == 1, timeout=.5)
        self.assertEqual(self.rd.telemetry_viewers, 1)
        await first.pump(until=lambda: False, timeout=.15)
        first.msgs.clear()
        second.msgs.clear()
        await asyncio.gather(first.pump(until=lambda: False, timeout=.75), second.pump(until=lambda: False, timeout=.75))
        self.assertFalse(any(m.get("t") == "servo" for m in first.msgs))
        self.assertGreaterEqual(sum(m.get("t") == "servo" for m in second.msgs), 5)
        second.close()
        await first.pump(until=lambda: self.rd.telemetry_viewers == 0, timeout=1.)
        self.assertEqual(self.rd.telemetry_viewers, 0)

    async def test_overview_fast_demand_expires_when_heartbeat_goes_quiet(self):
        with mock.patch("studio.app.HB_QUIET_S", .35):
            client = await self.connect()
            await client.send({"t": "hb", "visible": True, "route": "/", "diagnostics_visible": True})
            await client.pump(until=lambda: self.rd.telemetry_viewers == 1, timeout=.25)
            self.assertEqual(self.rd.telemetry_viewers, 1)
            await client.pump(until=lambda: self.rd.telemetry_viewers == 0, timeout=1.)
            self.assertEqual(self.rd.telemetry_viewers, 0)

    async def test_collapsed_overview_does_not_receive_owner_table_when_explicit_debug_owner_is_open(self):
        self.servo.state_dir = self.tmp.name
        self.assertTrue(self.servo.open()[0])  # Explicit simulated owner, never hardware.
        self.addCleanup(self.servo.close)
        client = await self.connect()
        await client.send({"t": "hb", "visible": True, "route": "/", "diagnostics_visible": True})
        await client.pump(until=lambda: self.rd.telemetry_viewers == 1, timeout=.5)
        await client.send({"t": "hb", "visible": True, "route": "/", "diagnostics_visible": False})
        await client.pump(until=lambda: self.rd.telemetry_viewers == 0, timeout=.5)
        await client.pump(until=lambda: False, timeout=.15)
        client.msgs.clear()
        await client.pump(until=lambda: False, timeout=.35)
        self.assertFalse(any(m.get("t") == "servo" for m in client.msgs))
        self.assertEqual(self.servo.state, "open")
