#!/usr/bin/env python3
"""Drive the macOS recovery installer to the Setup Assistant.

WHY THIS EXISTS. The unattended path (MANUAL=N) patches rc.cdrom.sh inside the recovery
image, and that patch matches macOS 14's text only, so on 15 and 26 it refuses with
"Expected exactly one rc.cdrom.sh bootstrap, found 0". Upstream ships the same needle,
so this is not a version input anyone can flip - the automated branch has never been
exercised on those releases.

So the screens are driven by looking at them. Everything below was measured from
captured frames rather than guessed, and the three versions turn out to be nearly
identical:

  option rows        y = 244 / 327 / 420 / 512   (14: 241/334/430/526, 26: 238/334/430/525)
  Continue           (1142, 588)                 (14: 1137,591  26: 1129,600)
  install entry text  "Reinstall macOS Sonoma" / "Sequoia" / "Tahoe"
  browser entry text  "Safari" on 14 and 15, "Web Browser" on 26

THE ONE THING THAT MATTERED MOST. Disk Utility's sidebar lists VOLUMES, and a freshly
created 64 GiB disk has none - it simply is not in the list. Every attempt to find it
there failed because it was never on screen. The View menu's "Show All Devices" is what
puts physical disks into the sidebar, and that one click is the whole prerequisite for
erasing anything.

The sequence is therefore:

  recovery menu        select Disk Utility, press Continue
  Disk Utility         View -> Show All Devices
                       select the largest physical disk  (the 64 GiB one; the recovery
                       and boot images are read-only and much smaller)
                       Erase -> APFS -> confirm
                       quit
  recovery menu        select Reinstall macOS, press Continue
  installer panes      agree/continue until Setup Assistant appears

Screen state is detected from the presence of structural elements, never from a label
that also appears in prose. That distinction cost several rounds: the recovery menu
contains the sentence "Disk Utility - Repair or erase a disk using Disk Utility", so any
test for the words "disk utility" or "erase" was true before the tool had even opened.
"""

import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Reuse Screen from drive-ocr.py: it already handles screendump path mapping (qemu
# resolves paths inside its container, where frames is mounted at /frames), tesseract
# TSV parsing with a confidence floor, and shift chords for capitals.
from importlib.machinery import SourceFileLoader

import qmp as qmpmod

_drive_ocr = SourceFileLoader("drive_ocr", os.path.join(HERE, "drive-ocr.py")).load_module()
Screen = _drive_ocr.Screen

W, H = 1920, 1080

# The recovery menu's own Continue. Every entry there is opened by selecting it and then
# pressing this; clicking the entry alone only highlights it.
CONTINUE_MENU = (1142, 588)

# The recovery list, measured with tesseract on a captured frame. Every entry's label is
# left-aligned at x=837 inside the window rather than centred, and the rows are 92px
# apart:
#
#   Restore from Time Machine   y=232   x=837-1002
#   Reinstall macOS Sequoia     y=324   x=837-986
#   Safari                      y=416   x=837-870
#   Disk Utility                y=508   x=837-901
#   Continue                    y=585   x=1115-1169
#
# ROW_X was 958, read off a picture instead of through OCR, with an 80px tolerance - so
# every one of these was excluded, since |837-958| is 121. row() could never match, and
# the driver sat on "waiting for the recovery menu" while the menu was right there.
ROW_Y = (232, 324, 416, 508)
ROW_X = 837
ROW_TOLERANCE = 40

# Disk Utility's View menu, measured from a frame captured with the menu open. The item
# that matters is Show All Devices, and its position matters more than it looks: it was
# previously guessed at (210,190), which lands outside the menu and simply closes it - so
# the switch was never flipped, the 64 GiB disk never entered the sidebar, and every erase
# attempt had nothing to erase.
#
# Measured with tesseract on the captured frame, not by eye - the word boxes come back
# with their own coordinates, so these are the centres of the text rather than a guess:
#
#   Show Only Volumes    y=36  x=294-413   centre (354,  41)
#   Show All Devices     y=58  x=294-397   centre (345,  63)   <- the target
#   Show APFS Snapshots  y=91  x=295-430   centre (362,  96)
#   Hide Sidebar         y=124             centre (333, 129)
#   Hide Toolbar         y=157             centre (333, 162)
#   Enter Full Screen    y=201             centre (345, 206)
#
# The toolbar row, same frame, all at y=332:
#
#   Volume 1101-1138   First Aid 1157-1179   Partition 1215-1258
#   Erase  1276-1304   Restore   1322-1361   Unmount   1377-1423   Info 1470
#
# And the sidebar, whose rows are read reliably once Show All Devices is on:
#
#   Internal                        y=350
#   Apple Inc. VirtIO Block Media   y=374
#   Apple Inc. VirtIO Block Media   y=402
#   disk2s1                         y=418
VIEW_MENU_BUTTON = (309, 11)
SHOW_ALL_DEVICES = (345, 63)
ERASE_BUTTON = (1290, 336)
SIDEBAR_X = 380


def words_of(screen, prefix, tag):
    return screen.text(screen.grab("%s-%s" % (prefix, tag)))


def joined(words):
    return " ".join(w[0] for w in words).lower()


def row(words, index, *needles):
    """Position of the clickable element for entry `index`, or None.

    `words` come from tesseract as individual WORDS, not lines: "Disk Utility" arrives as
    ("Disk", 837, 508) and ("Utility", 866, 508). Matching a needle against a single word
    therefore never succeeds for a two-word label, which is what kept this on "waiting for
    the recovery menu" - the phrase "disk utility" is in no single token.

    So the needles are single words, and the ROW is pinned by y: each of the four entries
    sits at a known height, and the description is 18px below its title, so a tolerance of
    40 keeps the match on the title. The returned point is the left edge of the label plus
    a little, which is inside the row's clickable area.
    """
    y_expected = ROW_Y[index]
    best = None
    for word, x, y, w, _h in words:
        low = word.lower().strip(".,:;")
        if not any(low == n or low.startswith(n) for n in needles):
            continue
        if abs(y - y_expected) > 40:
            continue
        if best is None or abs(y - y_expected) < abs(best[1] - y_expected):
            # Click near the middle of the label, not on its first glyph.
            best = (x + w // 2, y)
    return best


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

    # QMP, with the .qmp suffix. drive-in-container.sh creates two sockets and only the
    # .qmp one speaks JSON; connecting to the plain monitor gives a text prompt whose
    # first character is not '{', which surfaces as a JSONDecodeError that looks like an
    # empty response and sent several rounds chasing qemu instead of the path.
    qmp_path = qmp_socket if qmp_socket.endswith(".qmp") else qmp_socket + ".qmp"
    screen = None
    for attempt in range(1, 31):
        try:
            screen = Screen(qmp_path)
            log("qmp connected on attempt %d" % attempt)
            break
        except Exception as exc:
            log("qmp not ready (%d): %s" % (attempt, type(exc).__name__))
            time.sleep(10)
    if screen is None:
        log("qmp never became ready")
        return 1

    # The recovery boot shows nothing for minutes. Wait for words rather than for a
    # fixed delay.
    deadline = time.time() + 1500
    words = []
    while time.time() < deadline:
        words = words_of(screen, prefix, "boot")
        if len(words) > 3:
            log("readable: %s" % joined(words)[:140])
            break
        time.sleep(20)
    if not words:
        log("nothing readable after 25 minutes")
        screen.close()
        return 1

    erased = False
    installing = False
    in_view_menu = False
    erase_clicked = False

    for step in range(1, 200):
        words = words_of(screen, prefix, "s%02d" % step)
        text = joined(words)
        log("step %d: %s" % (step, text[:150] or "(nothing)"))

        if not text:
            time.sleep(5)
            continue

        if "country or region" in text or "select your country" in text:
            log("SETUP ASSISTANT REACHED at step %d" % step)
            screen.close()
            return 0

        # --- Disk Utility -------------------------------------------------------
        # Its toolbar is the natural signal, but it does not always read cleanly: one
        # captured frame returned the row as "view ad taro le" - First Aid as "fa",
        # Partition as "taro", Volume gone - which is fewer than three toolbar words and
        # left the driver reporting "waiting for the recovery menu" while Disk Utility was
        # filling the screen.
        #
        # The sidebar is the sturdier evidence. "Apple Inc. VirtIO Block Media" appears in
        # every Disk Utility frame and in none of the recovery menu, which lists four
        # entries and no device names. tesseract renders it "VirtlO" as often as
        # "VirtIO", so both spellings are accepted.
        toolbar = sum(1 for w in text.split()
                      if w in ("volume", "partition", "restore", "unmount", "first", "ald"))
        has_devices = "virtio" in text or "virtlo" in text
        in_du = has_devices or toolbar >= 3 or "physical volume" in text

        if in_du:
            if not erased:
                if not erase_clicked:
                    # Show All Devices. Measured, not guessed: the item is at (310,63) and
                    # the View button at (309,11). The previous guess of (210,190) landed
                    # outside the menu, so clicking it closed the menu and changed
                    # nothing - which is why the 64 GiB disk never appeared in the sidebar
                    # and every erase attempt had nothing to act on.
                    log("opening the View menu at %s" % (VIEW_MENU_BUTTON,))
                    screen.click(VIEW_MENU_BUTTON[0], VIEW_MENU_BUTTON[1])
                    time.sleep(3)
                    log("choosing Show All Devices at %s" % (SHOW_ALL_DEVICES,))
                    screen.click(SHOW_ALL_DEVICES[0], SHOW_ALL_DEVICES[1])
                    time.sleep(5)
                    erase_clicked = True
                    continue

                # Show All Devices has been pressed, so the sidebar now holds physical
                # disks as well as volumes. Three entries appear, and their measured
                # capacities identify them:
                #
                #   Capacity: Unknown    Child count: Not Supported   <- the target
                #   Capacity: 268.4 MB   Child count: 1               <- OpenCore
                #   Capacity: 3.22 GB    Child count: 1               <- recovery image
                #
                # The target does NOT report 64 GB. It has no partition table yet, so
                # Disk Utility has nothing to total and says "Unknown" - which is why the
                # earlier version, looking for "64", never found it and kept reporting
                # "no row reported a large capacity". The absence of both a size and a
                # child is what separates the unformatted disk from the two images.
                rows = [(w[2], w[1], w[0]) for w in words
                        if "virtio" in w[0].lower() or "virtl" in w[0].lower()]
                log("sidebar candidates: %s" % (rows,))

                target_row = None
                for y_centre, x_centre, label in rows:
                    screen.click(x_centre, y_centre)
                    time.sleep(3)
                    probe = words_of(screen, prefix, "probe")
                    right = " ".join(w[0] for w in probe if w[1] > 900)
                    low = right.lower()
                    log("row %s says: %s" % (label, right[:160]))
                    if "capacity: unknown" in low or "child count: not supported" in low:
                        target_row = (x_centre, y_centre)
                        log("the unformatted target disk is the row at (%d,%d)"
                            % target_row)
                        break

                if target_row is None:
                    log("no unformatted device in the sidebar yet")
                    time.sleep(5)
                    continue

                log("clicking Erase at %s" % (ERASE_BUTTON,))
                screen.click(ERASE_BUTTON[0], ERASE_BUTTON[1])
                time.sleep(10)
                continue

            # The erase sheet. Its own Erase commits; it is the lowest such control.
            cands = [(w[1], w[2]) for w in words
                     if w[0].strip().lower().startswith("era") and w[2] > 380]
            if cands:
                target = cands[-1]
                log("confirming Erase at %s" % (target,))
                screen.click(target[0], target[1])
                time.sleep(30)
                erased = True
                continue

            log("leaving Disk Utility")
            qmpmod.key_combo(screen.q, "meta_l", "q")
            time.sleep(12)
            continue

        # --- recovery menu ------------------------------------------------------
        if not erased:
            du = row(words, 3, "disk")
            if du is not None:
                log("opening Disk Utility")
                screen.click(du[0], du[1])
                time.sleep(2)
                screen.click(CONTINUE_MENU[0], CONTINUE_MENU[1])
                time.sleep(15)
                continue
            log("waiting for the recovery menu")
            time.sleep(5)
            continue

        if not installing:
            inst = row(words, 1, "reinstall", "install")
            if inst is not None:
                log("starting the reinstall")
                screen.click(inst[0], inst[1])
                time.sleep(2)
                screen.click(CONTINUE_MENU[0], CONTINUE_MENU[1])
                installing = True
                time.sleep(25)
                continue

        # Installer panes: agree, continue, and the occasional plain Continue.
        for needle in ("agree", "continue", "continue with", "install"):
            hit = None
            for word, x, y, _w, _h in words:
                if needle in word.lower() and y > 300:
                    hit = (x, y)
                    break
            if hit is not None:
                log("advancing at %s (%s)" % (hit, needle))
                screen.click(hit[0], hit[1])
                time.sleep(15)
                break
        else:
            screen.key("ret")
            time.sleep(12)

    log("did not reach Setup Assistant")
    screen.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
