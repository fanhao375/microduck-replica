# ZERO 3W 的 1104 出厂镜像刷新

新卡始终从 [1104 镜像发布页](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-replica-image-20261104)下载。页面显示镜像内的正式软件版本、镜像 SHA-256，以及原版 1104 卡首次升级的方法。烧录文件名固定为 [microduck-replica-20261104.img.xz](https://github.com/fanhao375/microduck-replica/releases/download/microduck-replica-image-20261104/microduck-replica-20261104.img.xz)。1104 表示 Radxa ZERO 3W + 飞特 HD-1910 的整卡底图。已经烧好的卡继续由用户在 Studio 中确认软件更新。

## 触发与选择版本

维护者正常发布公开的 `microduck-software-vX.Y.Z` 正式软件及完整的 `X.Y.Z.software-update.tar` 后，发布事件会唤醒默认分支上的 **Refresh public 1104 factory image**。它检查所有发布页，按三个整数比较软件版本，只选择最新的已公开正式版。草稿、预发布、RC 和镜像标签均会跳过。GitHub 的 Latest 徽标可以继续指向烧卡页。

默认分支每六小时复查一次，可补上旧标签缺少通知工作流、GitHub 抑制同一令牌触发后续工作流等情况。如果镜像已经是该正式版且附件摘要一致，流程会在下载底图之前结束。也可以从 Actions 选择该工作流，在 `master` 上点 **Run workflow**。CI 在同一时间只发布一份镜像。发版期间请让当前流程完成，再手工修改该发布页或附件。

## 输入和验收

| 输入 | 来源和校验 |
| --- | --- |
| `factory-1104-source.xz` | 固定放在 1104 发布页的原始底图。SHA-256 为 `f9970ec551bcddbeeae9748b0ce38f36450fa1497618c07920723849f975aa78`。每次从这份底图开始。 |
| `prepare-stock-1104-online.pyz` 和 `.minisig` | 来自 [2026.11.20 正式软件发布页](https://github.com/fanhao375/microduck-replica/releases/tag/microduck-software-v2026.11.20)。构建器核对固定工具摘要，用底图内置公钥验签。 |
| `X.Y.Z.software-update.tar` | 来自当次正式软件发布。构建器验证签名清单、软件负载和版本，发布器将实际包摘要与 GitHub Release 附件摘要比对。 |

构建在只读仓库权限的独立 Linux runner 上执行。它在离线底图副本上运行原 ARM 升级引擎，再重新解压最终镜像，检查文件系统、运行文件和 ARM Studio 服务。`1104-build.json` 记录实际执行的检查、软件源码提交，以及各输入和镜像的 SHA-256。实物烧卡、首启、热点切换及运动测试需另行记录。

具有仓库写权限的作业仅调用默认分支同一提交中的发布器。它从本次构建按 artifact ID 取回镜像、镜像 SHA-256 文件及构建记录，把这些文件作为数据读取。标签提交、软件包和准备工具中的代码都在读权限的构建阶段处理。

## 本地复现与发布

在有 loop、ext4、overlayfs 和网络命名空间支持的 Linux 上运行。安装 `python3`（3.10 或更高）、`gh`、`qemu-user-static`、`minisign`、`xz-utils`、`zstd`、`e2fsprogs`、`util-linux`、`iproute2` 和 GNU tar。工作目录需容纳两份展开的底图、输入和输出，放在 Linux 本地文件系统中。下面从已审核的默认分支检出目录运行，使用已登录的 `gh`。

```bash
set -euo pipefail
repo=fanhao375/microduck-replica
image_tag=microduck-replica-image-20261104
tool=software/image/refresh1104
version=$(python3 -I "$tool/publish.py" candidate)
if [ -z "$version" ]; then echo '1104 镜像已是最新正式版'; exit 0; fi
job=$(mktemp -d)
mkdir "$job/input"
gh release download "$image_tag" -R "$repo" -D "$job/input" -p factory-1104-source.xz
gh release download microduck-software-v2026.11.20 -R "$repo" -D "$job/input" \
  -p prepare-stock-1104-online.pyz -p prepare-stock-1104-online.pyz.minisig
gh release download "microduck-software-v$version" -R "$repo" -D "$job/input" -p "$version.software-update.tar"
sudo python3 -I "$tool/build.py" \
  --base "$job/input/factory-1104-source.xz" --package "$job/input/$version.software-update.tar" \
  --prepare "$job/input/prepare-stock-1104-online.pyz" --version "$version" \
  --out "$job/out" --work "$job/work"
python3 -I "$tool/publish.py" check --version "$version" --out "$job/out"
```

`check` 只读远端信息。确认本地结果后，使用对该仓库有 Contents 写权限的账号执行发布操作。

```bash
python3 -I "$tool/publish.py" promote --version "$version" --out "$job/out"
```

## 发布中断的恢复

发布器先上传临时附件，在 GitHub 核对所有新文件摘要，并重新确认这仍是最新正式软件版本。然后将旧附件暂存，逐一换入固定文件名，核对公开附件后更新页面并清理临时文件。GitHub 逐个修改附件名称，切换的短时间内自动下载者可能需要重试。页面显示的镜像摘要和同名校验文件都应与下载文件相符。

`SHA256SUMS.txt` 覆盖当前镜像、构建记录、镜像校验文件、固定底图及保留的许可和历史附件。校验清单自身的摘要记在发布页的 HTML 注释中。首次迁移时先上传固定底图并归档原版说明、旧 manifest 和验收文件，再执行发布。

上传或改名中断时，使用同一份输出重新运行 `promote`，或在 Actions 中重新运行失败的作业。发布器按摘要复用已上传的文件，核对全部固定名称后再移除暂存附件。若更新的软件版本已经公开，重新运行完整工作流以构建新版本。如果同一正式软件版本的软件包摘要发生变化，需以新版本正式发布。
