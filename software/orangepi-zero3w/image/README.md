# Orange Pi Zero 3W 配网基础镜像

本 Release 为 Orange Pi Zero 3W、Ubuntu Jammy、Linux 6.6.98 的**配网基础镜像**。包含 `orangepi-provision`，没有安装 Studio、robotd、模型或 UART/HAT 运动配置；后续按 Orange Pi 适配文档单独安装应用。离线文件系统和敏感数据清理已经验证，本次新生成的镜像尚未刷卡启动验证。

## 下载、验证与初始化

下载同一个 Release 的 `.img.gz`、`IMG-SHA256SUMS` 和 `image-audit.json`。解压并在两份文件都存在时校验：

```sh
gzip -dk microduck-orangepi-zero3w-provision-20261112.img.gz
shasum -a 256 -c IMG-SHA256SUMS
```

Release 里所有 Linux 账户均已锁定，没有预设登录密码。**先在自己的电脑上创建设置了个人密码的新镜像，然后再烧录**。不要直接刷原始 Release 文件来尝试默认密码。

本机需要 Python 3、e2fsprogs 和支持 `passwd -6` 的 OpenSSL 3。macOS 可用 `brew install e2fsprogs openssl@3`；Linux 通过发行版包管理器安装。将 `personalize_image.py` 和 `image_fs.py` 放在同一目录，运行：

```sh
python3 personalize_image.py \
  microduck-orangepi-zero3w-provision-20261112.img \
  my-orangepi-private.img \
  --sha256 '<IMG-SHA256SUMS 中未压缩 .img 的 SHA-256>'
```

程序在终端隐藏输入并要求重复确认密码（至少 12 字符），密码不会进入命令行参数、环境变量或输出。它校验输入摘要，在私有临时目录修改副本，读回检查 `/etc/shadow` 内容与权限，并通过 `e2fsck -fn` 后创建新文件；不会覆盖现有文件或更改 Release 原件。

将 `my-orangepi-private.img` 烧录到 SD 卡。使用 `orangepi` 和自己选定的密码登录；该用户已有 sudo 组权限，root 账户保持锁定。**个性化镜像含个人密码 hash，请保留在本机，不要上传 GitHub 或 Release。**

首次通电后，未连接已保存 Wi-Fi 时会出现开放的 `OrangePi_XXXXXX` 热点，连接后访问 `http://10.42.0.1/` 完成配网。首启在 SSH 监听前生成本机独有的 SSH host keys，systemd 根据空 `machine-id` 初始化本机标识。此镜像不包含已保存 Wi-Fi、网页登录密码或会话。配网行为的真板证据来自已有程序版本；本次生成镜像的首次启动、驱动和具体硬件仍需使用者验证。

## 从源码重建

`build_public_image.py`、`image_fs.py` 应放在同一目录。构建只接受这一份已知官方镜像：

- 文件：`Orangepizero3w_1.0.2_ubuntu_jammy_server_linux6.6.98.img`
- SHA-256：`e0a9f05e9e4af2aac7560b8ca3e752754d23581142d953dcc0c02b623552fa56`

```sh
python3 build_public_image.py \
  --base-image /path/to/Orangepizero3w_1.0.2_ubuntu_jammy_server_linux6.6.98.img \
  --project /path/to/orangepi-source \
  --output-dir /path/to/new-output-directory
```

`--project` 指向同时含 `provision/install.sh` 与 `tools/install_rootfs.py` 的 Orange Pi 源码目录；输出目录必须尚不存在。构建读取官方镜像的原始引导区，在副本中从源码注入配网服务，不连接任何实际设备、不访问其他项目、不读取本机账户密码。

清理过程移除官方镜像出厂 host 私钥、账户密码 hash、machine-id、随机种子、Wi-Fi/NetworkManager 状态、日志与常见用户认证状态；移除 shadow/gshadow 备份及 OpenVPN 文档中单独存放/内嵌的演示私钥。系统 GnuTLS 共享库含公开的内部自测密钥常量；保留官方库字节，不将这些公开测试向量视为设备认证凭据。清理后重建 ext4 journal，保留其全新 JBD2 superblock 并将日志记录区域清零，按 `dumpe2fs` 分配表覆盖全部空闲数据块与空闲 inode 槽。最后对完整磁盘镜像逐字节扫描原始三类 SSH 私钥、已删除 OpenVPN 演示私钥的 Base64 载荷行及原始 shadow 密码 hash，确认这些已知凭据不在活动文件、journal 或删除残留里。

官方 `orangepi-firstrun` 的 SSH 删除/生成/重启代码段被精确替换一次，其余官方首启逻辑保留；原脚本摘要记录在 `image-audit.json`。SSH 的 service、socket 和 socket activation 实例均显式依赖新的 `orangepi-image-identity.service`。新单元不依赖 SSH，避免官方首启调用 SSH restart 导致的排序等待环。该补丁不涉及密码设置；已检查官方首启脚本不会覆盖离线设置的 `orangepi` 密码。

`image-audit.json`、前后 `filesystem-*.txt` 和 `IMG-SHA256SUMS` 记录本次静态/离线验证。已知原凭据的全盘扫描是针对明确材料的检查，不等于证明任意系统软件绝无所有类型的秘密或漏洞。不要将私人完整备份、刷卡撤销文件或 journal 错误实验镜像上传为公开镜像。
