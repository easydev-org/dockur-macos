#!/usr/bin/env python3
"""Print the GPT of a raw disk image and the first bytes of each partition.

    disk-layout.py <disk.raw>

No external tools: hand-parses the GPT header and entries, then looks for the
filesystem signatures that distinguish a finished macOS install (an APFS
container with labelled volumes) from Apple's recovery image.
"""
import struct
import sys


def gpt_partitions(path):
    with open(path, "rb") as f:
        f.seek(512)
        hdr = f.read(92)
        if hdr[:8] != b"EFI PART":
            print("no GPT protective header at LBA1")
            return []
        entries_lba = struct.unpack_from("<Q", hdr, 72)[0]
        count = struct.unpack_from("<I", hdr, 80)[0]
        esize = struct.unpack_from("<I", hdr, 84)[0]
        parts = []
        for i in range(min(count, 128)):
            f.seek(entries_lba * 512 + i * esize)
            e = f.read(esize)
            if len(e) < 128 or e[:16] == b"\x00" * 16:
                continue
            first = struct.unpack_from("<Q", e, 32)[0]
            last = struct.unpack_from("<Q", e, 40)[0]
            name = e[56:128].decode("utf-16-le", errors="replace").rstrip("\x00")
            parts.append((i + 1, first, last, name))
        return parts


def main():
    path = sys.argv[1]
    parts = gpt_partitions(path)
    print("=== partitions ===")
    for idx, first, last, name in parts:
        print("  %d: LBA %d..%d  %s  (%d MiB at byte %d)"
              % (idx, first, last, name or "-", (last - first + 1) * 512 // 1048576,
                 first * 512))

    # Apple_APFS puts "NXSB" at offset 32 of the container superblock, and the
    # volume labels are UTF-8 inside the geometry. Recovery media from Apple is
    # an HFS+ image with a BaseSystem, so the strings differ clearly.
    markers = [b"NXSB", b"Apple_APFS", b"Apple_HFS", b"Macintosh HD",
               b"BaseSystem", b"Install macOS", b"Recovery", b"Preboot"]
    print()
    print("=== signatures per partition (first 8 MiB) ===")
    with open(path, "rb") as f:
        for idx, first, last, name in parts:
            off = first * 512
            f.seek(off)
            blob = f.read(min(8 * 1048576, (last - first + 1) * 512))
            found = [m.decode() for m in markers if m in blob]
            print("  part %d (%s): %s" % (idx, name or "-", found or "no known signature"))

    print()
    print("=== verdict ===")
    with open(path, "rb") as f:
        for idx, first, last, name in parts:
            f.seek(first * 512)
            blob = f.read(min(8 * 1048576, (last - first + 1) * 512))
            if b"Macintosh HD" in blob or (b"NXSB" in blob and b"Preboot" in blob):
                print("  part %d looks like a FINISHED macOS (APFS container with volumes)" % idx)
                return
            if b"BaseSystem" in blob:
                print("  part %d looks like Apple RECOVERY media (BaseSystem)" % idx)
                return
    print("  inconclusive - no APFS container volumes or BaseSystem found in the first 8 MiB")


main()
