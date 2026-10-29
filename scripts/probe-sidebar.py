#!/usr/bin/env python3
"""Click each sidebar row in Disk Utility and record what the detail pane says.

The driver has been clicking guessed points and reporting success while the selection
never moved: the frame shows disk2s1 - the 2.88 GB read-only recovery volume - still
highlighted, and Erase greyed out because of it. Rather than guess again, this clicks
each candidate row and photographs the result, so the row that selects the 64 GB
unformatted disk is identified by evidence.

Known from a measured frame:

  sidebar                                     detail pane
  Internal                y=350  x=426-466
  Apple Inc. VirtIO ...   y=374  x=459-635    disk2s1          y=374  x=781-862
  Apple Inc. VirtIO ...   y=402  x=459-635    Internal Physical Volume  y=400
  disk2s1                 y≈418               Capacity: 2.88 GB         y=621

The click column is x=520 (inside the label text) and the detail pane starts at x~700.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import qmp as qmpmod
from importlib.machinery import SourceFileLoader
_ocr = SourceFileLoader("drive_ocr", os.path.join(HERE, "drive-ocr.py")).load_module()
Screen = _ocr.Screen

CONTINUE_MENU = (1142, 588)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    qmp_socket, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-probe.log"

    def log(msg):
        line = "[sb %s] %s" % (time.strftime("%H:%M:%S"), msg)
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

    deadline = time.time() + 1500
    words = []
    while time.time() < deadline:
        words = screen.text(screen.grab("%s-boot" % prefix))
        if len(words) > 3:
            break
        time.sleep(20)
    if not words:
        log("nothing readable")
        screen.close()
        return 1

    # Open Disk Utility: select the fourth row and press Continue.
    du = None
    for word, x, y, w, _h in words:
        if word.strip().lower().startswith("disk") and 480 < y < 560:
            du = (x, y)
            break
    if du is None:
        log("Disk Utility row not found: %s" % " ".join(w[0] for w in words)[:200])
        screen.close()
        return 1
    log("opening Disk Utility at %s" % (du,))
    screen.click(du[0], du[1])
    time.sleep(2)
    screen.click(CONTINUE_MENU[0], CONTINUE_MENU[1])
    time.sleep(18)

    # Show All Devices, so the unformatted disk appears as its own row.
    #
    # Tried by mouse first, then by keyboard: a captured frame of a correctly selected
    # target returned no toolbar text at all, so the button's position cannot always be
    # read. Ctrl-F2 focuses the menu bar, "v" reaches View, Down+Return takes the second
    # item.
    words = screen.text(screen.grab("%s-du" % prefix))
    view_btn = None
    for word, x, y, w, _h in words:
        if word.strip().lower() in ("iew", "iow", "view", "vigw") and 300 < y < 380:
            view_btn = (x, y)
            break
    log("View button: %s" % (view_btn,))

    opened = False
    if view_btn is not None:
        screen.click(view_btn[0], view_btn[1])
        time.sleep(3)
        menu = " ".join(w[0] for w in screen.text(screen.grab("%s-vm" % prefix))).lower()
        log("menu after mouse: %s" % menu[:130])
        if "all" in menu:
            screen.click(345, 63)
            time.sleep(6)
            opened = True
        else:
            screen.key("esc")
            time.sleep(1)

    if not opened:
        log("mouse did not open the View menu; using the keyboard")
        qmpmod.key_combo(screen.q, "ctrl_l", "f2")
        time.sleep(1)
        screen.type("v", delay=0.1)
        time.sleep(1)
        menu = " ".join(w[0] for w in screen.text(screen.grab("%s-vk" % prefix))).lower()
        log("menu after keyboard: %s" % menu[:130])
        if "all" in menu:
            screen.key("down")
            time.sleep(0.5)
            screen.key("ret")
            time.sleep(6)
        else:
            screen.key("esc")

    after = " ".join(w[0] for w in screen.text(screen.grab("%s-av" % prefix))).lower()
    log("after View: %s" % after[:170])

    # THE EXPERIMENT: click each sidebar row and read the detail pane.
    for attempt in range(1, 8):
        words = screen.text(screen.grab("%s-scan%02d" % (prefix, attempt)))
        text = " ".join(w[0] for w in words).lower()
        log("scan %d: %s" % (attempt, text[:170]))

        rows = sorted([(w[2], w[1], w[0]) for w in words
                       if "virtio" in w[0].lower() or "virtl" in w[0].lower()])
        log("virtio words: %s" % (rows,))

        if not rows:
            time.sleep(5)
            continue

        # One click per attempt, walking down the list, so each detail reading is
        # attributable to exactly one row.
        if attempt - 1 < len(rows):
            y_centre, x_centre, label = rows[attempt - 1]
            click_pt = (max(500, min(x_centre, 660)), y_centre)
            log("clicking %s at %s" % (label, click_pt))
            screen.click(click_pt[0], click_pt[1])
            time.sleep(4)
            screen.grab("%s-after%02d" % (prefix, attempt))
            after = screen.text(screen.grab("%s-after-ocr%02d" % (prefix, attempt)))
            detail = " ".join(w[0] for w in after if w[1] > 700)
            log("detail pane now: %s" % detail[:220])

    # SECOND EXPERIMENT: which point on the Erase control actually opens the sheet?
    #
    # A frame of a correctly selected target (68.72 GB, Uninitialized, Erase drawn
    # enabled) produced NO toolbar text from tesseract - only a fragment "h" at x=1288 -
    # so the control's exact extent has to come from pixels, not from OCR. This walks a
    # small grid around where the label was measured (x 1276-1304, y 332-340) and reports
    # the first point that opens a sheet.
    #
    # The word "Erase" occupies y=332-340 - an 8px label under the icon. Clicking its
    # centre produced no sheet on three runs, so the tab is probably hit-tested on the
    # icon above the label. Sweep the toolbar column at 1290 from y=300 to y=345 and
    # record which one opens something.
    for y_try in (306, 312, 318, 324, 330, 336, 342):
        log("trying Erase at (1290,%d)" % y_try)
        screen.click(1290, y_try)
        time.sleep(3)
        grab = screen.grab("%s-erase-y%03d" % (prefix, y_try))
        w2 = screen.text(grab)
        t2 = " ".join(w[0] for w in w2).lower()
        opened = ("format" in t2 or "scheme" in t2 or "cancel" in t2
                  or "name" in t2)
        log("  at y=%d sheet open: %s | %s" % (y_try, opened, t2[:120]))
        if opened:
            log("  >>> y=%d opens the erase sheet" % y_try)
            break
        # A stray click elsewhere may have changed the selection; restore it.
        screen.click(540, 379)
        time.sleep(2)

    # If no y in that column worked, try shifting x as well - the tab may be wider than
    # its label.
    for x_try in (1270, 1280, 1290, 1300, 1310):
        log("trying Erase at (%d,324)" % x_try)
        screen.click(x_try, 324)
        time.sleep(3)
        grab = screen.grab("%s-erase-x%04d" % (prefix, x_try))
        t3 = " ".join(w[0] for w in screen.text(grab)).lower()
        opened = ("will be deleted" in t3 or "format" in t3 or "cancel" in t3)
        log("  at x=%d sheet open: %s | %s" % (x_try, opened, t3[:110]))
        if opened:
            log("  >>> x=%d opens the erase sheet" % x_try)
            break
        screen.click(540, 379)
        time.sleep(2)

    log("done")
    screen.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
