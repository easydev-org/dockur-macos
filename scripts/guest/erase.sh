#!/bin/sh
# Erase the installation target, from inside the recovery environment.
#
# WHY THIS EXISTS AT ALL. The obvious route is Disk Utility: open it, pick the disk,
# press Erase. That route was attempted for many rounds and never worked, for three
# separate reasons that only became visible one at a time:
#
#   Disk Utility opens only after selecting its row AND pressing Continue - clicking
#   the row alone just highlights it, and the tool never launched.
#
#   Its sidebar does not list the target at all. The default view is VOLUMES, and a
#   freshly created 64 GB disk has no volume yet, so the three rows on screen were the
#   recovery volume (2.88 GB) and the boot image (268 MB). Probing every row and
#   reading the capacity found no 64 GB anywhere - because it was never on the list.
#
#   Its sidebar text is unreadable to OCR: small, low-contrast, on a dark background.
#   tesseract returned none of "Apple Inc. VirtIO Block Media" at any psm setting, so
#   the driver could not even identify rows it could see in a screenshot.
#
# The unattended installer does not use Disk Utility at all. src/install/recovery/
# launch.sh selects the target with `diskutil list physical` plus `diskutil info`,
# skipping read-only media and anything under 16 GiB, and then calls
#
#   diskutil eraseDisk APFS "Macintosh HD" GPT /dev/diskX
#
# This is the same logic, run from Terminal instead of from a shell that recovery
# launches for us - because MANUAL=Y never injects that shell.

set -u

LOG=/tmp/erase.log
MIN_BYTES=16000000000

log() {
  printf '[erase %s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"
}

log "listing physical disks"
/usr/sbin/diskutil list physical >>"$LOG" 2>&1

best=""
best_size=0

for disk in $(/usr/sbin/diskutil list physical 2>/dev/null |
              /usr/bin/sed -n 's#^/dev/\(disk[0-9]*\).*#\1#p'); do

  info=$(/usr/sbin/diskutil info "/dev/$disk" 2>/dev/null) || continue
  [ -n "$info" ] || continue

  # Skip the recovery image and the boot image: both are attached read-only, and
  # erasing either destroys the installer that is running this script.
  if printf '%s\n' "$info" |
     /usr/bin/grep -Eq '^[[:space:]]*(Read-Only (Media|Device)|(Media|Device) Read-Only):[[:space:]]*Yes'; then
    log "/dev/$disk is read-only, skipped"
    continue
  fi

  size=$(printf '%s\n' "$info" |
         /usr/bin/sed -nE 's/^[[:space:]]*Disk Size:.*\(([0-9]+) Bytes\).*/\1/p' |
         /usr/bin/head -n 1)
  [ -n "$size" ] || continue
  [ "$size" -ge "$MIN_BYTES" ] 2>/dev/null || {
    log "/dev/$disk is $size bytes, too small"
    continue
  }

  log "candidate /dev/$disk ($size bytes)"
  if [ "$size" -gt "$best_size" ]; then
    best="$disk"
    best_size="$size"
  fi
done

if [ -z "$best" ]; then
  log "ERROR: no writable disk of at least 16 GiB"
  echo "ERASE-FAILED" >>"$LOG"
  exit 1
fi

log "erasing /dev/$best"
if /usr/sbin/diskutil eraseDisk APFS "Macintosh HD" GPT "/dev/$best" >>"$LOG" 2>&1; then
  log "ERASE-OK /dev/$best"
  # The flag the driver watches for, since OCR on this Terminal output is unreliable.
  echo "ERASE-OK" >>"$LOG"
else
  log "ERROR: eraseDisk failed"
  echo "ERASE-FAILED" >>"$LOG"
  exit 1
fi
