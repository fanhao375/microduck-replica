# 原版 1104 首次软件升级准备

适用于已安装公开原版 `2026.11.4`（来源 `f215b65`）且能正常进入 Studio 的 ZERO 3W。先让鸭子退出模型，结束舵机调试、IMU 标定和进行中的更新。准备会在下次正常启动时生效，现有标定、用户数据与签名软件目录保持原样。

1. 把本次交付的 `prepare-stock-1104.pyz` 和 `prepare-stock-1104.pyz.minisig` 放在电脑的同一目录。通过 SSH/SCP 从这张 1104 卡复制 `/etc/microduck-updater/trusted_keys/release.pub` 到该目录，文件名保留为 `release.pub`。
2. 原版镜像未预装 minisign。在装有 minisign 的电脑上进入该目录验签，记录验签成功的文件摘要。Windows 上也可在装有 minisign 的 WSL 终端执行以下两行。

```sh
minisign -Vm prepare-stock-1104.pyz -p release.pub
sha256sum prepare-stock-1104.pyz
```

3. 验签通过后，把准备工具复制到板上。通过 SSH 登录这台鸭子，在文件所在目录执行以下命令，并将最后一行的摘要与电脑上的结果逐字核对。

```sh
sudo install -d -m 0700 /root/stock1104
sudo install -m 0600 prepare-stock-1104.pyz /root/stock1104/
sudo sha256sum /root/stock1104/prepare-stock-1104.pyz
```

4. 两端摘要一致后，在板上检查原版文件并暂存修复。

```sh
sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104.pyz --check &&
sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104.pyz
```

5. 看到“主机准备已暂存”后，确认鸭子已放稳、运动已停止，再执行 `sudo systemctl reboot`。重连后再次执行 `--check`，应看到“主机修复已生效”。
6. 打开 Studio → 设置 → 软件更新，上传同目录的 `.software-update.tar` 文件，验证通过后点击安装。刷新并确认已安装版本号。若准备工具提示文件或服务配置与原版不同，请保留提示并核对卡的来源。

**撤销准备。** 仍在原版 1104 时，用同一份已验证的工具运行 `sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104.pyz --restore-stock`，然后正常重启。撤销只移除这个工具写的三个 systemd 配置文件。已暂存的 root 只读程序文件可保留；原有主机文件一直保存在原位。

## 实现与复现

工具先核对原版 suite 清单、Studio 源码、系统服务和主机更新器的摘要，并获取更新器的运动锁，检查更新协调器、签名引擎、Studio 与模型状态。安装采用三个具名的 systemd 服务配置文件和一份 root 所有的准备目录。引擎以 `0022` 创建可读版本并使用可等待的健康检查，协调器使用已修复的软件预检。Studio 启动器以普通服务用户运行，只为指定物理版本目录内、摘要匹配的原版提供两个准备过的模块。遇到后续版本直接执行该版本的 `run.py`。每次软件安装、回滚仍由已安装的签名引擎执行。

构建在包含两个来源提交的 Git 仓库中执行。源代码固定为 `f215b658a4f24b669cf664b6381a559c61472009` 和 `a9ef4020a610fb7f4948ec49845e8d23c9fbb432`。工具使用固定 ZIP 元数据，构建结果可逐字节复现。由发布者将独立的 minisign 签名随工具一同发布。

```sh
python3 software/updater/stock1104/build.py prepare-stock-1104.pyz
```
