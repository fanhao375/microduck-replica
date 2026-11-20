# Cubie A7Z：IMX219 摄像头预览

运行时源码基线是 [Microduck-kissqy 的 d1a2c62](https://github.com/fanhao375/Microduck-kissqy/commit/d1a2c62efdfb8c7a446ac99f562849e5a3a9587f)。在该仓库根目录应用本目录的 `imx219-preview2.patch`（先用 `git apply --check` 验证）；它包括 mediad 和共享配置中的 4:3 画质枚举，不需重新部署 robotd。

- 识别：A7Z 使用单路 `cubie-a7a-radxa-camera-8m-219.dtbo`。在原有 extlinux 启动项添加该设备树覆盖，保留原 root 参数。
- 出帧：VIN 采集由系统 GStreamer 1.18 子进程负责。显式使用 mmap，在已验证的 1280×960@30 模式启用 ISP；WebRTC 管线继续使用 mediad 自己的运行库。
- 颜色：RGB24 的实测字节序为 BGR。重新解释为 BGR/sRGB 后转换到 I420，再编码。可选 ISP 参数限于采集子进程，白纸校正表只修正 U/V，不能当成不同模组或光源的工厂标定。
- 中控：mediad 使用独立的 /etc/robot/mediad.toml 和 /run/mediad/media.sock。Studio 连接此 socket，并从 mediad.toml 读取摄像头配置。在「语音与视觉 → 摄像头 → 开始预览」查看完整 4:3 画面（960×720、15 fps、3 Mbps）。
- 验证：开发板实拍和桌面、手机预览已验证。镜像完成离线校验；全新 SD 卡冷启动仍待验收。仅打开基础预览，不含视觉识别服务。

preview2 的 mediad SHA256：`11ab3928126c13d6107e1152f7ca143c72339a2d3a93f4295c01cffa5ab5182d`。

[服务配置](service/) 保留板上使用的启动器、sysusers 配置、systemd 单元和 drop-in。启动器和插件扫描器安装于 /usr/local/sbin；配置安装于 /etc/robot/mediad.toml。两个调色 drop-in 分别依赖匹配的 ISP 库和白纸校正文件，缺少这些可选数据时不要启用对应 drop-in。服务还依赖兼容的板端运行库。
