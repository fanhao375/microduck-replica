# Orange Pi Zero 3W 程序迁移

这里保存 Orange Pi Zero 3W A733、官方 Ubuntu Jammy / Linux 6.6.98 的迁移源码、Studio 中控、开机配网及 UART 适配。预构建 ARM64 程序、ONNX 模型和独立 Python 运行时存放在本次贡献对应的 GitHub Release，见父目录说明；Git 中的部分 payload 是源码和配置模板，不能直接视为完整安装包。

## 安装与首次登录

1. 使用父目录提供的镜像和 SHA-256，或在适配的官方系统上按 `../provision/README.md` 安装 `orangepi-provision`。
2. 从同一 Release 下载完整应用迁移包并核对 SHA-256。解压后以 root 运行包内的 `install.py`，参数为包含 `manifest.json` 与 `payload/` 的目录。安装器仅接受 `orangepizero3w` / aarch64，拒绝覆盖已有应用或非空 Studio 状态目录。
3. 在可信局域网内打开 `http://<板当前地址>:8000/`，通过正常的“登录密码”界面自行设置新管理员密码。初始状态不含任何网页密码、会话、SSH 密钥、Wi-Fi 配置或旧设备标定；安装器不会从预览目录复制凭据，也不修改 Linux 用户密码。
4. 串口初始使用不存在的 `/dev/orangepi-robot-uart-unconfigured`，`robotd` 固定 `--read-only`、策略关闭，校准模板未校准。完成实际接线与板级核对后，按 [hardware/README.md](hardware/README.md) 专项适配。网页在线不代表硬件或运动验收通过。

发布版安装流程清除了原私有备份中特定预览目录、固定 IP 和个人路径。首次网页设密使用原应用现有逻辑；这次公开打包没有重新执行板端安装或运动验收。

配网完成并登录板子后，在下载目录执行（`SHA256SUMS` 校验通过后）：

```sh
tar -xzpf orangepi-quanzhi-20261112.tar.gz
sudo python3 orangepi-quanzhi/install.py orangepi-quanzhi
```

安装器会通过 apt 安装缺少的系统动态库和工具，需要网络连接。网页地址使用板子当前 DHCP 地址。

## 组成

- [control/README.md](control/README.md)：固定摘要的预构建主控、运行依赖和只读配置。
- [studio/README.md](studio/README.md)：Studio 应用源码、平台适配、服务与限定助手。
- [hardware/README.md](hardware/README.md)：HAT UART0、48 MHz 时钟与分组读配置，以及先前真板只读观测的适用边界。
- `source/`：128 个原始参考程序与资源的公开副本，包括 Cubie 专用代码、补丁、离线原型和许可证。其 `scope.json` 区分仅存档的内容；Cubie MMIO、温控和配网程序不在 Orange Pi 上启用。

`robotd` 完整构建工程不在这份迁移副本中；这里提供使用到的二进制来源摘要和 sync3 补丁，不能仅凭本目录重编译完整主控。Studio 包含真实后端与前端，不使用模拟器代替真实硬件。可选摄像头、ToF、configd 与离线语音模型未随本次安装启用。

## 主机工具

`../tools/find_master.py` 与 `board_ssh.expect` 面向 macOS 主机：需要操作者先通过可信渠道核验自己的 SSH 主机公钥，写入 `../output/orangepi_known_hosts`，别名为 `orangepizero3w-setup`。公开包不携带任何设备的 known_hosts。`find_master.py` 仅将匹配该密钥的结果标记为 `verified`；开放端口不能证明设备身份。

```sh
python3 tools/find_master.py
ORANGEPI_SSH_HOST=<板当前地址> tools/board_ssh.expect run 'uname -a'
```

上述命令从 `software/orangepi-zero3w/` 运行；SSH 密码通过环境变量 `ORANGEPI_SSH_PASSWORD` 提供，不能写入仓库、命令脚本或日志。

## 构建与离线检查

完整运行时准备好后，`build_bundle.py` 合并控制载荷、Studio 载荷及参考源码，并校验清单。缺少 Release 中的二进制或依赖时会明确停止。控制载荷准备脚本需要显式提供原始来源路径，见 control 说明。

父目录 `package_runtime.py PREPARED_PAYLOAD NEW_OUTPUT_DIR` 用于本次公开发布：输入必须是已准备的根目录布局载荷，脚本替换公开源码、移除训练导出、泛化 JSON 元数据、补齐许可证并重新生成全部文件摘要。不使用正在运行的板子的根目录作为输入。

```sh
python3 migration/build_source_snapshot.py --verify
python3 -m unittest discover -s migration/studio/tests -v
```

Studio 测试需先补齐同仓 `software/studio/deps` 的固定版本纯 Python 依赖，或使用 Release 内完整的 `app/deps`。本目录保留依赖版本和许可证，不重复提交其实现与 Python 运行时。离线测试只证明相应软件行为，不能替代刷卡、真板启动、UART 或运动测试。
