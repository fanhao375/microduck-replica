# 香橙派开机热点配网

参考 `11quanzhi/radxa-cubie-a7z/provision`，适配 Orange Pi Zero 3W 官方 Ubuntu Jammy、NetworkManager 1.36。

当前版本 `20261109-nm136-fix`。创建连接时由 NetworkManager 生成 UUID，程序查询并保存实际 UUID；NM 1.36 的 `nmcli connection add` 不能写入只读的 `connection.uuid`。开机自启链接和真实热点创建分别验证，不能以离线写入成功代替真板运行结果。

## 手机配网

1. 将安装后的 SD 卡插入香橙派并通电。
2. 未连接到已保存的 Wi-Fi 时，连接手机可见的 `OrangePi_XXXXXX` 热点，不用密码。手机提示无法上网时选择保持连接。
3. 若配网页没有自动弹出，在浏览器打开 `http://10.42.0.1/`。
4. 选择或输入家里 Wi-Fi 的名称和密码，点击“保存并连接”。手机随后会断开设备热点。
5. 成功后保存网络，下次开机自动连接；失败后恢复热点，重新连接可查看提示和重试。已有配置时，开机先等待 45 秒尝试自动连接。

## 安装

联网的板子上，以 root 执行本目录的 `sh install.sh`。依赖系统已安装的 `python3`、`nmcli`、`ip`、`dnsmasq` 和 `systemctl`。

离线挂载 Linux 根文件系统时，使用 `DESTDIR=/path/to/rootfs sh install.sh`。安装器启用 `orangepi-provision.service`，屏蔽独立的 `dnsmasq.service`、`hostapd.service`，由 NetworkManager 管理热点的 DHCP/DNS；原有配置文件保留。

macOS 不支持直接挂载 ext4。本项目的 `tools/install_rootfs.py` 将离线暂存目录写入已卸载的 ext4 分区；安装前核对板型和文件系统，使用 debugfs 撤销文件记录所有修改块，安装后校验文件内容、权限、服务链接及文件系统。对设备安装还会核对外置 USB、Linux 首分区和本次已识别 SD 卡容量。不能访问磁盘或校验失败时停止，不能视为安装成功。

若文件系统有 `needs_recovery`，先用 `e2fsck -p -E journal_only` 仅恢复已提交的日志事务，并独立保存 `journal.e2undo`。随后只读完整检查必须通过才安装；其余文件系统问题不自动修复。终端实时显示六个安装阶段并保留检查日志。撤销文件不能替代完整镜像备份，也不能用于恢复过程中断电或系统崩溃。

本次 macOS 手动入口为 `output/install-orangepi-provision.command`；结果写入 `output/sdcard-install-terminal.log` 与 `output/sdcard-provision-install-20261109/result.json`。只有结果状态为 `installed-and-readback-verified` 才表示卡内安装完成。修改块备份为同目录下 `install.e2undo`，仅用于原卡仍未再次启动时的撤销，不能在卡已开机并有新写入后直接恢复旧块。

更新入口每次使用独立的 `output/sdcard-provision-update-时间戳`，保存此前的服务状态和可用的配网日志；最近一次成功的更新目录由 `output/sdcard-provision-latest` 指向。不会读取已保存 Wi-Fi 的密码文件。

## 诊断与验证

板端查看：

```sh
systemctl status orangepi-provision.service
journalctl -u orangepi-provision.service -b
tail -n 100 /var/log/orangepi-provision.log
nmcli device status
```

本地验证：

```sh
python3 -m unittest discover -s tests -v
python3 -m py_compile provision/orangepi_provision.py tools/install_rootfs.py
sh -n provision/install.sh
```

自动测试覆盖策略、输入、固定热点地址、连接成功保存、失败恢复及已有配置保护。手机获得 DHCP 地址、板端驱动 AP 能力、真实 Wi-Fi 连接与重启重连需要真板验证。

程序同时保留最大 1 MB、最多三个文件的配网日志，记录程序/NM 版本、无线设备状态及失败原因；密码在错误详情进入日志前脱敏。

若不再使用本程序，可在板子上执行 `systemctl disable --now orangepi-provision.service`。只有需要恢复独立 AP/DNS 服务时，再执行 `systemctl unmask dnsmasq.service hostapd.service` 并按原用途启用，避免与 NetworkManager 争用端口或无线设备。
