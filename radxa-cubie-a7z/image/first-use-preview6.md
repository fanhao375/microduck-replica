# Cubie A7Z + HD1910 首次调试与走路

1. 下载同版镜像和 `.sha256`，在下载目录执行 `shasum -a 256 -c *.img.xz.sha256`（仅保留本版校验文件）。校验通过后把镜像写入至少 8 GB 的空闲 microSD 卡。
2. 断电接好 HD1910 舵机总线及对应 IMU，插卡启动。手机连接 `miniduck_XXXX` 热点，打开 `http://10.42.0.1/` 配网。记下页面显示的设备编号。
3. 手机回到同一 Wi-Fi，访问 `http://duck-XXXX.local/`。如果手机无法解析短名，打开 `http://设备IP:8000/`。
4. 在 Studio 支撑好机身，按本机装配逐个检查 15 个关节的编号、方向、零位和行程，完成 IMU 方向标定。确认反馈后提交到主程序并等待运动模式就绪。每台新机单独标定。
5. 在网页手柄中扶稳机身，执行站姿操作。确认双腿方向和受力正常后，点「开始走」，小幅推动摇杆，再松开并点「停下」。异常时支撑机身并急停卸力。如果页面显示「结果未确认」，先核对实时反馈和实物，再继续。
6. 出厂走路和恢复分别绑定 9600。试用另一套步态时，先停下并卸力，再到「模型中心 → 模型库 → 自行导入」选择「走路策略 HD1910 · 9000」，点「使用此模型」，确认当前用于走路。恢复槽仍使用 9600。可在走路槽恢复内置模型。坐站、捡取、前滚翻和左右踢球各用独立模型。

板端排查可运行 `systemctl status cubie-duck-firstboot cubie-provision microduck-studio robotd --no-pager` 及 `journalctl -b -u cubie-duck-firstboot -u microduck-studio -u robotd --no-pager`。先查首启、舵机总线和标定反馈，再尝试行走。网页手柄通过局域网使用。

可选的 DuckBuddy 蓝牙配网安装包位于 `/usr/share/cubie-duck/optional/cubie-ble-provision.tar.gz`，已有热点配网可直接使用。需要蓝牙时，在板上单独解包，进入包目录，运行 `python3 install_ble.py --binary ./cubie-ble` 预检，再按需加 `sudo` 和 `--apply` 安装，本机会生成独立设置码。

本版为整机预发布，新的空白卡冷启动、逐台标定及实际地面步态须由现场验收。
