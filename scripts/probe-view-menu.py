#!/usr/bin/env python3
"""Open Disk Utility's View menu and photograph what drops down.

Disk Utility's sidebar lists VOLUMES, and the empty 64 GiB install target has none - so
the target is not in the list at all until "Show All Devices" is switched on. That one
menu item is the prerequisite for erasing anything, and its position has never been
measured; the driver has been guessing (210,190), which is why erase has never worked.

This does nothing else. It opens Disk Utility, clicks View, captures a frame with the menu
open, then quits. The frame is the answer: it shows the menu items and where they are.
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
        line = "[probe %s] %s" % (time.strftime("%H:%M:%S"), msg)
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

    # Wait for the recovery menu.
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
    log("recovery menu: %s" % " ".join(w[0] for w in words)[:150])

    # Disk Utility sits on the fourth row; select it, then Continue opens it.
    du = None
    for word, x, y, _w, _h in words:
        if "disk" in word.lower() and 480 < y < 560:
            du = (x, y)
            break
    if du is None:
        log("Disk Utility row not found: %s" % " ".join(w[0] for w in words)[:200])
        screen.close()
        return 1

    log("selecting Disk Utility at %s" % (du,))
    screen.click(du[0], du[1])
    time.sleep(2)
    screen.click(CONTINUE_MENU[0], CONTINUE_MENU[1])
    time.sleep(18)

    words = screen.text(screen.grab("%s-du" % prefix))
    log("in Disk Utility: %s" % " ".join(w[0] for w in words)[:180])

    # The View control is in the toolbar row. Its label was at (699,264) on one captured
    # frame; find it rather than assume, since the toolbar shifts between windows.
    view = None
    for word, x, y, _w, _h in words:
        if word.strip().lower() == "view" and y < 340:
            view = (x, y)
            break
    if view is None:
        log("View control not found")
        screen.close()
        return 1

    log("clicking View at %s" % (view,))
    screen.click(view[0], view[1])

    # THE FRAME THAT ANSWERS THE QUESTION. The menu is open here, so its items and their
    # positions are in this image.
    time.sleep(4)
    screen.grab("%s-menu-open" % prefix)
    words = screen.text(screen.grab("%s-menu-ocr" % prefix))
    log("menu contents: %s"
        % " | ".join("%s@%d,%d" % (w[0], w[1], w[2])
                     for w in sorted(words, key=lambda w: w[2]))[:600])

    # Leaving the menu open would swallow later clicks, so close it before quitting.
    screen.key("esc")
    time.sleep(2)
    log("done")
    screen.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
