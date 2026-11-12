# Orange Pi Zero 3W Studio

R2 完整镜像预装真实 Studio 后端、`20261109-console1` 中控前端、WebGL 资源、独立 CPython 3.13.14 和固定版本 Python 依赖。`microduck-studio.service` 已启用开机自启，以 `orangepi` 身份运行，附加 `robot dialout` 组，通过 `unix:/run/robotd.sock` 读取主控状态。

## 烧卡后的首次使用

手机完成热点 Wi-Fi 配网后，打开 `http://<设备当前 IP>:8000/`。没有网页密码时直接显示“设一个登录密码”，两次输入并保存后进入中控。无需先 SSH 登录或手动运行安装器。

首次页面复用现有 `PasswordSettings` 和 `/api/password`，仍校验同源 Origin 和 open session 的 CSRF。保存后服务器创建自己的登录 Cookie，前端重新获取会话，再挂载中控和 WebSocket。未启用 `--dev-autologin`，首次表单也不使用模拟数据。

出厂 `/var/lib/microduck-studio` 为空、模式 `0700`、归 `orangepi` 所有，不含网页密码、会话或旧板标定。网页密码独立于 Linux 登录。Linux 账户默认锁定不影响 systemd 启动服务；需要 SSH 时可在“系统设置 → 账号与 SSH”另行设置自己的登录方式。

## 平台适配与权限

主控保持 `--read-only`、策略关闭，Studio 默认串口是不存在的 `/dev/orangepi-robot-uart-unconfigured`。configd、ToF、摄像头 socket 明确禁用，不启动硬件模拟器。默认网页首次设密与状态查看不要求接管舵机总线；校准、接管和运动分别属于后续硬件操作。

`/usr/local/sbin/microduck-helper` 是实际部署的受限助手，代码和独立 Python 由 root 持有。sudoers 只允许固定路径的 helper，并在预装检查中通过 `visudo -cf`。helper 用 `sshd -T` 读回 SSH 密码认证状态，关闭后若仍开启或状态未知会如实报错。旧热点动词返回 `external_network_manager`，避免与 `orangepi-provision.service` 争用网络。

应用内 `deploy/` 保留原部署模板；其 helper 没有后来针对 Ubuntu 的状态修正，不能用它覆盖根布局的实际 helper。应用状态目录不得从旧板复制。语音代码保留，但 Vosk 模型、ASR 依赖和语音 HTTPS 服务未随本次镜像启用。

## 源码与验证

Git payload 保留应用源码、资源、依赖版本和许可证；完整独立 Python 与依赖实现在 Release 运行包和完整镜像中。源码测试前从同仓固定版本依赖补齐：

```sh
# 从 software/orangepi-zero3w/ 执行；完整镜像已安装依赖。
cp -R ../studio/deps/. migration/studio/payload/opt/microduck-studio/app/deps/
python3 -m unittest discover -s migration/studio/tests -v
```

测试覆盖干净状态、首次设密的真实 ASGI 路由、Cookie 和 CSRF、同源限制以及只读主控拒绝受保护写入；测试数据存放在临时目录。镜像内 ARM64 Python 的 SSL/SQLite/TOML 导入和 Studio 自检已通过。

最终封装镜像的独立副本已在原生 ARM64 Linux 中运行真实 robotd 和 Studio，通过 Chrome 验证首次设密、两次密码不一致/错误密码拒绝、刷新、退出重登、Studio 服务重启后密码和会话保留、新浏览器不能重新初设，以及实际 3D 资源加载和渲染。真实 RPC 为 `read_only/waiting_for_bus`。首次设置和中控均已保存 1440px、390px 截图；`browser-verification.json` 随 R2 Release 提供。26 项 Studio 自动测试通过。

没有可用实板，未验证 SD 首启和硬件驱动，也未通过摄像头、ToF、校准或运动验收。
