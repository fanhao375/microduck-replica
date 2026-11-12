#!/bin/sh
set -eu
cd "$(dirname "$0")"
test "$(id -u)" = 0 || { echo 'run as root' >&2; exit 1; }
python3 thermal_policy.py --check
target=/usr/local/lib/cubie-thermal-policy
unit=/etc/systemd/system/cubie-thermal-policy.service
test ! -e "$target" && test ! -e "$unit" || {
    echo 'thermal workaround already exists; inspect before replacing' >&2
    exit 1
}
systemd-analyze verify "$(pwd)/cubie-thermal-policy.service" 2>&1
install -d -m 755 "$target"
install -m 755 thermal_policy.py "$target/thermal_policy.py"
install -m 644 cubie-thermal-policy.service "$unit"
systemctl daemon-reload
if ! systemctl enable --now cubie-thermal-policy.service; then
    # The helper rolls back partial runtime changes; remove boot activation too.
    systemctl disable cubie-thermal-policy.service
    exit 1
fi
systemctl --no-pager --full status cubie-thermal-policy.service
