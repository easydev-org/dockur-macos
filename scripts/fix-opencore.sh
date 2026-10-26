#!/usr/bin/env bash
# Rewrite the OpenCore config inside boot.img so the guest boots macOS by itself.
#
# WHY. The bake produced a boot.img whose config keeps the picker on screen:
#
#   ShowPicker     = true        a menu waits for a keypress
#   HideAuxiliary  = false       so Reset NVRAM and Toggle SIP are listed
#
# which is literally what the screenshot showed - two "exec" tiles for Reset
# NVRAM and Toggle SIP, no macOS volume - and it never reached the desktop.
# assets/config.plist in the easysandbox-macos tree sets the opposite:
#
#   ShowPicker     = false       boot immediately
#   Timeout        = 2
#   HideAuxiliary  = true
#   ScanPolicy     = 0           scan everything
#   SecureBootModel= Disabled

# plus the kexts macOS needs under KVM (Lilu, VirtualSMC, WhateverGreen,
# VoodooPS2, AppleMCEReporterDisabler) and VMHide, which hides the hypervisor.
#
# Machine identity is NOT taken from that template. macOS ties the installed
# system to the serial, board id, ROM and UUID it was installed with, so the
# values baked next to the disk must be carried over - otherwise the system sees
# different hardware. dockur leaves them in macos.id / macos.mlb and prints
# Model/Rom/Serial/Board at install time.
#
# usage: fix-opencore.sh <vmdir> <template-config.plist>

set -euo pipefail

VMDIR="${1:?vmdir}"
TEMPLATE="${2:?template config.plist}"
IMG="$VMDIR/boot.img"

[ -s "$IMG" ] || { echo "missing $IMG" >&2; exit 1; }
[ -s "$TEMPLATE" ] || { echo "missing $TEMPLATE" >&2; exit 1; }

# The identity dockur used. Kept in these small files next to the disk.
ID_FILE="$VMDIR/macos.id"
MLB_FILE="$VMDIR/macos.mlb"
MAC_FILE="$VMDIR/macos.mac"

echo "=== identity on disk ==="
cat "$ID_FILE" 2>/dev/null || echo "(no macos.id)"
cat "$MLB_FILE" 2>/dev/null || echo "(no macos.mlb)"

# Read the identity the installer used, from the OpenCore config currently in
# boot.img. That is the authoritative source: whatever it says is what macOS was
# installed against. The template only supplies the *policy* keys.
WORK=/tmp/ocfix
rm -rf "$WORK"; mkdir -p "$WORK"

# boot.img is a GPT disk with one FAT partition starting at 1 MiB (which is what
# dockur builds), so mtools can address it with an offset rather than mounting.
OFFSET=${OC_OFFSET:-1M}
export MTOOLS_SKIP_CHECK=1

echo "=== current config inside boot.img ==="
mcopy -i "$IMG@@$OFFSET" ::/EFI/OC/config.plist "$WORK/current.plist" \
  || { echo "cannot read ::/EFI/OC/config.plist" >&2; exit 1; }
ls -la "$WORK/current.plist"

python3 - "$WORK/current.plist" "$TEMPLATE" "$WORK/merged.plist" <<'PY'
import plistlib, sys

cur_path, tpl_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
cur = plistlib.load(open(cur_path, "rb"))
tpl = plistlib.load(open(tpl_path, "rb"))

# Identity from the running config - what the installation was tied to.
cur_gen = cur.get("PlatformInfo", {}).get("Generic", {})
keep = {k: cur_gen.get(k) for k in
        ("SystemProductName", "SystemSerialNumber", "MLB", "SystemUUID", "ROM")}

# Boot policy from the template - that is the part that was wrong.
misc = tpl.setdefault("Misc", {})
boot = misc.setdefault("Boot", {})
boot["ShowPicker"] = False
boot["HideAuxiliary"] = True
boot["Timeout"] = 2
boot["PickerMode"] = "Builtin"      # Builtin needs no OpenCanopy at boot time
misc.setdefault("Security", {})["ScanPolicy"] = 0
misc["Security"]["SecureBootModel"] = "Disabled"
misc["Security"]["DmgLoading"] = "Signed"

# Re-apply the identity on top of the template.
gen = tpl.setdefault("PlatformInfo", {}).setdefault("Generic", {})
for k, v in keep.items():
    if v is not None:
        gen[k] = v

plistlib.dump(tpl, open(out_path, "wb"))

print("kept identity:", {k: (v.hex() if isinstance(v, bytes) else v) for k, v in keep.items() if v is not None})
print("boot policy :", {k: boot[k] for k in ("ShowPicker", "HideAuxiliary", "Timeout", "PickerMode")})
print("scan policy :", misc["Security"]["ScanPolicy"], misc["Security"]["SecureBootModel"])
PY

# Put the merged config back. -o overwrites.
mcopy -o -i "$IMG@@$OFFSET" "$WORK/merged.plist" ::/EFI/OC/config.plist

echo "=== verify it landed ==="
mcopy -i "$IMG@@$OFFSET" ::/EFI/OC/config.plist "$WORK/check.plist"
if cmp -s "$WORK/merged.plist" "$WORK/check.plist"; then
  echo "config.plist replaced successfully"
else
  echo "replacement did not stick" >&2
  exit 1
fi

python3 - "$WORK/check.plist" <<'PY'
import plistlib, sys
d = plistlib.load(open(sys.argv[1], "rb"))
b = d["Misc"]["Boot"]
print("in-image policy: ShowPicker=%s HideAuxiliary=%s Timeout=%s PickerMode=%s"
      % (b.get("ShowPicker"), b.get("HideAuxiliary"), b.get("Timeout"), b.get("PickerMode")))
PY
