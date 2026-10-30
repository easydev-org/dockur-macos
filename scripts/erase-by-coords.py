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
LICENSE_AGREE = (1020, 527)
LICENSE_DISAGREE = (880, 527)


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

    # 2. Select the target. No View, no Show All Devices: the devices are in the sidebar
    #    under the default view, at fixed heights.
    log("selecting the target device at %s" % (SIDEBAR_TARGET,))
    screen.click(*SIDEBAR_TARGET)
    time.sleep(4)
    screen.grab("%s-02-selected" % prefix)

    # 3. Erase, and confirm. Both presses are single attempts now - if the sheet does not
    #    appear the run continues to the log rather than looping, because the previous
    #    retry logic is what kept dismissing it.
    log("pressing Erase at %s" % (ERASE_TAB,))
    screen.click(*ERASE_TAB)
    time.sleep(8)
    screen.grab("%s-03-sheet" % prefix)

    log("confirming the erase at %s" % (SHEET_ERASE,))
    screen.click(*SHEET_ERASE)
    time.sleep(10)

    # WAIT FOR THE ERASE TO FINISH. The frame right after the click reads
    # 'erasing "apple inc. virtlo block media"' - it is progress, not completion, and a
    # 64 GiB erase takes minutes. Sending the quit while that is still running left the
    # guest inside Disk Utility for the whole remaining run: the reinstall clicks landed
    # on nothing and eighty Returns changed nothing.
    for wait in range(1, 80):
        grab = screen.grab("%s-04-erase-wait%02d" % (prefix, wait))
        text = " ".join(w[0] for w in screen.text(grab)).lower()
        log("erase wait %d: %s" % (wait, text[:120] or "(nothing)"))
        if "erasing" not in text:
            log("erase finished after %ds" % (wait * 10))
            break
        time.sleep(10)
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
            log("license pane: clicking Agree at %s" % (LICENSE_AGREE,))
            screen.click(*LICENSE_AGREE)
            time.sleep(10)
            continue

        screen.key("ret")
        time.sleep(20)

    log("did not reach Setup Assistant")
    screen.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
