# Orange Pi Zero 3W 完整 Studio 镜像

[R2 Release](https://github.com/Liangjin5437/microduck-replica/releases/tag/orangepi-zero3w-20261112-r2) 预装 Studio、只读机器人主控、模型、独立 Python、Python 依赖、系统动态库和开机配网。应用及依赖在构建阶段安装，首次开机不需要下载安装程序。

## 烧卡与首次设密

下载以下文件到同一目录：

- `microduck-orangepi-zero3w-studio-20261112-r2.img.gz`
- `IMG-SHA256SUMS`

解压并核对压缩文件和原始镜像的 SHA-256，全部通过后再烧卡：

```sh
gzip -dk microduck-orangepi-zero3w-studio-20261112-r2.img.gz
shasum -a 256 -c IMG-SHA256SUMS
```

Linux 也可使用 `sha256sum -c IMG-SHA256SUMS`。用镜像烧录工具把 `.img` 写入至少 8 GB 的 SD 卡，插入 Orange Pi Zero 3W 后通电。

本版压缩文件大小为 999,648,791 字节，SHA-256 为 `054c66a3b1dbc2637f3eb93935adb41e62803f55aafbb7cbcc165958118716ef`；未压缩镜像为 6 GiB，SHA-256 为 `566f00c37d6675d625d1d1b5fbcdd84e38d798a8d6be0f41900d6f6657ef3710`。gzip CRC 和解压后的原始摘要均已核对。

1. 手机连接开放热点 `OrangePi_XXXXXX`，访问 `http://10.42.0.1/` 完成 Wi-Fi 配网。
2. 手机回到设备所在局域网，打开 `http://<设备当前 IP>:8000/`。
3. 首次页面显示“设一个登录密码”。输入两次自己的网页密码并保存，随后进入 Studio。

设备 IP 由路由器分配，可在 DHCP 设备列表查看。无需先登录 Linux、SSH 安装应用或运行电脑端设密工具。网页密码通过 Studio 原有 API 设置，持久化到本机私有状态目录；设密前的首次页面不挂载中控和 WebSocket 控制界面。

Linux 账户默认锁定，没有出厂密码。Studio 通过 systemd 直接以 `orangepi` 身份运行，锁定交互登录不影响它启动。如需 SSH，进入 Studio“系统设置 → 账号与 SSH”，按页面设置自己的 SSH 登录方式。网页密码与 Linux/SSH 密码独立。

## 镜像布局和自启

R2 默认磁盘镜像大小为 6 GiB，保留已知官方底座的引导区、内核与板型配置，并扩展根分区以容纳完整应用。

- `/opt/microduck-studio/{app,python}`：真实 Studio、WebGL 资源、独立 CPython 3.13.14 和纯 Python 依赖。
- `/opt/robot/feetech-ft5-r5`：主控及辅助程序、ONNX Runtime 1.28、10 份模型和许可证。
- `/etc/robot/feetech-ft5`：策略关闭、未配置串口和未校准模板。
- `/var/lib/microduck-studio`：出厂为空、属主 `orangepi`、模式 `0700` 的网页状态目录。
- `orangepi-provision.service`、`robotd.service`、`microduck-studio.service`：已经配置开机自启。

`robotd` 使用 `--read-only`，初始等待不存在的占位串口，不会因此显示硬件已连接。手柄、媒体守护进程及可选 configd、ToF、摄像头、语音 HTTPS 服务未启用。UART 接入、校准与运动验收按[硬件说明](../migration/hardware/README.md)另行进行。

## 从源码构建完整镜像

需要 Python 3.12 或更新版本、e2fsprogs、正在运行的本机 Docker，以及可执行 `linux/arm64` 容器的环境。本轮构建使用 Docker ARM64 环境；macOS 可通过 Homebrew 安装 `e2fsprogs`。构建阶段会联网安装 Ubuntu 依赖，具体版本写入 `packages.tsv`。

构建只接受以下已知官方镜像：

- `Orangepizero3w_1.0.2_ubuntu_jammy_server_linux6.6.98.img`
- SHA-256：`e0a9f05e9e4af2aac7560b8ca3e752754d23581142d953dcc0c02b623552fa56`

从 `software/orangepi-zero3w/` 运行，`--runtime-sha256` 填入已核对的完整 R2 运行包摘要：

```sh
python3 image/build_full_image.py \
  --base-image /path/to/Orangepizero3w_1.0.2_ubuntu_jammy_server_linux6.6.98.img \
  --runtime-archive /path/to/orangepi-quanzhi-20261112-r2.tar.gz \
  --runtime-sha256 '<运行包的 SHA-256>' \
  --output-dir /path/to/new-r2-output \
  --size-gib 6
```

输出目录必须尚不存在。构建器在一次性 ARM64 Docker 容器中创建镜像文件对应的 loop 设备；没有传入宿主机磁盘设备。容器使用 `--privileged` 完成虚拟机内的 loop 挂载，结束时卸载并删除容器。源码只读挂载，写入范围是指定的构建工作目录。

构建顺序为：校验输入 → 扩展离线根分区 → 安装系统依赖和配网 → 预装完整运行包 → 在 ARM64 chroot 内执行读回和兼容性检查 → 卸载 → 清理凭据与设备状态 → 重建 journal 并擦除空闲区域 → 文件系统检查 → 组装、扫描与压缩磁盘镜像。安装期间通过 `policy-rc.d` 阻止包管理器启动服务；构建器不启动镜像中的机器人服务。

`preinstall_runtime.py` 负责目标文件、权限、属主、服务启用链接及空状态校验。`prepare_rootfs.sh` 负责依赖和配网。`--finalize-rootfs` 与 `--preinstall-report` 是成对的检查点选项，仅用于继续处理同一运行包的已预装离线副本；继续前仍会重新核对 manifest、完整文件和 ARM64 chroot 检查。

输出包括 `.img`、`.img.gz`、`IMG-SHA256SUMS`、`image-audit.json`、`preinstall-report.json`、`packages.tsv` 和文件系统检查记录。apt 源和 Docker 基础环境可能随时间变化；这些记录用于核对实际构建结果，不承诺不同时间构建的逐字节一致性。

## 运行包与路径清理

完整镜像的输入是经过清理、重新生成 manifest 的 `orangepi-quanzhi-20261112-r2.tar.gz`。该包也可用于[已有系统的手动安装](../migration/README.md)，刷完整镜像后无需再安装。

`sanitize_runtime.py` 对未发布运行包副本中的 ELF `.rodata` 诊断源路径进行等长泛化，保留 ELF 结构并更新 manifest；`path-sanitization.json` 记录前后摘要。此步骤改变了 `robotd` 文件字节，运行程序以清理后的摘要为准。模型权重没有改动，训练导出和不需要的训练元数据已移除；剩余 JSON 中的个人构建路径和内网地址已泛化。

## 出厂清理与验证边界

清理移除 SSH host 私钥、账户密码 hash、Wi-Fi 配置、网页密码与会话、machine-id、随机种子、系统日志和常见用户认证状态。保留预装文件及公开 manifest，重新校验清理后的文件摘要、权限、属主、空 Studio 状态和自启链接。已删除凭据的残留通过重建 journal、覆盖全部空闲块和空闲 inode 槽处理，并针对收集到的原始私钥载荷与密码 hash 执行全盘字节扫描。

OpenVPN 文档演示私钥也被移除。官方 GnuTLS 共享库中的公开自测常量保留原库字节；这些测试向量不作为设备认证材料。首次 SSH 启动前由独立单元生成本机 host keys；systemd 根据清空的 machine-id 初始化标识。官方首启脚本中重复删除/生成 SSH key 的代码段已精确替换，避免重新更换主机身份。

本轮 ARM64 chroot 预装读回、动态库解析、Python SSL/SQLite/TOML、主控帮助、全部 10 份 ONNX 逐一 `validate-policy`（均 `valid=true`）、sudoers 和服务单元检查已通过。18 项镜像测试、66 项主机工具测试、26 项 Studio 测试共 110 项通过。

从最终封装镜像建立独立副本，在原生 ARM64 Linux 中运行真实 robotd 和 Studio，通过 Chrome 实际完成首次设密、密码不一致/错误密码拒绝、刷新、退出重登、Studio 服务重启后密码和会话保留、新浏览器不能重新初设，以及 3D 资源加载与渲染；RPC 实测为 `read_only/waiting_for_bus`。1440px 与 390px 的首次设置和中控截图随 [Release](https://github.com/Liangjin5437/microduck-replica/releases/tag/orangepi-zero3w-20261112-r2) 提供，完整记录为 `browser-verification.json`。

180 条已知原凭据扫描模式在完整磁盘字节中均无残留，实际操作人的私人路径标记也未命中；旧凭据和设备状态清理检查通过。源码 Gitleaks 无检出；运行时仅有一条 Windows Registry API 赋值，经人工确认是 API 常量误报。扫描结果限定于实际检查的材料和模式。

本次没有可用实板，未做 SD 卡启动、A733 驱动、真实 Wi-Fi 配网或重启恢复验证。chroot 与容器共享其 Linux 内核，不能验证镜像的板级内核启动。校准、上力、站立、行走和其他运动不在本次验收范围。

## 历史配网基础版

旧配网版及其运行包已撤下，由 R2 完整镜像取代。`build_public_image.py` 和 `personalize_image.py` 保留为历史构建源码；R2 正常使用流程不需要它们。
