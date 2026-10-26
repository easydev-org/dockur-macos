#!/usr/bin/env python3
"""Click through macOS Setup Assistant to the desktop, and keep every frame.

    setup-assistant-drive.py <monitor.sock> <frame-prefix>

WHY CLICKS AND NOT KEYS. The first version sent sendkey ret/tab and typed the
country name. Nineteen frames later the screen differed from the first by 27 KB
out of 6.2 MB - the clock had moved, the country list had not. The captured frame
explains it: the list holds keyboard focus (blue focus ring) but does not act on
typed letters, and Continue stays grey until a row is actually chosen, which on
this pane means a click. So this clicks.

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


class Monitor:
    def __init__(self, path):
        self.path = path
        self.sock = None
        self.connect()

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(20)
        s.connect(self.path)
        time.sleep(0.4)
        try:
            s.recv(65536)
        except socket.timeout:
            pass
        self.sock = s
        return s

    def cmd(self, line, wait=0.25):
        if self.sock is None:
            self.connect()
        self.sock.sendall((line + "\n").encode())
        time.sleep(wait)
        try:
            self.sock.recv(65536)
        except socket.timeout:
            pass

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def shot(mon, path):
    ppm = path + ".ppm"
    mon.cmd("screendump " + ppm, wait=2.0)
    return ppm if os.path.exists(ppm) else None


def click(mon, x, y, wait=0.5):
    mon.cmd("mouse_move %d %d" % (x, y), wait=0.3)
    mon.cmd("mouse_button 1", wait=0.15)
    mon.cmd("mouse_button 0", wait=wait)


def signature(mon, path):
    """Screenshot and return a coarse fingerprint of the frame.

    ppm2png prints 'distinct=N dominant=P%'. Those two numbers are enough to tell
    'the pane changed' from 'nothing happened' without interpreting the image.
    """
    ppm = shot(mon, path)
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

    mon = Monitor(mon_path)
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
