# 原版 1104 首次软件升级准备

适用于已安装公开原版 `2026.11.4`（来源 `f215b65`）且能正常进入 Studio 的 ZERO 3W。先让鸭子退出模型，结束舵机调试、IMU 标定和进行中的更新。准备会在下次正常启动时生效，现有标定、用户数据与签名软件目录保持原样。

1. 从本次发布中取得 `prepare-stock-1104.pyz` 和同名的 `.minisig`。把两份文件复制到板上同一目录。
2. 在 SSH 终端将文件复制到 root 私有目录，用板上原有的发布公钥验证。验证成功后执行检查、暂存，再正常重启。

```sh
sudo install -d -m 0700 /root/stock1104
sudo install -m 0600 prepare-stock-1104.pyz prepare-stock-1104.pyz.minisig /root/stock1104/
sudo minisign -Vm /root/stock1104/prepare-stock-1104.pyz -p /etc/microduck-updater/trusted_keys/release.pub &&
sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104.pyz --check &&
sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104.pyz
```

如果板上没有 minisign，可将板上原有的 `/etc/microduck-updater/trusted_keys/release.pub` 复制到电脑，在电脑上运行 `minisign -Vm prepare-stock-1104.pyz -p release.pub`。验证成功后用 `sha256sum` 或 PowerShell 的 `Get-FileHash` 计算已验证文件的 SHA-256，与板上的 `sudo sha256sum /root/stock1104/prepare-stock-1104.pyz` 对照，完全相同后执行上面最后两条 Python 命令。发布包里单独提供的摘要可用于检查传输。

3. 看到“主机准备已暂存”后，确认机器人仍在安全静止状态，执行 `sudo systemctl reboot`。重连后再次执行上面的 `--check` 命令，应看到“主机修复已生效”。
4. 打开 Studio → 设置 → 软件更新，上传本次发布的 `.software-update.tar`，验证通过后点击安装。更新器会再次核对模型接管状态，按原有签名和健康检查流程安装。板子若仍停在原版，可重复运行同一份准备工具。

**撤销准备。** 仍在原版 1104 时，用同一份已验证的工具运行 `sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104.pyz --restore-stock`，然后正常重启。撤销只移除这个工具写的三个 systemd 配置文件。已暂存的 root 只读程序文件可保留；原有主机文件一直保存在原位。

## 实现与复现

工具先核对原版 suite 清单、Studio 源码、系统服务和主机更新器的摘要，并获取更新器的运动锁，检查更新协调器、签名引擎、Studio 与模型状态。安装采用三个具名的 systemd 服务配置文件和一份 root 所有的准备目录。引擎以 `0022` 创建可读版本并使用可等待的健康检查，协调器使用已修复的软件预检。Studio 启动器以普通服务用户运行，只为指定物理版本目录内、摘要匹配的原版提供两个准备过的模块。遇到后续版本直接执行该版本的 `run.py`。每次软件安装、回滚仍由已安装的签名引擎执行。

构建在包含两个来源提交的 Git 仓库中执行。源代码固定为 `f215b658a4f24b669cf664b6381a559c61472009` 和 `a9ef4020a610fb7f4948ec49845e8d23c9fbb432`。工具使用固定 ZIP 元数据，构建结果可逐字节复现。由发布者将独立的 minisign 签名随工具一同发布。

```sh
python3 software/updater/stock1104/build.py prepare-stock-1104.pyz
```
