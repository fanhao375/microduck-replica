# A7Z 中控前端集成

把批准的 v18 页面结构接到原 Studio 的 Preact、WebSocket、认证和业务接口。安装包只更新 `web/` 的变化文件，不安装后端、模型、开发模拟器或本机状态。

2026-11-09 已安装到 `cubie-42f4`：<http://192.0.2.116:8000/>，发布版本 `20261109-console1`。仅更新前端，Studio/robotd 的 PID、后端、二进制和标定摘要均未改变。用户确认当前只给主控供电、未连接舵机；本次采用 `--static-startup`，未验证实物卸力、站立或行走。

旧网页备份：`/opt/microduck-studio/console-backups/20261109T024849766301Z-c5f27ff2b853/`。安装结果在本地 `artifacts/deployment-20261109.json`，验证范围见 [VERIFICATION.md](VERIFICATION.md)。未安装或关闭的板端能力仍显示不可用。

## 目录与基线

- `web/`：可版本化的前端变化文件，不是完整 Studio。
- `baseline-web.json`：导出时固定的完整原版前端 SHA256。路径以 `web/` 开头；原模型摘要也保留作比对，但模型不进入 payload。
- `deleted-web.json`：只有确实删除原文件时才生成，内容为 web 相对路径列表；稀疏 `web/` 中缺少某文件并不表示删除。
- `prepare_console.py`：从指定上游提交和两份 A7Z 补丁重建完整本地工作区，再叠加 `web/`。
- `workspace/`：忽略的工作区、原版摘要及本机取证；不打包。
- `package_console.py`：导出、生成 manifest、校验 payload、生成可复现压缩包。
- `install_console.py`：默认预览；显式 `--apply` 才安装或回滚。
- `run_dev.py`：固定本机模拟硬件联调入口，不打包。
- `artifacts/`：忽略的包、模拟运行状态与日志，不提交 Git。

源提交是 `f682b7bd30d1ef4e6f931db16406b340d4d54fec`，随后依次应用上级目录的 `a7z-readonly-studio.patch`、`a7z-disabled-configd-pad.patch`。现有补丁已包含 `configd none`、500 ms 回读有效期和 10 Hz 缓存查询修正，不再覆盖旧安装归档或未安装的跟踪后端。

重建与真实安装均核对以下 A7Z 后端摘要；它们不属于前端 payload，安装器也不会改写它们：

```text
studio/pad.py     27bbe0ae137b6e827f5b1d1b8a40a62ae57e670b19d9c36cc498ca884eaf2b5f
studio/robotd.py  8f3f4412901756fdd29fb6305b89421cbf1a9fa45e33df73e413faaef69c2750
studio/state.py   fba77fae1068faed81267bd86687e81bf6851a62e93d1ce860d4ede61fc38c77
```

## 重建与本地联调

使用 Python 3.13。以下命令在本目录执行；`source_repo` 是含上游提交的仓库根目录，不是其中的 `software/studio` 子目录。

```sh
cd <workspace>/11quanzhi/radxa-cubie-a7z/studio/console-integration
python3.13 prepare_console.py <workspace>/10fanhao_duck/studio-replica
python3.13 -I run_dev.py --check
python3.13 -I run_dev.py
```

`prepare_console.py` 拒绝覆盖现有输出。已有工作区时，先保留未导出的修改；独立核对可以使用 `--output artifacts/rebuild/studio` 指定一个不存在的目录。正常联调固定使用 `workspace/studio`。

浏览器打开 <http://127.0.0.1:18880/>。这是原 Studio 后端配合模拟 robotd/configd，端口固定为 `127.0.0.1:18701`、`18702`、`18880`。模拟器拒绝设备 Unix socket、真实串口、外部地址与真实 helper；凭据、日志和测试状态只写入新建的 `artifacts/dev-*`。`--check` 不启动监听服务。Ctrl+C 会停止两个子进程，不停止已有进程、不连接主控。

## 验证

首先执行不使用硬件的契约和安装器测试：

```sh
python3.13 -m py_compile package_console.py prepare_console.py install_console.py run_dev.py
python3.13 -m unittest discover -s tests -p 'test_*.py'
node --test tests/startup-contracts.mjs
```

原 Studio 前端相关回归在重建后的工作区运行：

```sh
cd workspace/studio
node --test tests/models_ui_unit.mjs tests/ai_ui.test.mjs tests/ai_settings_lifecycle.test.mjs tests/updates_ui.test.mjs tests/wifi_find.mjs tests/stepper.test.mjs tests/motion_heartbeat.test.mjs tests/motion_studio_lifecycle.test.mjs
```

安装器测试使用临时目录和注入的状态守卫，验证哈希错配、路径/链接拒绝、原子替换、失败恢复和回滚；不查询主控。首次启动测试验证真实读回证据、未加载模型、旧舵机缓存、失联与卸力确认，不能替代现场动作检查。涉及模拟后端的测试需要允许本机测试端口。

## 导出与可复现打包

修改只发生在 `workspace/studio/web/` 时，导出变化并构建：

```sh
python3.13 package_console.py --export --release 20261109-console1
```

`--export` 比较 `workspace/baseline-web.json` 与工作区，仅把变化文件写入可版本化的 `web/`，同时写入本目录 `baseline-web.json`。它会替换上一次导出的稀疏 `web/`；不会修改工作区。模型、链接、隐藏文件、不支持的类型或工作区后端摘要变化会使导出失败。显式删除普通前端文件会记录在 `deleted-web.json`。

后续在新检出目录，可直接用已版本化的前端与摘要构建，不依赖工作区或相邻上游仓库：

```sh
python3.13 package_console.py --release 20261109-console1
```

输出：

```text
artifacts/bundle/manifest.json
artifacts/bundle/payload/web/<变化文件>
artifacts/console-web-20261109-console1.tar.gz
artifacts/console-web-20261109-console1.tar.gz.sha256
artifacts/install_console.py
artifacts/install_console.py.sha256
```

manifest 格式与安装器共用校验代码。新文件的 `before_sha256` 为 `null`；删除文件的 `after_sha256` 为 `null` 且没有 payload。payload 文件集合必须与 manifest 完全一致，每个 SHA256 都会重算核对。包只含 `bundle/manifest.json` 和 `bundle/payload/web/*`，不含安装器、后端、`model/**`、`workspace/baseline-app.js`、`board-before`、密钥、日志或 Python 缓存。

同一 release、相同输入在同一 Python/zlib 工具链下生成相同压缩字节：文件按路径排序，tar 时间、所有者、权限固定，gzip 时间与文件名字段固定。压缩算法工具链变化可能影响 gzip 字节，应使用发布时记录的 Python/zlib 版本复核；manifest 和文件摘要不受压缩方式影响。

## 安装预览、应用与回滚

以下命令用于后续安装或恢复。先通过现有认证方式传输 `artifacts/` 下的压缩包、安装器和各自的 `.sha256`；不在仓库记录 SSH 密码或私钥。安装器与 payload 分开核验，不进入网页目录。板端使用现有 Studio 独立 Python，避免系统 Python 版本差异。

```sh
cd /tmp
sha256sum -c console-web-20261109-console1.tar.gz.sha256
sha256sum -c install_console.py.sha256
mkdir console-web-20261109-console1
tar -xzf console-web-20261109-console1.tar.gz -C console-web-20261109-console1
/opt/microduck-studio/python/bin/python3 install_console.py /tmp/console-web-20261109-console1/bundle --base /opt/microduck-studio/app
```

默认命令只验证包与当前前端摘要并列出变化，输出 `preview: true`、`readiness_checked: false`，不会查询或控制机器人。摘要不符时应先核对实际板端版本，不改 manifest 绕过。

真实应用前，在页面交还维护控制权，并由现场操作者确认卸力。安装器再独立只读检查：主控处于预期 motion 模式、策略关闭、扭矩确认关闭、无维护/恢复/IMU 验证任务；同时核对运行 PID、二进制、标定文件与上面三项后端摘要。未满足时拒绝发布，不发送卸力或运动命令。

```sh
sudo /opt/microduck-studio/python/bin/python3 install_console.py /tmp/console-web-20261109-console1/bundle --base /opt/microduck-studio/app --apply
```

安装器备份完整 web，再以目录原子交换发布；不重启 Studio 或 robotd，不改变主控二进制、配置或标定。只删除已变更文件同名的旧 `.gz`/`.br` 缓存，模型及其压缩文件保留。发布前后核对指纹和完整 web，失败会尝试原子回滚。不支持原子目录交换的系统直接拒绝。

成功 JSON 的 `backup` 字段指向 `/opt/microduck-studio/console-backups/<时间与唯一标识>/`，`rollback_command` 给出准确回滚参数。备份必须原路径保留在 `base.parent/console-backups/` 内，并保持为安装器所有的私有目录。页面刷新后检查桌面与手机、登录、真实状态、标定入口、模型与设置，再由现场操作者验证实际功能。

回滚同样先预览，把 `BACKUP` 替换为成功输出的真实备份路径：

```sh
/opt/microduck-studio/python/bin/python3 install_console.py --rollback BACKUP --base /opt/microduck-studio/app
sudo /opt/microduck-studio/python/bin/python3 install_console.py --rollback BACKUP --base /opt/microduck-studio/app --apply
```

回滚核对当前完整前端仍是该安装版本、备份摘要未变，再执行相同状态守卫与原子交换；前端已被另行修改时拒绝回滚，避免覆盖后续工作。无需重启服务。

### 主控等待总线时的非干预发布

默认 `--apply` 仍要求 motion/control、策略关闭与完整卸力读回。若现场主控仅返回 `mode: motion, phase: waiting_for_bus`，扭矩与策略字段可能没有读回，不能称为已卸力或硬件正常。明确使用 `--static-startup` 才允许此精确阶段做静态 UI 发布；其他阶段或模式不会走此例外。

```sh
sudo /opt/microduck-studio/python/bin/python3.13 /tmp/install_console.py /tmp/console-web-20261109-console1/bundle --base /opt/microduck-studio/app --apply --static-startup
```

该模式只交换静态网页目录，不停止或启动服务，不发运动、上力、卸力或修复命令，也不刷新已打开的浏览器。仍拒绝维护持有、活动的 home recovery/IMU 验证、明确开启的策略，完整核对 PID、二进制、三项后端、标定与 web 指纹。发布前后 phase 必须不变；变化时取消或恢复原 web。

结果明确输出 `static_startup: true`、`readiness_checked: false`、`torque_off_verified: false`，不把静态发布描述为设备故障已恢复。用户主动刷新后才加载新页面；主控等待或舵机无响应应如实显示，并另行处理。静态回滚使用同一显式参数：

```sh
sudo /opt/microduck-studio/python/bin/python3.13 /tmp/install_console.py --rollback BACKUP --base /opt/microduck-studio/app --apply --static-startup
```

若主控已离开 `waiting_for_bus`，该命令会拒绝；应先重新只读核对状态，再选用对应的正常流程。
