# 网页维护：备份、恢复和安全关机

桌面版从「设置」、手机版从「更多」进入「关机与备份」。两版顶栏也可以安全关机。请先设置网页登录密码并登录。私密备份下载和所有维护操作均核对实际登录会话。上传和关机还校验请求来源及 CSRF 令牌。

## 安装维护助手

在使用 `/var/lib/microduck-studio` 保存状态的目标板上，将 `deploy/microduck-maintenance` 以 root:root、0755 安装到 `/usr/local/sbin/microduck-maintenance`。助手与总线、标定助手共用 `/run/microduck-helper/helper.lock`。需要 Python 3.11 或更新版本、sudo 和 systemd。恢复相机设备树时还需要 fdtoverlay。

先核对 Studio 服务用户，再用 visudo 授权。若用户为 radxa，授权行如下。四个参数必须逐个列出，内部延时操作由 systemd 调用。

```sudoers
radxa ALL=(root) NOPASSWD: /usr/local/sbin/microduck-maintenance export, /usr/local/sbin/microduck-maintenance preview, /usr/local/sbin/microduck-maintenance restore, /usr/local/sbin/microduck-maintenance shutdown
```

## 私密备份

ZIP 未加密，可能包含 AI 与 TTS 服务的 API Key、设备配置和主机名。请选择私密位置保存，只导入可信来源的文件。网页登录密码和会话只保存在本机。预览列出配置名称及来源信息。使用标准套件布局时，会附上 `/opt/microduck/software/current/studio` 下的 Studio 和网页源码文件供离线查阅。

助手核对白名单、成员数量、路径、解压大小和清单 SHA256。SHA256 用于检查完整性，备份没有签名。ZIP 与所有解压成员合计各限 16 MiB，JSON/Base64 请求限 24 MiB。导入只写所列配置。源文件不参与恢复。

## 恢复与关机

请扶稳鸭子并保持卸力，直到服务完成重启或设备关机。恢复前，助手将原配置备份到 `/var/backups/microduck-import-*` 的私有目录。写入、重载单元或安排重启失败时会尝试回滚。恢复已有文件时保留原属主。新建的 Studio 私有配置使用状态目录的属主。相机设备树在下次整机启动时生效。

关机先请求停止和卸力。助手分别核对运动或标定模式下的实际扭矩反馈，并在延时操作执行前再次确认。如果在此期间重新上力，助手会取消这次重启或关机。已成功写入的配置仍保存在磁盘。重新卸力后可以重试，或通过 `journalctl -u microduck-web-maintenance.service` 查看延时操作的结果。页面显示「已安排关机」后，请等设备完全停止再断电。
