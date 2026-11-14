# Studio 的 root 小助手：舵机总线交接、写标定、手柄配对（安装、卸载、上机核对）

写给人看，也写给以后接手的 AI 看：每一步都写了为什么。

## 重做初次 IMU 校准（imu-recalibrate）

0.7.2 在同一个固定动词的 `enter` 增加可选 `profile="legacy"|"qinmo"`，供 IMU 页一次选择后保存。参数不接受命令、路径或服务。需要新 robotd 的 `configured_imu_profile` 明确能力证据；不按 API 版本号一刀切。Qinmo 标记为已配置，支持 Feetech，角速度 raw/16.4 °/s；原有板卡默认自动识别，raw×0.0175 °/s。用户流程见[网页使用指南](../../../docs/Qinmo-IMU-网页使用.md)。

类型事务同时持有 helper 与 `/run/microduck-service-management.lock`，拒绝更新 / 总线 / 旧 redo 竞争，暂停手柄及固定的 duck-echo 语音服务，安全落地后恢复原服务状态；网页亦拒绝会话期间的控制动作，卸力与必要的标定操作保留。仅操作实际直接 robotd ExecStart 的固定 `--params /etc/robot/feetech-ft5/robotd.toml`、`--socket /run/robotd.sock`，已知服务覆盖，以及该配置的 saved 文件和已知标定文件。逐级拒绝软链接、非 root 属主或其他用户可写目录 / 文件；拒绝 read-only、外来覆盖、I²C 来源、不支持的协议与标定路径。主 / saved 配置只改 `bus.imu_profile` 和兼容的 `bus.imu_protocol`，解析前后比较确保其余值保持，包括镜像带引号的键与内联策略数组。原始字节、属主 / 权限、摘要和模式先持久备份；标定仅清除总线安装方向 verified，避免复用历史同类型方向。

正常控制使用原来的两次新鲜失能 / 无策略 / 增益已恢复门槛。`configuring_bus` / `waiting_for_bus` 的受支持预控制启动恢复依据当前守护进程生命周期和配置能力，不伪称已知物理扭矩关闭，始终进入 commissioning。原出厂 commissioning 可选择类型，验证结束后保留出厂流程。启动恢复的取消先安装已知的出厂标定模式，再移除自己的 redo 模式文件，恢复原配置 / 标定并结束会话；仍保持标定模式，可重新选择，避免留下永久阻塞的终止会话。普通控制取消精确恢复原配置 / 标定和模式。

完成要求磁盘主 / saved 类型配置仍匹配写入摘要、运行配置和实际发现类型一致、新方向的 protocol + profile 绑定与 verified 均读回一致。失败恢复原文件；无法恢复则保留 `recovery_required` 和原备份，保持可恢复现场。普通 commit、redo 与取消保留可选 `imu_bus_profile`，缺省仍是 legacy；类型变化会使待采样 / 计算 / 待写结果失效。Qinmo 未经主程序验证不能由“跳过”或普通写入提升为 verified。当前没有实物验收；此改动不更新公开镜像、不自动部署 robotd。

新版小助手增加固定动词 `imu-recalibrate`，stdin 只接受 `enter/status/finish/cancel` 四种 action，不能指定命令、路径或服务。运动模式下已有 `verified=true` 和已保存四元数也可以重新验证：先扶稳、卸力，`enter` 两次读取新状态，要求 `motion/control`、`policy_enabled=false`、无起身、原生扭矩明确为 false、无卸力待确认、HD1910 临时增益已恢复。手柄服务先停，随后再次检查门槛；任何不明确的状态都拒绝。

- 进入：`{"by":"Studio","action":"enter","confirmed":true}`。保留实际生效的直接 robotd 启动参数，只增加 `--commissioning`；包装命令或有歧义的 ExecStart 拒绝。不会使用或删除出厂 `zzzz-zz-microduck-commissioning.conf`。
- 状态：`{"by":"Studio","action":"status"}`。会话有 `active/phase/session_id/source`；网页读取公开只读 manifest，不在每次推状态时调用 sudo。
- 完成：`{"by":"Studio","action":"finish","session_id":"…","source":"bus","quat":[1,0,0,0]}`。只在原来的 `verify-imu` 通过，且当前来源、verified、归一化四元数及标定文件逐项读回一致之后调用。原 verifier 会把 `motion_enabled` 设为 false；助手只恢复进入前的这个值，其他字段除当前芯片的安装方向 / verified 都必须保持一致。不会重建整份标定，也不会把非 2048 零位、方向、限位、另一颗 IMU 或未知设置覆盖成默认值。若主程序未保留这些字段，拒绝完成，保留会话供取消恢复。
- 取消：`{"by":"Studio","action":"cancel","confirmed":true,"session_id":"…"}`。主标定和 saved 各自恢复原始备份，恢复原来的模式与手柄服务状态；不算新校准成功。

会话先持久化到 `/var/lib/microduck-helper/imu-recalibrate/manifest.json`（root:root 0644），每次独立的 `<session_id>/` 备份目录 0700，主标定、saved 和解析后的原始标定分别备份并校验 SHA256。父目录 0755，操作记录仍为 0600。会话使用自己的持久模式文件 `/etc/systemd/system/robotd.service.d/zzzzzz-studio-imu-recalibrate.conf`，有会话 ID 和内容摘要，只允许删除本会话原样的文件；重启 Studio / 刷新网页会恢复会话，断电后持久模式文件仍保留。开始阶段失败恢复原状态；完成阶段失败恢复已验证的安全标定模式，可重新做原验证或取消。只有运动模式、完整标定读回一致且已失能时才恢复手柄服务。

`imu-recalibrate` 和总线 / 标定操作共用 `helper.lock`。会话活动时 `bus-take`、普通 `calib-commit` 拒绝；网页卸力仍可用。卸载（含 `--purge`）遇到未结束或读不回的会话会拒绝，先完成或取消，避免删除恢复备份。该流程保留原来的静止 3 秒 / 前倾 3 秒粗校及直立后低头的 runtime 验证；出厂标定模式直接使用原流程。

方案出处：scratchpad `console/handover-plan.md`（v2 定稿）§2.2、§4.2、§5；规矩照 `console/studio-ops-plan.md` §7。

## 1. 它是干什么的

Studio 的舵机页要自己打开舵机总线 `/dev/ttyS2`，所以主程序 robotd 要先停下，用完再交还。

- 停、起 robotd 要 root，Studio 不是 root。
- 所以只给 Studio 一把「只能开这一扇门的钥匙」：sudoers 只放行 `/usr/local/sbin/microduck-helper` 这一个文件。
- 它只认十二个固定动词，参数逐个校验，robotd 是什么状态它自己去读，不信网页传来的（0.5.0 起：新增 `calib-read`，`calib-commit` 按 fork 的真实启动方式认标定 / 运动模式；0.5.2：`bus-give-back` 把标定模式的实时快照也算成「主程序起来了」；0.6.0：新增 `ssh-status` / `ssh-password`，「更多」页的 SSH 开关；0.7.0：新增 `imu-recalibrate`，持久保存重做初次 IMU 校准会话）。

**标定模式的 busStatus**（fork `robotd/src/main.rs` 的 `robot.busStatus`、`feetech_commissioning.rs` 的 `Status`）：robotd 一起来先写一份启动报告 `{"build","mode":"commissioning","phase":"configuring_bus","port"}`，phase 还是启动阶段就一直回它；第一次读成功以后才回实时快照（phase `ready` / `moving` / `read_error` / `stop_unconfirmed` / `power_pending`，带 `last_sample`）。旧版 fork 从来不清启动报告——从舵机页交还时小助手会等满 30 秒判 `soft_failed`（`detail.still_starting = true`，话写「一直报启动中」，不说成「起不来」）。要新版 fork（20261020 之后的镜像）。

**出厂镜像跑标定模式**：镜像放 `/etc/systemd/system/robotd.service.d/zzzz-zz-microduck-commissioning.conf`，`ExecStart` 带 `--commissioning`（fork `main.rs:1300-1315`：busStatus 的 mode 只看启动参数，跟标定文件里写了什么无关）。标定模式下 `calibration-status` 马上能读，busStatus 带 `last_sample`（15 颗舵机 + `imu`），`verify-imu` 会把方向写进标定文件；运动模式下标定不合格 / 舵机总线打不开会停在 `waiting_for_bus`，`calibration-status` 回 `calibration not available yet`。第 3 步写成功时小助手删掉这个文件、`daemon-reload`、重启 robotd，主程序回到运动模式。如果 `ExecStart` 里有 `--commissioning` 但文件不在，小助手拒绝自动切（`foreign_commissioning`），让人去日志页导出诊断包。

## 镜像里 vs 手动装

「连 WiFi」卡片不是 Studio 单独就能提供的，它依赖复刻镜像里的四样东西：

- netcheck 写的 `/run/microduck/net.json`；
- NetworkManager 里的 `Microduck-Hotspot` 热点配置；
- configd 以 `--allow-user btd,radxa` 运行；
- mDNS 配置（`duck-xxxx.local`）。

按本文手动装 Studio 和 root 小助手、但没有复刻镜像 overlay 的鸭子，没有这些，Studio 就不显示「连 WiFi」卡片；总览、手柄、舵机、IMU、日志和网页设密码都照常。
网页设密码在「更多 → 登录密码」；命令行 `python3 -I run.py set-password` 要用 radxa 用户运行，别用 `sudo`，否则 Studio 读不了密码文件。

| 动词 | stdin（一个 JSON 对象） | 做什么 |
|---|---|---|
| `bus-take` | `{"by": "谁·哪台电脑", "allow_seated": false}` | 接管，**不卸力**：先停手柄服务 → 隔 200 ms 读两次主程序状态（策略在跑就拒绝；坐姿只有 `allow_seated` 并且不动才放行；正在回站姿最多等 3 秒）→ 立旗 → `systemctl stop --no-block robotd`，10 秒没停就 KILL → 确认串口没人占 |
| `bus-give-back` | `{"by": …, "soft": true}` | 软交还：Studio 已经在总线上全卸了。撤旗 → 起 robotd → 等启动 4 步都 success、而且主程序起来了（运动模式 `phase=control`，或者标定模式的实时快照 `ready` / `moving` / `read_error` / `stop_unconfirmed` / `power_pending`；`read_error` 照样算成，原话写进 reason）→ 拉回手柄服务 → 放锁。30 秒还一直报启动阶段：`soft_failed`、`detail.still_starting = true` |
| | `{"by": …, "marker": {…}}` | 不卸力交还：写交接标记 → 撤旗（**锁不放**）→ 起 robotd → 等 robotd 接手（接手保持、不开模型）。没接手就把总线拿回来，交给 Studio 接着保持。标定模式不认标记（开总线先全部卸力）：实时快照一来就回 `limp`，不等 15 秒再拿回总线 |
| `bus-abort` | `{"by": …}` | 中止交接（交接倒数期间按了网页卸力）：立旗 → KILL robotd → 删标记 → 确认串口空出来。目标 1 秒以内，之后 Studio 自己开串口卸力。**没有会话时拒绝（退出 3）**：旗、管理锁、交接标记都不在就什么都不碰 |
| `pad-pair` | `{"by": …, "mac": 可选, "timeout": 10–120（默认 60）}` | 以 root 跑官方 `/usr/local/bin/robotctl pad pair --json --timeout N [MAC]`。MAC 先转大写再校验。子进程总超时 = `timeout + 30` 秒（超时就杀掉）。robotctl 在 `/var/lib/robot/weird-ble` 板上会先停 btd、给适配器断电再开，最后把 btd 起回来。**不碰**总线旗子、hold 单元、robotd，也不拿服务管理锁；只拿 `helper.lock`，一次只跑一个配对 |
| `pad-forget` | `{"by": …, "mac": …}` | 以 root 跑官方 `/usr/local/bin/robotctl pad forget MAC`（MAC 必填，校验同上）。同样不碰总线、hold 单元、robotd |
| `wifi-hotspot-down` | `{"by": …, "ssid": 可选}` | 关热点**之前**写 `/run/microduck/studio-connecting.json`（root 0644，`until` = 现在 + 90 秒，带目标 SSID；先写临时文件再 rename，目录 0755），netcheck 在这段时间里不插手。然后 `nmcli connection down Microduck-Hotspot`（命令超时 15 秒）。热点本来就没开算成功（nmcli 说「不是活动连接」也当成功）；最多等 10 秒，等 wlan0 不再是这个连接。**不碰**总线旗子、hold 单元、robotd，也不拿 `helper.lock`，用 `wifi.lock` |
| `wifi-hotspot-up` | `{"by": …}` | `nmcli connection up Microduck-Hotspot`。本来就开着算成功；从 up 开始最多等 30 秒，等 wlan0 真的挂上这个连接。开起来以后删掉 `/run/microduck/studio-connecting.json`（文件本来不在不算错）。同样不碰舵机总线，用 `wifi.lock` |
| `calib-read` | `{"by": …}`（可省） | 只读当前标定文件：回解析后的 `calibration`、现在是哪种模式（`commissioning` / `motion` / `foreign`）、commissioning drop-in 在不在、`ExecStart` 里有没有 `--commissioning`。**不拿 `helper.lock`、不写任何文件**，给 Studio 在运动模式卡 `waiting_for_bus`、读不到 `calibration-status` 时兜底。 |
| `calib-commit` | `{"by": …, "joints": [{"id", "direction"} × 15], "imu": {"source": "bus\|bno08x\|lsm6dsv16x", "quat": [w,x,y,z]}, "dry_run": false}` | 把舵机零位（2048）、方向、IMU 安装方向写进 robotd 的 `hd1910-calibration.toml`。顺序：**先拿 `helper.lock`，再查 bus-held 旗**；标定模式跳过着势门槛，运动模式隔 200 ms 读两次 busStatus（策略在跑 / 站着就拒绝，退出 3）；认模式：镜像的 `zzzz-zz-microduck-commissioning.conf` 在 = 标定模式（写完删掉、`daemon-reload` 切运动模式），不在且 `ExecStart` 没有 `--commissioning` = 运动模式（只换标定），`ExecStart` 有但文件不在 = `foreign_commissioning`（不敢自动切，退出 3）。读现在的文件、按标准限位组新内容、逐条校验（跟 fork 的 `Calibration::validate` 一样）。每次写一组 `calib-backups/<4 位序号>-<UTC>/`（主文件、`.saved.toml`、commissioning 文件、`manifest.json`，序号取已有最大值 +1，保留 10 组）；`.previous`、`.toml`、`.saved.toml` 都先写临时文件、fsync、rename，保留原属主和权限；写完读回来逐项校验。从第一次写开始，任何异常都先回滚再报错。重启 robotd 后最多等 40 秒 hello；再每 0.5 秒读一次 `robot.busStatus`、最多 30 秒：**成功 = `mode=="motion"` 且 `phase=="control"`，并且 `calibration-status` 读回的 15 颗（name / id / zero_raw / direction / calibrated / min_rad / max_rad）、`motion_enabled`、总线小板 `imu_mount_quat` / `imu_mount_verified`、`imu_mounts` 每一格的 quat / verified 跟写进去的一致（浮点 1e-9）**。失败按原因回 `calib_rejected`（标定没通过主程序的检查）或 `bus_unavailable`（主程序打不开舵机总线，先看舵机电源），并回滚。非 dry_run 的整段用 `systemd-run` 跑在独立单元里，结果写 `last-result.json`；`dry_run: true` 到校验完就停，只在 `detail.toml` 回全文，不写文件、不重启。 |
| `ssh-status` | `{"by": …}`（可省） | 只读：SSH 密码登录开没开（看 `05-microduck-ssh-password.conf` 在不在）、`ssh` 服务在不在跑、radxa 的 `authorized_keys` 登记了几把钥匙。不拿锁、不写任何文件（也不覆盖 `last-result.json`）。 |
| `ssh-password` | `{"by": …, "on": true, "password": "…"}` / `{"by": …, "on": false}` | 打开：密码 8–64 个可见字符、不能是 `microduck` 这类大家都猜得到的（不合格回 `bad_password`，退出 2）；拿 `ssh.lock`，`chpasswd` 从 stdin 设 radxa 的密码，写 `/etc/ssh/sshd_config.d/05-microduck-ssh-password.conf`（`PasswordAuthentication yes`），`sshd -t` 过了、`sshd -T` 读回 `passwordauthentication yes` 才 `systemctl reload-or-restart ssh`；哪一步不对就删掉这个文件退回去（`sshd_config_bad` / `not_effective` / `reload_failed`）。关掉：删文件、reload，radxa 的密码不动（sudo 还用它）。**密码只在 stdin 里，不进命令行、journal、`last-result.json`、oplog。** |

**输出**：一行一个 JSON：`{"step", "level", "ms", "msg", "detail"}`，最后一行 `step="done"`，`detail` 里有 `exit` 和 `result`。同一份结果写进 `/run/microduck-helper/last-result.json`（0644），Studio 重启以后读它。

`pad-pair` 的 `detail` 还有：`pair`（robotctl 的 stdout，按 `PadPairResult` 解析：`{"outcome":"paired","pad":{…}}` 或 `{"outcome":"failed","reason":"not_found|ambiguous|timeout|no_adapter|rejected|other","detail":…}`）、`stderr`（robotctl 的几行提示，如 `paused btd …`、`warning: …`）和 `stdout`。配上了退出 0；robotctl 说失败退出 5，`detail.reason` 保留官方原因。`pad-forget` 的 `detail` 有 `removed`（true / false / null）、`stdout`、`stderr`。

**退出码**

| 码 | 意思 |
|---|---|
| 0 | 成功 |
| 2 | 参数错（动词不认识、stdin 不是 JSON 对象、有不认识的键、标记校验没过） |
| 3 | 状态不允许（没装保护、策略在跑、鸭子站着 / 在走、没有会话、不是 root；`calib-commit` 还可能是 bus-held、`foreign_commissioning`） |
| 4 | 忙或中止（管理锁被板端脚本占着、小助手正在跑别的总线操作 / 另一个配对或忘掉、另一个 WiFi 热点操作、交还被中止） |
| 5 | 执行失败（停不下来、串口被占、robotd 没认标记已经全卸、软交还起不来；或者 `pad-pair` / `pad-forget` 找不到 robotctl、robotctl 说配对失败、超时被杀；热点关 / 开没成、nmcli 出错、等超时；`calib-commit` 写失败 / 重启失败 / 主程序没进运动模式 / 读回不一致，已经回滚） |
| 6 | 交接没成，总线已拿回（旗重新立好、robotd 停了、锁还在），Studio 重开串口接着保持 |

`result` 的取值：

- bus-take：`taken`、`refused`、`no_guard`、`busy`、`stop_failed`、`port_busy`
- bus-give-back：`adopted`、`retake`、`retake_failed`、`limp`（运动模式没认标记，或者标定模式）、`aborted`、`soft`、`soft_failed`（主程序一直报启动阶段时 `detail.still_starting = true`、`detail.msg` 是整句）、`refused`、`no_session`、`detach_failed`
- bus-abort：`aborted`、`abort_incomplete`、`no_session`（没有会话时拒绝，退出 3）
- pad-pair：`paired`、`pair_failed`（退出 5，`detail.reason` 是官方原因）、`timeout`、`no_robotctl`、`bad_output`
- pad-forget：`forgotten`、`forget_failed`、`timeout`、`no_robotctl`
- wifi-hotspot-down：`hotspot_down`、`already_down`、`down_failed`、`timeout`、`nmcli_failed`
- wifi-hotspot-up：`hotspot_up`、`already_up`、`up_failed`、`timeout`、`nmcli_failed`
- calib-read：`read`。`detail` 有 `calibration`、`mode`（`commissioning` / `motion` / `foreign`）、`commissioning_present`、`exec_has_commissioning`、`mode_text`；不拿锁、不写文件
- calib-commit：`preview`（dry_run）、`committed`（`mode=motion` 且 `phase=control`，并且 `calibration-status` 读回逐项一致）、`not_motion`（30 秒还没进运动模式）、`calib_rejected`（主程序说标定没通过检查）、`bus_unavailable`（主程序打不开舵机总线）、`readback_mismatch`（进了运动模式但读回不一致）、`bus_held`、`no_calibration`、`bad_calibration`、`joint_mismatch`、`invalid`、`bad_toml`、`restart_failed`、`write_failed`、`backup_failed`、`read_failed`、`foreign_commissioning`、`refused`（策略在跑 / 站着）。失败结果里 `detail.rolled_back` / `files_restored` / `mode_restored` / `robotd_up_after_rollback` 分开报；`readback_ok`、`mismatches`、`bus_status`（最后一次的 mode / phase / error）和 `backup` 也在。若回滚自己没完全成功，`msg` 会写「去日志页导出诊断包」
- 都可能有：`bad_args`、`bad_marker`、`bad_verb`、`not_root`、`busy`、`error`

## 2. 文件和路径

| 东西 | 位置 | 为什么 |
|---|---|---|
| 小助手 | `/usr/local/sbin/microduck-helper`（root:root 0755，`#!/usr/bin/python3 -IS`） | `-I` 不读用户的环境和目录，`-S` 不加载 site。Linux 把 shebang 后面当成一个参数，所以写成 `-IS`，不能写 `-I -S` |
| robotd 的 drop-in | `/etc/systemd/system/robotd.service.d/zzzz-studio-bus.conf` | `ConditionPathExists=!/run/microduck-helper/bus-held`：旗在，robotd 就起不来。**不用 `systemctl mask --runtime`**：robotd 的本体在 /etc，/run 里的 mask 盖不住 |
| sudoers | `/etc/sudoers.d/microduck-studio`（0440） | 只放行一个全路径。装之前 `visudo -cf` |
| 旗 | `/run/microduck-helper/bus-held`（目录 0755、文件 0644） | 在内存盘里：断电、重启就没了，robotd 照常开机自启。不放在 Studio 的目录里，是因为 Studio 单元以后要是用 `RuntimeDirectory`，systemd 会在 Studio 重启时删掉那个目录。**Studio 的单元不许用 `RuntimeDirectory=microduck-helper`** |
| 中止请求 | `/run/microduck-helper/abort` | bus-abort 写，正在跑的 bus-give-back 看到就停 |
| 小助手自己的锁 | `/run/microduck-helper/helper.lock`（flock） | bus-take、bus-give-back、calib-commit、imu-recalibrate、pad-pair、pad-forget 一次只跑一个（写标定 / 重启主程序时舵机页不能接管总线，一次也只允许一个配对）。**bus-abort 不拿**：卸力不能被正在跑的交还挡住，它的几步都可以重复做 |
| 热点锁 | `/run/microduck-helper/wifi.lock`（flock） | `wifi-hotspot-down` / `wifi-hotspot-up` 一次只跑一个；跟舵机总线的 `helper.lock` 分开，互不挡 |
| 官方 robotctl | `/usr/local/bin/robotctl`（写死在 `deploy/microduck-helper` 里） | 只有 `pad-pair` / `pad-forget` 调它。不存在就回 `no_robotctl`（退出 5），不会去别的路径找 |
| 管理锁 | `/run/microduck-service-management.lock`，由临时单元 `microduck-bus-hold` 一直拿着（`flock -n … sleep infinity`） | 板上的 `service.sh` 拿不到这把锁就不动 robotd |
| 交接标记 | `/run/microduck-handover/handover.json`（目录 0700、文件 0600，同目录临时文件 + fsync + rename） | 只有 root 读写；robotd 读完就删 |
| 操作记录 | `/var/lib/microduck-helper/oplog.jsonl`（父目录 0755、日志文件 0600；私有备份子目录 0700） | 一次一行：谁、什么时候、结果。父目录允许 Studio 读取公开的 IMU 会话状态，日志和备份仍只允许 root 读取 |
| 标定备份 | `/var/lib/microduck-helper/calib-backups/<4 位序号>-<UTC>/`（目录 0700、文件 0600，只留最新 10 组） | 每组有写之前的 `.toml`、`.saved.toml`（各备各的）、commissioning drop-in（有就备）和 `manifest.json`（原属主、权限、sha256）。序号按已有目录最大值 +1，不靠墙钟排序；回滚照 manifest 各自换回去 |

**标记格式**（fork 的 robotd 照这个读）：

```
{"schema": "microduck-handover/1", "requested_by": str, "boot_id": str, "mono_ms": int,
 "ttl_ms": int, "port": "/dev/ttyS2", "arm_registers": {"limit", "p", "d"}（可选）,
 "joints": [{"id", "torque", "goal", "present", "offset", "load"?, "temp"?, "status"?} × 15]}
```

- `boot_id`、`mono_ms`（CLOCK_MONOTONIC 毫秒）由**小助手自己填**，Studio 给了也覆盖掉。
- **ttl_ms 超过 30000 直接拒（退出码 2），不悄悄截断**，低于 1000 也拒；默认 20000。robotd 那边最多也只认 30 秒，两边说法一致。
- id 必须正好是 10–14、20–24、30–34，每个一次；torque 必须是 1（主程序只接手 15 颗全开的）；goal、present、offset 是 −32767..32767 的整数。

**几个时间**：

| 时间 | 值 |
|---|---|
| gate 两次读的间隔 | 0.2 s |
| 等回站姿 | 3 s |
| stop 到 KILL | 10 s |
| KILL 以后再等 | 3 s |
| robotd 读标记 | 15 s |
| 接手后走完启动第 4 步 | 20 s |
| 软交还 | 30 s |
| 中止交接 | KILL 后等 0.5 s |
| 写标定后等 robotd | 40 s（unit active + `/run/robotd.sock` 的 hello） |
| 等运动模式 | 30 s（每 0.5 s 读一次 busStatus；成功还要读回 calibration-status 逐项一致） |
| Studio 等小助手 | 180 s（超时也去读 `last-result.json`，不直接报 TimeoutExpired） |

**几个环境变量**：`MICRODUCK_HELPER_DETACHED`、`MICRODUCK_HELPER_REQUEST`、`MICRODUCK_HELPER_REQUESTED_MS`（`bus-give-back` 和 `calib-commit` 都用）。

- 用途：bus-give-back / 非 dry_run 的 calib-commit 第一次被调用时，用 `systemd-run --wait --pipe --collect --quiet --unit=microduck-handover-<时间>-<pid>` 把自己放进独立的临时单元再跑一遍，stdin 原样转过去，输出原样转回来。这样 Studio 死了，交还也不会断在半路。
- 这几个变量**只**决定「不要再转一次」，以及中止请求、结果怎么对号，**不给任何权限**。sudo 默认 env_reset，调用方也传不进来。
- **没有任何环境变量能改根目录**：测试是 import 进去、换一个假的 Ctx，不靠环境变量。

**所有判断 robotd 起没起来的地方**，都看 `systemctl show robotd -p ActiveState,SubState,Result,ConditionResult`。旗在的时候 `systemctl start robotd` 返回 0，但单元停在 inactive、`ConditionResult=no`，不能当成起来了。每次拉 robotd 之前都先 `reset-failed`，防撞上 start-limit-hit。

**信任边界**：能 sudo 小助手的用户（现在是 radxa）本来就能直接打开 ttyS2；robotd 又会在总线上回读核对标记。所以伪造标记不会多给权限，最坏是「保持住本来就顶着的舵机」。

## 3. 安装（一次，SSH）

```sh
# 电脑上：把 deploy 目录拷到鸭子上
scp -r deploy radxa@鸭子:/tmp/studio-deploy
# 鸭子上
cd /tmp/studio-deploy && sudo sh install.sh radxa
```

`install.sh` 做四件事：

1. 装小助手到 `/usr/local/sbin`（0755）；`/usr`、`/usr/local`、`/usr/local/sbin` 要是属主不是 root，或者组 / 其他人可写，就提醒；
2. 装 drop-in，然后 `systemctl daemon-reload`。**不重启 robotd**；
3. 生成 sudoers 那一行，`visudo -cf` 过了才装（0440）；
4. 打印核对信息。

**第 1 步只装 drop-in 的 `[Unit]` 那段。** `[Service] Environment=MICRODUCK_HANDOVER_MARKER=…` 是注释掉的：等 fork 的 robotd 装好、第 3 步（Studio 交还不卸力）验收以后，再手动打开那两行、`daemon-reload`。打开以后 robotd 下次启动才会看交接标记；没有标记就跟 V167 完全一样。旧版 robotd 不认这个变量，打开了也没影响。

装完马上核对：

```sh
systemctl show robotd -p DropInPaths            # 要有 zzzz-studio-bus.conf
echo '{}' | sudo -n /usr/local/sbin/microduck-helper bus-nope; echo $?   # 用 radxa：应该是 2
echo '{}' | sudo -n /usr/local/sbin/microduck-helper bus-take;  echo $?  # 应该是 2：缺 by（什么都没动）
echo '{}' | sudo -n /usr/local/sbin/microduck-helper pad-pair;  echo $?  # 应该是 2：缺 by（不跑 robotctl）
echo '{}' | sudo -n /usr/local/sbin/microduck-helper wifi-hotspot-down; echo $?  # 应该是 2：缺 by（不碰 nmcli）
echo '{}' | sudo -n /usr/local/sbin/microduck-helper wifi-hotspot-up;   echo $?  # 应该是 2：缺 by（不碰 nmcli）
echo '{}' | sudo -n /usr/local/sbin/microduck-helper calib-read;       echo $?  # 应该是 0：读文件 + 模式（不拿锁、不写）
echo '{}' | sudo -n /usr/local/sbin/microduck-helper calib-commit;   echo $?  # 应该是 2：缺 by（不读写标定）
ls -l /usr/local/bin/robotctl                                        # 官方 0.15.1 或更新
stat -c '%U:%G %a %n' /usr /usr/local /usr/local/sbin /usr/local/sbin/microduck-helper
sudo -l -U radxa | grep microduck-helper
```

## 4. 卸载

```sh
sudo sh uninstall.sh            # 旗在（Studio 正占着总线）时拒绝
sudo sh uninstall.sh --purge    # 连 /var/lib/microduck-helper 一起删
```

卸载会删掉 sudoers 那一行、drop-in（然后 daemon-reload）、小助手，停掉 `microduck-bus-hold`，清掉 `/run/microduck-helper` 和 `/run/microduck-handover`。robotd 不重启。

## 5. 出事时怎么看

- `sudo ls -l /run/microduck-helper/`：`bus-held` 在 = 主程序被 Studio 挡着，起不来是故意的。要放开：在 Studio 里交还，或者重启。
- `cat /run/microduck-helper/last-result.json`：上一次做了什么、卡在哪一步（`steps` 是最后 30 行进度）。
- `journalctl -t microduck-helper -b`：每一行进度都写在这里。
- `systemctl status microduck-bus-hold`：管理锁在不在 Studio 手里。
- `sudo tail /var/lib/microduck-helper/oplog.jsonl`：操作记录。

## 6. 上机核对清单（二号鸭，用户扶着；他说开始就连着跑，不用每次问）

**先只读核对**（不动任何东西）：

- `systemctl --version`（257）、`sudo -V | grep -i -E 'env_reset|secure_path'`、`ls -l /etc/sudoers.d`。
- **systemd-run 那几个行为**：
  - `systemd-run --wait --pipe --collect --quiet -p … /bin/sh -c 'exit 6'; echo $?`：`--wait` 会不会把服务的退出码带回来。小助手不靠这个，先读 last-result 里的 request_id 对号，对不上才用 systemd-run 的退出码；
  - `--pipe` 下把调用方杀掉，服务里面的进程写 stdout 会拿到 EPIPE（小助手忽略 SIGPIPE），而不是被一起杀掉；
  - 临时单元默认 `KillMode=control-group`，所以 `systemctl stop microduck-bus-hold` 会连 flock 和 sleep 一起停掉、锁跟着放掉。用 `flock -n /run/microduck-service-management.lock true; echo $?` 核对。

**第 1 步的上机测试（跟小助手有关的）**：

| 编号 | 做什么 | 看什么 |
|---|---|---|
| U1 | 卸力状态下 `bus-take` | 退出码 0；旗在；robotd inactive；Studio 开串口读 40 全是 0、位置不变 |
| U2 | 站姿保持（init 以后）`bus-take` | 0；Studio 读 40 全是 1，位置差 <1°，鸭子不往下沉 |
| U3 | 策略开着（手柄 Start 进保持）`bus-take` | 3，原因是「先按手柄 Start…」；padd 回来了；robotd 没被停。按 Start 2 秒回站姿以后再 take → 0，不软 |
| U4 | 旗在的时候 `sudo systemctl start robotd` | 退出码 0，但 `systemctl show robotd -p ActiveState,ConditionResult` 是 inactive / no；板上的 `service.sh status`（路径以板上实际为准）被锁拒：「已有服务管理操作正在执行」 |
| U4b | 旗在时 KILL 以后的自动重启 | **不要在用户拿着总线的时候拿 robotd 做。** 先用一个假单元验：建一个带同样 Condition 的临时服务，起来、立旗、`systemctl kill -s KILL`，看 2 秒后它有没有被拉起来。用户空下来以后，再在 robotd 上做一次 |
| U4c | 旗在时连着 `systemctl start robotd` 6 次 | 看 `Result` 会不会变成 start-limit-hit。然后撤旗（`bus-give-back {"soft":true}` 会先 reset-failed）确认能起来 |
| U5 | 会话中 `systemctl restart studio-trial` | 鸭子不软；旗、`microduck-bus-hold` 还在；Studio 起来以后 `bus-take` 返回 0，detail 里 `session_was_open=true` |
| U6 | 软交还 | 扶住；`bus-give-back {"soft":true}` 返回 0；robotd 四步 success；padd 回来；`microduck-bus-hold` 停了 |
| U7 | 离开舵机页 | 有保持就不交还（另一个子代理的部分，一起验） |
| V0（小助手部分） | 量 `bus-take` 和 `bus-abort` 各用多久 | 看 last-result 的 `ms`；abort 目标 ≤1 秒 |
| U8 | 在 Studio 里配一次手柄（雷神 G50 Pro，Switch Pro 模式，名字 `Pro Controller`） | 向导里「配对走的是」写小助手；`cat /run/microduck-helper/last-result.json` 的 `verb=pad-pair`、`result=paired`，`detail.stderr` 有 `paused btd …`；30 秒内 padd 认到并进按键测试。没有新版小助手时向导会提示先重装 |
| U9 | 热点开着时 `wifi-hotspot-down {"by":"核对","ssid":"Home"}` | 退出 0、`result=hotspot_down`；关之前已有 `/run/microduck/studio-connecting.json`（root 0644，`until` 约 90 秒后，`ssid=Home`）；`nmcli connection show --active` 里没有 `Microduck-Hotspot`；手机的热点连接断开。热点本来关着再调一次 → 0、`already_down`，且不写连接窗口 |
| U10 | 热点关着时 `wifi-hotspot-up {"by":"核对"}` | 退出 0、`result=hotspot_up`；`nmcli connection show --active` 里有 `Microduck-Hotspot`（wlan0）；手机能搜到热点；`studio-connecting.json` 已删掉。再调一次 → 0、`already_up`，文件不在也不报错 |
| U11 | 先只预览：`calib-commit {…, "dry_run": true}`（joints / imu 用 Studio 预览的同一份） | 退出 0、`result=preview`；`detail.toml` 里 15 颗 `calibrated = true`、`motion_enabled = true`、限位和四元数在；`detail.mode` 是 `commissioning`（出厂）或 `motion`；`hd1910-calibration.toml` 的 sha256 和 mtime 都没变，robotd 没重启 |
| U12 | 真写（扶好鸭子）：同上 `dry_run: false` | 退出 0、`result=committed`；`detail.mode_before=commissioning`、`detail.bus_status.mode=motion`、`phase=control`、`readback_ok=true`、`mismatches=[]`；`zzzz-zz-microduck-commissioning.conf` 没了、`daemon-reload` 跑过；`.saved.toml` 跟主文件一样、`.previous.toml` 是写之前的；`calib-backups/` 多一组（manifest 里三份都在）；`systemctl show robotd -p ActiveState` 是 active；Studio 清单第 3 步变完成、顶栏显示运动模式 |
| U12b | 回滚（标定被拒）：把正在用的 IMU 改成没 verified（或来源对不上），再跑一次 U12 | 退出 5、`result=calib_rejected`；`reason` 是「标定没通过主程序的检查：motion requires all 15 calibrated joints…」；`detail.bus_status` 是 `mode=motion`、`phase=waiting_for_bus`；`detail.files_restored` / `mode_restored` / `robotd_up_after_rollback` 都是 true，`detail.rolled_back` 也是 true；主文件、`.saved.toml` 跟 U12 之前一样，commissioning 文件放回去了，robotd 又起了一次（回到标定模式） |
| U12c | 回滚（舵机没上电）：关舵机电源，再跑一次 U12 | 退出 5、`result=bus_unavailable`；`reason` 是「主程序打不开舵机总线：torque readback…。舵机电源开了吗？」；`detail.bus_status.phase=waiting_for_bus`；文件和模式都换回 |
| U12d | 回滚（读回不一致）：临时把假主程序 / 测试桩的 calibration-status 改一颗 `zero_raw`，再跑一次 U12 | 退出 5、`result=readback_mismatch`、`detail.readback_ok=false`、`detail.mismatches` 里写明哪一项；文件和模式都换回 |
| U13 | 回滚：临时让 robotd 起不来（建一个 `robotd.service.d/zzzz-e2e-fail.conf`，写 `[Unit]` + `ConditionPathExists=/nonexistent-e2e`，`daemon-reload`），再跑一次 U12 | 退出 5、`result=restart_failed`、`detail.files_restored=true`、`detail.robotd_up_after_rollback=false`（真的等到 hello 才算 true）；主文件和 `.saved.toml` 跟 U12 之前一样；`detail.journal` 有 robotd 最后 30 行；`reason` 带「去日志页导出诊断包」。核对完删掉那个 drop-in、`daemon-reload`、`systemctl start robotd` |

**还没覆盖到的**：标记交还（`{"marker": …}`）要等 fork 的 robotd 装上、drop-in 打开 Environment 以后才能在板上测，那是第 4 步的 V1–V12。

## 7. 已知的限制

- bus-take 在「判完状态」到「立旗、停 robotd」之间，有大约 0.1 秒的窗口，这期间蓝牙 App 还能把策略打开。padd、gamesir 已经先停了，btd 没停。
- configd 的 `system.reboot`、SSH 里直接 `poweroff` 拦不住（方案 §2.5）。重启的话，robotd 起来时照老规矩全卸。
- 交接倒数期间手柄 Select 不起作用（padd 要等 robotd 四步都 success 才启动）；那几秒用网页卸力（走 bus-abort）或关电源。
- 旧版小助手（不认 `pad-pair`）会回 `bad_verb`；Studio 把它当没有新版小助手，退回 configd → bluetoothctl。这块板（`/var/lib/robot/weird-ble`）上后备路可能配不上，向导会提示重装小助手或在鸭子上运行 `sudo robotctl pad pair`。
- 旧版小助手（0.2.0，不认 `wifi-hotspot-down` / `wifi-hotspot-up`）也回 `bad_verb`；Studio 配网时会提示「小助手太旧，重装 deploy/install.sh」。
