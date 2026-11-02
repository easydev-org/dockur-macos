#!/usr/bin/env python3
"""Time a single QMP screendump, so the capture rate can follow measurement."""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qmp


def main():
    if len(sys.argv) < 3:
        sys.exit("usage: probe-screendump.py <qmp-sock> <frames-dir>")
    sock, frames = sys.argv[1], sys.argv[2]
    if not sock.endswith(".qmp"):
        sock += ".qmp"
    q = qmp.QMP(sock)
    target = os.path.join(frames, "_probe.ppm")
    for attempt in range(1, 4):
        started = time.time()
        q.execute("screendump", filename="/frames/_probe.ppm")
        elapsed = time.time() - started
        ok = os.path.exists(target)
        size = os.path.getsize(target) if ok else 0
        print("screendump %d: %.2fs, exists=%s, %d bytes" % (attempt, elapsed, ok, size), flush=True)
        if ok:
            os.remove(target)
        time.sleep(0.5)
    q.close()


if __name__ == "__main__":
    main()
