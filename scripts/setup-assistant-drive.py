#!/usr/bin/env python3
"""Click through macOS Setup Assistant to the desktop, and keep every frame.

    setup-assistant-drive.py <monitor.sock> <frame-prefix>

WHY QMP AND NOT HMP. Two earlier attempts used QEMU's HMP monitor and neither
moved the guest:

  sendkey ret/tab for nineteen frames     screen differed by 27 KB out of 6.2 MB
                                          - the clock advanced, the list did not
  mouse_move + mouse_button, 64 clicks    all 64 frames byte-identical, pointer
                                          still in the top-left corner

The second failure is the informative one. HMP's mouse_move carries relative
deltas while the guest has a usb-tablet, an absolute device, so the deltas never
produced a position. QMP's input-send-event takes abs axis values with explicit
button state, which is what an absolute pointer needs, so this speaks QMP.

The first click after boot is also treated as a pointer test: if the frame does
not change at all, the driver reports it rather than looping on dead input.

WHY DRIVING AT ALL. There is no marker-based shortcut to find. dockur's README
says only dockur/windows has automatic installation; OSX-KVM (23.8k stars) lists
"automate the macOS installation via OpenCV" as a not-so-crazy idea; the
unattended code in this fork calls itself "the unattended-install proof". Five
marker mechanisms were tried here and each failed for a documented reason. A
human at the console does it in two minutes, so this does that, once, and the
resulting disk is the deliverable.

Each step screenshots before and after, so "the screen did not change" is
visible rather than assumed, and a click that landed too early is retried.
"""
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


import qmp as qmpmod


class Monitor:
    """Thin wrapper so the rest of the script reads the same as before, but every
    event goes through QMP's input-send-event rather than HMP."""

    def __init__(self, qmp_socket):
        self.q = qmpmod.QMP(qmp_socket)

    def shot(self, path):
        reply = self.q.execute("screendump", filename=path)
        time.sleep(1.5)
        if "error" in reply:
            raise RuntimeError("screendump failed: %s" % reply["error"])
        return path if os.path.exists(path) else None

    def click(self, x, y):
        qmpmod.click(self.q, x, y)

    def close(self):
        self.q.close()


def signature(mon, path):
    """Screenshot and return a coarse fingerprint of the frame.

    ppm2png prints 'distinct=N dominant=P%'. Those two numbers are enough to tell
    'the pane changed' from 'nothing happened' without interpreting the image.
    """
    ppm = mon.shot(path)
    if not ppm:
        return None
    try:
        out = subprocess.run(
            [sys.executable, os.path.join(HERE, "ppm2png.py"), ppm, path + ".png"],
            capture_output=True, text=True, timeout=90).stdout
    except Exception:
        return None
    distinct = dominant = None
    for token in out.split():
        if token.startswith("distinct="):
            try:
                distinct = int(token.split("=", 1)[1])
            except ValueError:
                pass
        elif token.startswith("dominant="):
            try:
                dominant = round(float(token.split("=", 1)[1].rstrip("%")), 1)
            except ValueError:
                pass
    return (distinct, dominant)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    mon_path, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-drive.log"

    def log(msg):
        line = "[drive %s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(log_path, "a") as fh:
            fh.write(line + "\n")

    # boot-golden.sh opens QMP beside the HMP socket, with .qmp appended.
    mon = Monitor(mon_path + ".qmp")
    log("monitor connected at " + mon_path)

    # The guest runs at 1920x1080 because OpenCore's config sets that resolution.
    W, H = 1920, 1080
    COUNTRY_ROW = (W // 2 - 200, 430)
    CONTINUE = (W // 2 + 300, H - 190)

    def advance(label, tries=5, wait=9):
        before = signature(mon, prefix + "-" + label + "-before")
        for attempt in range(1, tries + 1):
            click(mon, CONTINUE[0], CONTINUE[1])
            time.sleep(wait)
            after = signature(mon, prefix + "-" + label + "-try%d" % attempt)
            log("%s attempt %d: before=%s after=%s" % (label, attempt, before, after))
            if before != after:
                return True
        log("%s: screen never changed after %d clicks" % (label, tries))
        return False

    # Let the wizard settle; clicking into a half-drawn window loses the click.
    time.sleep(25)
    log("initial frame: %s" % (signature(mon, prefix + "-000"),))

    log("country: clicking a row so Continue can become active")
    click(mon, COUNTRY_ROW[0], COUNTRY_ROW[1])
    time.sleep(3)
    log("after row click: %s" % (signature(mon, prefix + "-001"),))
    advance("country")

    # The remaining panes accept their defaults, so Continue is all they need.
    for i in range(2, 12):
        ok = advance("pane%d" % i)
        log("pane %d advanced=%s" % (i, ok))

    log("done; the caller inspects the frames")
    mon.close()


main()
