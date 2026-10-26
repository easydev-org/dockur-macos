#!/usr/bin/env bash
# Decide, from the disk itself, whether stage 1 produced a real macOS system.
#
# WHY. Every bake produces a disk of roughly 27 GiB and then nothing boots from
# it: OpenCore's picker shows Reset NVRAM and Toggle SIP but no macOS volume. Two
# explanations need opposite responses:
#
#   A. stage 1 only wrote Apple's recovery environment. Then startosinstall never
#      actually installed anything and the booter is fine.
#   B. stage 1 wrote a real system and OpenCore cannot see it. Then the booter is
#      the problem.
#
# The strings in the APFS container separate these without inference: a finished
# install carries the System volume's payload (System/Library/CoreServices,
# boot.efi, dyld cache), a recovery-only disk carries BaseSystem and no System.
#
# usage: inspect-installed-disk.sh <data.img>
set -uo pipefail
DISK="${1:?disk image}"

echo "=== $DISK ==="
ls -la "$DISK"
du -h "$DISK"

echo
echo "=== GPT ==="
sgdisk -p "$DISK" 2>/dev/null || echo "(sgdisk unavailable)"

# 4 GiB is enough to cover the APFS container superblock, the volume
# superblocks and the start of the System volume payload; a full read of 27 GiB
# would cost minutes for no extra signal.
echo
echo "=== strings in the first 4 GiB, counted ==="
dd if="$DISK" bs=1M count=4096 2>/dev/null \
  | strings -n 6 \
  | grep -Eo 'Macintosh HD|BaseSystem|Preboot|Recovery|System/Library/CoreServices|boot\.efi|com\.apple\.recovery|Install macOS|Apple_APFS|NXSB|apfs' \
  | sort | uniq -c | sort -rn | head -25 || true

# The decisive pair. A finished macOS has a System volume with CoreServices and a
# boot.efi inside it; a recovery-only disk has neither.
echo
echo "=== verdict ==="
slice=$(dd if="$DISK" bs=1M count=4096 2>/dev/null | strings -n 6)

has_system=0
has_coreservices=0

has_installer=0
case "$slice" in *"System/Library/CoreServices"*) has_coreservices=1 ;; esac
case "$slice" in *"Macintosh HD"*)                  has_system=1 ;; esac
case "$slice" in *"BaseSystem"*)                    has_installer=1 ;; esac
case "$slice" in *"Install macOS"*)                 has_installer=1 ;; esac

echo "System/Library/CoreServices present: $has_coreservices"
echo "Macintosh HD label present:          $has_system"
echo "BaseSystem/Install macOS present:    $has_installer"
echo
if [ "$has_coreservices" = "1" ] && [ "$has_system" = "1" ]; then
  echo "VERDICT: a real macOS system is on the disk -> the booter is the problem"
elif [ "$has_installer" = "1" ] && [ "$has_coreservices" = "0" ]; then
  echo "VERDICT: recovery only, no System volume -> startosinstall never installed"
else
  echo "VERDICT: inconclusive - neither a System payload nor clear recovery media"
fi
