#!/usr/bin/env python3
"""Record the guest by taking QMP screendumps at a fixed rate.

WHY NOT VNC. Two runs of an RFB recorder connected, completed the handshake, sent
SetPixelFormat and SetEncodings, and then waited 120 seconds for a first framebuffer update
while the guest was drawing happily - the same guest whose frames QMP captures instantly.

WHY THE RATE CAN BE HIGH. The comments elsewhere say a screendump costs 0.2-1.6s, but that
figure came from the OCR driver, whose grab() sleeps 1.5s after each dump. Measured on its
own, a 1920x1080 dump takes 0.01s:

    screendump 1: 0.01s, exists=True, 6220817 bytes

so 5 fps costs about five percent of one core and cannot disturb the action timing.

The frames are written as cap-NNNNN.ppm and muxed to video by the caller.
"""

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qmp


def log(msg):
    print("[rec %s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--fps", type=int, default=5)
    ap.add_argument("--seconds", type=int, default=2400)
    args = ap.parse_args()

    sock = args.sock if args.sock.endswith(".qmp") else args.sock + ".qmp"
    os.makedirs(args.outdir, exist_ok=True)
    interval = 1.0 / float(max(1, args.fps))
    deadline = time.time() + args.seconds

    client = qmp.QMP(sock)
    log("connected, capturing at %d fps to %s" % (args.fps, args.outdir))

    count = 0
    failures = 0
    while time.time() < deadline:
        started = time.time()
        count += 1
        leaf = "cap-%05d.ppm" % count
        host = os.path.join(args.outdir, leaf)
        try:
            client.execute("screendump", filename="/frames/" + leaf)
        except Exception as exc:
            failures += 1
            log("dump %d failed: %s" % (count, exc))
            if failures > 20:
                break
            time.sleep(0.5)
            continue
        spent = time.time() - started
        if spent < interval:
            time.sleep(interval - spent)
        if count % 60 == 0:
            log("%d frames, last dump %.3fs" % (count, spent))

    try:
        client.close()
    except Exception:
        pass
    log("finished after %d frames, %d failures" % (count, failures))
    return 0 if count > 1 else 1


if __name__ == "__main__":
    sys.exit(main())
