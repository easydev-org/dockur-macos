#!/usr/bin/env bash
# Give OpenCore an APFS driver taken from the disk it is supposed to boot.
#
# WHY. The disk holds a complete macOS Sonoma - 692 boot.efi hits, 128
# System/Library/CoreServices, 455 Preboot, on an AF0A APFS partition - yet
# OpenCore's picker lists only Reset NVRAM and Toggle SIP, with no macOS volume.
#
# The reason is that OpenCore has no APFS driver:
#
#   APFS on ESP     : NONE
#   EnableJumpstart : True
#
# EnableJumpstart does not supply a driver, it hooks one that the firmware is
# expected to already have. OVMF has none, so the AF0A partition cannot be read
# and no volume is offered.
#
# macOS itself drops apfs.efi into the EFI system partition while installing
# (EFI/APPLE/EXTENSIONS/apfs.efi). That file is sitting inside data.img, in the
# 200 MiB EFI partition. Copy it into boot.img's EFI/OC/Drivers and enable it in
# the config, and OpenCore can read the container.

set -euo pipefail

DISK="${1:?data.img}"
BOOTIMG="${2:?boot.img}"
WORK="${3:-/tmp/apfsfix}"

mkdir -p "$WORK"

# The EFI partition starts at sector 40 on this layout (sgdisk reported
# 40..409639, 200 MiB). Read a byte at a time from the right offset rather than
# converting the whole 64 GiB image.
ESP_OFFSET=$((40 * 512))
ESP_SIZE=$((200 * 1024 * 1024))

echo "=== extracting the ESP (200 MiB at byte $ESP_OFFSET) ==="
dd if="$DISK" of="$WORK/esp.img" bs=1M skip=$((ESP_OFFSET / 1048576)) count=200 status=none
ls -la "$WORK/esp.img"

echo "=== what the ESP contains ==="
mcopy -i "$WORK/esp.img" -s ::/ 2>/dev/null | head -40 || true

# Look for an APFS driver wherever macOS may have put it. Two known locations:
#   EFI/APPLE/EXTENSIONS/apfs.efi   the firmware driver macOS installs
#   EFI/OC/Drivers/apfs.efi        in case something already staged it
echo
echo "=== searching for an APFS driver ==="
FOUND=""
for candidate in \
  "::/EFI/APPLE/EXTENSIONS/apfs.efi" \
  "::/EFI/APPLE/EXTENSIONS/ApfsDriverLoader.efi" \
  "::/apfs.efi"
do
  if mcopy -i "$WORK/esp.img" "$candidate" "$WORK/apfs.efi" 2>/dev/null; then
    echo "found $candidate"
    FOUND="$candidate"
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "NO APFS DRIVER in the ESP - listing everything to be sure"
  mcopy -i "$WORK/esp.img" -s ::/ "$WORK/esp-tree" 2>/dev/null || true
  find "$WORK/esp-tree" -iname '*apfs*' 2>/dev/null || true
  echo "--- full ESP listing ---"
  find "$WORK/esp-tree" -type f 2>/dev/null | head -40 || true
  echo "ABSENT"
  exit 2
fi

ls -la "$WORK/apfs.efi"

# boot.img is a GPT disk with one FAT partition at 1 MiB, which is the layout
# dockur builds. Copy the driver in and register it in the config.
echo
echo "=== installing the driver into boot.img ==="
mcopy -o -i "$BOOTIMG@@1M" "$WORK/apfs.efi" ::/EFI/OC/Drivers/apfs.efi
mcopy -i "$BOOTIMG@@1M" ::/EFI/OC/Drivers/apfs.efi "$WORK/check.efi"
cmp -s "$WORK/apfs.efi" "$WORK/check.efi" && echo "driver copied and verified" || { echo "driver copy failed"; exit 3; }

echo
echo "=== drivers now on boot.img ==="
mdir -i "$BOOTIMG@@1M" ::/EFI/OC/Drivers/ | head -60
