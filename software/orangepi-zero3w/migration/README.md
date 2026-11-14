# Orange Pi Zero 3W 应用与迁移源码

R2 完整镜像已经预装这里的 Studio、主控、模型、独立 Python、依赖和配网服务。正常使用是烧卡 → 手机配网 → 打开 `http://<设备当前 IP>:8000/` → 在首次页面设置网页密码，无需再 SSH 安装。下载和烧录见[父目录](../README.md)及[镜像说明](../image/README.md)。

## 预装应用

- [control](control/README.md)：只读 `robotd`、辅助程序、ONNX Runtime、10 份模型及未校准配置。
- [studio](studio/README.md)：真实网页后端与前端、CPython 3.13.14、固定版本依赖、受限 helper 和服务。
- [hardware](hardware/README.md)：HAT UART0、48 MHz 时钟、只读接入和分组读适配。
- `source/`：128 个参考程序和资源的公开副本，包括 Cubie 专用代码、补丁、离线原型与许可证。其 `scope.json` 标明仅存档内容，Cubie MMIO、温控和旧配网程序不在 Orange Pi 上启用。

Git 中的 payload 保存源码与配置，完整 ARM64 ELF、ONNX 和独立 Python 随 Release 提供。`robotd` 完整 Rust 构建工程不在本目录；程序来源及清理前后摘要见 control 说明。摄像头、ToF、configd、额外媒体/手柄服务与可选语音模型没有在本镜像中启用。

## 已有系统的可选手动安装

仅当继续使用已有 Orange Pi Zero 3W / Ubuntu Jammy 系统，而没有刷 R2 完整镜像时，才需要下载 [R2 Release](https://github.com/Liangjin5437/microduck-replica/releases/tag/orangepi-zero3w-20261112-r2) 的 `orangepi-quanzhi-20261112-r2.tar.gz`。先按 [provision 说明](../provision/README.md)安装并验证配网服务，核对下载文件的 SHA-256，再在板端运行：

```sh
tar -xzpf orangepi-quanzhi-20261112-r2.tar.gz
sudo python3 orangepi-quanzhi/install.py orangepi-quanzhi
```

安装器仅接受 `orangepizero3w` / aarch64，要求配网服务正常，拒绝覆盖已有应用、非空 Studio 状态目录或状态符号链接。缺少系统依赖时会通过 apt 安装，需要网络。已有应用的升级或迁移不是此全新安装器的用途。

安装完成后打开 `http://<设备当前 IP>:8000/`，首次表单设置自己的网页密码。安装器创建空的状态目录，不复制预览凭据、会话、Wi-Fi、SSH 授权或旧板标定，也不修改 Linux 用户密码。

## 首次状态与硬件接入

镜像和安装包都使用 `/dev/orangepi-robot-uart-unconfigured` 占位串口，`robotd` 固定 `--read-only`、策略关闭，15 个关节均未校准。缺少实际串口时主控可以提供 RPC 并显示只读等待；这不等于总线或运动已验收。

按 [hardware/README.md](hardware/README.md)核对实际接线和板级配置后，分别完成 UART 接入、校准与运动验证。公开包不携带某台机器的实测零位或 IMU 标定。

## 主机维护工具

`../tools/find_master.py` 与 `board_ssh.expect` 面向 macOS 主机，属于可选维护工具。操作者需先通过可信渠道核验自己的 SSH 主机公钥，保存为 `output/orangepi_known_hosts`，别名 `orangepizero3w-setup`。公开包不附设备的 known_hosts。仅主机公钥匹配的探测结果可标记为 `verified`。

从 `software/orangepi-zero3w/` 执行：

```sh
python3 tools/find_master.py
ORANGEPI_SSH_HOST=<设备当前地址> tools/board_ssh.expect run 'uname -a'
```

SSH 密码由 `ORANGEPI_SSH_PASSWORD` 环境变量提供，不写入仓库、命令脚本或日志。R2 的普通使用流程不需要这些工具。

## 构建与检查

`package_runtime.py PREPARED_PAYLOAD NEW_OUTPUT_DIR` 将已准备的根目录布局载荷与公开源码打包，移除训练导出、泛化 JSON 元数据、保留许可证并生成文件摘要。ELF 路径清理由 `image/sanitize_runtime.py` 对未发布副本执行，完成后重新校验并在 Linux ARM64 中运行兼容性检查，再交给 `image/build_full_image.py` 预装。完整构建器用法见[镜像说明](../image/README.md)。不要以运行中的设备根目录作为发布输入。

从本移植根目录执行离线测试：

```sh
python3 migration/build_source_snapshot.py --verify
cp -R ../studio/deps/. migration/studio/payload/opt/microduck-studio/app/deps/
python3 -m unittest discover -s migration/studio/tests -v
```

本轮 ARM64 chroot 内的运行时、预装和全部 10 份 ONNX 加载预热验证通过。110 项自动测试通过。最终封装镜像的独立副本已在原生 ARM64 Linux 中运行真实 robotd 和 Studio，Chrome 首次设密、校验失败提示、刷新、退出重登、服务重启后密码/会话保留、新浏览器不能重新初设及 3D 渲染均通过，真实 RPC 为 `read_only/waiting_for_bus`。截图与 `browser-verification.json` 随 R2 Release 提供。

本次没有可用实板，未刷卡验证内核启动、热点驱动或重启恢复，也未执行校准和运动。
