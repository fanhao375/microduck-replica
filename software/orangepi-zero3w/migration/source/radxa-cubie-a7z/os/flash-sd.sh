#!/bin/bash
# Write the Cubie A7Z Debian SD image to the single external removable card.
set -euo pipefail
IMG="${1:?Usage: flash-sd.sh IMAGE.img.xz [LOG_PATH]}"
LOG="${2:-./flash-sd.log}"
exec > >(tee -a "$LOG") 2>&1
echo "=== flash start $(date) ==="

if [[ ! -f "$IMG" ]]; then
  echo "ABORT missing image"
  exit 2
fi

python3 - << 'PY'
import subprocess, sys
out = subprocess.check_output(["diskutil", "list", "external", "physical"], text=True)
print(out)
candidates = []
for line in out.splitlines():
    if line.startswith("/dev/disk"):
        dev = line.split()[0]
        info = subprocess.check_output(["diskutil", "info", dev], text=True)
        fields = {}
        for row in info.splitlines():
            if ":" not in row:
                continue
            k, v = row.split(":", 1)
            fields[k.strip()] = v.strip()
        size = fields.get("Disk Size", "")
        loc = fields.get("Device Location", "")
        removable = fields.get("Removable Media", "")
        protocol = fields.get("Protocol", "")
        whole = fields.get("Whole", "")
        readonly = fields.get("Media Read-Only", "")
        virtual = fields.get("Virtual", "")
        print(f"CANDIDATE {dev} size={size} loc={loc} removable={removable} protocol={protocol} whole={whole} ro={readonly} virtual={virtual}")
        ok = (
            loc == "External"
            and removable == "Removable"
            and protocol == "USB"
            and whole == "Yes"
            and readonly == "No"
            and virtual == "No"
            and dev != "/dev/disk0"
            and "31914983424" in size
        )
        if ok:
            candidates.append(dev)
if len(candidates) != 1:
    print(f"ABORT expected exactly one 31.9GB external SD, found {candidates}")
    sys.exit(3)
open("/tmp/cubie-a7z-sd-target", "w").write(candidates[0] + "\n")
print("TARGET", candidates[0])
PY

DEV="$(cat /tmp/cubie-a7z-sd-target)"
RAW="/dev/r${DEV#/dev/}"
echo "unmount $DEV raw $RAW"
diskutil unmountDisk force "$DEV"

OUTDEV=""
if dd if="$RAW" of=/dev/null bs=512 count=1 >/dev/null 2>/tmp/cubie-dd-probe; then
  OUTDEV="$RAW"
elif dd if="$DEV" of=/dev/null bs=512 count=1 >/dev/null 2>/tmp/cubie-dd-probe; then
  OUTDEV="$DEV"
else
  echo "ABORT cannot read the SD device:"
  cat /tmp/cubie-dd-probe
  echo "macOS blocked disk access. Enable Full Disk Access for Terminal, then run this script again."
  exit 4
fi
echo "writing image to $OUTDEV"
xz -dc "$IMG" | dd of="$OUTDEV" bs=4m conv=fsync
sync
echo "=== after write ==="
diskutil list "$DEV"
diskutil eject "$DEV" || true
echo "FLASH_OK $(date)"
