# A7Z Studio 整机预发布 · preview6

[下载烧录镜像](https://github.com/Liangjin5437/microduck-replica/releases/download/a7z-duck-image-20261127-studio-preview6/cubie-a7z-duck-20261127-studio-preview6.img.xz) · [SHA256 校验文件](https://github.com/Liangjin5437/microduck-replica/releases/download/a7z-duck-image-20261127-studio-preview6/cubie-a7z-duck-20261127-studio-preview6.img.xz.sha256) · [全部附件](https://github.com/Liangjin5437/microduck-replica/releases/tag/a7z-duck-image-20261127-studio-preview6)

本版提供完整 microSD 镜像，解压后为 6,776,878,080 字节。压缩文件的 SHA256 为 `61738dca448ad501e31033f0d2234613882b586a61a4d3e12c7d3c0fafd0fc67`。

[烧录、配网、标定与网页手柄操作](first-use-preview6.md)。出厂使用 9600 行走与恢复，模型库在首次打开前会初始化 HD1910 9000 可选项。包内包含该模型的关节顺序、部署合同、运行参数和执行参数。选用时 Studio 调用 robotd 做预检。坐站、前滚翻、捡取和左右踢球沿用独立模型。开机进入待标定模式，首次启动不自动站起或走路。

调试链路包含 Studio 前后端及独立 Python、舵机和 IMU 标定助手、UART 初始化、ARM64 robotd、ONNX Runtime、配网以及网页手柄。保留相机、视觉识别和 Robot HAT 音频。DuckBuddy 蓝牙配网另附离线安装包，热点可直接配网。

## 验证范围

成品通过主备 GPT、ext4 只读检查、根分区外字节比对和完整解压 SHA256 校验。180 项覆盖内容逐项读回，另核对了 115 个运行文件的 SHA256，包括继承的模型、推理库和标定助手。镜像保留每台单独标定的要求。本版空白卡冷启动、实物标定、持续地面行走和恢复仍待实机验收。

## 按发布负载重建

从[基底 preview3 Release](https://github.com/Liangjin5437/cubieA7Zduck/releases/tag/a7z-duck-image-20261115-vision-preview3) 下载 `cubie-a7z-duck-20261115-vision-preview3.img.xz`。从本版附件下载 `studio-preview6-overlay.tar.gz` 和成品同名 `.manifest.json`。在此目录准备 Python 3.13、xz、debugfs、e2fsck，并预留 10 GiB 可用空间。

```sh
python3 rebuild_studio_preview6.py \
  --base cubie-a7z-duck-20261115-vision-preview3.img.xz \
  --payload studio-preview6-overlay.tar.gz \
  --manifest cubie-a7z-duck-20261127-studio-preview6.img.xz.manifest.json \
  --output rebuilt-preview6.img.xz
```

脚本固定三个输入的 SHA256，复用发布时的保护文件与读回检查。文件系统时间可能影响重建后的整包摘要。覆盖包包含本次写入的 Studio、配网、服务、运动主程序、模型、音频和来源清单，基底提供其余系统和运行库。
