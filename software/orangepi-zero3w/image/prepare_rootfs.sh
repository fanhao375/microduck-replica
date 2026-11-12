#!/bin/sh
# Run inside the disposable ARM64 Linux builder, against an offline image mount.
set -eu
root=${1:?Usage: prepare_rootfs.sh MOUNTED_ROOT SOURCE_DIRECTORY}
source_dir=${2:?Usage: prepare_rootfs.sh MOUNTED_ROOT SOURCE_DIRECTORY}
[ "$root" != / ] && mountpoint -q "$root" || { echo 'Expected an offline mounted image' >&2; exit 1; }
grep -qx BOARD=orangepizero3w "$root/etc/orangepi-release"

work=$(mktemp -d)
cleanup() {
    [ ! -f "$work/resolv.conf" ] || cp -P "$work/resolv.conf" "$root/etc/resolv.conf"
    if [ -L "$work/resolv.conf" ]; then
        rm -f "$root/etc/resolv.conf"
        cp -P "$work/resolv.conf" "$root/etc/resolv.conf"
    fi
    rm -f "$root/usr/sbin/policy-rc.d"
    [ ! -e "$work/policy-rc.d" ] || cp -p "$work/policy-rc.d" "$root/usr/sbin/policy-rc.d"
    for path in proc dev; do umount "$root/$path" 2>/dev/null || true; done
    rm -rf "$work"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cp -P "$root/etc/resolv.conf" "$work/resolv.conf"
rm -f "$root/etc/resolv.conf"
cp /etc/resolv.conf "$root/etc/resolv.conf"
if [ -e "$root/usr/sbin/policy-rc.d" ]; then cp -p "$root/usr/sbin/policy-rc.d" "$work/policy-rc.d"; fi
printf '#!/bin/sh\nexit 101\n' > "$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
mount --bind /dev "$root/dev"
mount -t proc proc "$root/proc"
chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get update -qq
chroot "$root" /usr/bin/env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    sudo psmisc alsa-utils v4l-utils ffmpeg libudev1 libstdc++6 libgcc-s1 \
    libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 libgstreamer-plugins-bad1.0-0
DESTDIR="$root" sh "$source_dir/provision/install.sh"
chroot "$root" /usr/bin/dpkg-query -W '-f=${binary:Package}\t${Version}\n'
