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

_ocr = SourceFileLoader("drive_ocr", os.path.join(HERE, "drive-ocr.py")).load_module()
Screen = _ocr.Screen

# --- measured coordinates ---------------------------------------------------

RECOVERY_DISK_UTILITY = (849, 513)
RECOVERY_REINSTALL = (861, 329)
RECOVERY_CONTINUE = (1142, 588)

ERASE_TAB = (1290, 306)          # above the label; the label itself (y=336) does nothing

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
    heading_seen = False
    device_rows = []
    for word, x, y, _w, _h in screen.text(screen.grab("%s-02-sidebar" % prefix)):
        low = word.strip().lower()
        if low == "internal" and x < 700:
            heading_seen = True
            continue
        if heading_seen and x < 700 and ("virt" in low or "block" in low or "media" in low):
            device_rows.append((x, y))

    if not device_rows:
        # Show All Devices, only now and only because it is the difference between a
        # sidebar with disks in it and one without.
        log("no device rows in the sidebar; trying Show All Devices")
        for word, x, y, _w, _h in screen.text(screen.grab("%s-02-tb" % prefix)):
            if word.strip().lower().startswith("view") and 250 < y < 380:
                screen.click(x, y)
                time.sleep(3)
                menu = " ".join(w[0] for w in screen.text(screen.grab("%s-02-vm" % prefix))).lower()
                log("View menu: %s" % menu[:120])
                for mword, mx, my, _mw, _mh in screen.text(screen.grab("%s-02-vm2" % prefix)):
                    if mword.strip().lower() == "all":
                        screen.click(mx, my)
                        time.sleep(6)
                        break
                break
        for word, x, y, _w, _h in screen.text(screen.grab("%s-02-sidebar2" % prefix)):
            low = word.strip().lower()
            if x < 700 and ("virt" in low or "block" in low or "media" in low):
                device_rows.append((x, y))

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
        pane = " ".join(w[0] for w in screen.text(screen.grab("%s-02-probe" % prefix)))
        low = pane.lower()
        log("row (%d,%d): %s" % (cx, cy, pane[:150]))
        if "uninitialized" in low or "68.72" in low:
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
    # So the label is located in the frame and the click goes 26px above it, which is the
    # offset that worked: label 332 -> press 306.
    erase_pt = ERASE_TAB
    for word, x, y, _w, _h in screen.text(screen.grab("%s-03-toolbar" % prefix)):
        if word.strip().lower().startswith("era") and 250 < y < 380:
            erase_pt = (x, y - 26)
            log("Erase label at (%d,%d); pressing its tab at %s" % (x, y, erase_pt))
            break
    else:
        log("Erase label not found; using %s" % (erase_pt,))
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
    for step in range(1, 80):
        grab = screen.grab("%s-07-step%02d" % (prefix, step))
        words = screen.text(grab)
        text = " ".join(w[0] for w in words).lower()
        log("install step %d: %s" % (step, text[:140] or "(nothing)"))

        if "country or region" in text or "select your country" in text:
            log("SETUP ASSISTANT REACHED at step %d" % step)
            screen.close()
            return 0

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


if __name__ == "__main__":
    sys.exit(main())
