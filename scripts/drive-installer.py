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
SHOW_ALL_DEVICES = (345, 63)
ERASE_BUTTON = (1290, 318)
SIDEBAR_X = 380

# Sidebar row heights and the x to click inside them, from measured frames. The rows sit
# under the "Internal" heading and are 28px apart; x=540 is inside the labels.
SIDEBAR_ROWS_Y = (379, 407, 435)
SIDEBAR_CLICK_X = 540


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
            best = (x, y)
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
    erase_attempts = 0

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
                    # The View control is a BUTTON IN THE WINDOW TOOLBAR, at about
                    # (707, 339) - tesseract reads it as "iew" at x=700,y=334 - not a
                    # menu-bar item. The menu bar at the top of the screen also has a
                    # "View" word on a different window state, and clicking there does
                    # nothing here; (309,11) was that mistake and it silently failed,
                    # leaving Show Only Volumes in force and the sidebar holding just the
                    # k2s1 volume.
                    #
                    # So it is located from the frame: a word ending in "iew" below the
                    # menu bar. That survives the truncation tesseract applies to it.
                    # tesseract renders this button as "iew" on one frame and "iow" on
                    # another - the capital V is dropped and the middle vowel guessed.
                    # Matching a suffix is therefore too strict. What is stable is that
                    # it is the leftmost toolbar control, sitting after the window title
                    # and before Volume, at y~334. Both spellings are accepted and the
                    # first candidate in that band wins.
                    view_btn = None
                    for word, x, y, w, _h in words:
                        low = word.strip().lower()
                        if low in ("iew", "iow", "view", "vigw", "lew") and 300 < y < 380:
                            view_btn = (x, y)
                            break
                    if view_btn is None:
                        # Fallback: the toolbar's own geometry. Volume is at x=1101, so
                        # View - the first control - is around x=707, y=334.
                        view_btn = (707, 339)
                        log("View not matched; using the measured fallback %s" % (view_btn,))
                    if view_btn is None:
                        log("View button not found in this frame: %s" % text[:120])
                        time.sleep(5)
                        continue
                    log("opening the View menu at %s" % (view_btn,))
                    screen.click(view_btn[0], view_btn[1])
                    time.sleep(3)
                    log("choosing Show All Devices at %s" % (SHOW_ALL_DEVICES,))
                    screen.click(SHOW_ALL_DEVICES[0], SHOW_ALL_DEVICES[1])
                    time.sleep(6)
                    erase_clicked = True
                    continue
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
                # The sidebar legs under "Internal" are SIBLINGS - two physical disks
                # and one volume - not a tray with a child. Verified by clicking: the
                # frame after a click at (540,379) reports
                #
                #   Apple Inc. VirtIO Block Media  Uninitialized  Capacity: Unknown
                #   Child count: Not Supported     Device: disk0
                #
                # which is the 64 GB target, and Erase becomes enabled. Row 2 is the
                # OpenCore image and row 3 is disk1s1, the 2.88 GB read-only recovery
                # volume that Disk Utility selects by default - erasing THAT would delete
                # the installer the guest is running from.
                #
                # Rows are addressed by position rather than by text: the detail pane
                # repeats "VirtIO Block Media" as the selection's title, so a text search
                # finds those words on both sides of the divider and was clicking x=927 in
                # the pane. Each row is then confirmed by reading the pane back.
                rows = [(y, SIDEBAR_CLICK_X, "row@%d" % y)
                        for y in SIDEBAR_ROWS_Y]
                log("sidebar candidates: %s" % (rows,))

                # x>700 is the detail pane; the sidebar ends around x=660. The earlier
                # threshold of 900 cut the pane off before its own labels, which start at
                # x=708 for "Capacity:" and x=781 for the device name, so the capacity
                # was never actually read.
                target_row = None
                for y_centre, x_centre, label in rows:
                    screen.click(x_centre, y_centre)
                    time.sleep(4)
                    probe = words_of(screen, prefix, "probe")
                    right = " ".join(w[0] for w in probe if w[1] > 700)
                    low = right.lower()
                    log("row %s says: %s" % (label, right[:200]))
                    # The unformatted disk announces itself as Uninitialized with no
                    # capacity and no child count. Verified against a real frame: after
                    # clicking (540,379) the pane reads
                    #   Apple Inc. VirtIO Block Media  Uninitialized  Capacity: Unknown
                    #   Child count: Not Supported  Device: disk0
                    if ("uninitialized" in low or "capacity: unknown" in low
                            or "child count: not supported" in low):
                        target_row = (x_centre, y_centre)
                        log("the unformatted target disk is the row at (%d,%d)"
                            % target_row)
                        break

                if target_row is None:
                    log("no unformatted device in the sidebar yet")
                    time.sleep(5)
                    continue

                # Erase is a toolbar button, not a fixed point: the toolbar sits at
                # y=332 on the frames captured so far, and its Erase sits between
                # Partition and Restore. Read it from this frame when possible, fall back
                # to the measured position when not.
                # The word "Erase" is the button's LABEL, and its box is only 8px tall
                # (y=332-340). The control itself is the icon above that label, which is
                # why clicking the label's centre produces nothing. The tabs are drawn
                # icon-over-text with the icons around y=318.
                erase_btn = None
                for word, x, y, _w, _h in words:
                    if word.strip().lower().startswith("era") and 300 < y < 380:
                        erase_btn = (x, 318)
                        break
                if erase_btn is None:
                    erase_btn = ERASE_BUTTON
                    log("Erase not matched in OCR; using measured %s" % (erase_btn,))
                log("clicking Erase at %s" % (erase_btn,))
                screen.click(erase_btn[0], erase_btn[1])
                # Capture immediately afterwards. If the click opened a sheet this shows
                # it; if it did nothing, that is equally visible, and either way the next
                # round does not have to guess which happened.
                time.sleep(4)
                screen.grab("%s-after-erase-%02d" % (prefix, step))
                after = words_of(screen, prefix, "after-erase-ocr-%02d" % step)
                after_text = joined(after)
                log("after Erase: %s" % after_text[:220])

                # The sheet is the point of the click. Recognise it, so that a click which
                # did nothing is retried instead of being treated as done - that is what
                # happened before: erase_clicked was set, the toolbar Erase was pressed on
                # each pass, and the loop could not tell a failed open from a slow one.
                sheet_open = ("format" in after_text or "scheme" in after_text
                              or "name" in after_text or "cancel" in after_text
                              or "erase" in after_text.replace("disk utility", ""))
                log("erase sheet open: %s" % sheet_open)
                if not sheet_open:
                    # Do NOT go back to the View menu. The target is already selected -
                    # the pane says Uninitialized/disk0 - and re-running the selection
                    # re-opened Show All Devices, which is how this looped. Retry only the
                    # Erase press, and give the sheet longer to appear: the first attempt
                    # looked at the frame four seconds after the click.
                    erase_attempts += 1
                    if erase_attempts > 8:
                        log("Erase produced no sheet after %d attempts" % erase_attempts)
                        erase_attempts = 0
                    time.sleep(6)
                    continue
                time.sleep(6)
                continue

            # The erase sheet asks for nothing that is not already filled in: Name is
            # preset, Format defaults to APFS and Scheme to GUID Partition Map, which is
            # what an install target needs. Its own Erase button is the one that commits,
            # and it is the LOWEST such control on screen because the toolbar's Erase is
            # at y=332 while the sheet's is near its bottom edge.
            cands = [(w[2], w[1]) for w in words
                     if w[0].strip().lower().startswith("era")]
            if cands:
                y_low, x_low = max(cands)
                log("confirming Erase at (%d,%d)" % (x_low, y_low))
                screen.click(x_low, y_low)
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
