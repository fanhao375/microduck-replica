#!/bin/sh
# 卸掉 Studio 的 root 小助手、robotd 的保护 drop-in、sudoers 那一行。
# 用法：sudo sh uninstall.sh [--purge]   （--purge 连操作记录 /var/lib/microduck-helper 一起删）
# Studio 正占着舵机总线（旗在）时拒绝：先在 Studio 里交还，或者重启。
set -eu
[ "$(id -u)" -eq 0 ] || { echo "要 root：sudo sh uninstall.sh" >&2; exit 1; }
if [ -e /run/microduck-helper/bus-held ]; then
    echo "Studio 正占着舵机总线（/run/microduck-helper/bus-held 在）：先在 Studio 里交还主程序，再卸" >&2
    exit 1
fi
if [ -e /etc/systemd/system/robotd.service.d/zzzzzz-studio-imu-recalibrate.conf ]; then
    echo "初次 IMU 校准重做会话还在：先在 Studio 完成或取消，再卸载（保留会话和备份）" >&2
    exit 1
fi
if [ -e /var/lib/microduck-helper/imu-recalibrate/manifest.json ]; then
    /usr/bin/python3 -IS -c 'import json,sys;sys.exit(1 if json.load(open("/var/lib/microduck-helper/imu-recalibrate/manifest.json"))["active"] else 0)' || {
        echo "初次 IMU 校准会话未结束或读不回：先恢复会话，再卸载" >&2
        exit 1
    }
fi
rm -f /etc/sudoers.d/microduck-studio
rm -f /etc/systemd/system/robotd.service.d/zzzz-studio-bus.conf
systemctl daemon-reload
rm -f /usr/local/sbin/microduck-helper
systemctl stop microduck-bus-hold 2>/dev/null || true
rm -rf /run/microduck-helper /run/microduck-handover
if [ "${1:-}" = "--purge" ]; then rm -rf /var/lib/microduck-helper; fi
echo "卸好了。robotd 没重启；systemctl show robotd -p DropInPaths 里应该没有 zzzz-studio-bus.conf 了。"
