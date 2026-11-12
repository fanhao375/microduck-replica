# Orange Pi Zero 3W Studio

`payload/` 采用目标系统根目录布局，包含应用源码、WebGL 资源、服务、sudoers 和受限管理助手。完整 ARM64 Python 与纯 Python 依赖随 Release 运行包提供；Git 仅保留自有应用源码、依赖版本和许可证。依赖与同仓 `software/studio/deps` 的版本清单一致。

## 平台适配

应用基于 `20261109-console1` 中控与 A7Z 后端，账号使用 `orangepi`，服务附加 `robot dialout` 组，通过 `unix:/run/robotd.sock` 访问真实主控。默认串口为不存在的 `/dev/orangepi-robot-uart-unconfigured`，主控只读。configd、ToF、摄像头 socket 明确禁用；不启动模拟器。

root 布局的 `/usr/local/sbin/microduck-helper` 是实际部署版本：SSH 密码认证状态以 `sshd -T` 读回；关闭后若仍启用或未知则返回失败。它只操作自身负责的 SSH 配置，不改其他配置。旧热点动词返回 `external_network_manager`，避免与 `orangepi-provision.service` 争用网络。

应用内 `deploy/` 是随源保存的原部署模板；其中 helper 未叠加后来针对 Ubuntu 的 SSH 状态修正。Orange Pi 安装应使用根布局 helper，不能用 `app/deploy/` 再次覆盖它。sudoers 仅允许固定路径 `/usr/local/sbin/microduck-helper`，安装器先用 `visudo -cf` 核验。

## 首次使用

安装器创建干净的 `/var/lib/microduck-studio` 与日志目录，属主为 `orangepi`。无预设网页密码、session、TLS 私钥、Linux 密码或旧板状态。用户在可信局域网打开 `http://<设备地址>:8000/`，通过正常网页自行设置管理员密码；未启用 `--dev-autologin`。命令行 `run.py set-password --state-dir /var/lib/microduck-studio` 也只能由操作者交互使用。

运行代码、独立 Python、helper 由 root 持有。应用自有状态目录不得从旧板复制。可选语音代码仍保留，但 Vosk 模型与 ASR 依赖需要另行配置；未启用语音 HTTPS 服务。

## 离线验证

从 `software/orangepi-zero3w/` 执行：

```sh
# 仅在源码 checkout 中补齐依赖；完整 Release 已包含该目录。
cp -R ../studio/deps/. migration/studio/payload/opt/microduck-studio/app/deps/
python3 -m unittest discover -s migration/studio/tests -v
```

测试使用临时目录、禁用的 RPC 地址和伪系统命令，不访问 UART、不启动系统服务、不更改真实密码。Linux aarch64 Python、动态库及服务启动仍需真板验证。
