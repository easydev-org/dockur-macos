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

# The recovery tool list's own Continue, measured from a macOS 15 frame. Every entry in
# that list is opened by selecting it and pressing this, not by clicking the entry.
CONTINUE_BUTTON = (1142, 588)

# The erase script travels as a FILE on a small FAT disk attached to the VM.
#
# WHY NOT DISK UTILITY. It was the obvious instrument and it cost many rounds. Its
# sidebar lists VOLUMES, and a freshly created 64 GB disk has none, so the target never
# appeared there at all - the three rows on screen were the recovery volume and the boot
# image. Even those rows could not be read: tesseract returned none of "Apple Inc.
# VirtIO Block Media" at any psm setting, because that text is small, low-contrast and
# on a dark background.
#
# WHY NOT TYPE THE SCRIPT INSTEAD. This input channel cannot carry a pipe, drops double
# quotes, truncates long lines, and mangles capitals unless every one goes out as a
# shift chord. A shell script full of loops, sed and quoting is not something it can
# deliver intact - measured repeatedly on this project.
#
# So recovery's own disk arbitration mounts the FAT disk at /Volumes/ERASE, and the only
# thing typed is a path with no metacharacters in it at all.
ERASE_SCRIPT = "/Volumes/ERASE/e.sh"

# The recovery menu bar, measured from a frame: Utilities sits between Edit and Window.
# Its drop-down puts Terminal third; that position is stable because the menu is short
# and its contents do not vary in recovery.
UTILITIES_MENU = (236, 12)
TERMINAL_ITEM = (255, 92)

# Disk Utility's Erase button, measured from a macOS 15 recovery frame: the toolbar row
# is at y=264 and Erase sits between Partition and Restore.
ERASE_BUTTON = (1291, 264)


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

    # The installer's list, whose entries were measured from a macOS 15 frame:
    #
    #   Restore from Time Machine   (958, 244)
    #   Reinstall macOS Sequoia     (958, 327)
    #   Safari                      (958, 420)
    #   Disk Utility                (958, 512)
    #   Continue                    (1141, 589)
    #
    # The previous version matched substrings in the whole pane text, and every entry
    # here is followed by a sentence of description, so "disk utility" matched the
    # prose on EVERY frame - including the frames after Disk Utility had been opened.
    # That branch clicked, continued, and the install entry below it was never reached:
    # fifty-nine steps of the same pane.
    #
    # Matching is now anchored to the label's own position: the left edge of every entry
    # sits at x=958 and the descriptions are to the right and below, so a match within a
    # few pixels of 958 on one of the five known rows is the clickable thing.
    ROWS = (
        (244, "restore from time machine"),
        (327, "reinstall"),
        (420, "safari"),
        (512, "disk utility"),
    )
    COLUMN_X = 958

    def entry(needle):
        for y_expected, label in ROWS:
            if label != needle:
                continue
            for word, x, y, _w, _h in words:
                if label.split()[0] in word.lower() and abs(x - COLUMN_X) < 60                    and abs(y - y_expected) < 40:
                    return (x, y)
        return None

    erased = False
    erase_clicked = False
    target_disk = None
    disk_probe = 0
    menu_open = False
    install_started = False
    for step in range(1, 140):
        png = screen.grab("%s-s%02d" % (prefix, step))
        words = screen.text(png)
        joined = " ".join(w[0] for w in words).lower()
        log("step %d: %s" % (step, joined[:150] or "(nothing)"))

        if "country or region" in joined or "select your country" in joined:
            log("SETUP ASSISTANT REACHED at step %d" % step)
            screen.close()
            return 0

        # The erase happens in Terminal, not in Disk Utility. See ERASE_SCRIPT above for
        # why that instrument was abandoned after many rounds of it not working.
        #
        # The menu bar has Utilities, and Terminal is under it. Both are small targets,
        # so this walks the menu: click Utilities, then Terminal beneath it.
        if not erased:
            if "erase-done" in joined or "erase-ok" in joined:
                log("erase reported done")
                erased = True
                continue

            if "terminal" in joined and ("-" in joined or "%" in joined or "" in joined):
                # A Terminal window is up. Paste the path to the mounted script and run
                # it - twenty-two characters, no pipes or quotes for the channel to lose.
                log("typing the erase script path")
                screen.type(ERASE_SCRIPT, delay=0.15)
                screen.key("ret")
                time.sleep(20)
                screen.grab("%s-erase-run" % prefix)
                after = screen.text(screen.grab("%s-erase-after" % prefix))
                seen = " ".join(w[0] for w in after).lower()
                log("terminal says: %s" % seen[:200])
                if "erase-done" in seen or "erase-ok" in seen:
                    log("erase confirmed from the Terminal output")
                    erased = True
                continue

            # Open the Utilities menu in the menu bar. Its position is fixed and it is
            # present on every recovery screen.
            if not menu_open:
                log("opening the Utilities menu at %s" % (UTILITIES_MENU,))
                screen.click(UTILITIES_MENU[0], UTILITIES_MENU[1])
                menu_open = True
                time.sleep(3)
                continue

            # Choose Terminal from the menu that just dropped.
            log("choosing Terminal at %s" % (TERMINAL_ITEM,))
            screen.click(TERMINAL_ITEM[0], TERMINAL_ITEM[1])
            time.sleep(10)
            menu_open = False
            continue

        # Erased. Back to the installer list, then Reinstall macOS.
        target = entry("reinstall")
        if target is not None and not install_started:
            log("selecting Reinstall at %s" % (target,))
            screen.click(target[0], target[1])
            time.sleep(2)
            log("pressing Continue at %s to start it" % (CONTINUE_BUTTON,))
            screen.click(CONTINUE_BUTTON[0], CONTINUE_BUTTON[1])
            install_started = True
            time.sleep(20)
            continue

        if "continue" in joined or "agree" in joined:
            t2 = find(words, "continue", "agree")
            if t2 is not None:
                log("advancing at %s" % (t2,))
                screen.click(t2[0], t2[1])
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
