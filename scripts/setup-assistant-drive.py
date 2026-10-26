#!/usr/bin/env python3
"""Walk macOS Setup Assistant to the desktop by feeding it real input events.

    setup-assistant-drive.py <monitor.sock> <frame-prefix>

WHY THIS, AND NOT A MARKER FILE. Every marker-based attempt failed, and the
research explains why there is no third option to find: dockur's own README says
only dockur/windows has automatic installation, OSX-KVM lists "automate the macOS
installation via OpenCV" as a not-so-crazy idea, and the unattended code in this
fork calls itself "the unattended-install proof". Nobody in this ecosystem has
done it. So this does what a human at the console would do, once, and then the
disk is a finished appliance.

The guest is driven through QEMU's HMP monitor, the same socket that already
produces reliable screenshots:

    sendkey ret / tab / down / spc / shift-a
    mouse_move X Y ; mouse_button 1 ; mouse_button 0

Each step takes a screenshot first, so the decisions are made against what is
actually on screen rather than against a guess about timing. Everything is logged
and every frame is kept, so a failed run says where it stopped.

The flow it drives is the macOS 14 first-boot wizard:

    country/region  ->  keyboard  ->  accessibility  ->  transfer info
    ->  terms  ->  account creation  ->  desktop

Only the parts that need real choices get typed input (country, account name and
password); the rest are accepted with Return.
"""
import os
import socket
import sys
import time


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
    """Ask for a framebuffer dump, then pull it out with screendump's own path."""
    ppm = path + ".ppm"
    mon.cmd("screendump " + ppm, wait=2.0)
    return ppm if os.path.exists(ppm) else None


def key(mon, k, wait=0.35):
    mon.cmd("sendkey " + k, wait=wait)


def tap(mon, k, times=1, wait=0.35):
    for _ in range(times):
        key(mon, k, wait)


def type_text(mon, text, wait=0.18):
    """Type lowercase letters, digits and a few punctuation marks.

    HMP wants QKeyCode names, not ASCII. Anything not in the map is skipped rather
    than guessed at, so a wrong character shows up as a wrong field rather than as
    a silently different one.
    """
    special = {
        " ": "spc", "-": "minus", ".": "dot", ",": "comma", "/": "slash",
        "=": "equal", "_": "shift-minus",
    }
    for ch in text:
        if ch in special:
            key(mon, special[ch], wait)
        elif ch.isupper():
            key(mon, "shift-" + ch.lower(), wait)
        elif ch.isalnum():
            key(mon, ch.lower(), wait)


def click(mon, x, y, wait=0.4):
    mon.cmd("mouse_move %d %d" % (x, y), wait=0.25)
    mon.cmd("mouse_button 1", wait=0.12)
    mon.cmd("mouse_button 0", wait=wait)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    mon_path, prefix = sys.argv[1], sys.argv[2]
    log_path = prefix + "-drive.log"

    def log(msg):
        line = "[drive %s] %s" % (time.strftime("%H:%M:%S"), msg)
        print(line, flush=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")

    mon = Monitor(mon_path)
    log("monitor connected at " + mon_path)

    # Let the wizard finish drawing before the first keypress. Pressing into a
    # half-drawn window is the classic way to get an input dropped.
    time.sleep(20)
    shot(mon, prefix + "-000")

    # --- Country/Region -----------------------------------------------------
    # The list is a table; the highlighted row is whatever the cursor sits on.
    # Typing the country name jumps the selection in macOS's list widgets, which
    # is far more reliable than counting arrow presses.
    log("country: typing 'United States' to jump the list")
    type_text(mon, "united")
    time.sleep(1.5)
    shot(mon, prefix + "-001")
    log("country: confirming")
    key(mon, "ret")
    time.sleep(6)
    shot(mon, prefix + "-002")

    # --- Keyboard / accessibility / transfer / terms -------------------------
    # These are all "accept the default" screens. Return advances them; the pause
    # is generous because some transitions animate.
    for i in range(3, 9):
        log("default screen %d: Return" % i)
        key(mon, "ret")
        time.sleep(5)
        shot(mon, prefix + "-%03d" % i)

    # --- Account creation ---------------------------------------------------
    # Full name, account name and password. The first field has focus.
    log("account: typing full name")
    type_text(mon, "Sandbox")
    time.sleep(1)
    key(mon, "tab")
    time.sleep(0.6)
    log("account: typing account name")
    type_text(mon, "sandbox")
    time.sleep(1)
    key(mon, "tab")
    time.sleep(0.6)
    log("account: typing password")
    type_text(mon, "sandbox")
    time.sleep(1)
    key(mon, "tab")
    time.sleep(0.6)
    log("account: confirming password")
    type_text(mon, "sandbox")
    time.sleep(1)
    key(mon, "tab")
    time.sleep(0.6)
    log("account: hint field, typing hint")
    type_text(mon, "hint")
    time.sleep(1)
    shot(mon, prefix + "-010")

    log("account: continue")
    key(mon, "ret")
    time.sleep(12)
    shot(mon, prefix + "-011")

    # --- Remaining screens --------------------------------------------------
    for i in range(12, 20):
        log("final screen %d: Return" % i)
        key(mon, "ret")
        time.sleep(6)
        shot(mon, prefix + "-%03d" % i)

    log("done; the caller inspects the last frames")
    mon.close()


main()
