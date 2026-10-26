#!/usr/bin/env python3
"""Ensure apfs.efi is listed and enabled in OpenCore's UEFI/Drivers.

    enable-apfs-driver.py <config.plist>

A driver file copied into EFI/OC/Drivers does nothing until the config lists it.
This adds the entry when missing and flips Enabled on when present, and leaves
everything else in the config untouched - machine identity, boot policy and the
quirk set all stay as they are.
"""
import plistlib
import sys


def main():
    path = sys.argv[1]
    with open(path, "rb") as f:
        cfg = plistlib.load(f)

    uefi = cfg.setdefault("UEFI", {})
    drivers = uefi.setdefault("Drivers", [])

    # OpenCore accepts either bare strings or dicts with Path/Enabled; normalise
    # to dicts so Enabled can be set.
    normalised = []
    for d in drivers:
        if isinstance(d, str):
            normalised.append({"Path": d, "Enabled": True})
        else:
            normalised.append(d)

    have = False
    for d in normalised:
        if d.get("Path", "").lower() == "apfs.efi":
            d["Enabled"] = True
            have = True
            break
    if not have:
        normalised.append({"Path": "apfs.efi", "Enabled": True})

    uefi["Drivers"] = normalised

    # ConnectDrivers must be on or the freshly listed driver is never started.
    uefi["ConnectDrivers"] = True

    with open(path, "wb") as f:
        plistlib.dump(cfg, f)

    print("UEFI/Drivers now:")
    for d in uefi["Drivers"]:
        print("  %-30s Enabled=%s" % (d.get("Path"), d.get("Enabled")))
    print("ConnectDrivers =", uefi.get("ConnectDrivers"))


main()
