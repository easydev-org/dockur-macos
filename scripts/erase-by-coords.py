#!/usr/bin/env python3
"""Erase the install target in Disk Utility using fixed coordinates only.

OCR is not used at run time. Every number below was measured offline from captured
frames; the driver just walks the sequence. That is a deliberate reversal: five rounds of
driving from OCR kept failing for reasons that had nothing to do with the UI -

  a whole toolbar row dropped from one frame ("Erase not matched in OCR") while Erase was
  plainly drawn and enabled, so a correct selection was judged a failure

  curly quotes: the sheet's title is Erase \u201cApple Inc. VirtIO Block Media\u201d? and OCR
  emits \u201c, not ASCII ", so a sheet that had opened was reported closed and the retry
  promptly dismissed it

  words split and truncated: Disk Utility arrives as Disk + Utility, View as iew or iow,
  VirtIO as VirtlO

None of that matters if the sequence is fixed and the screen is a known size. The guest is
1920x1080 and the recovery UI does not move between boots.

MEASURED COORDINATES

  recovery menu          Disk Utility row        (849, 513)
                         Reinstall macOS row     (861, 329)
                         Continue                (1142, 588)

  Disk Utility toolbar   View/Volume/First Aid/Partition/Erase/Restore/Unmount/Info
                         all at y=332; Erase's label box is x=1276-1304
                         -- and the tab is hit-tested ABOVE its label, at y=306

  sidebar rows           Internal       y=350
                         device 1       y=374   <- the 64 GiB target
                         device 2       y=402
                         volume disk1s1 y=430
                         x=540 is inside the labels

  erase sheet            Name    (862,518)-(1119,539) = Untitled
                         Format  (862,541)-(1119,561) = APFS            (already correct)
                         Scheme  (862,564)-(1119,585) = GUID Partition Map (already correct)
                         Cancel  (1066, 639)
                         Erase   (1146, 639)

NO VIEW CLICK. Show All Devices is not needed and is actively harmful here: the physical
disks are listed under "Show Only Volumes" as well - the captured frame shows two
"Apple Inc. VirtIO Block Media" rows beside disk1s1 in that very mode - and attempting the
View menu produced runs that spent twenty minutes failing to open it. The sidebar rows are
at fixed heights regardless, so the sequence selects by position and never touches View.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from importlib.machinery import SourceFileLoader
import qmp as qmpmod

import xsh

_ocr = SourceFileLoader("drive_ocr", os.path.join(HERE, "drive-ocr.py")).load_module()
Screen = _ocr.Screen

# --- measured coordinates ---------------------------------------------------

RECOVERY_DISK_UTILITY = (849, 513)
RECOVERY_REINSTALL = (861, 329)
RECOVERY_CONTINUE = (1142, 588)


SIDEBAR_TARGET = (540, 374)      # first device row = the 64 GiB unformatted disk
SIDEBAR_OTHERS = ((540, 402), (540, 430))

SHEET_ERASE = (1146, 639)
SHEET_CANCEL = (1066, 639)

# TWO Agree buttons exist on this pane, and only one is live.
#
# The main window has Disagree (908,710) / Agree (1002,710), and a MODAL sheet sits on top
# of it reading "I have read and agree to the terms of the software license agreement"
# with its own Disagree (880,527) / Agree (1020,527). The sheet dims everything behind it,
# so the main window's Agree cannot be clicked at all - which is why y=713 did nothing and
# the run pressed it for eighty steps.
#
# The sheet's Agree is the one that commits. the default focus sits on Disagree, so Return advances
# nothing and the run stalls there indefinitely.
# Licensing is TWO steps, and both buttons are called Agree:
#
#   1. the license pane itself - ENGLISH selector, agreement text, Disagree/Agree at
#      y=710: Agree is (1002, 710)
#   2. a modal sheet that appears after that click, reading "I have read and agree to the
#      terms of the software license agreement": Agree is (1020, 527)
#
# Clicking only the second point did nothing, because on step 1 it lands on empty pane.
# Clicking only the first produced the sheet and then stalled. Both are clicked in turn,
# which is idempotent: if the pane is already past step 1 the first click hits empty space.
LICENSE_AGREE = (1002, 710)
LICENSE_SHEET_AGREE = (1020, 527)
LICENSE_DISAGREE = (880, 527)

# The installer's disk list, after the erase. The entry is the volume eraseDisk created;
# 'Untitled' is its default name and 68.51 GB its size.
INSTALLER_DISK = (865, 621)
INSTALLER_CONTINUE = (1005, 712)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    qmp_socket, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-coords.log"

    def log(msg):
        line = "[co %s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

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
        return 1

    # Wait for the recovery menu. This is the one place a picture is still consulted, and
    # only to answer "has the guest booted yet" - not "what does the UI say".
    deadline = time.time() + 1500
    words = []
    while time.time() < deadline:
        words = screen.text(screen.grab("%s-boot" % prefix))
        if len(words) > 3:
            log("recovery menu is up")
            break
        time.sleep(20)
    if not words:
        log("nothing readable after 25 minutes")
        screen.close()
        return 1

    # 1. Disk Utility.
    log("selecting Disk Utility at %s" % (RECOVERY_DISK_UTILITY,))
    screen.click(*RECOVERY_DISK_UTILITY)
    time.sleep(2)
    log("Continue at %s" % (RECOVERY_CONTINUE,))
    screen.click(*RECOVERY_CONTINUE)
    time.sleep(20)
    screen.grab("%s-01-du" % prefix)

    # 2. Select the target.
    #
    # The physical disks are not always in the sidebar. A captured frame shows only
    # disk2s1 - one volume, 2.88 GB, mounted read-only - and no device rows at all, which
    # means Show Only Volumes is in force for that boot. Selecting by a fallback point in
    # that state clicks the recovery volume, erases the recovery volume, and leaves the
    # installer with nothing to install onto.
    #
    # So the disk list is inspected first, and the row is chosen by what the detail pane
    # reports after clicking it - the 64 GiB target is the one that reads Uninitialized
    # with 68.72 GB. No fallback: if no row qualifies, the run stops rather than guessing.
    # THE SIDEBAR IS OCR-READABLE ONLY WHEN CROPPED. A whole-frame pass at psm 3 or psm 11
    # returns nothing from those pixels - on a captured macOS 15 frame it reported an empty
    # sidebar while the crop read
    #
    #   Internal
    #   Apple Inc. VirtIO Block Media
    #   Apple Inc. VirtIO Block Media
    #
    # Two runs of 15 and 26 were failed on that silence, with a confident explanation about
    # virtio enumeration. The disks were there. sidebar_rows() crops first.
    sidebar_ppm = screen.grab("%s-02-sidebar" % prefix)
    rows = sidebar_rows(screen, sidebar_ppm)
    log("sidebar OCR (%d words): %s" % (len(rows), joined(rows)[:150]))

    # The device rows are the ones naming a controller; the volume rows read disk0s1 etc.
    # x and y here are crop-relative, so the crop origin is added back.
    device_rows = []
    for word, x, y, _w, _h in rows:
        low = word.strip().lower()
        if low in ("apple", "inc.", "virtio", "virtlo", "block", "media"):
            device_rows.append((400 + x, 300 + y))
    # Collapse the several words of one label into one point per row.
    merged = []
    for x, y in device_rows:
        for i, (mx, my) in enumerate(merged):
            if abs(my - y) <= 12:
                break
        else:
            merged.append((x, y))
    device_rows = merged

    # NO VIEW CLICK. When the sidebar holds only the recovery volume, the guest did not
    # get its install disk - the volume name differs between boots (disk1s1 on one,
    # disk2s1 on another), which is an enumeration difference, not a UI one. Show All
    # Devices does not fix that: the device rows either exist under the default view or
    # the disk is not there at all.
    #
    # So this is a stop condition, not something to work around. The job fails, and the
    # next run boots a guest that has its disk.
    if not device_rows:
        log("FATAL: the sidebar lists no physical disk - only the recovery volume,")
        log("so this guest did not attach the install target. The volume name")
        log("differs between boots (disk1s1 vs disk2s1), which is an enumeration")
        log("difference, not a UI one - Show All Devices cannot conjure a disk that")
        log("is not attached. Failing here rather than guessing; rerun.")
        screen.close()
        return 1

    log("device rows in the sidebar: %s" % (device_rows,))
    if not device_rows:
        log("FATAL: no physical disk in the sidebar; the target cannot be selected")
        screen.close()
        return 1

    # Click each candidate and keep the one whose detail pane says it is unformatted with
    # tens of gigabytes. The recovery image and the OpenCore disk report 2.88 GB and
    # 268.4 MB, and both have a child count; only the target reads Uninitialized.
    target_pt = None
    for cx, cy in device_rows[:4]:
        screen.click(cx, cy)
        time.sleep(4)
        # The detail pane is read the same way the sidebar is - cropped, then psm 6. The
        # whole-frame pass drops most of it: on this frame it returned "Internal |
        # Capacity:" and stopped, with the Child count line that decides the matter
        # missing entirely.
        probe_ppm = screen.grab("%s-02-probe" % prefix)
        pane_crop = _crop_ppm(probe_ppm, probe_ppm.replace(".ppm", "-pane.ppm"),
                              (700, 330, 1250, 480))
        pane = joined(_read_words(pane_crop)) if pane_crop else ""
        low = pane.lower()
        log("row (%d,%d): %s" % (cx, cy, pane[:180]))
        # The unformatted target is identified by its SHAPE, not by a word:
        #
        #   target        Capacity: 68.72 GB   Child count: 0    Partition Map: Not Supported
        #   recovery      Capacity: 2.88 GB    Child count: 1    Partition Map: GUID ...
        #   OpenCore      Capacity: 268.4 MB   Child count: 1
        #
        # 'Uninitialized' is not on this pane at all in the frame where the disk is
        # correctly selected, and '68.72' arrives from OCR as '6872.68' with the point
        # lost - so testing for either reports failure on success. Child count is the one
        # field that separates them cleanly: zero children means no partition table yet.
        # Child count reads as either '0' or 'Not Supported' on the same disk depending on
        # the frame, and both mean 'no partition table yet'.
        if ("child count: 0" in low
                or "child count: not supported" in low
                or "partition map: not supported" in low
                or "uninitialized" in low
                or "6872" in low or "68.72" in low):
            target_pt = (cx, cy)
            break

    if target_pt is None:
        log("FATAL: no row reported an unformatted 64 GiB disk")
        screen.close()
        return 1

    log("selecting the target device at %s" % (target_pt,))
    screen.click(*target_pt)
    time.sleep(4)
    screen.grab("%s-02-selected" % prefix)

    # The Erase tab is drawn icon-over-label, and only the icon responds: clicking the
    # label's own text does nothing. Its height is not fixed between frames - one capture
    # shows the label at y=332, another at y=294 - so a constant is wrong by up to 38px,
    # which is the whole height of the control.
    #
    # So the label is located in the frame and the click goes 26px above it.
    #
    # Measured across releases, and the reason this must not be a constant:
    #
    #   14  Erase label (1290,294)   -> press (1290,268)
    #   15  Erase label (1290,294)   -> press (1290,268)
    #   26  Erase label (1308,314)   -> press (1308,288)    18px right, 20px down
    #
    # NO FALLBACK. If the label is not on the frame the press is not attempted, because a
    # guessed point either hits nothing (the run then reports a false 'no erase sheet' and
    # retries forever) or hits a neighbouring control (Restore sits 32px to the right).
    # A wrong press here is worse than a stopped run: it can act on the wrong thing.
    # Crop the toolbar band before reading it, for the same reason as the sidebar: the
    # whole-frame pass drops it. Both runs of this commit reported
    #
    #   FATAL: no Erase label on this frame
    #
    # with the disk correctly selected and the button plainly on screen.
    tb_ppm = screen.grab("%s-03-toolbar" % prefix)
    # The toolbar is found on the WHOLE frame at psm 11 - no crop, no version table.
    #
    # The label row moves a lot: 294 on 14, 332 on 15, and 26 gave 314 on one boot and 355
    # on the next. Five crop ranges were tried against four captured frames and none
    # covered all of them (285-345 missed 26 at 355; 250-400 covered 26 but lost 14 and 15).
    # A whole-frame pass at psm 11 finds it in all four:
    #
    #   14      x=1276-1304  y=294-302  conf=96
    #   15      x=1276-1304  y=332-340  conf=96
    #   26      x=1294-1322  y=314-322  conf=84
    #   26      x=1294-1322  y=355-363  conf=96
    #
    # which is what sparse-text mode is for: it keeps isolated words that the default
    # page-segmentation discards as noise, and the toolbar is nothing but isolated words.
    erase_pt = None
    for word, x, y, _w, _h in _read_words(tb_ppm, psm="11", min_conf=30):
        if word.strip().lower().startswith("era") and 250 < y < 420:
            erase_pt = (x, y - 26)
            log("Erase label at (%d,%d); pressing its tab at %s" % (x, y, erase_pt))
            break
    if erase_pt is None:
        log("FATAL: no Erase label on this frame - refusing to press a guessed point")
        log("A blind press would either do nothing or hit Partition/Restore next to it.")
        screen.close()
        return 1
    log("pressing Erase at %s" % (erase_pt,))
    screen.click(*erase_pt)
    time.sleep(8)
    screen.grab("%s-03-sheet" % prefix)

    # WAIT FOR THE ERASE TO FINISH. The frame right after the click reads
    # 'erasing "apple inc. virtlo block media"' - it is progress, not completion, and a
    # 64 GiB erase takes minutes. Sending the quit while that is still running left the
    # guest inside Disk Utility for the whole remaining run: the reinstall clicks landed
    # on nothing and eighty Returns changed nothing.
    # The sheet has to appear first. Without this check a missed Erase press looked
    # identical to a completed one: no 'erasing' text on screen meant 'already finished',
    # so the run moved on with the disk untouched - and the installer then showed an empty
    # disk list, which is exactly what happened.
    sheet_seen = False
    for wait in range(1, 20):
        grab = screen.grab("%s-04-sheet-wait%02d" % (prefix, wait))
        text = " ".join(w[0] for w in screen.text(grab)).lower()
        log("sheet wait %d: %s" % (wait, text[:120] or "(nothing)"))
        if "will be deleted" in text or "erase" in text and "untitled" in text:
            sheet_seen = True
            break
        if wait >= 3:
            log("Erase produced no sheet - pressing it again")
            screen.click(*erase_pt)
        time.sleep(5)
    if not sheet_seen:
        log("WARNING: no erase sheet appeared; the disk was NOT erased")
    screen.grab("%s-04-sheet" % prefix)

    log("confirming the erase at %s" % (SHEET_ERASE,))
    screen.click(*SHEET_ERASE)
    time.sleep(10)

    # A progress dialog appears, and it CONTAINS THE WORD 'erasing' EVEN WHEN FINISHED:
    #
    #   Erasing "Apple Inc. VirtIO Block Media" and creating "Untitled"
    #   Erase process is complete. Click Done to continue.
    #
    # So waiting for 'erasing' to disappear never ends - that word is the dialog's title.
    # The run sat on it for thirteen minutes while the erase had actually finished in the
    # first ten seconds.
    #
    # The completion line is 'Erase process is complete', and the dialog then wants Done
    # at (1001, 572). That is the whole remainder of this step.
    done_pt = (1001, 572)
    for wait in range(1, 60):
        grab = screen.grab("%s-04-erase-wait%02d" % (prefix, wait))
        text = " ".join(w[0] for w in screen.text(grab)).lower()
        log("erase wait %d: %s" % (wait, text[:130] or "(nothing)"))
        if "complete" in text or "done" in text:
            log("erase reported complete after %ds" % (wait * 5))
            for word, x, y, _w, _h in screen.text(grab):
                if word.strip().lower().startswith("done"):
                    done_pt = (x, y)
                    log("Done button at (%d,%d)" % done_pt)
                    break
            break
        time.sleep(5)
    log("clicking Done at %s" % (done_pt,))
    screen.click(*done_pt)
    time.sleep(12)
    screen.grab("%s-04-erased" % prefix)

    # 4. Leave Disk Utility by its own menu. A synthesised Cmd-Q did not close it - the
    # menu bar's first item is Disk Utility, and Quit is its last entry.
    log("quitting Disk Utility from its menu")
    screen.click(63, 15)
    time.sleep(3)
    screen.grab("%s-05-appmenu" % prefix)
    menu = " ".join(w[0] for w in screen.text(screen.grab("%s-05-appmenu-ocr" % prefix))).lower()
    log("app menu: %s" % menu[:160])
    if "quit" in menu:
        for word, x, y, _w, _h in screen.text(screen.grab("%s-05-appmenu-ocr2" % prefix)):
            if word.strip().lower().startswith("quit"):
                log("clicking Quit at (%d,%d)" % (x, y))
                screen.click(x, y)
                break
    else:
        # Fall back to the chord, but then verify the window actually went away.
        log("no Quit in the menu; falling back to Cmd-Q")
        screen.key("esc")
        time.sleep(1)
        qmpmod.key_combo(screen.q, "meta_l", "q")
    time.sleep(15)
    screen.grab("%s-05-menu" % prefix)

    log("selecting Reinstall macOS at %s" % (RECOVERY_REINSTALL,))
    screen.click(*RECOVERY_REINSTALL)
    time.sleep(2)
    log("Continue at %s" % (RECOVERY_CONTINUE,))
    screen.click(*RECOVERY_CONTINUE)
    time.sleep(30)
    screen.grab("%s-06-install" % prefix)

    # 5. Follow the installer to Setup Assistant.
    #
    # Return is not enough. The license pane puts DISAGREE under the default focus, so
    # pressing Return thirty-two times navigated nothing - the run sat on that pane for
    # eleven minutes. Measured from the frame: Disagree (899,676), Agree (1005,676).
    #
    # The loop is coordinate-driven like everything else: click Agree whenever the pane is
    # the license, and press Return otherwise. Screenshots are taken throughout so a stuck
    # step can be read afterwards instead of guessed at.
    # SIXTY MINUTES, NOT TWENTY. The first run to actually install reached
    #
    #   step 74: about 21 minutes remaining
    #   step 76: (nothing)
    #   step 79: (nothing)
    #
    # and stopped there. The blank frames are the install's own reboot: macOS writes the
    # first stage, restarts, and continues from the disk, which takes roughly as long
    # again. A sixty-pass loop at twenty seconds covered twenty minutes and expired in the
    # middle of that, so the run was recorded as a failure while the install was on
    # schedule.
    #
    # Blank frames are therefore expected and are not a reason to press anything - Return
    # during the reboot would land on nothing, or worse on a menu.
    blank_run = 0
    graphics_run = 0
    for step in range(1, 190):
        grab = screen.grab("%s-07-step%02d" % (prefix, step))
        words = screen.text(grab)
        text = " ".join(w[0] for w in words).lower()
        log("install step %d: %s" % (step, text[:140] or "(nothing)"))

        if "country or region" in text or "select your country" in text:
            log("SETUP ASSISTANT REACHED at step %d" % step)
            screen.close()
            return 0

        # A progress screen carries NO TEXT AT ALL - just an Apple logo and a bar - so
        # OCR returns nothing and this loop had been calling it a blank screen and waiting
        # for it to end. It was the END, not a failure: measured on the captured frames,
        # the bright pixels sit at y=500-980 with a bar at y=560, x=926, 71px wide on
        # fourteen consecutive passes - the install completing on black.
        #
        # So 'no words' is not the same as 'nothing there'. The screen is checked for
        # content before it is called blank.
        if not text:
            if has_graphics(grab):
                graphics_run += 1
                if graphics_run in (1, 10, 30, 60):
                    log("textless progress screen (logo and bar) x%d - the install is "
                        "running or finishing; waiting" % graphics_run)
            else:
                blank_run += 1
                if blank_run in (5, 20, 60):
                    log("truly blank screen x%d" % blank_run)
            time.sleep(20)
            continue
        blank_run = 0
        graphics_run = 0

        if "license agreement" in text or "agree" in text:
            # Both steps, in order. See LICENSE_AGREE above: the pane and its sheet use the
            # same word at different heights, and pressing either alone stalls.
            log("license pane: Agree at %s then sheet Agree at %s"
                % (LICENSE_AGREE, LICENSE_SHEET_AGREE))
            screen.click(*LICENSE_AGREE)
            time.sleep(6)
            screen.click(*LICENSE_SHEET_AGREE)
            time.sleep(10)
            continue

        # The erase target now has to be chosen again, this time in the installer's own
        # disk list. Measured from the frame:
        #
        #   Untitled     x=842-888   y=616-626    centre (865,621)
        #   68.51 GB     y=632-639
        #   [Back] (915,712)  [Continue] (1005,712)
        #
        # Pressing Return here does nothing at all: the list needs a selection first, and
        # Return is not bound to it.
        if "select the disk" in text:
            # Find the volume rather than assuming where it sits: the list is centred and
            # its row position follows the number of disks, which is one here but need not
            # be on another boot.
            disk_pt = None
            cont_pt = INSTALLER_CONTINUE
            for word, x, y, _w, _h in words:
                low = word.strip().lower()
                if low.startswith("untitled") and 500 < y < 690:
                    disk_pt = (x, y)
                    log("found the target volume at (%d,%d)" % disk_pt)
                if low.startswith("continue") and y > 690:
                    cont_pt = (x, y)
            if disk_pt is None:
                disk_pt = INSTALLER_DISK
                log("no Untitled row found; using %s" % (disk_pt,))
            log("disk selection pane: choosing it, then Continue at %s" % (cont_pt,))
            screen.click(*disk_pt)
            time.sleep(3)
            screen.click(*cont_pt)
            time.sleep(20)
            continue

        screen.key("ret")
        time.sleep(20)

    log("did not reach Setup Assistant")
    screen.close()
    return 1


def _read_ppm(path):
    with open(path, "rb") as fh:
        if fh.readline().strip() != b"P6":
            return None
        line = fh.readline()
        while line.startswith(b"#"):
            line = fh.readline()
        w, h = map(int, line.split())
        fh.readline()
        return w, h, fh.read()


def _crop_ppm(src, dst, box):
    """Write a sub-rectangle of a P6 PPM. box is (x0, y0, x1, y1)."""
    got = _read_ppm(src)
    if not got:
        return None
    w, h, data = got
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    buf = bytearray()
    for y in range(y0, y1):
        buf += data[(y * w + x0) * 3:(y * w + x1) * 3]
    with open(dst, "wb") as fh:
        fh.write(b"P6\n%d %d\n255\n" % (x1 - x0, y1 - y0))
        fh.write(bytes(buf))
    return dst


def joined(words):
    """All recognised text on one lower-cased line, for phrase tests."""
    return " ".join(w[0] for w in words).lower()


def _read_words(ppm, psm="6", min_conf=25):
    """OCR a cropped PPM at a given psm, returning [(word, x, y, w, h)]."""
    r = xsh.run(["tesseract", ppm, "stdout", "--psm", psm, "tsv"], echo=False)
    words = []
    for line in (r.out or "").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
            x, y, w, h = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        except ValueError:
            continue
        word = parts[11].strip()
        if not word or conf < min_conf:
            continue
        words.append((word, x + w // 2, y + h // 2, w, h))
    return words


def has_graphics(ppm, threshold=40, min_rows=5):
    """True when the frame has something drawn on it, even if nothing is legible.

    The macOS progress screen is an Apple logo and a bar on black: tesseract finds no words,
    and a pixel census calls it 99.7% black, yet the install is running normally. Both of
    those signals were read as 'dead screen' and the loop waited for a recovery that was
    never coming, while the bar sat at the same width for fourteen consecutive frames.

    So content is judged by whether any rows contain lit pixels at all, not by how much of
    the frame is lit.
    """
    got = _read_ppm(ppm)
    if not got:
        return False
    w, h, data = got
    lit_rows = 0
    for y in range(0, h, 4):
        row = data[(y * w) * 3:(y * w + w) * 3]
        lit = sum(1 for i in range(0, len(row), 12) if row[i] > threshold)
        if lit > 2:
            lit_rows += 1
            if lit_rows >= min_rows:
                return True
    return False

def sidebar_rows(screen, ppm):
    """Return [(word, x_centre, y_centre, w, h)] for the Disk Utility sidebar only.

    WHY THIS CROP EXISTS. The whole-frame pass drops the sidebar entirely: on a captured
    frame, tesseract at --psm 3 and at --psm 11 both returned nothing from those
    400x200 pixels, while --psm 6 on the same pixels cropped out read them perfectly:

        Internal
        Apple Inc. VirtIO Block Media
        Apple Inc. VirtIO Block Media
        disk0s1

    That silence was read as "this guest has no install disk" and two runs of macOS 15 and
    26 were failed for it, with a confident note about virtio enumeration. The disks were
    there the whole time. The sidebar is at a fixed place, so crop it and use a mode that
    expects short lines.
    """
    cropped = _crop_ppm(ppm, ppm.replace(".ppm", "-sidebar.ppm"), (400, 300, 700, 500))
    if not cropped:
        return []
    r = xsh.run(["tesseract", cropped, "stdout", "--psm", "6", "tsv"], echo=False)
    words = []
    for line in (r.out or "").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12:
            continue
        try:
            conf = float(parts[10])
            x, y, w, h = (int(parts[6]), int(parts[7]), int(parts[8]), int(parts[9]))
        except ValueError:
            continue
        word = parts[11].strip()
        if not word or conf < 30:
            continue
        words.append((word, x + w // 2, y + h // 2, w, h))
    return words


if __name__ == "__main__":
    sys.exit(main())
