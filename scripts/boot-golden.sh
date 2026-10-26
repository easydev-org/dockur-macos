#!/usr/bin/env bash
# Boot an installed macOS disk directly, the way easysandbox-macos does.
#
# WHY NOT THE dockur CONTAINER. dockur's entry.sh runs install.sh, and install.sh
# decides whether a disk "has data" by comparing its first 100 KiB against zero:
#
#   hasData() { cmp -s -n 102400 "$source" /dev/zero || rc=$?
#               case "$rc" in 0) return 1 ;; ... esac }
#
# An APFS disk whose first 100 KiB happen to be blank is therefore judged EMPTY
# and reinstalled - install() starts with `rm -f "$dest"`. That is exactly what
# the previous run did: storage climbed to 28500 MiB, dropped to 770 MiB at
# 1380 s, then climbed again. The bake could never settle.
#
# easysandbox-macos avoids this entirely by not using dockur's scripts at all: it
# assembles qemu-system-x86_64 itself and boots the golden disk. This does the
# same, with the argument set copied from a working dockur run minus the install
# medium:
#
#   data.img   the installed system            bootindex=3
#   boot.img   OpenCore, built from assets/    bootindex=9
#   macos.rom  OVMF code      ) pflash, the firmware pair
#   macos.vars OVMF variables )
#
# base.dmg is dropped on purpose: it is the installer, and leaving it attached is
# what lets firmware fall back to installing again.
#
# usage: boot-golden.sh <storage-dir> <monitor-sock> <seconds>

set -euo pipefail

STORAGE="${1:?storage dir}"
MONITOR="${2:?monitor socket}"
SECONDS_TO_RUN="${3:-180}"

VMDIR="$STORAGE"
DISK="$VMDIR/data.img"
BOOT="$VMDIR/boot.img"
ROM="$VMDIR/macos.rom"
VARS="$VMDIR/macos.vars"

for f in "$DISK" "$BOOT" "$ROM" "$VARS"; do
  [ -s "$f" ] || { echo "missing $f" >&2; exit 1; }
done

# Machine identity must stay stable: macOS ties the installation to the serial,
# board id and MAC it was installed with. Reuse what is on the disk's directory,
# generating once if absent.
ident() {
  local file="$VMDIR/$1" gen="$2"
  if [ ! -s "$file" ]; then
    if [ "$gen" = "mac" ]; then
      printf '00:16:CB:%02X:%02X:%02X' $((RANDOM%256)) $((RANDOM%256)) $((RANDOM%256)) > "$file"
    else
      printf '%s' "$(head -c 100 /dev/urandom | od -An -tx1 | tr -d ' \n' | cut -c1-8)" > "$file"
    fi
  fi
  cat "$file"
}
MAC=$(ident mac mac)

# The CPU model matters: dockur uses Skylake-Client-v4 for macOS 14+ with a set
# of flags that compensate for running Intel code on AMD hosts. Copy that list
# rather than inventing one - macOS refuses to boot under many others.
CPU="Skylake-Client-v4,kvm=on,l3-cache=on,+hypervisor,+invtsc,vendor=GenuineIntel,vmx=off,vmware-cpuid-freq=on,-pdpe1gb,-spec-ctrl,+pcid,+invpcid,-tsc-deadline,+xsavec,+xsaves,+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+fma,+bmi1,+bmi2,+smep,+xsave,+xsaveopt,+xgetbv1,+movbe,+rdrand,check"

rm -f "$MONITOR"
echo "booting $DISK (monitor $MONITOR, ${SECONDS_TO_RUN}s)"

# Prefer a qemu supplied beside this script - the workflow copies one out of the
# launcher image - over whatever is on PATH. Installing qemu from apt cost 1154
# seconds on a previous run, and the image already ships the binary together with
# /usr/share/qemu, so there is nothing to fetch.
QEMU="$(cd "$(dirname "$0")/.." && pwd)/qemu/qemu-system-x86_64"
if [ ! -x "$QEMU" ]; then
  QEMU="$(command -v qemu-system-x86_64 || true)"
fi
[ -n "$QEMU" ] || { echo "no qemu-system-x86_64 available" >&2; exit 1; }
echo "using qemu: $QEMU"

# -L points at the firmware directory that came with that qemu; without it the
# binary looks for its BIOS/option ROMs in the build-time prefix.
QEMU_SHARE="$(dirname "$QEMU")/share"

"$QEMU" \
  -L "$QEMU_SHARE" \
  -nodefaults \
  -machine type=q35,smm=off,graphics=off,vmport=off,dump-guest-core=off,hpet=off,accel=kvm \
  -enable-kvm \
  -global kvm-pit.lost_tick_policy=discard \
  -cpu "$CPU" \
  -smp 4,sockets=1,dies=1,cores=4,threads=1 \
  -m 8G \
  -name macOS,process=macos \
  -display none \
  -vga vmware \
  -monitor "unix:$MONITOR,server=on,wait=off,nodelay=on" \
  -qmp "unix:${MONITOR}.qmp,server=on,wait=off" \
  -serial mon:stdio \
  -device nec-usb-xhci,id=xhci \
  -device usb-kbd,bus=xhci.0 \
  -global nec-usb-xhci.msi=off \
  -device usb-tablet \
  -netdev user,id=hostnet0 \
  -device virtio-net-pci,id=net0,netdev=hostnet0,romfile=,mac="$MAC" \
  -drive "file=$DISK,id=data3,format=raw,cache=none,aio=native,discard=unmap,detect-zeroes=on,if=none" \
  -device virtio-blk-pci,drive=data3,bus=pcie.0,addr=0xa,iothread=io2,bootindex=3 \
  -object iothread,id=io2 \
  -drive "file=$BOOT,id=OpenCore,format=raw,cache=unsafe,readonly=on,if=none" \
  -device virtio-blk-pci,drive=OpenCore,bus=pcie.0,addr=0x5,bootindex=9 \
  -rtc base=utc \
  -smbios type=2 \
  -global ICH9-LPC.disable_s3=1 \
  -global ICH9-LPC.disable_s4=1 \
  -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off \
  -device isa-applesmc,osk=ourhardworkbythesewordsguardedpleasedontsteal\(c\)AppleComputerInc \
  -drive if=pflash,format=raw,readonly=on,file="$ROM" \
  -drive if=pflash,format=raw,file="$VARS" \
  -object rng-random,id=objrng0,filename=/dev/urandom \
  -device virtio-rng-pci,rng=objrng0,id=rng0,bus=pcie.0 \
  -device virtio-balloon-pci,id=balloon0,bus=pcie.0 \
  -fw_cfg name=opt/org.tianocore/UninstallMemAttrProtocol,string=y \
  > /tmp/qemu-boot.log 2>&1 &

# Capture the REAL qemu pid, not the pid of this script or of sudo: killing the
# wrapper leaves qemu holding a write lock on the disk, and qemu-img then fails
# with 'Failed to get shared "write" lock'. $! is qemu only when qemu is the
# foreground child, so look it up by name after it starts.
QPID=""
for _ in $(seq 1 20); do
  QPID=$(pgrep -f "qemu-system-x86_64.*$DISK" | head -1 || true)
  [ -n "$QPID" ] && break
  sleep 0.5
done
echo "$QPID" > /tmp/qemu-boot.pid
echo "qemu pid ${QPID:-unknown}"

# Watch the framebuffer settle: the point of this script is that a pre-installed
# disk comes up in a minute or two, not fifty.
end=$((SECONDS + SECONDS_TO_RUN))
while [ "$SECONDS" -lt "$end" ]; do
  pgrep -f "qemu-system-x86_64.*$DISK" >/dev/null 2>&1 || { echo "qemu exited early"; tail -40 /tmp/qemu-boot.log; exit 1; }
  sleep 10
done

if pgrep -f "qemu-system-x86_64.*$DISK" >/dev/null 2>&1; then echo "qemu still running"; else echo "qemu stopped"; fi
