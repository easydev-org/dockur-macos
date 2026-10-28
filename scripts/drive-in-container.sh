#!/bin/sh
# Boot the golden disk and walk Setup Assistant, from inside the launcher image.
#
# THIS IS THE POINT OF RUNNING HERE. Earlier versions copied qemu-system-x86_64
# out of this image onto the runner, which then failed library by library:
# libcapstone.so.5 first, then libjpeg.so.62, and libc was queued behind them.
# The image is a complete environment for this binary; the runner is not, and
# reassembling it dependency by dependency was never going to end.
#
# So qemu runs where it belongs, and it finds /dev/kvm through --device, the disk
# through /vm, and writes frames to /frames which is the host's bind mount.
#
# The interface is entirely QMP: screenshots via screendump, clicks via
# input-send-event with absolute axes. HMP was tried and does not work here -
# its mouse_move carries relative deltas and the guest has a usb-tablet.
set -eu

VMDIR=/vm
OUT=/frames
MON="$OUT/monitor.sock"
LOG="$OUT/drive.log"

log() {
  printf '[drive %s] %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$LOG"
}

[ -s "$VMDIR/data.img" ] || { log "no disk at $VMDIR/data.img"; exit 1; }
for f in boot.img macos.rom macos.vars; do
  [ -s "$VMDIR/$f" ] || { log "missing $VMDIR/$f"; exit 1; }
done
log "disk $(du -h "$VMDIR/data.img" | cut -f1), starting qemu"

CPU="Skylake-Client-v4,kvm=on,l3-cache=on,+hypervisor,+invtsc,vendor=GenuineIntel,vmx=off,vmware-cpuid-freq=on,-pdpe1gb,-spec-ctrl,+pcid,+invpcid,-tsc-deadline,+xsavec,+xsaves,+ssse3,+sse4.2,+popcnt,+avx,+avx2,+aes,+fma,+bmi1,+bmi2,+smep,+xsave,+xsaveopt,+xgetbv1,+movbe,+rdrand,check"

rm -f "$MON" "$MON.qmp"

# /vm is a writable bind of the host's vm directory, so qemu edits the disk in
# place and the result is already on the host when this exits. Copying 11 GiB into
# the container first would double the disk requirement for no benefit.

qemu-system-x86_64 \
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
  -monitor "unix:$MON,server=on,wait=off,nodelay=on" \
  -qmp "unix:$MON.qmp,server=on,wait=off" \
  -serial mon:stdio \
  -device nec-usb-xhci,id=xhci \
  -device usb-kbd,bus=xhci.0 \
  -global nec-usb-xhci.msi=off \
  -device usb-tablet \
  -netdev user,id=hostnet0,hostfwd=tcp::2222-:22 \
  -device virtio-net-pci,id=net0,netdev=hostnet0,romfile=,mac=00:16:CB:53:E9:8C \
  -drive file="$VMDIR/data.img",id=data3,format=raw,cache=none,aio=native,discard=unmap,detect-zeroes=on,if=none \
  -device virtio-blk-pci,drive=data3,bus=pcie.0,addr=0xa,iothread=io2,bootindex=3 \
  -object iothread,id=io2 \
  -drive file="$VMDIR/boot.img",id=OpenCore,format=raw,cache=unsafe,readonly=on,if=none \
  -device virtio-blk-pci,drive=OpenCore,bus=pcie.0,addr=0x5,bootindex=9 \
  -rtc base=utc \
  -smbios type=2 \
  -global ICH9-LPC.disable_s3=1 \
  -global ICH9-LPC.disable_s4=1 \
  -global ICH9-LPC.acpi-pci-hotplug-with-bridge-support=off \
  -device isa-applesmc,osk='ourhardworkbythesewordsguardedpleasedontsteal(c)AppleComputerInc' \
  -drive if=pflash,format=raw,readonly=on,file="$VMDIR/macos.rom" \
  -drive if=pflash,format=raw,file="$VMDIR/macos.vars" \
  -object rng-random,id=objrng0,filename=/dev/urandom \
  -device virtio-rng-pci,rng=objrng0,id=rng0,bus=pcie.0 \
  -device virtio-balloon-pci,id=balloon0,bus=pcie.0 \
  -fw_cfg name=opt/org.tianocore/UninstallMemAttrProtocol,string=y \
  > "$OUT/qemu.log" 2>&1 &

QEMU_PID=$!
log "qemu pid $QEMU_PID"
sleep 20
if ! kill -0 "$QEMU_PID" 2>/dev/null; then
  log "qemu died at startup:"
  tail -30 "$OUT/qemu.log" || true
  exit 1
fi

# Hand the QMP socket back to the caller and stay alive.
#
# The DRIVER does not run here. This image is a complete environment for qemu and a
# poor one for everything else - it is Alpine, with no OCR tooling and no easy way
# to add any. The host has apt. So the split is: qemu stays where its libraries
# live, the driver runs on the host where its tools live, and they meet at the QMP
# socket on the bind-mounted frames directory. A Unix socket survives that mount,
# so the host can speak QMP to a process in here without any port forwarding.
log "qemu is up; QMP socket at $MON.qmp is visible to the host through the bind mount"
while kill -0 "$QEMU_PID" 2>/dev/null; do
  sleep 5
done
log "qemu exited"
