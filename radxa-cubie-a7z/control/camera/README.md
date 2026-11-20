# Cubie A7Z IMX219 摄像头预览

此目录提供可选的 A7Z 源码适配。开发者报告已在适配板上用桌面和手机查看 960×720、15 fps 的完整 4:3 WebRTC 画面，配置起始码率为 3 Mbps。已发布的 A7Z 整卡为 preview1，摄像头在该镜像上默认关闭。preview2 摄像头整卡未公开发布，空白 SD 卡冷启动仍待验收。[A7Z preview1 镜像及附件](https://github.com/Liangjin5437/microduck-replica/releases/tag/a7z-duck-image-20261109-preview1)

## 准备板端运行时

开发者记录的构建提交为 `d1a2c62efdfb8c7a446ac99f562849e5a3a9587f`，所测 mediad 的 SHA256 为 `11ab3928126c13d6107e1152f7ca143c72339a2d3a93f4295c01cffa5ab5182d`。下列公开上游提交中，补丁修改的六个原文件与补丁的 Git blob ID 全部匹配，可以从干净检出重建补丁后的源码。该二进制摘要仅供比对原开发板构建。

在复刻仓库根目录执行以下命令，使用新的 `a7z-runtime` 工作目录。

```sh
camera=$(realpath radxa-cubie-a7z/control/camera)
git init a7z-runtime
git -C a7z-runtime fetch --depth=1 https://github.com/kissqy/Microduck-kissqy.git 0791fcd44f71c705dc8bb0fbcde405593b52d19b
git -C a7z-runtime switch --detach FETCH_HEAD
git -C a7z-runtime apply --check "$camera/imx219-preview2.patch"
git -C a7z-runtime apply "$camera/imx219-preview2.patch"
```

为 A7Z 的 aarch64 环境编译 `microduck-hd1910/firmware` 中的 `mediad`。板端需要将它安装到 `/opt/robot/feetech-ft5-r5/mediad`，并在 `/opt/robot/gst-root` 准备与之匹配的动态加载器、GStreamer 运行库和 WebRTC 插件。板级系统另需带 Allwinner VIN/ISP 支持的 GStreamer 1.18。仓库仅提供补丁和配置，运行库及板端二进制需另备。

在现有 extlinux 启动项加入单路 `cubie-a7a-radxa-camera-8m-219.dtbo` 覆盖，保留原 root 参数，重启后确认 IMX219 对应 `/dev/video0`。采集进程通过 mmap 在 1280×960@30 模式启用 ISP，把实测的 BGR 字节顺序按 sRGB 转为 I420，再缩放并编码。可选 ISP 库和只校正 U/V 的白纸校正表均需匹配具体模组和光源。

## 在已准备的 A7Z 上启用

以下命令适用于原始 preview1 Studio。先准备上述硬件、二进制和依赖，从含本次适配的复刻仓库根目录执行。先检查现有 Studio 后端并预演补丁。相机服务读取独立的 `/etc/robot/mediad.toml`，Studio 补丁只更改其摄像头来源查询。

```sh
board=$(realpath radxa-cubie-a7z)
camera="$board/control/camera"
service="$camera/service"
app=/opt/microduck-studio/app
(cd "$app" && sha256sum -c - <<'SHA'
5ad1f1b63a0d37590682c7fe1161711f18afffd009194cfac821ce76b4ea0cc9  studio/sensors.py
601abdf1d360d02eba171ceead6363cbdefad1590294674c005e355f3bf7bf9e  web/sensors.js
2c838f7d3dd97d58de01b4cbe1bcf4d516b9b77cdfe1777e4bbe7f68d715d683  web/console-layout.js
54ef8cc92852159b50edbc165c2377520892144ef325582669f3476df2168e5b  web/console.css
87c3292fc8216d5ddc6b45852e09624018abd0a5cdb82a1bb5157e5e98afa334  web/console-experience.css
SHA
)
patch --dry-run --fuzz=0 --forward -d "$app" -p1 < "$camera/studio-camera-config.patch"
```

确认上述校验和预演均成功后，保留这些文件的备份，并安装服务配置。常规配置启用 CPU/网络设置和首启依赖。仅在另备匹配 ISP 库或校正表时安装对应的调色 drop-in。

```sh
sudo install -D -m644 "$service/sysusers/mediad.conf" /etc/sysusers.d/mediad.conf
sudo systemd-sysusers /etc/sysusers.d/mediad.conf
sudo install -m755 "$service/mediad-run" "$service/gst-plugin-scanner-a7z" /usr/local/sbin/
sudo install -D -m644 "$service/mediad.toml" /etc/robot/mediad.toml
sudo install -m644 "$service/mediad.service" /etc/systemd/system/
sudo install -d /etc/systemd/system/mediad.service.d
sudo install -m644 "$service/mediad.service.d/a7z-cpu.conf" "$service/mediad.service.d/firstboot.conf" /etc/systemd/system/mediad.service.d/
sudo patch --fuzz=0 --forward -d "$app" -p1 < "$camera/studio-camera-config.patch"
for name in sensors.js console-layout.js console.css console-experience.css; do
    sudo install -m644 "$board/studio/console-integration/web/$name" "$app/web/$name"
done
sudo install -D -m644 "$service/microduck-studio.service.d/a7z-camera.conf" /etc/systemd/system/microduck-studio.service.d/a7z-camera.conf
sudo systemctl daemon-reload
sudo systemctl restart microduck-studio.service
```

Studio 通过其 drop-in 拉起 mediad，并连接 `/run/mediad/media.sock`。核对 `systemctl is-active mediad microduck-studio`、`test -S /run/mediad/media.sock`，在同一局域网的 Studio 打开「语音与视觉 → 摄像头 → 开始预览」。以实际画面和 960×720 视频尺寸确认出帧。无法出帧时查阅 `journalctl -u mediad -b`。此流程仅启用基础预览。

移除 `/etc/systemd/system/microduck-studio.service.d/a7z-camera.conf`、恢复备份的五个 Studio 文件后，运行 `sudo systemctl daemon-reload`、`sudo systemctl restart microduck-studio` 和 `sudo systemctl stop mediad`，即可回到 preview1 的相机关闭状态。
