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
            du = (x + w // 2, y)
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

    # Switch to Show All Devices so the unformatted disk exists at all.
    words = screen.text(screen.grab("%s-du" % prefix))
    view_btn = None
    for word, x, y, w, _h in words:
        if word.strip().lower() in ("iew", "iow", "view", "vigw") and 300 < y < 380:
            view_btn = (x + w // 2, y)
            break
    log("View button: %s" % (view_btn,))
    if view_btn is not None:
        screen.click(view_btn[0], view_btn[1])
        time.sleep(3)
        screen.click(345, 63)
        time.sleep(6)

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

    log("done")
    screen.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
