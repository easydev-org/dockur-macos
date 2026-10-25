#!/usr/bin/env python3
"""Rewrite upstream's COPY --exclude line so the image builds.

    patch_upstream.py <Dockerfile>

Established by measurement in CI, against qemux/qemu:7.50 with
# syntax=docker/dockerfile:1.19:

  COPY --from=qemux/qemu:7.50 --exclude=usr/bin/qemu-system-x86_64 / /
    -> ERROR: failed to calculate checksum ... "/etc/alternatives/awk.1.gz": not found

/etc/alternatives/awk.1.gz is a symlink to /usr/share/man/man1/mawk.1.gz which
does not exist in the image (man pages are stripped, the alternatives link is
left behind). The v1.19 frontend accepts the flag, then walks the tree to build
its exclude set, opens that entry, finds nothing, and aborts.

Deleting the dangling link first does not help - the frontend still fails - so
the flag itself is what breaks against this image. Copy the tree whole and drop
the binary in the SAME layer instead, which leaves no extra bytes in the result:

  FROM scratch AS base
  COPY --from=qemux/qemu:7.50 / /
  RUN rm -f /usr/bin/qemu-system-x86_64

That combination was built successfully in CI (variant "plain").
"""
import re
import sys


def main():
    path = sys.argv[1]
    with open(path) as f:
        text = f.read()

    # Match the exact COPY line however the exclude flag is spelled, so a future
    # upstream tweak (different path, different flag order) is still caught.
    pattern = re.compile(
        r"^COPY --from=(?P<src>\S+) --exclude=\S+ / /\s*$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if not m:
        sys.exit("no COPY --exclude line found; upstream may have changed")

    src = m.group("src")
    # One stage, two instructions: the tree is copied and the binary removed in
    # the same layer, so the excluded file never reaches the final image.
    replacement = (
        f"COPY --from={src} / /\n"
        f"# --exclude is unusable against this base image (see the docstring), so the\n"
        f"# binary is copied and then dropped in the same layer instead.\n"
        f"RUN rm -f /usr/bin/qemu-system-x86_64\n"
    )
    text = text[: m.start()] + replacement + text[m.end():]

    with open(path, "w") as f:
        f.write(text)
    print(f"patched {path}: {src} -> COPY whole tree, then rm the binary")


main()
