# A7Z 低温限频修复

适用：Cubie A7Z，内核 `5.15.147-21-a733`。

现用板级设备树把第一个 CPU trip 改成 `active`，只留下一个
`passive` trip，导致 `power_allocator` 在低温也一直分配功率。大核空闲、
请求功率为零时，即使预算充足仍分配零功率，进而将最高频率限制到
416 MHz。原始 perf 证据在本机 `../control/evidence/thermal-perf-20261106.txt`。

本修复仅将 `cpul_thermal_zone` 和 `cpub_thermal_zone` 的 governor 改为
内核自带的 `step_wise`。保留风扇绑定、60°C active/passive trip、2°C 回差、
110°C critical trip 和温控 enabled；不写频率、cooling state 或模拟温度。
其他 thermal zone 保持其原策略。

脚本按 zone 的 type 定位，核验型号、内核、trip 和 CPU cooling 绑定。
首次应用保存原策略；写入或低温释放验证失败时恢复此前策略。
systemd oneshot 在开机时应用，运行期间继续由内核执行温控。

本地测试：

```sh
cd radxa-cubie-a7z/thermal
python3 -m unittest -v test_thermal_policy
sh -n install.sh
```

在板端将这三个运行文件放在同一目录后安装：

```sh
sudo sh install.sh
python3 /usr/local/lib/cubie-thermal-policy/thermal_policy.py --check
```

回退到首次应用前的策略：

```sh
sudo systemctl disable --now cubie-thermal-policy.service
sudo python3 /usr/local/lib/cubie-thermal-policy/thermal_policy.py --restore
```

回退会恢复有低温限频问题的原 `power_allocator`。本修复不重启或更改
robotd、Studio、串口、舵机或标定配置；开机持久化须在后续实际重启时验证。

## 2026-11-06 板端验证

- 本机与板端各执行同一组 9 项测试，全部通过；部署脚本 SHA-256 与本机一致。
- 服务 `cubie-thermal-policy.service` 已启用，执行结果 success，两个 CPU zone 均为 `step_wise`。
- 30 秒被动采样共 150 次：CPU cooling 均为 0，两组最高允许频率始终等于各自硬件上限；瞬时频率仍正常随负载变化。
- 在已确认 `robotd --read-only` 的状态下，对 CPU6/7 做有时限的 15 秒负载测试；大核达到 2002 MHz，温度最高 63.86°C。
- 超过 60°C 后实际出现 24 次限频样本，上限降到 1716–1950 MHz；负载结束后降至 40.796°C，上限恢复 2002 MHz、cooling 回到 0，负载进程全部退出。
- 两个 CPU zone 的温控 enabled、60°C trip 和 110°C critical 配置回读保持原值。未实际升温到 110°C，也未为验证而重启设备。

本机证据：`../control/evidence/thermal-fix-idle-20261106.json`、
`thermal-fix-load-20261106.json` 和 `thermal-fix-20261106.summary.json`。
