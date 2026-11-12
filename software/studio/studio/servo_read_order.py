"""One guarded configuration operation for the current UART owner."""
import asyncio

from .servo import Refused, robotd_read_order_guard, validate_read_order
from .rpc import RpcError, Timeout, Unreachable


class ReadOrderControl:
    def __init__(self, robotd, servo):
        self.rd, self.servo = robotd, servo
        self._lock = asyncio.Lock()

    def prepare_open(self):
        """Copy only owner-reported disk configuration, never page-supplied paths."""
        native = (self.rd.bus.value or {}).get("native") or {}
        hd = native.get("hd1910") or {}
        self.servo.gains_restored_before_takeover = (hd.get("temporary_gains_applied") is False
                                                     and hd.get("restore_pending") is False)
        order = native.get("read_order")
        if isinstance(order, dict):
            path = order.get("configuration_path")
            if isinstance(path, str) and path:
                self.servo.read_order_path = path

    async def change(self, args):
        mode, last, ids = validate_read_order(args)
        async with self._lock:
            if self.servo.state == "open":
                return await asyncio.to_thread(self.servo.set_read_order, args)
            with self.servo._cv:
                if self.servo.state == "opening" or self.servo.closing or self.servo._inflight:
                    raise Refused("舵机会话正在切换或操作中：没有修改读取顺序")
                bus = self.rd.bus.value or {}
                guard = robotd_read_order_guard(self.rd, bus)
                if not guard["allowed"]:
                    raise Refused(guard["reason"])
                self.servo._read_order_changing = True
            session = ((bus.get("native") or {}).get("read_order") or {}).get("session")
            try:
                reply = await self.rd.cmd.call("robot.feetechReadOrder", {"mode": mode, "last_id": last}, timeout=3.)
                if not isinstance(reply, dict) or reply.get("applied") is not True or reply.get("saved") is not True:
                    raise Refused("主程序没有确认读取顺序已应用并保存")
                readback = await self.rd.cmd.call("robot.busStatus", {}, timeout=2.)
                order = ((readback or {}).get("native") or {}).get("read_order")
                if (not isinstance(order, dict) or order.get("mode") != mode or order.get("last_id") != last
                        or order.get("ids", order.get("servo_ids")) != ids or order.get("session") != session):
                    raise Refused("读取顺序没有准确读回，不能确认已保存和应用")
                self.rd.bus.set(readback)
                self.prepare_open()
                self.servo.read_order = dict(order)
                return {"applied": True, "saved": True, "read_order": dict(order)}
            except (RpcError, Timeout, Unreachable) as err:
                raise Refused(f"读取顺序操作未确认：{err}") from err
            finally:
                with self.servo._cv:
                    self.servo._read_order_changing = False
                    self.servo._cv.notify_all()
