#!/bin/sh
set -eu
cd "$(dirname "$0")"
root=${DESTDIR:-}
if [ -n "$root" ]; then
    case "$root" in /*) ;; *) echo 'DESTDIR must be an absolute offline root' >&2; exit 1 ;; esac
    [ -d "$root/etc" ] || { echo 'DESTDIR does not contain a Linux root filesystem' >&2; exit 1; }
else
    [ "$(id -u)" -eq 0 ] || { echo 'Run this installer as root' >&2; exit 1; }
    for command in python3 nmcli ip dnsmasq systemctl; do
        command -v "$command" >/dev/null || { echo "Missing dependency: $command" >&2; exit 1; }
    done
    # NetworkManager launches its own dnsmasq instance; standalone AP/DNS units
    # must stay stopped, including their legacy SysV boot links.
    systemctl disable --now dnsmasq.service hostapd.service
    systemctl mask dnsmasq.service hostapd.service
fi
install -d "$root/usr/local/lib/orangepi-provision" "$root/var/lib/orangepi-provision"
install -d "$root/etc/systemd/system/multi-user.target.wants"
install -m 755 orangepi_provision.py "$root/usr/local/lib/orangepi-provision/orangepi_provision.py"
install -m 644 orangepi-provision.service "$root/etc/systemd/system/orangepi-provision.service"
ln -sfn /etc/systemd/system/orangepi-provision.service \
    "$root/etc/systemd/system/multi-user.target.wants/orangepi-provision.service"
if [ -n "$root" ]; then
    for unit in dnsmasq hostapd; do
        rm -f "$root/etc/systemd/system/multi-user.target.wants/$unit.service"
        ln -sfn /dev/null "$root/etc/systemd/system/$unit.service"
    done
    echo "Installed and enabled Orange Pi provisioning in $root"
else
    systemctl daemon-reload
    systemctl enable NetworkManager.service
    systemctl restart orangepi-provision.service
    systemctl --no-pager --full status orangepi-provision.service
fi
