#!/usr/bin/env python3
"""Print the driver list a config.plist ends up with, and whether APFS is among them.

    check-config-drivers.py <config.plist>

Used after patching to prove the change landed in the image rather than only in
a temp file.
"""
import plistlib
import sys


def main():
    d = plistlib.load(open(sys.argv[1], "rb"))
    uefi = d.get("UEFI", {})
    drivers = [x.get("Path") if isinstance(x, dict) else x for x in uefi.get("Drivers", [])]
    print("drivers:", drivers)
    print("apfs listed:", any("apfs" in (x or "").lower() for x in drivers))
    print("ConnectDrivers:", uefi.get("ConnectDrivers"))


main()
