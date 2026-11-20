# 独立常驻语音入口

用于已验证的语音版长期运行。8000 正式版仍由签名更新器维护；本安装只写当前用户的家目录，不覆盖正式版，也不更换签名公钥。

在鸭哥上，以 `radxa` 用户从包含完整 `run.py`、`studio/`、`web/`、`deps/` 和本目录的源码执行：

```sh
python3 -I deploy/install_voice_user.py --tls-source /home/radxa/studio-phone-tls --name 192.0.2.118
systemctl --user daemon-reload
systemctl --user enable --now microduck-voice.service microduck-voice-https.service
```

安装会冻结当前代码和 Python 依赖，首次导入原 Studio 的登录和标定状态，以后重装保留新状态。此后语音入口和 8000 的设置分别保存；日常使用语音入口，并在修改登录密码时分别管理两个入口。认证文件和 TLS 私钥仅保存在鸭哥的私有目录。

管理员必须执行一次下面的命令，让用户服务在开机时启动、退出 SSH 后继续运行。`systemctl --user enable` 成功不代表这一步已经完成。

```sh
sudo loginctl enable-linger radxa
```

入口为 `https://duck-f6af.local:8444/#/voice`（名称使用鸭哥自己的主机名）。现有证书已包含的 IP 也可访问；换 IP 后优先用主机名。浏览器需信任现有签发证书；安装脚本不会修改电脑或手机的信任设置。应在证书到期前更新 `~/.local/share/microduck-voice/tls/` 中的证书，再重启 HTTPS 服务；现有证书不会被重装覆盖。

当前语音开放“鸭哥”、站起来、开始走、坐下、起坐、前进、后退、左转、右转、停下和翻个跟头。所有动作复用手柄页的姿态、状态新鲜度、控制权及急停保护；已经站着时拒绝重复起身。坐站前先扶稳鸭哥。移动、坐下和翻跟头前，先站稳，再说“开始走”或在手柄页点“开始走”；移动只发送一帧低速指令，原有超时保护自动回零；翻跟头执行一次。“停下”只停止行走，已经开始的技能按原动作结束。识别器使用包含全部动作、否定词和未知片段的专用词表，保留否定和多动作表达，后端拒绝否定或冲突口令。语音版后端仅监听本机 18081，局域网通过有密码保护的 HTTPS 8444 接入，未启用开发免登录。

验收：检查两个用户服务 active、`loginctl show-user radxa -p Linger` 为 yes，退出 SSH 后仍可访问，再在鸭哥已卸力时重启设备并重新验证页面、连接状态和试叫回执。未实际重启时不得宣称开机验证通过。

停止常驻入口：

```sh
systemctl --user disable --now microduck-voice-https.service microduck-voice.service
```

旧代码保存在 `~/.local/share/microduck-voice/previous` 指向的目录。回退代码前先停止两个服务，再将 `current` 指向该目录并启动；状态目录不随代码回退覆盖。
