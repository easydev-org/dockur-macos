#!/usr/bin/env python3
"""Compare the OpenCore drivers a config asks for against what the image has.

    check-drivers.py <config.plist> <listing.txt>

listing.txt is the output of `ls /opencore/release/EFI/OC/Drivers/` inside the
launcher image, one file per line.

This answers one question without inference: OpenCore cannot show a macOS volume
unless it can read APFS. If the config enables an APFS driver the image does not
ship, the picker will be empty no matter what is on the disk.
"""
import plistlib
import sys


def main():
    cfg_path, listing_path = sys.argv[1], sys.argv[2]
    cfg = plistlib.load(open(cfg_path, "rb"))
    uefi = cfg.get("UEFI", {})

    print("=== UEFI/Drivers in config ===")
    for dr in uefi.get("Drivers", []):
        print("  %-30s Enabled=%s" % (dr.get("Path"), dr.get("Enabled")))

    print()
    print("=== UEFI/APFS ===")
    for k, v in sorted(uefi.get("APFS", {}).items()):
        print("  %-18s= %s" % (k, v))
    print("  ConnectDrivers   =", uefi.get("ConnectDrivers"))

    have = set()
    try:
        have = {l.strip() for l in open(listing_path) if l.strip()}
    except FileNotFoundError:
        print()
        print("(no driver listing available)")

    want = {dr.get("Path") for dr in uefi.get("Drivers", []) if dr.get("Enabled")}

    print()
    print("=== comparison ===")
    print("  on the ESP        :", sorted(have) or "(unknown)")
    print("  config wants      :", sorted(want))
    if have:
        missing = sorted(want - have)
        print("  MISSING           :", missing or "none")

    apfs_present = [x for x in have if "apfs" in x.lower()]
    apfs_wanted = [x for x in want if "apfs" in x.lower()]
    print("  APFS on ESP       :", apfs_present or "NONE")
    print("  APFS in config    :", apfs_wanted or "NONE")

    # A smaller check that matters just as much: is APFS jumpstart on?
    apfs = uefi.get("APFS", {})
    print("  EnableJumpstart   :", apfs.get("EnableJumpstart"))


main()
