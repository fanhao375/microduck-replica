#!/bin/sh
set -eu
cd "$(dirname "$0")"
install -d /usr/local/lib/cubie-provision /var/lib/cubie-provision
install -m 755 cubie_provision.py /usr/local/lib/cubie-provision/cubie_provision.py
install -m 644 cubie-provision.service /etc/systemd/system/cubie-provision.service
systemctl daemon-reload
systemctl enable --now cubie-provision.service
systemctl --no-pager --full status cubie-provision.service | head -n 20
