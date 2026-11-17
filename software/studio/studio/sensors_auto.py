"""开机自动用上摄像头和 ToF（用户 11-17：接了就能看，不用 SSH 改配置）。

摄像头：看内核认没认到 IMX219（没接的话驱动探测不过，sysfs 里没有这个设备）。
  认到了、配置还是测试画面 → 让小助手切成真实画面；
  没认到、配置是真实画面   → 切回测试画面（不然视频服务每 5 秒重启一次，网页上的视频和遥控都没了）。
  切成真实画面以后视频服务起不来，小助手自己退回测试画面，这里只把话说清楚。
ToF：tofd 没接传感器时自己安静地等、接上就工作，所以只管把它设成开机启动。

每次 Studio 起来做一次，没有要改的就什么都不动。想关掉：在 Studio 的 state 目录放一个 sensors-auto-off 文件。
"""
import asyncio
import os
import re
import tomllib
from pathlib import Path

from . import helper

OFF_NAME = "sensors-auto-off"
SETTLE_S = 8.0           # 开机时驱动可能还没认完：最多等这么久再下「没接」的结论
TOF_UNITS = ("etc/systemd/system/tofd.service", "usr/lib/systemd/system/tofd.service", "lib/systemd/system/tofd.service")


def camera_present(root="/"):
    try:
        return any(re.fullmatch(r"\d+-0010", entry.name) for entry in (Path(root) / "sys/bus/i2c/drivers/imx219").iterdir())
    except OSError:
        return False


def media_source(root="/"):
    try:
        media = tomllib.loads((Path(root) / "etc/robot/robotd.toml").read_text(encoding="utf-8")).get("media")
    except (OSError, UnicodeError, ValueError):
        return None
    return media.get("source") if isinstance(media, dict) else None


def tof_pending(root="/"):
    """tofd 装了、还没设成开机启动。"""
    root = Path(root)
    return (any((root / unit).is_file() for unit in TOF_UNITS)
            and not os.path.lexists(root / "etc/systemd/system/multi-user.target.wants/tofd.service"))


class SensorAuto:
    def __init__(self, log, state_dir=None, root="/", apply=None, available=None, busy=None, settle=SETTLE_S):
        self.log, self.root, self.settle = log, root, settle
        self.off = Path(state_dir) / OFF_NAME if state_dir else None
        self.apply = apply or helper.sensors_apply
        self.available = available or helper.available
        self.busy = busy or (lambda: False)
        self.last = None            # 上一次的结果，给摄像头页看

    def view(self):
        return self.last

    def _done(self, state, note, **extra):
        self.last = {"state": state, "note": note, **extra}
        return self.last

    async def _camera_seen(self):
        """认到了马上回；没认到等一会儿再看，开机早的时候驱动还没认完。"""
        end = asyncio.get_running_loop().time() + self.settle
        while True:
            if camera_present(self.root):
                return True
            if asyncio.get_running_loop().time() >= end:
                return False
            await asyncio.sleep(0.5)

    async def run(self, by):
        if self.off is not None and self.off.exists():
            return self._done("off", None)
        source = media_source(self.root)
        if source not in ("camera", "test"):
            # 读不懂配置就不碰摄像头；ToF 照样管
            present, want = None, None
        else:
            present = await self._camera_seen()
            want = "camera" if present and source == "test" else "test" if not present and source == "camera" else None
        tof = tof_pending(self.root)
        if want is None and not tof:
            return self._done("ok", None, camera_present=present, source=source)
        if not self.available():
            return self._done("unavailable", None, camera_present=present, source=source)
        if self.busy():
            self.log.info("软件正在更新：这次开机不自动设摄像头和 ToF", cat="传感器")
            return self._done("busy", None, camera_present=present, source=source)
        ok, result, detail = await asyncio.to_thread(self.apply, by, want, tof)
        detail = detail if isinstance(detail, dict) else {}
        if tof and detail.get("tof") == "enabled":
            self.log.info("ToF 服务设成开机启动（没接传感器它自己等着，接上就有读数）", cat="传感器", dedup=False)
        if ok and want == "camera":
            self.log.info("认到了摄像头：视频来源切成真实画面", cat="传感器", dedup=False)
            return self._done("ok", "开机认到了摄像头，已切到真实画面。", camera_present=True, source="camera")
        if ok and want == "test":
            self.log.info("没认到摄像头：视频来源切回测试画面", cat="传感器", dedup=False,
                          hint="接了摄像头的话：关机检查排线方向和有没有插到底，再开机")
            return self._done("ok", "开机没认到摄像头，用测试画面。接了的话关机检查排线，再开机。",
                              camera_present=False, source="test")
        if ok:
            return self._done("ok", None, camera_present=present, source=source)
        if result == "camera_unstable":
            self.log.warn("认到了摄像头，但视频服务用真实画面起不来，已退回测试画面", cat="传感器", raw=str(detail)[:400],
                          hint="多半是排线没插好：关机重插，再开机")
            return self._done("failed", "开机认到了摄像头，但真实画面起不来，已退回测试画面。关机重插排线，再开机。",
                              camera_present=True, source=source)
        self.log.warn("自动设摄像头 / ToF 没成功", cat="传感器", raw=f"{result}: {str(detail)[:400]}",
                      hint="不影响站和走；摄像头页、ToF 页照原来的设置显示")
        return self._done("failed", None, camera_present=present, source=source, result=result)
