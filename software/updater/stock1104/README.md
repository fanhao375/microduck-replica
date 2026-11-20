# 原版 1104 开通 Studio 在线更新

公开版 ZERO 3W 镜像 `2026.11.4` 需要先做一次主机准备。准备生效后，在 Studio 设置里打开「软件更新」即可查看正式版，在线更新包含下载、验签和安装。已使用首版准备工具的卡可以直接执行下面的步骤。

## 验签并复制到板上

取本次发布的 `prepare-stock-1104-online.pyz` 和同名 `.minisig` 签名，放在电脑的同一目录。通过 SSH/SCP 从这张 1104 卡复制 `/etc/microduck-updater/trusted_keys/release.pub` 到该目录。

在装有 minisign 的电脑终端或 WSL 中执行：

```sh
minisign -Vm prepare-stock-1104-online.pyz -p release.pub
sha256sum prepare-stock-1104-online.pyz
```

验签成功后，将 `.pyz` 复制到板上，在文件所在目录执行：

```sh
sudo install -d -m 0700 /root/stock1104
sudo install -m 0600 prepare-stock-1104-online.pyz /root/stock1104/
sudo sha256sum /root/stock1104/prepare-stock-1104-online.pyz
```

## 准备、重启并更新

1. 确认板上与电脑显示的 SHA-256 一致。让鸭子退出运动模型，结束舵机调试、IMU 标定和现有更新。
2. 在板上检查主机并暂存准备工具：

   ```sh
   sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104-online.pyz --check &&
   sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104-online.pyz
   ```

3. 看到“主机准备已暂存”后，将鸭子放稳，确认运动停止，再执行 `sudo systemctl reboot`。重连后再执行一次上述 `--check` 命令，确认出现“主机修复已生效”。
4. 打开 Studio → 设置 → 软件更新。在线检查发现正式版后，点击该版本的更新按钮。安装期间网页会短暂断开，重连后核对当前版本。也可在同一页面手动上传已签名的软件包。

**已经从原版 1104 升到 `2026.11.20-rc.1` 或正式版 `2026.11.20` 的卡**也可以按上述步骤开通，或重启后用 `--check` 核验。工具会核对当前签名发布包的套件清单、其中各文件与原版主机更新器。若系统已自动清理原版 `2026.11.4` 软件目录，工具通过板上保留的原版镜像清单识别这张卡。rc.1 用户在原有页面依次检查更新、下载并安装正式版。已经安装正式版的用户重启后即可通过该版本自带的按钮获取后续正式更新。

**撤销**需要先回到原版 `2026.11.4`。运行以下命令并正常重启，三个服务将恢复原版配置：

```sh
sudo /usr/bin/python3 -I /root/stock1104/prepare-stock-1104-online.pyz --restore-stock
```

若工具报告文件或配置摘要不符，请保留提示并核对卡的来源。曾在另一张卡上准备成功也需要在本卡独立验签、检查。

## 构建与生效范围

先签署并验签正式版套件，从其 `2026.11.20.tar.zst` 中读取 `suite-version.json` 的 SHA-256。然后在相同发布提交检出本目录，将该摘要与完整 Git 提交 ID 传给构建器。

```sh
tar --zstd -xOf 2026.11.20.tar.zst suite-version.json > stable-suite-version.json &&
suite_sha=$(sha256sum stable-suite-version.json | cut -d' ' -f1) &&
python3 software/updater/stock1104/build.py --ref "$(git rev-parse HEAD)" \
  --suite-manifest-sha256 "$suite_sha" prepare-stock-1104-online.pyz
```

构建器使用固定 ZIP 元数据。工厂原版来源是 `f215b658a4f24b669cf664b6381a559c61472009`。Studio 仅替换原版应用中的就绪检查函数，该函数和健康检查来自 `a9ef4020a610fb7f4948ec49845e8d23c9fbb432`。发布者另行使用板上信任的发布密钥签署新工具。

准备目录位于 `/usr/local/lib/microduck-stock1104-online`。三个 systemd 配置链接先映射到各服务原有的配置，全部就绪后统一切换。迁移首版时核对其原始签名工具所安装的六个文件及三个服务配置。Studio 以普通服务用户启动，按原版物理目录和摘要选取准备好的应用模块、更新接口与两个静态文件。后续软件版本直接启动自身的 `run.py`，已安装签名引擎继续负责软件包验签、切换与回滚。
