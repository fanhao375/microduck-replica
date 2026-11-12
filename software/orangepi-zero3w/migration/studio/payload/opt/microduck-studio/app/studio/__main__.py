"""启动 Studio。用 run.py 启动（它负责把 deps/ 放进搜索路径）：

    鸭子上：  python3 -I run.py --listen 0.0.0.0:8000
    电脑上：  py -3.13 -I run.py --listen 127.0.0.1:8000 --robotd tcp:127.0.0.1:17001 --configd tcp:127.0.0.1:17002
              （先起假 robotd：py -3.13 -I dev/fake_robotd.py）
    只读自检：python3 -I run.py --selfcheck
    设密码：  网页「更多 → 登录密码」，或者用 orangepi 用户运行 python3 -I run.py set-password
"""
import argparse
import asyncio
import getpass
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def camera_port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("视频信令端口需为 1–65535")
    return port


def default_state_dir():
    if sys.platform.startswith("linux") and os.path.isdir("/var/lib/microduck-studio"):
        return "/var/lib/microduck-studio"
    return str(ROOT / "state")


def parse(argv=None):
    ap = argparse.ArgumentParser(prog="studio", description="Microduck Studio")
    ap.add_argument("cmd", nargs="?", default="serve", choices=["serve", "set-password"])
    ap.add_argument("--listen", default=None, help="监听地址:端口（鸭子上默认 0.0.0.0:8000；台架模式默认 127.0.0.1:18080）")
    ap.add_argument("--robotd", default=None, help="默认 unix:/run/robotd.sock；台架模式不连")
    ap.add_argument("--configd", default=None, help="默认 unix:/run/configd.sock；台架模式不连")
    ap.add_argument("--tof", default="unix:/run/tofd/tof.sock", help="ToF 只读服务地址；none 表示不连接")
    ap.add_argument("--camera-socket", default="unix:/run/mediad/media.sock", help="摄像头服务只读探测地址；none 表示不连接")
    ap.add_argument("--camera-signaling-port", type=camera_port, default=8443, help="本机 mediad 的视频信令端口（默认 8443）")
    ap.add_argument("--bench", action="store_true",
                    help="台架模式：主控没烧、只用飞特调试板 / USB 转 TTL 插在电脑上调舵机。不连主程序，自动打开浏览器进舵机页，在页面上选 COM 口")
    ap.add_argument("--state-dir", default=None, help="密码、会话存这里（默认 ./state）")
    ap.add_argument("--log-dir", default=None, help="日志目录（默认 ./logs）")
    ap.add_argument("--web-dir", default=str(ROOT / "web"))
    ap.add_argument("--name", action="append", default=[], help="额外认可的主机名（可以写多个）")
    ap.add_argument("--duck-label", default=None, help="界面上显示的名字；不写就用主机名（鸭子上是 duck-xxxx）")
    ap.add_argument("--net-state", default="/run/microduck/net.json",
                    help="netcheck 写的配网状态文件（默认 /run/microduck/net.json；不在就整块 WiFi 隐藏）")
    ap.add_argument("--duck-color", default="#0F7C80", help="标识色")
    ap.add_argument("--servo-port", default="/dev/orangepi-robot-uart-unconfigured" if sys.platform.startswith("linux") else None,
                    help="舵机总线串口（鸭子上是 /dev/ttyS2；电脑上接 USB 转 TTL 写 COM5 之类）")
    ap.add_argument("--servo-sim", action="store_true", help="开发用：舵机页用演示舵机（没接硬件，软件里假装有 15 颗）")
    ap.add_argument("--selfcheck", action="store_true", help="只读自检：打印版本，对各个 socket 各问一次，然后退出")
    ap.add_argument("--dev-autologin", action="store_true",
                    help="开发用：本机来的请求不用登录（只在 --listen 127.0.0.1 时允许，截图测试用）")
    ap.add_argument("--dev-duck-root", default=None,
                    help="开发用：调参卡读这个目录下的假文件树（dev/duckfs.py 造的），不读 /etc、/opt。只能配假 robotd（tcp:）用")
    return ap.parse_args(argv)


def main(argv=None):
    a = parse(argv)
    # 默认值放在最前面定：后面 Robotd、自检都要用
    if a.listen is None:
        a.listen = "127.0.0.1:18080" if a.bench else "0.0.0.0:8000"
    if a.robotd is None:
        a.robotd = "none" if a.bench else "unix:/run/robotd.sock"
    if a.configd is None:
        a.configd = "none" if a.bench else "unix:/run/configd.sock"
    state_dir = a.state_dir or default_state_dir()
    log_dir = a.log_dir or str(ROOT / "logs")

    from .auth import Auth
    from .log import setup

    if a.cmd == "set-password":
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise SystemExit("别用 sudo：用 orangepi 用户运行，不然 Studio 读不了密码文件")
        pw = getpass.getpass("新的管理员密码（至少 6 位）：")
        if pw != getpass.getpass("再输一次："):
            raise SystemExit("两次不一样，没改")
        Auth(state_dir).set_password(pw)
        print("已设好，旧的登录全部作废。")
        return

    log = setup(log_dir)
    import starlette
    import uvicorn

    from .app import VERSION, build_app, precompress
    from .robotd import Robotd

    log.info(f"依赖：starlette {starlette.__version__} · uvicorn {uvicorn.__version__} · 来自 {Path(starlette.__file__).parent.parent}",
             cat="系统", dedup=False)
    rd = Robotd(a.robotd, a.configd, log)

    if a.selfcheck:
        async def check():
            r = await rd.selfcheck()
            await rd.stop()
            return r
        print(f"Studio {VERSION} · Python {sys.version.split()[0]}")
        for k, v in asyncio.run(check()).items():
            print(f"  {k:12s} {v}")
        return

    auth = Auth(state_dir, log)
    if not auth.has_password():
        log.info("没设密码：同一个网络里打开网页就能用（想加密码：更多 → 登录密码）", cat="系统",
                 hint=f"网页里设，或者用 orangepi 用户运行：python3 -I {ROOT / 'run.py'} set-password（别用 sudo）")
    host, _, port = a.listen.rpartition(":")
    if a.dev_autologin and host != "127.0.0.1":
        raise SystemExit("--dev-autologin 只能配 --listen 127.0.0.1:端口 用")
    if a.dev_autologin and (os.path.exists("/run/robotd.sock") or a.robotd.startswith("unix:")):
        # 鸭子上本机转发（反向代理、SSH 隧道）来的请求也是 127.0.0.1，开了等于人人是管理员
        raise SystemExit("--dev-autologin 只能在电脑上配假 robotd 用，鸭子上不能开")
    # 镜像里不写死名字：没给 --duck-label 就用主机名（新卡第一次开机是 duck-xxxx）
    label = a.duck_label or socket.gethostname()
    duck = {"label": label, "short": label.split(" ")[-1], "color": a.duck_color}
    names = list(a.name)
    precompress(a.web_dir)
    from .servo import ServoSession, load_directions
    # 舵机页一直有：鸭子上默认 /dev/ttyS2；电脑上没给 --servo-port 就在页面上选串口（跟 servo-web 一样）
    servo = ServoSession(log, port=a.servo_port, sim=a.servo_sim, directions=load_directions(state_dir),
                         state_dir=state_dir)
    from .imu import ImuBackend
    imu = ImuBackend(rd, log, state_dir, root=a.dev_duck_root or "/")
    from .pad import default_pad
    # 台架模式（--bench）不连主程序，也就没有手柄页的后端
    pad = None if a.bench else default_pad(log, configd_addr=a.configd, robotd_addr=a.robotd, state_dir=state_dir)
    from .onboard import Onboarding
    # 总览上的上手清单（记录在 state 目录的 onboarding.json）；台架模式不连主程序，清单只写一句「不检查」
    onboard = Onboarding(state_dir, log)
    from .commit import CommitService
    # 上手清单第 3 步「写进主程序」：准备情况、预览、调 root 小助手写（电脑上没有小助手就只给预览、写入拒绝）
    commit = CommitService(rd, log, state_dir)
    from .battery_svc import BatteryService
    # 电量估算（舵机电压查表，只警告）：设置在 state 目录的 battery-settings.json
    battery = BatteryService(rd, log, state_dir, servo=servo, onboard=onboard)
    from .net import Net
    # 配网状态：电脑上开发 / 没装小助手时，连 WiFi 不关热点（日志里写一句）
    net = Net(a.configd, a.net_state, state_dir, log, dev_mode=bool(a.dev_autologin), port=int(port))
    # 总览下面「运行和调参」、设置页的模型（调参方案 T1–T3，只读）。台架模式不连主程序，就不开
    tune = None
    models = None
    if not a.bench:
        from .tune import Tune
        if a.dev_duck_root and not a.robotd.startswith("tcp:"):
            raise SystemExit("--dev-duck-root 只能在电脑上配假 robotd（--robotd tcp:…）用")
        tune = Tune(rd, log, state_dir, root=a.dev_duck_root or "/", duck=duck)
        from .models import Models
        models = Models(rd, log, state_dir, tune=tune, root=a.dev_duck_root or "/")
    from .sensors import Sensors
    sensors = Sensors(log=log, root=a.dev_duck_root or "/", tof_addr=a.tof,
                      camera_signaling_port=a.camera_signaling_port, camera_addr=a.camera_socket)
    app = build_app(auth=auth, robotd=rd, log=log, web_dir=a.web_dir, duck=duck, names=names, state_dir=state_dir,
                    dev_autologin=a.dev_autologin, servo=servo, imu=imu, pad=pad, onboard=onboard, battery=battery,
                    tune=tune, net=net, commit=commit, models=models, sensors=sensors)
    log.info(f"网页地址：http://{host if host not in ('0.0.0.0', '') else '<鸭子 IP>'}:{port}/", cat="系统", dedup=False)
    if a.bench:
        import threading
        import webbrowser
        url = f"http://{host or '127.0.0.1'}:{port}/#/servos"
        log.info(f"台架模式：不连主程序，浏览器打开 {url}，在舵机页选 COM 口连接", cat="系统", dedup=False)
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    from .ws_protocol import MusicWebSocketProtocol
    uvicorn.run(app, host=host or "0.0.0.0", port=int(port), ws=MusicWebSocketProtocol, http="h11", lifespan="on",
                log_level="warning", server_header=False, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
