#!/usr/bin/env python3
"""Ask qemu's HMP monitor for a screendump.

Run inside the container, where the monitor socket lives. The launcher image is Alpine
with a thin toolset, so this does not assume socat or nc exists - only python3, which
dockur's own scripts already require.

usage: shot.py <path-inside-container>
"""

import socket
import sys
import time


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(15)
    try:
        s.connect("/run/shm/monitor.sock")
    except Exception as exc:
        print("connect failed: %s" % exc, file=sys.stderr)
        return 1

    # HMP is line-oriented plain text: no JSON, no capabilities handshake. That is the
    # whole reason this route is used for capture - the QMP path needed a suffix, a
    # greeting read and a retry loop before it would answer at all.
    try:
        s.sendall(("screendump %s\n" % path).encode())
    except Exception as exc:
        print("write failed: %s" % exc, file=sys.stderr)
        return 1

    # The dump is written asynchronously; give qemu a moment, then drain whatever reply
    # arrived so the socket closes cleanly.
    time.sleep(1.5)
    try:
        s.recv(4096)
    except Exception:
        pass
    s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
