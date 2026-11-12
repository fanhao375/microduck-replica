# Cubie A7Z 鸭子整机镜像

此镜像面向 **Radxa Cubie A7Z + 当前项目的 HD1910 舵机/总线 IMU 装配**，把系统、联网配网、Studio、A7Z 主控程序和动作模型一起交付。候选文件为：

```text
artifacts/cubie-a7z-duck-20261109-preview1.img.xz
artifacts/cubie-a7z-duck-20261109-preview1.img.xz.sha256
artifacts/cubie-a7z-duck-20261109-preview1.img.xz.manifest.json
```

镜像下载与校验附件见 [A7Z preview1 Release](https://github.com/Liangjin5437/microduck-replica/releases/tag/a7z-duck-image-20261109-preview1)，源码维护于 [Liangjin5437/microduck-replica](https://github.com/Liangjin5437/microduck-replica) 的 `radxa-cubie-a7z/`。完整镜像作为 Release 附件分发，不放入 Git 源码。

这是待上板验收的预览版。构建期间原板 `BOARD_IP` 连接超时，尚未验证本镜像的烧录启动、实物联网、动作或声音。

## 烧录和首次使用

1. 核对同名 `.sha256`，用支持 `.img.xz` 的烧录工具写入至少 8 GB 的 microSD 卡（解压后为 6,776,878,080 字节）。写入会清除目标卡的数据。**不要使用 Zero 3W 镜像替代。**
2. 将卡放入 A7Z，接好对应硬件并供电。首次启动会完成系统初始化、分区扩容、生成设备身份和声音文件；等待配网热点出现。
3. 手机连接无密码热点 `qiqilong_XXXX`，提示“无互联网”时保持连接。打开 [http://10.42.0.1/](http://10.42.0.1/)，填入使用者自己的 Wi-Fi 名称和密码。提交后热点暂时关闭；连接失败会恢复热点，页面显示原因。
4. 手机回到同一 Wi-Fi，打开 `http://cubie-XXXX.local:8000/`。若手机不支持 `.local` 解析，从路由器查到鸭子的地址，打开 `http://设备IP:8000/`。配网页使用 **80** 端口，Studio 使用 **8000** 端口。
5. 进入 Studio，依次完成舵机零位/方向、IMU 安装方向及主程序标定提交。新装配必须重新标定；镜像没有沿用另一只鸭子的运动授权。初始 Studio 可在同一网络直接打开，管理密码可在设置中建立。系统保留基础发行版默认登录凭据，首次登录后修改 `radxa` 用户密码。
6. 标定通过、页面确认就绪后，由现场操作者支撑并检查活动范围，再显式执行站起、行走及其他动作。镜像启动不会自动站起或走动。

设备的 machine-id、热点/主机名后缀和 SSH 主机密钥在新设备启动时生成，不把原板的 Wi-Fi 密码、Studio 登录状态或 SSH 主机密钥复制给别人。已保存 Wi-Fi 后，后续开机会尝试重连；没有可用网络时恢复配网热点。

## 已打包的功能

- Studio 调试、舵机与 IMU 标定、网页虚拟手柄和当前中控前端 `20261109-console1`。
- 行走/保持、坐下/站起、低头捡取、前滚翻、左右踢球模型；另保留随包滑轮模型资源。
- 鸭叫合成程序与首启音库生成，主控通过 ALSA 播放声音。配置使用 `default` 声卡，A7Z 的实际扬声器/功放输出仍需上板确认；打包声音程序不能证明已经能听到鸭叫。
- 自动配网、A7Z UART 适配和已有温控策略。

首启为 **commissioning 标定模式**，15 个关节和 IMU 均未授权运动；Studio 提交新标定后切入正常模式，再由人开启动作。标定模式关闭主控音频，正常鸭叫应在完成标定后检查。

实体手柄、Studio 内的 configd 配网、相机、ToF、持续语音识别以及关节跟踪后端没有在此版本启用。首次联网使用独立配网页；网页虚拟手柄不依赖实体手柄服务。此前安装有实物迈步反馈，也有尚未解决的总线偶发读取失败记录；本次打包没有修复该根因。持续步态、捡取和翻滚仍待现场验证。

## 维护者重建

构建需要下方列出的固定外部输入；普通源码检出不包含完整依赖，不能直接一键重建。脚本保留原构建目录布局的默认值，维护者须准备匹配版本、摘要和目录位置的输入，或显式配置对应脚本的输入参数；`create_image.py` 入口目前只提供 `--base`、`--output`、`--release`。

在本目录执行；使用全新输出路径，构建程序拒绝覆盖已有产物：

```sh
python3 create_image.py \
  --base ../os/radxa-a733_bullseye_kde_r6.output_512.img.xz \
  --output artifacts/cubie-a7z-duck-20261109-preview1.img.xz
```

需要 Python 3.13（本次使用 3.13.6）、`xz`、`debugfs` 和 `e2fsck`；保持足够临时磁盘空间容纳解压镜像、根分区副本和压缩输出。构建在本机文件副本上完成，不连接板卡、不写物理磁盘。

源文件与版本固定如下：

- 基础系统：`../os/radxa-a733_bullseye_kde_r6.output_512.img.xz`。
- 动作、声音和推理库：外部运行时归档 `Microduck-Robotd-FT6-R17.tar.gz`（脚本默认布局为相邻 `10fanhao_duck/runtime/microduck-hd1910/firmware/console/updates/`）；SHA256 为 `bcfd5c183c697b9c4e0fdbd563d995eda6a48331a7c49b9e76d20bd156c65cce`。逐文件另核对归档中的 `SHA256SUMS`。
- A7Z robotd：`../control/artifacts/robotd`；SHA256 为 `1e46542006209b10a1e1d4d07bceb66bd492e6057138b7a4491dce47d9f940dc`，构建记录见 `../control/runtime-build.json`。
- Studio 独立 Python 与依赖：`../studio/artifacts/cubie-studio.tar.gz`；SHA256 为 `db8bcde8d9823c3e10abce1a015cd2422b81c25b279ac4f6a516d51c1fd10590`。最新应用由外部 Studio 源码仓库（脚本默认布局为相邻 `10fanhao_duck/studio-replica`）中固定提交 `f682b7bd30d1ef4e6f931db16406b340d4d54fec`、本仓库 A7Z 补丁和 `../studio/console-integration/web/` 重建，三个关键后端文件单独核验摘要。

`prepare_runtime.py` 生成新装配配置和运行时覆盖层；`prepare_image.py` 加入最新 Studio、首启、配网和服务配置；`build_image.py` 在原系统根分区应用覆盖层。旧 R17 `service.sh` 与通用升级流程不适合作为本镜像的 A7Z 升级入口。分发时保留随包许可、模型来源和第三方声明。

## 验证记录

本机已通过 99 项相关测试：镜像工具 17 项、配网 12 项、温控 9 项、前端安装器 43 项、首次启动契约 18 项。另已静态核对全部动作模型路径、推理库依赖及 Studio 标定构建/校验流程；`units_verified=false` 不阻止正常标定提交，它表示尚未确认电流换算。

本次镜像已生成：压缩后 1,191,047,044 字节，解压后 6,776,878,080 字节，SHA256 为 `5ff1112d37f843f6da3335a0e1f56f741945acd639515a3b840967c2146a9e25`。5,364 项覆盖文件/目录/链接的内容、权限和所有者读回通过，主备 GPT 核验通过，`e2fsck -fn` 退出 0，`xz -t` 通过；完整清单见同名 `.manifest.json`。此记录不替代烧录后实际启动及动作验收。

排查首启时可查看：

```sh
systemctl status cubie-duck-firstboot robotd microduck-studio cubie-provision --no-pager
journalctl -u cubie-duck-firstboot -b --no-pager
cat /var/lib/cubie-duck/firstboot.done
cat /var/lib/cubie-duck/audio-devices.txt
```

镜像内发布信息位于 `/usr/share/cubie-duck/image-release.json`；运行时摘要位于 `/usr/share/cubie-duck/runtime-manifest.json`。初始标定未完成或缺少舵机/IMU 时，应先处理页面报告的问题，再验证动作。
