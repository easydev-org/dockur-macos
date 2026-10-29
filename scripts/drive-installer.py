#!/usr/bin/env python3
"""Drive the macOS *installer* to the Setup Assistant, then stop.

STAGE 1 OF THREE. The unattended path (MANUAL=N) injects a bootstrap script into the
recovery image by binary-patching rc.cdrom.sh, and that patch matches macOS 14 only:

    Recovery bootstrap patch failed: Expected exactly one rc.cdrom.sh bootstrap, found 0

upstream dockur/macos ships the same patch.py with the same SCRIPT_ORIGINAL, so this is
not something a version input can fix - the automated branch has never been exercised
on 15 or 26 by anyone.

So the installer is driven the way the Setup Assistant already is: by looking at the
screen and clicking. This script takes the guest from the recovery boot to the point
where Setup Assistant appears, which is the boundary stage 2 (drive-ocr.py) starts
from.

What it does, in order:

  wait for readable pixels      the recovery boot takes minutes and shows nothing
  pick Disk Utility             the installer's first screen offers four items
  erase the target disk         an empty 64 GiB virtio disk is not formatted
  quit Disk Utility             back to the installer list
  choose Install macOS          and follow it until Setup Assistant appears

Every click is placed from OCR of the live screen rather than from hardcoded
coordinates, because 15 and 26 lay these screens out differently from 14 - which is
what stage 1 needs to tolerate.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Reuse the Screen class from drive-ocr.py rather than re-implementing it: it already
# solves screendump path mapping (qemu resolves paths inside its container, where the
# frames directory is mounted at /frames), the tesseract TSV parsing with a confidence
# threshold, and the shift-chord handling for capitals. A second copy would drift.
from importlib.machinery import SourceFileLoader

_drive_ocr = SourceFileLoader("drive_ocr", os.path.join(HERE, "drive-ocr.py")).load_module()
Screen = _drive_ocr.Screen

W, H = 1920, 1080


def find(words, *needles):
    """Position of the first word containing any needle, or None.

    Screen.text returns (text, x_centre, y_centre, width, height) - five fields, not
    three. Unpacking three here would raise on the first frame.
    """
    for word, x, y, _w, _h in words:
        low = word.lower()
        for needle in needles:
            if needle in low:
                return (x, y)
    return None


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    qmp_socket, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-installer.log"

    def log(msg):
        line = "[inst %s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    # Wait for the QMP handshake, not merely for the socket file.
    #
    # qemu creates the socket before it is ready to speak: drive-in-container.sh
    # reports "qemu is up" as soon as the process exists, and the first connect after
    # that returns an empty read, which surfaces as
    #
    #   json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
    #
    # from inside QMP.__init__ while it parses the greeting. The socket is fine and
    # qemu is fine; it simply has not sent its greeting yet. So the connect is retried
    # until one of them produces a greeting that parses.
    # The .qmp suffix matters. drive-in-container.sh creates TWO sockets:
    #
    #   -monitor "unix:/frames/monitor.sock,..."     HMP, speaks text
    #   -qmp     "unix:/frames/monitor.sock.qmp,..." QMP, speaks JSON
    #
    # Connecting to the first and parsing its output as JSON is exactly what produced
    #   JSONDecodeError: Expecting value: line 1 column 1 (char 0)
    # thirty times over five minutes while qemu was healthy the whole time. drive-ocr
    # appends .qmp inside its own constructor, which is why the desktop stage never hit
    # this; this script took the path verbatim.
    qmp_path = qmp_socket if qmp_socket.endswith(".qmp") else qmp_socket + ".qmp"

    screen = None
    for attempt in range(1, 31):
        try:
            screen = Screen(qmp_path)
            log("qmp connected on attempt %d (%s)" % (attempt, qmp_path))
            break
        except Exception as exc:
            log("qmp not ready (attempt %d): %s: %s"
                % (attempt, type(exc).__name__, str(exc)[:120]))
            time.sleep(10)
    if screen is None:
        log("qmp never became ready; giving up")
        return 1

    # The recovery boot is slow and unreadable for minutes. Waiting for words rather
    # than for a fixed delay is what makes this work across versions and hosts.
    deadline = time.time() + 1500
    words = []
    while time.time() < deadline:
        png = screen.grab("%s-boot" % prefix)
        words = screen.text(png)
        if len(words) > 3:
            log("installer readable after %ds: %s"
                % (int(1500 - (deadline - time.time())),
                   " ".join(w[0] for w in words)[:160]))
            break
        time.sleep(20)
    if not words:
        log("nothing readable after 25 minutes; stopping")
        screen.close()
        return 1

    # The macOS installer offers: Restore from Time Machine, Install macOS,
    # Safari, Disk Utility. The disk must be erased first, so Disk Utility.
    for step in range(1, 60):
        png = screen.grab("%s-s%02d" % (prefix, step))
        words = screen.text(png)
        joined = " ".join(w[0] for w in words).lower()
        log("step %d: %s" % (step, joined[:150] or "(nothing)"))

        # Setup Assistant is the boundary: stage 2 owns everything from here.
        if "country or region" in joined or "select your country" in joined:
            log("SETUP ASSISTANT REACHED at step %d" % step)
            screen.close()
            return 0

        if "disk utility" in joined:
            target = find(words, "disk utility")
            if target:
                log("opening Disk Utility at %s" % (target,))
                screen.click(target[0], target[1])
                time.sleep(10)
            continue

        if "erase" in joined:
            # Disk Utility's Erase button. The destination disk is already selected
            # in its sidebar, so Erase opens a sheet with a format and a name.
            target = find(words, "erase")
            if target:
                log("clicking Erase at %s" % (target,))
                screen.click(target[0], target[1])
                time.sleep(8)
            continue

        if "apfs" in joined and "erase" in joined:
            # The erase sheet. Accept whatever is offered and confirm.
            target = find(words, "erase")
            if target:
                log("confirming Erase at %s" % (target,))
                screen.click(target[0], target[1])
                time.sleep(15)
            continue

        # The install entry, whose label differs by version and was the reason this
        # loop ran fifty-nine steps without moving. macOS 15 says
        #
        #   Reinstall macOS Sequoia - Install a new copy of macOS Sequoia onto your Mac
        #
        # so "install macos" never matches: there is no such phrase on the pane. The
        # word to look for is "reinstall", with "install" as the fallback for versions
        # that word it the other way.
        if "reinstall" in joined or "install macos" in joined:
            target = find(words, "reinstall", "install macos")
            if target:
                log("starting the install at %s" % (target,))
                screen.click(target[0], target[1])
                time.sleep(15)
                continue

        if "continue" in joined or "agree" in joined or "install" in joined:
            target = find(words, "continue", "agree")
            if target:
                log("advancing at %s" % (target,))
                screen.click(target[0], target[1])
                time.sleep(12)
            continue

        # Nothing recognised: press Return, which is the default button on most of
        # these panes, and costs nothing when it is not.
        screen.key("ret")
        time.sleep(10)

    log("did not reach Setup Assistant within 60 steps")
    screen.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
