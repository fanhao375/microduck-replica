# Orange Pi Zero 3W 移植

适用于 Orange Pi Zero 3W（Allwinner A733）、官方 Ubuntu Jammy 1.0.2 / Linux 6.6.98。包含开机热点配网、Studio 适配、只读机器人控制载荷准备与原 HAT 的 UART0 适配。与仓库主线的 Radxa Zero 3W 镜像分别维护，不能互刷。

## 下载与安装

镜像和完整 ARM64 应用运行包放在贡献者 fork 的 [Release：orangepi-zero3w-20261112](https://github.com/Liangjin5437/microduck-replica/releases/tag/orangepi-zero3w-20261112)，源码由本 PR 提交上游。维护者可以在合并后自行转存附件。

- **配网基础镜像**：官方系统加 `orangepi-provision`，不包含已安装的 Studio / robotd。已移除共享 SSH 主机密钥、密码哈希、机器标识与系统日志；刷卡前须按 [镜像说明](image/README.md)在本机设置自己的 `orangepi` 登录密码，生成个人镜像。不要上传个人镜像。
- **应用运行包**：`orangepi-quanzhi-20261112.tar.gz`，包含 Studio、独立 CPython、robotd、ONNX Runtime 和模型。配网后按 [安装说明](migration/README.md)安装。网页密码由使用者在首次打开 Studio 时自行设置。
- **SHA256SUMS**：下载后先执行 `shasum -a 256 -c SHA256SUMS`（Linux 可用 `sha256sum`），全部通过后再解压、刷卡。

本次发布没有从正在使用的机器人复制系统、Wi-Fi、网页登录状态、SSH 授权或实物标定。默认控制服务使用 `--read-only`、策略关闭、串口占位且所有关节未校准。完成板级 UART 接入后仍只读；实际校准和运动须另行操作。

## 源码入口

- [开机热点配网](provision/README.md)：未连接 Wi-Fi 时创建 `OrangePi_XXXXXX`，浏览器访问 `http://10.42.0.1/`。
- [应用安装和源码范围](migration/README.md)：Studio、控制载荷、程序源快照与运行包。
- [HAT / UART0 适配](migration/hardware/README.md)：设备树、控制台释放、只读接入和分组读取验证。
- [镜像净化与本地设密](image/README.md)：构建和校验可公开的配网基础镜像。

## 验证边界

原开发板已验证配网、Studio 服务、重启恢复，以及 HAT 上 15 个舵机与 ID200 IMU 的只读通信。分组读取的有限观测窗口没有通信错误；单次读取方案出现 UART overrun 后恢复为每请求最多 3 个设备。

新发布的净化镜像仅完成离线文件系统、文件读回和敏感数据检查，尚未在实板重新刷卡验收。公开版应用安装器已移除开发机预览登录状态的依赖；其全新安装流程仍需实板复验。摄像头、ToF、标定、站立和行走不在本移植的验收范围。

## 本地检查

在本目录执行：

```sh
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s migration/studio/tests -v
sh -n provision/install.sh
```

Studio 的源码副本不重复提交整套 Python 和第三方依赖；测试前按 `migration/README.md` 准备依赖，Release 已随包提供。

## 来源与许可

Studio 和移植代码遵循各目录原有许可；保留源快照中的上游声明与第三方许可证。`robotd` 在此提供已构建的 A7Z sync3.1 版本和补丁参考，完整 Rust 工作区仍在其上游运行时项目，不能仅凭本目录重新编译主控。

模型沿用原作者的个人非商业许可，不能视为 Apache-2.0 代码许可的一部分。系统、CPython、ONNX Runtime 和其他依赖分别遵循随包许可证，详见仓库 [NOTICE](../../NOTICE.md) 与 [软件导航](../README.md)。
