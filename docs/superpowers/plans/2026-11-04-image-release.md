# 20261104 IMU 与配网镜像发布计划

> **For agentic workers:** 使用 executing-plans 执行；原生集成、更新清单审计与镜像输入审计可独立并行，发布与验证保持顺序。

**Goal:** 交付包含站姿IMU重标、ID200独立方向核验、热点恢复及31-r1既有功能的新镜像与配套签名软件包，附可核对更新清单。

**Architecture:** Studio以1855a68为基线加入独立热点补丁2fa4ec9；原生以公开27d7921源树为基线集成今天的5个IMU提交，保留秦墨、舵机/IMU独立状态和手柄映射。固定新版主程序的来源/哈希/API43，与既有已核验映射padd成套打包。复用已核验Armbian底图、模型payload、语音资源和ARM升级执行器，从冻结Git源码构建出厂清理镜像。

**Tech Stack:** Rust1.99、Python3.12/3.13、WSL Ubuntu22.04、QEMU ARM64、minisign、ext4镜像工具、GitHub CLI。

- [x] 核对今日源码/实机/实验件清单和旧公开功能保留范围，记录 artifacts/image-release-20261104/change-inventory.txt。
- [x] 原生独立工作树 `.worktrees/native-image-release-20261104`：公开27d7921上集成82aec98、3f0dc0a、cdebd43、0d0e3e0、7036345；解决冲突时保留既有秦墨和独立状态逻辑，使用API43及control.48构建标识。先跑公开基线，再跑合并后的相同测试；构建ARM64并记录精确源码和哈希。
- [x] Studio发布工作树 `.worktrees/image-release-20261104`：加入2fa4ec9，更新 `software/studio/deploy/native_update.py` 和 `controller_bundle.py` 的原生来源/API/哈希/大小，保留已发布padd映射。增加并运行配套声明一致、旧版.47可升级及错误载荷拒绝回归。
- [x] 运行Studio、配网、镜像、语音和升级器相关套件，按失败名称与旧公开基线区分，不把遗留失败写作通过。
- [x] 从冻结Git导出LF源码，使用现有发行私钥签名2026.11.4软件包。私钥不复制到源码、镜像或附件。验证旧27引擎安装/回退及新原生配套元数据。
- [x] 以 `/root/microduck-build-20261027/inputs/` 的已核验底图/payload/updaterd/resources构建 `/root/microduck-build-20261104/`。payload中更新robotd及已核验padd，默认legacy协议输出，不默认启用实验full_q4_v1。出厂标定仍未标定、默认标定模式，移除Wi-Fi、维护者SSH钥匙、个人API Key/音乐/动作/日志。
- [x] 对最终镜像做文件哈希、分区/ext4、启动服务、首次启动清理、默认配置和依赖核验；校验压缩文件和发行附件，不把离线检查称为烧卡实测。
- [x] 独立复核后推送精确源码并创建配套GitHub发布，上传镜像/签名软件/校验和/更新清单/许可与来源记录；以测试版注明新镜像烧卡验收和运动效果仍待现场验证。
- [x] 回读GitHub公开附件大小/hash和可访问下载链接，交付中文更新摘要、下载地址及旧卡升级方法。

范围边界：不改变用户现机模型、零位、已保存IMU方向、动作参数；固定安装方向预设仍留待后续。实验MCU1.1完整四元数和1.2补偿不得默认刷入镜像或宣称已经解决走路/起身；新镜像不预装维护者个人标定和联网资料。
